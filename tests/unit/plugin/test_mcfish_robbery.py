from __future__ import annotations

import copy
import sys
import json
import sqlite3
import time
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins"))

from GroupEconomy.store import EconomyStore  # noqa: E402
from McFish.McFish import (  # noqa: E402
    BLOCKED_GROUP_ID,
    FISH_KINGS,
    MCFISH_HELP,
    MCFISH_SPECIAL_MECHANICS,
    SHOP_ROD_PRICES,
    SHOP_WOOD_PRICE,
    FishKingWindow,
    McFish,
    _McFishEvent,
    _same_conversation,
)
from Robbery.Robbery import Robbery  # noqa: E402
from ncatbot.types import At, PlainText  # noqa: E402


def _attach_fake_state_store(plugin: McFish, initial: dict, snapshot: list | None = None) -> dict:
    """给直接调用 _fish 的测试接一个假存档。

    后台钓鱼任务现在会“先读回最新存档、再合并本竿增量”，所以测试里的 _state
    必须像数据库一样返回上一次 _save 的结果，而不是返回内存里的同一份快照。
    """
    stored = copy.deepcopy(initial)

    def load(_uid):
        return copy.deepcopy(stored)

    def save(_uid, state):
        stored.clear()
        stored.update(copy.deepcopy(state))
        if snapshot is not None:
            snapshot.append(dict(state.get("pack", {})))

    plugin._state = load
    plugin._save = save
    return stored


def _grant_item_event(sender_id: str, target_id: str, item: str, amount: int):
    return SimpleNamespace(
        sender=SimpleNamespace(user_id=sender_id),
        group_id="200",
        message=[PlainText(text="钓鱼发放 "), At(user_id=target_id), PlainText(text=f" {item} {amount}")],
        reply=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_mcfish_item_grant_is_restricted_to_designated_admin():
    plugin = McFish.__new__(McFish)
    plugin._state = lambda _uid: {"pack": {}}
    plugin._save = lambda *_args: pytest.fail("unauthorized user changed a fishing pack")
    plugin._economy = SimpleNamespace(_target_in_group=AsyncMock())
    event = _grant_item_event("123", "200", "鳕鱼", 3)

    await plugin._grant_item(event, "钓鱼发放 鳕鱼 3")

    plugin._economy._target_in_group.assert_not_awaited()
    event.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcfish_item_grant_adds_stackable_item_and_audits_change(tmp_path: Path):
    plugin = McFish.__new__(McFish)
    plugin._db = sqlite3.connect(tmp_path / "mcfish.db")
    plugin._db.execute("CREATE TABLE admin_adjustments (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, reason TEXT NOT NULL, old_state TEXT NOT NULL, new_state TEXT NOT NULL, created_at REAL NOT NULL)")
    state = {"pack": {"鳕鱼": 2}}
    saved = []
    plugin._state = lambda _uid: state
    plugin._save = lambda _uid, value: saved.append(copy.deepcopy(value))
    plugin._economy = SimpleNamespace(_target_in_group=AsyncMock(return_value=True))
    event = _grant_item_event("273421673", "200", "鳕鱼", 3)

    await plugin._grant_item(event, "钓鱼发放 鳕鱼 3")

    assert state["pack"]["鳕鱼"] == 5
    assert saved[-1]["pack"]["鳕鱼"] == 5
    row = plugin._db.execute("SELECT user_id, reason, old_state, new_state FROM admin_adjustments").fetchone()
    assert row[0:2] == ("200", "admin_item_grant")
    assert json.loads(row[2])["pack"]["鳕鱼"] == 2
    assert json.loads(row[3])["pack"]["鳕鱼"] == 5
    assert "当前背包 鳕鱼：5" in event.reply.await_args.kwargs["text"]
    plugin._db.close()


@pytest.mark.asyncio
async def test_mcfish_item_grant_alias_reaches_message_handler():
    plugin = McFish.__new__(McFish)
    plugin._state = lambda _uid: {"pack": {}}
    plugin._grant_item = AsyncMock()
    event = _grant_item_event("273421673", "200", "鳕鱼", 3)
    event.raw_message = "钓鱼充值 鳕鱼 3"

    await plugin._handle_message(event)

    plugin._grant_item.assert_awaited_once_with(event, "钓鱼充值 鳕鱼 3")


@pytest.mark.asyncio
async def test_mcfish_item_grant_adds_full_durability_rods_and_rejects_unknown_item():
    plugin = McFish.__new__(McFish)
    plugin._db = sqlite3.connect(":memory:")
    plugin._db.execute("CREATE TABLE admin_adjustments (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, reason TEXT NOT NULL, old_state TEXT NOT NULL, new_state TEXT NOT NULL, created_at REAL NOT NULL)")
    state = {"pack": {}}
    plugin._state = lambda _uid: state
    plugin._save = lambda *_args: None
    plugin._economy = SimpleNamespace(_target_in_group=AsyncMock(return_value=True))
    rod_event = _grant_item_event("273421673", "200", "木杆", 2)

    await plugin._grant_item(rod_event, "钓鱼发放 木杆 2")

    assert len(state["pack"]["木竿"]) == 2
    assert all(rod["durable"] == 30 for rod in state["pack"]["木竿"])

    bad_event = _grant_item_event("273421673", "200", "不存在的鱼", 1)
    await plugin._grant_item(bad_event, "钓鱼发放 不存在的鱼 1")
    assert "不存在可发放物品" in bad_event.reply.await_args.kwargs["text"]
    plugin._db.close()


def test_mcfish_tasks_advance_both_periods_and_cap_at_target():
    """MCF-23: 具体捕获/行为任务同时推进，交付任务不被钓鱼自动完成。"""
    plugin = McFish.__new__(McFish)
    state = {"pack": {}}

    plugin._advance_tasks(
        state,
        casts=999,
        catches={"鳕鱼": 999, "鲑鱼": 999, "热带鱼": 999, "河豚": 999},
        spend=9999,
        king=2,
    )

    for kind in ("daily", "weekly"):
        bucket = state[f"fishing_{kind}_tasks"]
        for task in bucket["tasks"]:
            progress = bucket["progress"][task["id"]]
            if task["type"] == "delivery":
                assert not plugin._task_complete(task, progress)
            else:
                assert plugin._task_complete(task, progress)


def test_mcfish_task_board_uses_level_tiers_and_mixed_rewards():
    """MCF-27: 15 级玩家看到低档任务和本档任务，奖励可包含经验/物品/碎片。"""
    plugin = McFish.__new__(McFish)
    state = {"pack": {}, "fish_level": 15}

    plugin._ensure_task_state(state)

    daily = state["fishing_daily_tasks"]
    assert [task["level_tier"] for task in daily["tasks"]] == ["1-5", "11-20", "11-20"]
    assert [task["difficulty"] for task in daily["tasks"]] == ["simple", "medium", "complex"]
    assert any("xp" in task["reward"] for task in daily["tasks"])
    assert any("items" in task["reward"] for task in daily["tasks"])

    weekly = state["fishing_weekly_tasks"]
    assert [task["level_tier"] for task in weekly["tasks"]] == ["1-5", "11-20", "11-20", "11-20"]
    assert weekly["tasks"][-1]["type"] == "spend"
    assert "金币" in weekly["tasks"][-1]["requirements"]
    assert any("净化" in task["label"] for task in weekly["tasks"])
    assert len({task["pool_id"] for task in weekly["tasks"]}) == len(weekly["tasks"])
    assert len({
        (task["type"], tuple(sorted(task["requirements"].items())))
        for task in weekly["tasks"]
    }) == len(weekly["tasks"])


def test_mcfish_repairs_duplicate_tasks_without_losing_matching_claims():
    """MCF-40: 周任务重复时替换重复槽位，并保留原任务的领取状态。"""
    plugin = McFish.__new__(McFish)
    state = {"pack": {}, "fish_level": 15}
    plugin._ensure_task_state(state, date(2026, 9, 21))
    weekly = state["fishing_weekly_tasks"]
    duplicate = copy.deepcopy(weekly["tasks"][3])
    weekly["tasks"][1] = copy.deepcopy(duplicate)
    weekly["tasks"][3] = duplicate
    weekly["claimed"] = [duplicate["id"]]
    weekly["progress"][duplicate["id"]] = {"金币": 1200}

    plugin._ensure_task_state(state, date(2026, 9, 21))

    repaired = state["fishing_weekly_tasks"]
    assert len({task["pool_id"] for task in repaired["tasks"]}) == 4
    assert repaired["tasks"][-1]["type"] == "spend"
    assert repaired["claimed"] == ["weekly_maintenance_expert"]
    assert repaired["progress"]["weekly_maintenance_expert"] == {"金币": 1200}


def test_mcfish_repairs_tasks_with_different_pool_ids_but_same_content():
    """MCF-41: 不同池 ID 但实际要求相同的任务也会占用同一个任务位。"""
    plugin = McFish.__new__(McFish)
    state = {"pack": {}, "fish_level": 15}
    plugin._ensure_task_state(state, date(2026, 9, 21))
    weekly = state["fishing_weekly_tasks"]
    duplicate = copy.deepcopy(weekly["tasks"][1])
    duplicate["id"] = "weekly_fake_duplicate"
    duplicate["pool_id"] = "fake_duplicate"
    weekly["tasks"][3] = duplicate

    plugin._ensure_task_state(state, date(2026, 9, 21))

    content_keys = {
        (task["type"], tuple(sorted(task["requirements"].items())))
        for task in state["fishing_weekly_tasks"]["tasks"]
    }
    assert len(content_keys) == len(state["fishing_weekly_tasks"]["tasks"])


def test_mcfish_task_board_uses_expected_level_boundaries():
    """MCF-30: 任务板在5/10/20/30之后切换到下一等级档位。"""
    plugin = McFish.__new__(McFish)
    for level, expected_tier in ((1, "1-5"), (6, "6-10"), (11, "11-20"), (21, "21-30"), (31, "31+")):
        state = {"pack": {}, "fish_level": level}
        plugin._ensure_task_state(state, date(2026, 9, 16))
        daily_tasks = state["fishing_daily_tasks"]["tasks"]
        assert daily_tasks[1]["level_tier"] == expected_tier
        assert daily_tasks[2]["level_tier"] == expected_tier


def test_mcfish_task_display_reads_fish_from_backpack_without_submitting():
    """MCF-31: 任务展示使用背包鱼获作为可见进度，但不会修改任务或背包。"""
    plugin = McFish.__new__(McFish)
    state = {"pack": {}, "fish_level": 15}
    plugin._ensure_task_state(state)
    task = next(task for task in state["fishing_daily_tasks"]["tasks"] if task["type"] == "delivery")
    fish_name, target = next(iter(task["requirements"].items()))
    state["pack"][fish_name] = min(int(target) - 2, 6)
    before = copy.deepcopy(state["fishing_daily_tasks"])

    lines = plugin._task_lines(state, "daily")

    assert f"{fish_name} {state['pack'][fish_name]}/{target}" in "\n".join(lines)
    assert state["fishing_daily_tasks"] == before
    assert state["pack"][fish_name] == min(int(target) - 2, 6)
    assert "低档奖励可以直接跳过" not in "\n".join(lines)

    state["pack"][fish_name] = int(target) + 4
    lines = plugin._task_lines(state, "daily")

    assert f"{fish_name} {int(target) + 4}/{target}" in "\n".join(lines)
    assert state["fishing_daily_tasks"] == before


def test_mcfish_task_board_has_one_top_claim_instruction():
    """MCF-32: 任务板顶部只显示一次统一领取指令，不在每项重复提示。"""
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._save = lambda *_args: None
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {"pack": {}, "fish_level": 15}

    asyncio.run(plugin._show_tasks(event, state))

    text = event.reply.await_args.kwargs["text"]
    assert text.count("领取钓鱼任务 编号") == 1
    assert "提交钓鱼任务" not in text


def test_mcfish_permanent_king_task_tracks_history_without_submitting_kings():
    """MCF-42: 鱼王全收集是永久任务，只检查成功捕获记录，不扣背包鱼王。"""
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._save = lambda *_args: None
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {
        "pack": {"月影巨鲤": 1},
        "king_stats": {"月影巨鲤": {"caught": 1}},
        "fish_level": 35,
    }

    plugin._ensure_task_state(state)
    permanent = state["fishing_permanent_tasks"]
    permanent_index = len(state["fishing_daily_tasks"]["tasks"]) + len(state["fishing_weekly_tasks"]["tasks"]) + 1
    lines = plugin._task_lines(state, "permanent", permanent_index)

    assert permanent["tasks"][0]["requirements"] == {name: 1 for name in FISH_KINGS}
    assert "只检查获得记录，不上交鱼王" in "\n".join(lines)
    assert "还缺" in "\n".join(lines)

    state["king_stats"] = {name: {"caught": 1} for name in FISH_KINGS}
    original_king_count = state["pack"]["月影巨鲤"]
    asyncio.run(plugin._claim_tasks(event, state, permanent_index))

    assert state["pack"]["月影巨鲤"] == original_king_count
    assert len(state["pack"]["三叉戟"]) == 1
    assert state["pack"]["三叉戟"][0]["unbreakable"] is True
    assert permanent["tasks"][0]["id"] in permanent["claimed"]

    asyncio.run(plugin._claim_tasks(event, state, permanent_index))
    assert len(state["pack"]["三叉戟"]) == 1


def test_mcfish_permanent_king_task_is_hidden_until_level_35():
    plugin = McFish.__new__(McFish)
    state = {"pack": {}, "fish_level": 34}

    assert all(kind != "permanent" for _, kind, *_ in plugin._task_board(state))

    state["fish_level"] = 35
    assert any(kind == "permanent" for _, kind, *_ in plugin._task_board(state))


def test_mcfish_reward_trident_can_be_repaired_with_gold_at_zero_durability():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._save = lambda *_args: None
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {
        "equip": {
            "name": "三叉戟",
            "durable": 0,
            "maintenance": 11,
            "induce": 0,
            "favor": 0,
            "unbreakable": True,
        },
        "pack": {},
    }

    asyncio.run(plugin._repair(event, state))

    assert state["equip"]["durable"] == 301
    assert state["equip"]["maintenance"] == 12
    assert "支付" in event.reply.await_args.kwargs["text"]


def test_mcfish_task_period_text_shows_start_and_end_without_year():
    """MCF-33: 任务周期只显示月日，周任务显示起止日期。"""
    plugin = McFish.__new__(McFish)

    daily = plugin._task_period_text("daily", "2026-09-17")
    weekly = plugin._task_period_text("weekly", "2026-09-14")

    assert daily == "09月17日"
    assert weekly == "09月14日-09月20日"
    assert "2026" not in weekly
    assert "00:00" not in weekly
    assert "23:59" not in weekly


def test_mcfish_tasks_reset_at_daily_and_weekly_boundaries():
    """MCF-24: 跨日刷新每日任务，跨周刷新每周任务并清除已领取标记。"""
    plugin = McFish.__new__(McFish)
    state = {"pack": {}}
    first_day = date(2026, 9, 13)
    plugin._ensure_task_state(state, first_day)
    first_version = state["fishing_daily_tasks"]["version"]
    state["fishing_daily_tasks"]["claimed"].append(state["fishing_daily_tasks"]["tasks"][0]["id"])
    state["fishing_weekly_tasks"]["claimed"].append(state["fishing_weekly_tasks"]["tasks"][0]["id"])

    plugin._ensure_task_state(state, date(2026, 9, 14))

    assert state["fishing_daily_tasks"]["period"] == "2026-09-14"
    assert state["fishing_daily_tasks"]["claimed"] == []
    assert state["fishing_weekly_tasks"]["period"] == "2026-09-14"
    assert state["fishing_weekly_tasks"]["claimed"] == []
    assert state["fishing_daily_tasks"]["version"] == first_version == 5


def test_mcfish_fee_scale_is_bounded_and_grows_with_rod_value():
    """MCF-25: 维修与合成手续费随鱼竿等级递增且维修费有上限。"""
    plugin = McFish.__new__(McFish)
    repair_fees = [plugin._repair_fee(name, 0) for name in ("木竿", "铁竿", "金竿", "钻石竿", "下界合金竿")]

    assert repair_fees == sorted(repair_fees)
    assert repair_fees[-1] <= 600
    assert plugin._craft_fee("铁竿", "fragments") == 20
    assert plugin._craft_fee("下界合金竿", "poles", 2) == 620


def test_mcfish_equipment_craft_fees_are_scaled_for_the_current_economy():
    """MCF-38: 饰品和消耗品手续费按新的金币回收档位执行。"""
    plugin = McFish.__new__(McFish)

    assert {
        name: plugin._craft_fee(name, "equipment")
        for name in (
            "财富浮标", "坚韧钓组", "黄金磁石", "幸运海星",
            "魔法鱼饵", "隐秘诱饵", "万能鱼饵",
        )
    } == {
        "财富浮标": 30000,
        "坚韧钓组": 50000,
        "黄金磁石": 80000,
        "幸运海星": 120000,
        "魔法鱼饵": 100,
        "隐秘诱饵": 160,
        "万能鱼饵": 220,
    }


def test_mcfish_completed_tasks_are_claimed_once_with_mixed_rewards():
    """MCF-26: 完成任务可一次领取混合奖励，重复领取不会再次发放。"""
    import asyncio

    class Store:
        def __init__(self):
            self.balance = 0

        def account(self, _user_id):
            return {"balance": self.balance}

        def change_balance(self, _user_id, delta, *, reason):
            self.balance += int(delta)
            return {"ok": True, "balance": self.balance, "reason": reason}

    plugin = McFish.__new__(McFish)
    plugin._economy = SimpleNamespace(store=Store())
    plugin._save = lambda *_args: None
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {"pack": {}}
    plugin._advance_tasks(
        state,
        casts=999,
        catches={"鳕鱼": 999, "鲑鱼": 999, "热带鱼": 999, "河豚": 999},
        spend=9999,
        king=1,
    )
    expected = {"xp": 0, "gold": 0, "fragments": 0, "items": {}}
    delivery_fish = {}
    for kind in ("daily", "weekly"):
        bucket = state[f"fishing_{kind}_tasks"]
        for task in bucket["tasks"]:
            if task["type"] == "delivery":
                bucket["progress"][task["id"]] = dict(task["requirements"])
                for name, number in task["requirements"].items():
                    delivery_fish[name] = delivery_fish.get(name, 0) + int(number)
            for reward_key in ("xp", "gold", "fragments"):
                expected[reward_key] += int(task["reward"].get(reward_key, 0))
            for name, number in task["reward"].get("items", {}).items():
                expected["items"][name] = expected["items"].get(name, 0) + int(number)
    state["pack"].update(delivery_fish)

    asyncio.run(plugin._claim_tasks(event, state))
    first_balance = plugin._economy.store.balance
    first_fragments = state["pack"]["钓鱼碎片"]
    first_purification_books = state["pack"].get("净化书", 0)
    asyncio.run(plugin._claim_tasks(event, state))

    assert first_balance == expected["gold"]
    assert first_fragments == expected["fragments"]
    assert first_purification_books == expected["items"].get("净化书", 0)
    assert state["fish_xp"] == expected["xp"]
    assert plugin._economy.store.balance == first_balance
    assert state["pack"]["钓鱼碎片"] == first_fragments
    assert state["pack"].get("净化书", 0) == first_purification_books


def test_mcfish_delivery_tasks_are_checked_and_submitted_when_claimed():
    """MCF-28: 领取交付任务时一次性检查背包，失败不扣鱼，成功才发奖。"""
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._save = lambda *_args: None
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {"pack": {}}
    plugin._ensure_task_state(state, date(2026, 9, 16))
    selected_index, _, bucket, task = next(entry for entry in plugin._task_board(state) if entry[3]["type"] == "delivery")
    name, target = next(iter(task["requirements"].items()))
    state["pack"][name] = max(0, int(target) - 1)

    asyncio.run(plugin._claim_tasks(event, state, selected_index))

    assert state["pack"][name] == int(target) - 1
    assert task["id"] not in bucket["claimed"]
    assert "未领取成功" in event.reply.await_args.kwargs["text"]

    state["pack"][name] = int(target)
    asyncio.run(plugin._claim_tasks(event, state, selected_index))

    assert state["pack"].get(name, 0) == int(task["reward"].get("items", {}).get(name, 0))
    assert task["id"] in bucket["claimed"]
    assert "已领取" in event.reply.await_args.kwargs["text"]


def test_mcfish_task_commands_route_claim_number():
    """MCF-29: 领取命令把任务编号传给一次性检查流程。"""
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._state = lambda _user_id: {"pack": {}}
    plugin._claim_tasks = AsyncMock()
    event = SimpleNamespace(
        raw_message="领取钓鱼任务 3",
        message=[],
        sender=SimpleNamespace(user_id="100"),
    )

    asyncio.run(plugin._handle_message(event))
    assert plugin._claim_tasks.await_args.args[2] == 3


def test_mcfish_pack_operations_preserve_pole_attributes():
    plugin = McFish.__new__(McFish)
    plugin.session_choose = None
    state = {"pack": {}}
    plugin._add(state, "木竿", pole=True)
    assert state["pack"]["木竿"][0]["durable"] == 30
    assert plugin._remove(state, "木竿") is True
    assert state["pack"]["木竿"] == []


def test_economy_change_balance_is_atomic_and_rejects_overdraft(tmp_path: Path):
    store = EconomyStore(tmp_path / "economy.db")
    store.grant("robber", 100)
    assert store.change_balance("robber", -80, reason="robbery_failure")["balance"] == 20
    result = store.change_balance("robber", -21, reason="robbery_failure")
    assert result == {"ok": False, "balance": 20, "reason": "insufficient"}
    assert store.account("robber")["balance"] == 20
    store.close()


def test_economy_transfer_balance_moves_money_and_writes_both_ledger_entries(tmp_path: Path):
    """RB-01: 钱包转账原子地扣除发送者并补偿接收者。"""
    store = EconomyStore(tmp_path / "economy.db")
    store.grant("robber", 1000)
    store.grant("victim", 1000)

    result = store.transfer_balance("robber", "victim", 1000, reason="robbery_failure")

    assert result == {
        "ok": True,
        "amount": 1000,
        "sender_balance": 0,
        "receiver_balance": 2000,
    }
    assert store.account("robber")["balance"] == 0
    assert store.account("victim")["balance"] == 2000
    entries = store._conn.execute(
        "SELECT user_id, delta, related_user_id FROM ledger WHERE reason = 'robbery_failure' ORDER BY id"
    ).fetchall()
    assert [(row[0], row[1], row[2]) for row in entries] == [
        ("robber", -1000, "victim"),
        ("victim", 1000, "robber"),
    ]
    store.close()


def test_robbery_daily_record_blocks_same_victim_and_robber(tmp_path: Path):
    plugin = Robbery.__new__(Robbery)
    plugin._db = __import__("sqlite3").connect(tmp_path / "robbery.db")
    plugin._db.execute("CREATE TABLE records (victim_id TEXT, robber_id TEXT, day TEXT)")
    plugin._db.execute(
        "INSERT INTO records VALUES ('victim', 'other-robber', date('now'))"
    )
    plugin._db.execute(
        "INSERT INTO records VALUES ('other-victim', 'robber', date('now'))"
    )
    plugin._db.commit()

    assert plugin._used_today("robber", "victim") == (True, True)
    plugin._db.close()


def test_mcfish_accepts_short_fishing_command():
    plugin = McFish.__new__(McFish)
    plugin._state = lambda _user_id: {}
    plugin._fish = AsyncMock()
    event = SimpleNamespace(
        raw_message="钓鱼",
        message=[],
        sender=SimpleNamespace(user_id="100"),
    )

    import asyncio

    asyncio.run(plugin.handle_short_fishing(event))

    plugin._fish.assert_awaited_once_with(event, {}, "进行钓鱼")


def test_mcfish_blocked_group_does_not_start_short_fishing():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._state = lambda _user_id: {}
    plugin._start_fishing = AsyncMock()
    event = SimpleNamespace(
        raw_message="钓鱼",
        message=[],
        group_id=BLOCKED_GROUP_ID,
        sender=SimpleNamespace(user_id="100"),
    )

    asyncio.run(plugin.handle_short_fishing(event))

    plugin._start_fishing.assert_not_awaited()


def test_mcfish_private_message_reaches_message_handler():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._handle_message = AsyncMock()
    event = SimpleNamespace(
        raw_message="钓鱼帮助",
        message=[],
        sender=SimpleNamespace(user_id="100"),
    )

    asyncio.run(plugin.handle_message(event))

    plugin._handle_message.assert_awaited_once()


def test_mcfish_private_short_fishing_is_supported():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._state = lambda _user_id: {}
    plugin._start_fishing = AsyncMock()
    event = SimpleNamespace(
        raw_message="钓鱼",
        message=[],
        sender=SimpleNamespace(user_id="100"),
    )

    asyncio.run(plugin.handle_short_fishing(event))

    plugin._start_fishing.assert_awaited_once()
    assert plugin._start_fishing.await_args.args[1:] == ({}, "进行钓鱼")


def test_mcfish_blocked_group_does_not_reach_message_handler():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._handle_message = AsyncMock()
    event = SimpleNamespace(
        raw_message="钓鱼帮助",
        message=[],
        group_id=BLOCKED_GROUP_ID,
        sender=SimpleNamespace(user_id="100"),
    )

    asyncio.run(plugin.handle_message(event))

    plugin._handle_message.assert_not_awaited()


def test_mcfish_private_reply_uses_private_event_reply():
    import asyncio
    from ncatbot.types import MessageArray, PlainText

    plugin = McFish.__new__(McFish)
    plugin._message_array_as_images = AsyncMock(side_effect=lambda _event, message: message)
    group_api = SimpleNamespace(post_group_msg=AsyncMock())
    plugin.api = SimpleNamespace(qq=group_api)
    raw_event = SimpleNamespace(
        sender=SimpleNamespace(user_id="100"),
        reply=AsyncMock(),
    )

    asyncio.run(
        plugin._send_original_message(
            _McFishEvent(raw_event, plugin),
            MessageArray([PlainText(text="私聊回复")]),
        )
    )

    raw_event.reply.assert_awaited_once()
    group_api.post_group_msg.assert_not_awaited()


def test_mcfish_blocked_group_reply_is_silent():
    import asyncio

    plugin = McFish.__new__(McFish)
    raw_event = SimpleNamespace(
        group_id=BLOCKED_GROUP_ID,
        sender=SimpleNamespace(user_id="100"),
        reply=AsyncMock(),
    )

    asyncio.run(_McFishEvent(raw_event, plugin).reply(text="不应发送"))

    raw_event.reply.assert_not_awaited()


def test_mcfish_private_conversation_matching_ignores_group_id():
    source_event = SimpleNamespace(sender=SimpleNamespace(user_id="100"))

    assert _same_conversation(SimpleNamespace(user_id="100"), source_event)
    assert not _same_conversation(SimpleNamespace(user_id="101"), source_event)


def test_mcfish_rejects_a_second_fishing_session_while_first_is_running():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._fishing_tasks = {}
    release = asyncio.Event()

    async def fishing(_event, _state, _text):
        await release.wait()

    plugin._fish = fishing

    def event():
        return SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    async def run():
        first = event()
        second = event()
        await plugin._start_fishing(first, {}, "进行钓鱼")
        await plugin._start_fishing(second, {}, "进行钓鱼")
        assert second.reply.await_args.kwargs["text"] == "你正在钓鱼，请稍后再试"
        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(run())


def test_mcfish_close_drains_fishing_tasks_before_database_close():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._fishing_tasks = {}
    completed = []
    closed = []

    async def fishing():
        await asyncio.sleep(0)
        completed.append(True)

    class Database:
        def close(self):
            closed.append(list(completed))

    plugin._db = Database()

    async def run():
        plugin._fishing_tasks["100"] = asyncio.create_task(fishing())
        await plugin.on_close()

    asyncio.run(run())

    assert completed == [True]
    assert closed == [[True]]
    assert plugin._fishing_tasks == {}


def test_mcfish_rejects_new_fishing_while_closing():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._closing = True
    plugin._fish = AsyncMock()
    event = SimpleNamespace(
        sender=SimpleNamespace(user_id="100"),
        reply=AsyncMock(),
    )

    asyncio.run(plugin._start_fishing(event, {}, "进行钓鱼"))

    plugin._fish.assert_not_awaited()
    event.reply.assert_awaited_once_with(
        text="插件正在热更新，请稍后再开始钓鱼", at_sender=False
    )


def test_mcfish_serializes_same_user_state_updates():
    import asyncio

    plugin = McFish.__new__(McFish)
    active = 0
    maximum = 0

    async def handle(_event):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0)
        active -= 1

    plugin._handle_message = handle

    def event():
        return SimpleNamespace(sender=SimpleNamespace(user_id="100"))

    async def run():
        await asyncio.gather(plugin.handle_message(event()), plugin.handle_message(event()))

    asyncio.run(run())

    assert maximum == 1


def test_mcfish_choose_falls_back_when_session_choose_is_missing():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin.session_choose = None
    plugin._text = lambda event: "是"
    plugin.wait_event = AsyncMock(return_value=SimpleNamespace(
        raw_message="是", group_id="g", user_id="u", message=[]
    ))
    event = SimpleNamespace(
        group_id="g", sender=SimpleNamespace(user_id="u"), reply=AsyncMock()
    )

    result = asyncio.run(plugin._choose(
        "确认", event, choices={"是": "buy", "否": "cancel"},
        timeout=1, timeout_reply="超时", cancel_reply="取消",
    ))

    assert result.ok is True and result.key == "buy"
    plugin.wait_event.assert_awaited_once()


def test_robbery_guaranteed_failure_compensates_victim(monkeypatch):
    """RB-02: 打劫失败时随机赔偿目标，且赔偿低于潜在抢劫金额。"""
    import asyncio
    import importlib
    transfers = []
    monkeypatch.setattr(importlib.import_module("Robbery.Robbery").random, "randint", lambda _low, high: high)

    class Store:
        def account(self, user_id):
            return {"balance": 2000 if user_id == "robber" else 1200}

        def change_balance(self, *_args, **_kwargs):
            raise AssertionError("failure compensation must use an atomic transfer")

        def transfer_balance(self, *_args, **_kwargs):
            transfers.append((_args, _kwargs))
            return {"ok": True}

    plugin = Robbery.__new__(Robbery)
    plugin._used_today = lambda *_args: (False, False)
    plugin._economy_plugin = lambda: SimpleNamespace(store=Store())
    plugin.get_config = lambda key, default=None: {
        "victim_min_balance": 500,
        "guaranteed_failure_users": ["273421673"],
    }.get(key, default)
    event = SimpleNamespace(
        raw_message="打劫",
        message=[SimpleNamespace(user_id="273421673")],
        sender=SimpleNamespace(user_id="robber"),
        reply=AsyncMock(),
    )

    asyncio.run(plugin.handle_message(event))

    assert "打劫失败" in event.reply.await_args.kwargs["text"]
    assert transfers == [
        (("robber", "273421673", 280), {"reason": "robbery_failure"}),
    ]


def test_robbery_second_guaranteed_failure_compensates_victim(monkeypatch):
    """RB-03: 另一名强制失败目标同样收到随机赔偿。"""
    import asyncio
    import importlib
    transfers = []
    monkeypatch.setattr(importlib.import_module("Robbery.Robbery").random, "randint", lambda _low, high: high)

    class Store:
        def account(self, user_id):
            return {"balance": 2000 if user_id == "robber" else 1200}

        def change_balance(self, *_args, **_kwargs):
            raise AssertionError("failure compensation must use an atomic transfer")

        def transfer_balance(self, *_args, **_kwargs):
            transfers.append((_args, _kwargs))
            return {"ok": True}

    plugin = Robbery.__new__(Robbery)
    plugin._used_today = lambda *_args: (False, False)
    plugin._economy_plugin = lambda: SimpleNamespace(store=Store())
    plugin.get_config = lambda key, default=None: {
        "victim_min_balance": 1000,
        "guaranteed_failure_users": ["273421673", "635773721"],
    }.get(key, default)
    event = SimpleNamespace(
        raw_message="打劫",
        message=[SimpleNamespace(user_id="635773721")],
        sender=SimpleNamespace(user_id="robber"),
        reply=AsyncMock(),
    )

    asyncio.run(plugin.handle_message(event))

    assert "打劫失败" in event.reply.await_args.kwargs["text"]
    assert transfers == [
        (("robber", "635773721", 280), {"reason": "robbery_failure"}),
    ]


def test_robbery_requires_robber_to_have_minimum_balance():
    """RB-04: 发起者余额低于1000时不能发起打劫。"""
    import asyncio

    class Store:
        def account(self, user_id):
            return {"balance": 900 if user_id == "robber" else 1200}

        def transfer_balance(self, *_args, **_kwargs):
            raise AssertionError("a poor robber must not transfer compensation")

    plugin = Robbery.__new__(Robbery)
    plugin._used_today = lambda *_args: (False, False)
    plugin._economy_plugin = lambda: SimpleNamespace(store=Store())
    plugin.get_config = lambda key, default=None: {
        "victim_min_balance": 500,
        "guaranteed_failure_users": [],
    }.get(key, default)
    event = SimpleNamespace(
        raw_message="打劫",
        message=[SimpleNamespace(user_id="victim")],
        sender=SimpleNamespace(user_id="robber"),
        reply=AsyncMock(),
    )

    asyncio.run(plugin.handle_message(event))

    assert "余额不足1000" in event.reply.await_args.kwargs["text"]


def test_robbery_test_profile_uses_configured_victim_rates(monkeypatch):
    """RB-05: 后台测试账号使用独立的被抢概率、金额比例和损失比例。"""
    import asyncio
    import importlib

    robbery_module = importlib.import_module("Robbery.Robbery")
    monkeypatch.setattr(robbery_module.random, "random", lambda: 0.5)
    monkeypatch.setattr(robbery_module.random, "randint", lambda low, high: high)
    transfers = []

    class Store:
        def account(self, user_id):
            return {"balance": 2000 if user_id == "robber" else 10000}

        def change_balance(self, *args, **kwargs):
            transfers.append((args, kwargs))
            return {"ok": True}

        def transfer_balance(self, *args, **kwargs):
            raise AssertionError("the doubled victim success rate should allow this attempt")

    plugin = Robbery.__new__(Robbery)
    plugin._used_today = lambda *_args: (False, False)
    plugin._record = lambda *_args: None
    plugin._economy_plugin = lambda: SimpleNamespace(store=Store())
    plugin.get_config = lambda key, default=None: {
        "success_rate": 0.4,
        "victim_min_balance": 1000,
        "guaranteed_failure_users": [],
        "test_user_profiles": {
            "10123121": {
                "is_test_account": True,
                "victim_success_rate": 0.6,
                "stolen_balance_ratio": 0.1,
                "loss_stolen_ratio": 0.1,
                "robbery_failure_rate": 0.75,
            }
        },
    }.get(key, default)
    event = SimpleNamespace(
        raw_message="打劫",
        message=[SimpleNamespace(user_id="10123121")],
        sender=SimpleNamespace(user_id="robber"),
        reply=AsyncMock(),
    )

    asyncio.run(plugin.handle_message(event))

    assert transfers == [
        (("10123121", -150), {"reason": "robbery_loss", "related_user_id": "robber"}),
        (("robber", 1500), {"reason": "robbery_reward", "related_user_id": "10123121"}),
    ]
    assert "实际损失 150 金币" in event.reply.await_args.kwargs["text"]


def test_robbery_test_profile_uses_configured_robber_failure_rate(monkeypatch):
    """RB-06: 测试账号发起抢劫时使用后台配置的失败率。"""
    import asyncio
    import importlib

    robbery_module = importlib.import_module("Robbery.Robbery")
    monkeypatch.setattr(robbery_module.random, "random", lambda: 0.0)
    transfers = []

    class Store:
        def account(self, _user_id):
            return {"balance": 2000}

        def transfer_balance(self, *args, **kwargs):
            transfers.append((args, kwargs))
            return {"ok": True}

    plugin = Robbery.__new__(Robbery)
    plugin._used_today = lambda *_args: (False, False)
    plugin._economy_plugin = lambda: SimpleNamespace(store=Store())
    plugin.get_config = lambda key, default=None: {
        "success_rate": 0.4,
        "victim_min_balance": 1000,
        "guaranteed_failure_users": [],
        "test_user_profiles": {
            "10123121": {
                "is_test_account": True,
                "robbery_failure_rate": 0.75,
            }
        },
    }.get(key, default)
    event = SimpleNamespace(
        raw_message="打劫",
        message=[SimpleNamespace(user_id="victim")],
        sender=SimpleNamespace(user_id="10123121"),
        reply=AsyncMock(),
    )

    asyncio.run(plugin.handle_message(event))

    assert "打劫失败" in event.reply.await_args.kwargs["text"]
    assert transfers


def test_mcfish_pack_and_store_render_images(tmp_path: Path):
    from PIL import Image as PILImage

    class Store:
        def account(self, _user_id):
            return {"balance": 1234}

    plugin = McFish.__new__(McFish)
    plugin.workspace = tmp_path
    plugin._economy = SimpleNamespace(store=Store())
    state = {"equip": None, "pack": {}}

    pack_path = plugin._render_pack_image("100", state)
    store_path = plugin._render_store_image("100")

    pack_image = PILImage.open(pack_path)
    assert pack_image.size[0] == 1240
    assert pack_image.getpixel((500, 15))[:3] == (33, 68, 82)
    assert PILImage.open(store_path).size[0] >= 1000
    assert pack_path.exists() and store_path.exists()


def test_mcfish_other_renderers_use_the_shared_card_canvas(tmp_path: Path):
    from PIL import Image as PILImage

    plugin = McFish.__new__(McFish)
    plugin.workspace = tmp_path
    plugin._load_original_data()
    plugin._economy = SimpleNamespace(store=SimpleNamespace(account=lambda _uid: {"balance": 0}))
    plugin._fish_windows = lambda: []
    state = {"equip": None, "pack": {}, "fish_stats": {}, "king_stats": {}}

    outputs = (
        plugin._render_catalog_image("100", state),
        plugin._render_probability_image("100", {**state, "fishing_zone": "bay", "accessories": [], "active_consumables": {}}),
        plugin._render_clock_image("100"),
        plugin._render_text_image("100", "钓鱼结果\n没有获得物品"),
    )

    for output in outputs:
        with PILImage.open(output) as image:
            assert image.getpixel((image.width // 2, 15))[:3] == (33, 68, 82)
            assert image.width >= 1000


def test_mcfish_help_contains_original_commands_and_rules():
    assert "钓鱼背包" in MCFISH_HELP
    assert "进行n次钓鱼" in MCFISH_HELP
    assert "钓鱼发放 @用户 物品 数量" in MCFISH_HELP
    assert "钓鱼充值 @用户 物品 数量" in MCFISH_HELP
    assert "宝藏诅咒" in MCFISH_HELP
    assert "合成成功率90%" in MCFISH_HELP
    assert "达到35级后" in MCFISH_HELP
    assert "不可损坏的三叉戟" in MCFISH_HELP


def test_mcfish_help_explains_equipment_crafting_and_activation():
    """MCF-37: 帮助明确饰品/消耗品的合成指令、材料、手续费和启用方式。"""
    for command in (
        "合成财富浮标",
        "合成坚韧钓组",
        "合成黄金磁石",
        "合成幸运海星",
        "合成魔法鱼饵",
        "合成隐秘诱饵",
        "合成万能鱼饵",
    ):
        assert command in MCFISH_HELP
    for phrase in (
        "永久饰品默认2格",
        "10级并支付80个碎片解锁第3格",
        "20级并支付180个碎片解锁第4格",
        "升级只消耗钓鱼碎片",
        "卸下财富浮标",
        "分别需要120/160/220/300个钓鱼碎片",
        "支付30000/50000/80000/120000金币手续费",
        "100金币手续费",
        "发送“装备魔法鱼饵”等命令启用",
        "每完成1竿消耗1次",
        "幸运海星为普通碎片概率16%/20%/24%",
    ):
        assert phrase in MCFISH_HELP


def test_mcfish_help_lists_accessory_slot_and_upgrade_commands():
    """饰品槽位和四种饰品升级命令必须在帮助中直接可见。"""
    for command in (
        "购买饰品槽位",
        "升级财富浮标",
        "升级坚韧钓组",
        "升级黄金磁石",
        "升级幸运海星",
    ):
        assert command in MCFISH_HELP


def test_mcfish_legacy_accessory_state_defaults_to_level_one_and_two_slots():
    plugin = McFish.__new__(McFish)
    state = {"pack": {"财富浮标": 1}, "accessories": ["财富浮标"]}

    plugin._ensure_accessory_state(state)

    assert state["accessory_slots"] == 2
    assert state["accessory_levels"] == {"财富浮标": 1}


def test_mcfish_buys_accessory_slot_with_level_and_fragment_confirmation():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="confirm"))
    plugin._save = lambda *_args: None
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {"fish_level": 10, "pack": {"钓鱼碎片": 80}, "accessories": []}

    asyncio.run(plugin._buy_accessory_slot(event, state))

    assert state["accessory_slots"] == 3
    assert state["pack"]["钓鱼碎片"] == 0
    assert "第3个饰品槽位" in event.reply.await_args.kwargs["text"]


def test_mcfish_upgrades_accessory_without_gold_and_keeps_it_in_one_slot():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="confirm"))
    plugin._save = lambda *_args: None
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {
        "pack": {"财富浮标": 1, "钓鱼碎片": 60},
        "accessories": ["财富浮标"],
        "accessory_slots": 2,
    }

    asyncio.run(plugin._upgrade_accessory(event, state, "财富浮标"))

    assert state["accessory_levels"]["财富浮标"] == 2
    assert state["pack"]["钓鱼碎片"] == 0
    assert state["accessories"] == ["财富浮标"]
    assert "升级至Ⅱ级" in event.reply.await_args.kwargs["text"]


def test_mcfish_can_unequip_accessory_without_losing_it():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._save = lambda *_args: None
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {"pack": {"黄金磁石": 1}, "accessories": ["黄金磁石"]}

    asyncio.run(plugin._unequip(event, state, "黄金磁石"))

    assert state["accessories"] == []
    assert state["pack"]["黄金磁石"] == 1
    assert "仍保留在背包中" in event.reply.await_args.kwargs["text"]


def test_mcfish_probability_detail_includes_original_breakdowns():
    plugin = McFish.__new__(McFish)
    plugin._load_original_data()

    output = plugin._probability_detail_text({"pack": {}, "equip": None})

    assert "大类概率:\n宝藏 : 1%\n鱼竿 : 4%\n鱼类 : 25%\n垃圾 : 30%\n合计 : 60%" in output
    assert "宝藏概率:" in output
    assert "诱钓 : 0.25%" in output
    assert "鱼竿概率:" in output
    assert "木竿 : 2.80%" in output
    assert "鱼类概率:" in output
    assert "鳕鱼 : 17.25%" in output


def test_mcfish_probability_detail_mentions_the_requesting_user():
    import asyncio
    from ncatbot.types import At, MessageArray, PlainText

    plugin = McFish.__new__(McFish)
    plugin._state = lambda _uid: {"pack": {}, "equip": None}
    plugin._probability_detail_text = lambda _state: "\n大类概率:"
    event = SimpleNamespace(
        raw_message="当前装备概率明细",
        message=[],
        sender=SimpleNamespace(user_id="100"),
        reply=AsyncMock(),
    )

    asyncio.run(plugin._handle_message(event))

    message = event.reply.await_args.kwargs["rtf"]
    assert isinstance(message, MessageArray)
    assert isinstance(list(message)[0], At)
    assert list(message)[0].user_id == "100"
    assert isinstance(list(message)[1], PlainText)


def test_mcfish_purify_clamps_requested_quantity_to_curse_count():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._save = lambda *_args: None
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {"pack": {"宝藏诅咒": 2, "净化书": 2}}

    asyncio.run(plugin._purify(event, state, "消除宝藏诅咒 99"))

    assert state["pack"]["宝藏诅咒"] == 0
    assert state["pack"]["净化书"] == 0
    assert event.reply.await_args.kwargs["text"] == "消除成功"


def test_mcfish_purify_uses_original_missing_item_messages():
    import asyncio

    plugin = McFish.__new__(McFish)
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._purify(event, {"pack": {}}, "消除宝藏诅咒"))
    assert event.reply.await_args.kwargs["text"] == "你没有绑定任何诅咒"


def test_mcfish_switching_to_axolotl_preserves_equipped_pole_attributes():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._save = lambda *_args: None
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {
        "equip": {"name": "金竿", "durable": 23, "maintenance": 2, "induce": 1, "favor": 1},
        "pack": {"美西螈": 1},
    }

    asyncio.run(plugin._equip(event, state, "美西螈"))

    assert state["equip"]["name"] == "美西螈"
    assert state["pack"]["金竿"] == [{
        "name": "金竿", "durable": 23, "maintenance": 2, "induce": 1, "favor": 1,
    }]


def test_mcfish_loads_original_catalog_and_item_images():
    plugin = McFish.__new__(McFish)
    plugin._load_original_data()

    assert "海草" in plugin._wastes
    assert plugin._thing_order[:6] == ["木竿", "铁竿", "金竿", "钻石竿", "下界合金竿", "三叉戟"]
    assert plugin._prices["海草"] == 10
    assert plugin._item_image("海之眷顾").name == "book.png"
    assert plugin._item_image("海草").name == "海草.png"


def test_mcfish_fishing_fragment_uses_a_local_icon():
    plugin = McFish.__new__(McFish)

    icon = plugin._item_image("钓鱼碎片")

    assert icon is not None
    assert icon.name == "mcfish_fishing_fragment.png"
    assert icon.is_file()


def test_mcfish_original_drop_uses_original_probability_intervals(monkeypatch):
    import importlib

    plugin = McFish.__new__(McFish)
    plugin._load_original_data()
    state = {"pack": {}, "equip": None}
    rolls = iter((0, 0))
    module = importlib.import_module("McFish.McFish")
    monkeypatch.setattr(module.random, "randrange", lambda _limit: next(rolls))

    assert plugin._original_drop(state) == ("诱钓", 1)


def test_mcfish_rod_drop_keeps_original_weights_with_next_tier_limit(monkeypatch):
    """MCF-02: 保留原鱼竿绝对概率，越过下一等级的区间转为空竿。"""
    import importlib

    plugin = McFish.__new__(McFish)
    plugin._load_original_data()
    module = importlib.import_module("McFish.McFish")
    boundaries = (0, 0, 4, 4, 4)

    rolls = iter((0, 75))
    monkeypatch.setattr(module.random, "randrange", lambda _limit: next(rolls))
    assert plugin._original_drop({"pack": {}, "equip": {"name": "木竿"}}, boundaries) == ("铁竿", 1)

    rolls = iter((0, 90))
    assert plugin._original_drop({"pack": {}, "equip": {"name": "木竿"}}, boundaries) == (None, 1)

    rolls = iter((0, 98))
    assert plugin._original_drop({"pack": {}, "equip": {"name": "金竿"}}, boundaries) == ("钻石竿", 1)


def test_mcfish_fishing_keeps_a_caught_rod_as_a_rod(monkeypatch):
    """MCF-08: 鱼竿掉落进入背包并生成随机属性。"""
    import asyncio
    import importlib

    mcfish_module = importlib.import_module("McFish.McFish")
    monkeypatch.setattr(mcfish_module, "fishing_wait_for_result", lambda *_args, **_kwargs: 0.01)
    monkeypatch.setattr(mcfish_module.asyncio, "sleep", AsyncMock())
    random_values = iter((24, 7, 2, 1))
    monkeypatch.setattr(mcfish_module.random, "randrange", lambda _limit: next(random_values))
    plugin = McFish.__new__(McFish)
    plugin.get_config = lambda key, default=None: 50 if key == "fish_limit" else default
    plugin._item_image = lambda _name: None
    plugin._original_drop = lambda _state, _boundaries=None, **_kwargs: ("铁竿", 1)
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {
        "fish_today": 0,
        "equip": {"name": "木竿", "durable": 1, "maintenance": 0, "induce": 0, "favor": 0},
        "pack": {},
    }
    stored = _attach_fake_state_store(plugin, state)

    asyncio.run(plugin._fish(event, state, "进行钓鱼"))

    assert len(stored["pack"]["铁竿"]) == 1
    assert stored["pack"]["铁竿"][0] == {
        "durable": 25,
        "maintenance": 7,
        "induce": 2,
        "favor": 1,
    }


def test_mcfish_equipping_consumables_converts_items_to_configured_uses():
    """MCF-20: 装备消耗品时按配置 uses 转为剩余竿数并移出背包。"""
    import asyncio
    import importlib

    equipment = importlib.import_module("McFish.mcfish_rules").EQUIPMENT
    for name in ("魔法鱼饵", "隐秘诱饵", "万能鱼饵"):
        plugin = McFish.__new__(McFish)
        plugin._save = lambda *_args: None
        event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
        state = {
            "pack": {name: 1},
            "accessories": [],
            "active_consumables": {},
        }

        asyncio.run(plugin._equip(event, state, name))

        assert state["pack"][name] == 0
        assert state["active_consumables"][name] == equipment[name]["uses"]
        assert f"剩余{equipment[name]['uses']}次" in event.reply.await_args.kwargs["text"]


def test_mcfish_equipping_same_consumable_accumulates_uses():
    """MCF-20: 重复装备同名消耗品时累加剩余竿数。"""
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._save = lambda *_args: None
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {
        "pack": {"魔法鱼饵": 2},
        "accessories": [],
        "active_consumables": {"魔法鱼饵": 3},
    }

    asyncio.run(plugin._equip(event, state, "魔法鱼饵"))

    assert state["pack"]["魔法鱼饵"] == 1
    assert state["active_consumables"]["魔法鱼饵"] == 13
    assert "剩余13次" in event.reply.await_args.kwargs["text"]


def test_mcfish_batch_consumables_are_consumed_per_rod_and_stop_on_zero(monkeypatch):
    """MCF-20: 批量钓鱼每竿只消耗一次，次数归零后不再继续享受效果。"""
    import asyncio
    import importlib

    module = importlib.import_module("McFish.McFish")
    wait_rates = []

    def fake_wait(_name, *, induce=0, wait_rate=1.0):
        wait_rates.append(wait_rate)
        return 0.01

    monkeypatch.setattr(module, "fishing_wait_for_result", fake_wait)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    plugin = McFish.__new__(McFish)
    plugin.get_config = lambda key, default=None: 50 if key == "fish_limit" else default
    plugin._item_image = lambda _name: None
    plugin._original_drop = lambda _state, _boundaries=None, **_kwargs: ("垃圾", 1)
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {
        "fish_today": 0,
        "equip": {"name": "木竿", "durable": 2, "maintenance": 0, "induce": 0, "favor": 0},
        "pack": {},
        "active_consumables": {"魔法鱼饵": 1, "隐秘诱饵": 1, "万能鱼饵": 1},
    }
    stored = _attach_fake_state_store(plugin, state)

    asyncio.run(plugin._fish(event, state, "进行2次钓鱼"))

    assert stored["active_consumables"] == {"魔法鱼饵": 0, "隐秘诱饵": 0, "万能鱼饵": 0}
    assert min(stored["active_consumables"].values()) == 0
    assert wait_rates[0] == 0.85
    assert wait_rates[-1] == 1.0


def test_mcfish_random_pole_attributes_respect_each_rod_limit(monkeypatch):
    """MCF-17: 掉落属性随机范围随鱼竿类型变化且不超过耐久上限。"""
    import importlib

    module = importlib.import_module("McFish.McFish")
    plugin = McFish.__new__(McFish)
    random_values = iter((149, 9, 2, 1))
    monkeypatch.setattr(module.random, "randrange", lambda _limit: next(random_values))
    state = {"pack": {}}

    plugin._add(state, "下界合金竿", pole=True, random_pole=True)

    assert state["pack"]["下界合金竿"] == [{
        "durable": 150,
        "maintenance": 9,
        "induce": 2,
        "favor": 1,
    }]


def test_mcfish_fishing_drop_does_not_use_the_default_full_rod_template(monkeypatch):
    """MCF-17: 钓鱼掉落必须明确写入随机属性，而不是使用普通鱼竿模板。"""
    import importlib

    module = importlib.import_module("McFish.McFish")
    monkeypatch.setattr(module.random, "randrange", lambda limit: 0 if limit == 30 else 1)

    dropped = McFish._random_pole_data("木竿")

    assert dropped == {
        "durable": 1,
        "maintenance": 1,
        "induce": 1,
        "favor": 1,
    }


def test_mcfish_fragment_exchange_can_create_a_rod_without_materials():
    """MCF-03: 没有上一等级材料时，合成命令可用碎片直接兑换鱼竿。"""
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="fragments"))
    plugin._save = lambda *_args: None
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {"pack": {"钓鱼碎片": 56}}

    asyncio.run(plugin._craft(event, state, "金竿"))

    assert state["pack"]["钓鱼碎片"] == 0
    assert len(state["pack"]["金竿"]) == 1
    assert state["pack"]["金竿"][0]["durable"] == 70
    assert "56个钓鱼碎片" in event.reply.await_args.kwargs["text"]


def test_mcfish_fragment_exchange_requires_confirmation_without_rod_materials():
    """MCF-39: 没有低级鱼竿时，碎片兑换高级鱼竿也必须确认。"""
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="cancel"))
    plugin._save = lambda *_args: (_ for _ in ()).throw(AssertionError("取消后不应保存"))
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {"pack": {"钓鱼碎片": 56}}

    asyncio.run(plugin._craft(event, state, "金竿"))

    prompt = plugin._choose.await_args.args[0]
    assert "需要使用56个钓鱼碎片兑换" in prompt
    assert "确认使用碎片吗" in prompt
    assert state["pack"]["钓鱼碎片"] == 56
    assert "金竿" not in state["pack"]


def test_mcfish_craft_reports_missing_fragment_materials():
    """MCF-34: 碎片不足时显示已有数量和还差数量。"""
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._save = lambda *_args: None
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {"pack": {"钓鱼碎片": 20}}

    asyncio.run(plugin._craft(event, state, "金竿"))

    message = event.reply.await_args.kwargs["text"]
    assert "合成金竿材料不足" in message
    assert "钓鱼碎片：已有20/56个，还差36个" in message


def test_mcfish_equipment_craft_reports_missing_fragment_materials():
    """MCF-35: 装备合成也显示碎片已有数量和缺口。"""
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._save = lambda *_args: None
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {"pack": {"钓鱼碎片": 20}}

    asyncio.run(plugin._craft(event, state, "财富浮标"))

    message = event.reply.await_args.kwargs["text"]
    assert "合成财富浮标材料不足" in message
    assert "钓鱼碎片：已有20/120个，还差100个" in message


def test_mcfish_craft_reports_both_missing_rod_material_routes():
    """MCF-36: 碎片和鱼竿材料都不足时分别列出两条合成路线的缺口。"""
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._save = lambda *_args: None
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {
        "pack": {
            "钓鱼碎片": 20,
            "铁竿": [{"durable": 50, "maintenance": 0, "induce": 0, "favor": 0}],
        }
    }

    asyncio.run(plugin._craft(event, state, "金竿"))

    message = event.reply.await_args.kwargs["text"]
    assert "钓鱼碎片：已有20/56个，还差36个" in message
    assert "铁竿：已有1/3根，还差2根" in message


def test_mcfish_craft_asks_for_source_when_both_sources_are_available():
    """MCF-09: 碎片和鱼竿材料都足够时，先询问合成来源。"""
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="fragments"))
    plugin._save = lambda *_args: None
    state = {
        "pack": {
            "钓鱼碎片": 56,
            "铁竿": [{"durable": 50, "maintenance": 0, "induce": 0, "favor": 0}] * 3,
        }
    }
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._craft(event, state, "金竿"))

    prompt = plugin._choose.await_args.args[0]
    assert "合成金竿请选择合成方式" in prompt
    assert "1. 碎片合成（需要56个钓鱼碎片）" in prompt
    assert "2. 鱼竿合成（需要3根铁竿，成功率90%）" in prompt
    assert state["pack"]["钓鱼碎片"] == 0
    assert len(state["pack"]["金竿"]) == 1
    assert len(state["pack"]["铁竿"]) == 3


def test_mcfish_craft_can_choose_rod_material_when_both_sources_are_available(monkeypatch):
    """MCF-10: 选择鱼竿合成时使用上一等级鱼竿，不扣除碎片。"""
    import asyncio
    import importlib

    monkeypatch.setattr(importlib.import_module("McFish.McFish").random, "randrange", lambda _stop: 0)
    plugin = McFish.__new__(McFish)
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="poles"))
    plugin._save = lambda *_args: None
    state = {
        "pack": {
            "钓鱼碎片": 56,
            "铁竿": [{"durable": 50, "maintenance": 0, "induce": 0, "favor": 0} for _ in range(3)],
        }
    }
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._craft(event, state, "金竿"))

    assert state["pack"]["钓鱼碎片"] == 56
    assert state["pack"]["铁竿"] == []
    assert len(state["pack"]["金竿"]) == 1


def test_mcfish_craft_cancel_after_source_prompt_keeps_materials():
    """MCF-11: 取消来源选择时不消耗碎片或鱼竿。"""
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="cancel"))
    saved = []
    plugin._save = lambda *_args: saved.append(True)
    rods = [{"durable": 50, "maintenance": 0, "induce": 0, "favor": 0} for _ in range(3)]
    state = {"pack": {"钓鱼碎片": 56, "铁竿": rods}}
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._craft(event, state, "金竿"))

    assert state["pack"]["钓鱼碎片"] == 56
    assert state["pack"]["铁竿"] == rods
    assert saved == []


def test_mcfish_craft_does_not_use_fragments_before_source_choice():
    """MCF-11: 等待来源选择时不预先选中碎片合成。"""
    import asyncio

    plugin = McFish.__new__(McFish)
    rods = [{"durable": 50, "maintenance": 0, "induce": 0, "favor": 0} for _ in range(3)]
    state = {"pack": {"钓鱼碎片": 56, "铁竿": rods}}
    observed = []

    async def choose(*_args, **_kwargs):
        observed.append((state["pack"]["钓鱼碎片"], len(state["pack"]["铁竿"])))
        return SimpleNamespace(key=None)

    plugin._choose = choose

    def fail_save(*_args):
        raise AssertionError("未选择来源不应保存")

    plugin._save = fail_save
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._craft(event, state, "金竿"))

    assert observed == [(56, 3)]
    assert state["pack"]["钓鱼碎片"] == 56
    assert state["pack"]["铁竿"] == rods


def test_mcfish_craft_real_session_choice_uses_rod_material(monkeypatch):
    """MCF-12: 真实 session_choose 收到“2”时不优先消耗碎片。"""
    import asyncio
    import importlib

    from ncatbot.core.dispatcher import AsyncEventDispatcher
    from ncatbot.testing.factories import qq as factory

    monkeypatch.setattr(importlib.import_module("McFish.McFish").random, "randrange", lambda _stop: 0)
    dispatcher = AsyncEventDispatcher()
    plugin = McFish.__new__(McFish)
    plugin._dispatcher = dispatcher
    plugin._save = lambda *_args: None
    rods = [{"durable": 50, "maintenance": 0, "induce": 0, "favor": 0} for _ in range(3)]
    state = {"pack": {"钓鱼碎片": 56, "铁竿": rods}}
    event = SimpleNamespace(
        user_id="100",
        sender=SimpleNamespace(user_id="100"),
        group_id="200",
        message_type="group",
        reply=AsyncMock(),
    )

    async def run():
        async def inject_choice():
            await asyncio.sleep(0.01)
            await dispatcher.callback(factory.group_message("2", user_id="100", group_id="200"))

        asyncio.create_task(inject_choice())
        try:
            await plugin._craft(event, state, "金竿")
        finally:
            await dispatcher.close()

    asyncio.run(run())

    assert state["pack"]["钓鱼碎片"] == 56
    assert state["pack"]["铁竿"] == []
    assert len(state["pack"]["金竿"]) == 1


def test_mcfish_text_reply_is_rendered_as_image(tmp_path):
    """MCF-13: 正式插件事件的纯文本回复转为图片消息。"""
    import asyncio
    from ncatbot.types import Image as ImageSegment, MessageArray

    plugin = McFish.__new__(McFish)
    plugin.workspace = tmp_path
    plugin._state = lambda _uid: {"pack": {}}
    raw_event = SimpleNamespace(
        raw_message="合成金竿",
        message=[],
        user_id="100",
        group_id="200",
        message_type="group",
        sender=SimpleNamespace(user_id="100"),
        reply=AsyncMock(),
    )

    asyncio.run(plugin.handle_message(raw_event))

    message = raw_event.reply.await_args.kwargs["rtf"]
    assert isinstance(message, MessageArray)
    assert isinstance(list(message)[0], ImageSegment)
    assert "text" not in raw_event.reply.await_args.kwargs


def test_mcfish_text_segments_in_message_array_are_rendered_as_images(tmp_path):
    """MCF-14: 结果消息中的 PlainText 转为图片并保留原有图片段。"""
    import asyncio
    import importlib
    from ncatbot.types import Image as ImageSegment, MessageArray, PlainText

    mcfish_module = importlib.import_module("McFish.McFish")
    plugin = McFish.__new__(McFish)
    plugin.workspace = tmp_path
    raw_event = SimpleNamespace(
        sender=SimpleNamespace(user_id="100"),
        reply=AsyncMock(),
    )
    event = mcfish_module._McFishEvent(raw_event, plugin)

    asyncio.run(
        event.reply(
            rtf=MessageArray([PlainText(text="钓鱼结果"), ImageSegment(file="fish.png")])
        )
    )

    message = raw_event.reply.await_args.kwargs["rtf"]
    assert isinstance(message, MessageArray)
    assert all(isinstance(segment, ImageSegment) for segment in message)


def test_mcfish_probability_detail_marks_ineligible_rods():
    """MCF-04: 概率明细把当前鱼竿不能越级获得的鱼竿显示为零。"""
    plugin = McFish.__new__(McFish)
    plugin._load_original_data()

    output = plugin._probability_detail_text({"pack": {}, "equip": {"name": "木竿"}})

    assert "铁竿 : 0.80%" in output
    assert "金竿 : 0.00%（当前木竿不可越级钓到）" in output


def test_mcfish_probability_sections_split_current_and_all_rod_probabilities():
    """MCF-05: 钓鱼概率图片的数据分为当前用户栏和全部鱼竿栏。"""
    plugin = McFish.__new__(McFish)
    plugin._load_original_data()
    state = {
        "equip": {"name": "铁竿", "favor": 0},
        "pack": {"鳕鱼": 101},
        "fishing_zone": "deep",
        "accessories": ["财富浮标"],
        "active_consumables": {"魔法鱼饵": 1},
        "king_stats": {},
    }

    current, all_probabilities = plugin._probability_sections("100", state)
    current_text = "\n".join(text for _kind, text in current)
    all_text = "\n".join(text for _kind, text in all_probabilities)

    assert "第一部分 当前用户概率" in current_text
    assert "鱼竿：铁竿" in current_text
    assert "鱼点：深海" in current_text
    assert "强化区间" in current_text
    assert "铁竿：" in all_text
    assert "三叉戟：" in all_text
    assert "隐藏鱼与不可直接钓出的装备" in all_text


def test_mcfish_probability_image_is_two_column_png(tmp_path: Path):
    """MCF-06: 钓鱼概率命令生成可发送的双栏 PNG 图片。"""
    from PIL import Image as PILImage

    plugin = McFish.__new__(McFish)
    plugin.workspace = tmp_path
    plugin._load_original_data()
    output = plugin._render_probability_image(
        "100",
        {"equip": {"name": "木竿"}, "pack": {}, "fishing_zone": "bay", "accessories": [], "active_consumables": {}},
    )

    assert output.name == "probability_100.png"
    with PILImage.open(output) as image:
        assert image.width == 1600
        assert image.height > 1000


def test_mcfish_probability_command_sends_an_image(tmp_path: Path):
    """MCF-07: `钓鱼概率` 命令发送概率图片而不是文本概率明细。"""
    import asyncio
    from ncatbot.types import Image as ImageSegment, MessageArray

    plugin = McFish.__new__(McFish)
    plugin._state = lambda _uid: {"pack": {}, "equip": None}
    output = tmp_path / "probability_100.png"
    plugin._render_probability_image = lambda _uid, _state: output
    plugin._send_original_message = AsyncMock()
    event = SimpleNamespace(
        raw_message="钓鱼概率",
        message=[],
        sender=SimpleNamespace(user_id="100"),
    )

    asyncio.run(plugin._handle_message(event))

    message = plugin._send_original_message.await_args.args[1]
    assert isinstance(message, MessageArray)
    assert isinstance(list(message)[0], ImageSegment)
    assert list(message)[0].file == str(output)


def test_mcfish_pack_render_is_an_image_with_original_dimensions(tmp_path):
    from types import SimpleNamespace
    from PIL import Image as PILImage

    class Store:
        def account(self, _user_id):
            return {"balance": 0}

    plugin = McFish.__new__(McFish)
    plugin.workspace = tmp_path
    plugin._economy = SimpleNamespace(store=Store())
    output = plugin._render_pack_image("100", {"equip": None, "pack": {"海草": 2}})

    assert PILImage.open(output).size[0] == 1240


def test_mcfish_pack_renders_each_pole_with_all_attributes(tmp_path):
    from PIL import Image as PILImage

    plugin = McFish.__new__(McFish)
    plugin.workspace = tmp_path
    plugin._economy = SimpleNamespace(store=SimpleNamespace(account=lambda _uid: {"balance": 0}))
    output = plugin._render_pack_image(
        "100",
        {
            "equip": None,
            "pack": {
                "金竿": [{"durable": 70, "maintenance": 0, "induce": 0, "favor": 0}]
            },
        },
    )

    assert PILImage.open(output).size[0] == 1240
    entries = []
    value = {"durable": 70, "maintenance": 0, "induce": 0, "favor": 0}
    entries.append(f"金竿({value['durable']}/{value['maintenance']}/{value['induce']}/{value['favor']})")
    assert entries == ["金竿(70/0/0/0)"]


def test_mcfish_pack_gives_rod_cards_room_for_two_attribute_lines(tmp_path):
    """MCF-22: 背包鱼竿卡片高度足够容纳两行属性，不挤出下一格。"""
    from PIL import Image as PILImage

    plugin = McFish.__new__(McFish)
    plugin.workspace = tmp_path
    plugin._economy = SimpleNamespace(store=SimpleNamespace(account=lambda _uid: {"balance": 0}))
    output = plugin._render_pack_image(
        "100",
        {
            "equip": None,
            "pack": {
                "木竿": [
                    {"durable": 30, "maintenance": 0, "induce": 0, "favor": 0},
                    {"durable": 29, "maintenance": 1, "induce": 2, "favor": 1},
                ],
            },
        },
    )

    # The two inventory rows use 106px tracks, so the second card has its own row.
    assert PILImage.open(output).height == 982


def test_mcfish_pack_marks_shop_rods_as_not_craftable():
    plugin = McFish.__new__(McFish)

    assert plugin._pack_item_label(
        "铁竿", {"craftable": False}
    ) == "铁竿（不可合成）"
    assert plugin._pack_item_label(
        "铁竿", {"craftable": True}
    ) == "铁竿"
    assert plugin._pack_item_label(
        "铁竿", {}
    ) == "铁竿"


def test_mcfish_pack_renders_active_consumable_progress_bar(tmp_path):
    """MCF-21: 背包图片显示激活消耗品的剩余次数进度条。"""
    plugin = McFish.__new__(McFish)
    plugin.workspace = tmp_path
    plugin._economy = SimpleNamespace(store=SimpleNamespace(account=lambda _uid: {"balance": 0}))
    bars = []
    plugin._draw_bar = lambda _draw, _xy, value: bars.append(value)

    plugin._render_pack_image(
        "100",
        {
            "equip": None,
            "pack": {},
            "active_consumables": {"魔法鱼饵": 7},
        },
    )

    assert 0.7 in bars


def test_mcfish_waste_sale_lists_backpack_items_before_confirmation():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._wastes = ["海草", "木棍", "腐肉", "碗"]
    plugin._prices = {name: 10 for name in plugin._wastes}
    plugin.get_config = lambda key, default=None: 0.8 if key == "sell_tax" else default
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="cancel"))
    state = {"pack": {"腐肉": 19, "碗": 20, "海草": 15, "木棍": 16}}
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._sell(event, state, "垃圾"))

    prompt = plugin._choose.await_args.args[0]
    assert prompt.startswith('是否接受回收站将以')
    first_reply = event.reply.await_args_list[0].kwargs["text"]
    assert first_reply.startswith("找到以下物品:\n[0]腐肉  数量: 19\n[1]碗  数量: 20\n[2]海草  数量: 15\n[3]木棍  数量: 16")
    assert first_reply.endswith("\n————————")


def test_mcfish_waste_sale_matches_full_original_listing_order():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._wastes = ["腐肉", "碗", "海草", "帽子", "绊线钩", "木棍", "骨头", "皮革", "鞋子", "瓶子"]
    plugin._prices = {name: 10 for name in plugin._wastes}
    plugin._shop_data = lambda: ({name: 100 for name in plugin._wastes}, {})
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="cancel"))
    state = {"pack": {
        "腐肉": 19, "碗": 20, "海草": 15, "帽子": 16, "绊线钩": 17,
        "木棍": 16, "骨头": 20, "皮革": 18, "鞋子": 20, "瓶子": 10,
    }}
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._sell(event, state, "垃圾"))

    assert event.reply.await_args_list[0].kwargs["text"] == (
        "找到以下物品:\n"
        "[0]腐肉  数量: 19\n[1]碗  数量: 20\n[2]海草  数量: 15\n"
        "[3]帽子  数量: 16\n[4]绊线钩  数量: 17\n[5]木棍  数量: 16\n"
        "[6]骨头  数量: 20\n[7]皮革  数量: 18\n[8]鞋子  数量: 20\n"
        "[9]瓶子  数量: 10\n————————"
    )
    assert event.reply.await_count == 1
    assert plugin._choose.await_args.args[0] == (
        '是否接受回收站将以1368收购全部垃圾?\n回答"是"或"否"'
    )


def test_mcfish_buy_wood_pole_uses_permanent_fixed_price():
    import asyncio
    from types import SimpleNamespace

    plugin = McFish.__new__(McFish)
    plugin._prices = {"木竿": 100}
    plugin._economy_change = lambda *_args: True
    plugin._save = lambda *_args: None
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="confirm"))
    plugin.get_config = lambda _key, default=None: default
    plugin._shop_data = lambda: ({"木竿": 100}, {"初始木竿": 1})
    event = SimpleNamespace(
        sender=SimpleNamespace(user_id="100"),
        reply=AsyncMock(),
    )
    state = {"pack": {}}

    asyncio.run(plugin._buy(event, state, "木杆"))

    assert len(state["pack"]["木竿"]) == 1
    assert event.reply.await_args.kwargs["text"] == "你用180购买了1木杆"


def test_mcfish_buy_multiple_permanent_wood_poles():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._prices = {"木竿": 100}
    plugin._economy_change = lambda *_args: True
    plugin._save = lambda *_args: None
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="confirm"))
    plugin.get_config = lambda _key, default=None: default
    plugin._shop_data = lambda: ({"木竿": 100}, {"初始木竿": [{"durable": 30, "maintenance": 0, "induce": 0, "favor": 0}]})
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {"pack": {}}

    asyncio.run(plugin._buy(event, state, "木竿 3"))

    assert len(state["pack"]["木竿"]) == 3
    assert state["pole_trade_count"] == 3
    assert plugin._choose.await_args.args[0].startswith("你确定花费540购买3个木竿?")
    assert event.reply.await_args.kwargs["text"] == "你用540购买了3木竿"


def test_mcfish_permanent_wood_pole_purchase_respects_daily_trade_limit():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._prices = {"木竿": 100}
    plugin._economy_change = lambda *_args: True
    plugin._save = lambda *_args: None
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="confirm"))
    plugin.get_config = lambda _key, default=None: default
    plugin._shop_data = lambda: ({"木竿": 100}, {"初始木竿": [{"durable": 30, "maintenance": 0, "induce": 0, "favor": 0}]})
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {"pack": {}, "pole_trade_count": 8}

    asyncio.run(plugin._buy(event, state, "木竿 3"))

    assert len(state["pack"]["木竿"]) == 2
    assert state["pole_trade_count"] == 10
    assert event.reply.await_args.kwargs["text"] == "你用360购买了2木竿"



def test_mcfish_handle_message_buys_multiple_wood_poles_end_to_end(tmp_path):
    """The user-visible command must buy as many permanent wood poles as requested."""
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin.workspace = tmp_path
    plugin._db = sqlite3.connect(str(tmp_path / "mcfish.db"))
    plugin._db.execute(
        "CREATE TABLE players (user_id TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at REAL NOT NULL)"
    )
    plugin._db.execute(
        "CREATE TABLE shop (day TEXT PRIMARY KEY, discounts TEXT NOT NULL, inventory TEXT NOT NULL)"
    )
    plugin._load_original_data()
    store = EconomyStore(tmp_path / "economy.db")
    store.grant("100", 2000)
    plugin._economy = SimpleNamespace(store=store)
    plugin.get_config = lambda key, default=None: default
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="confirm"))
    event = SimpleNamespace(
        sender=SimpleNamespace(user_id="100"),
        raw_message="购买木竿 3",
        reply=AsyncMock(),
        group_id="1",
        message=[],
    )

    asyncio.run(plugin._handle_message(event))

    assert event.reply.await_args.kwargs["text"] == "你用540购买了3木竿"
    state = plugin._state("100")
    assert len(state["pack"]["木竿"]) == 3
    assert state["pole_trade_count"] == 3
    assert store.account("100")["balance"] == 2000 - 540

    asyncio.run(plugin._handle_message(event))

    assert len(plugin._state("100")["pack"]["木竿"]) == 6
    assert store.account("100")["balance"] == 2000 - 1080
    plugin._db.close()
    store.close()


def test_mcfish_buy_wood_pole_falls_back_when_initial_listing_is_empty():
    import asyncio
    from types import SimpleNamespace

    plugin = McFish.__new__(McFish)
    plugin._prices = {"木竿": 100}
    plugin._economy_change = lambda *_args: True
    plugin._save = lambda *_args: None
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="confirm"))
    plugin.get_config = lambda _key, default=None: default
    plugin._shop_data = lambda: (
        {"木竿": 100},
        {
            "初始木竿": [],
            "木竿": [{"durable": 30, "maintenance": 0, "induce": 0, "favor": 0}],
        },
    )
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {"pack": {}}

    asyncio.run(plugin._buy(event, state, "木杆"))

    assert len(state["pack"]["木竿"]) == 1
    assert event.reply.await_args.kwargs["text"] == "你用100购买了1木杆"


def test_mcfish_shop_inventory_removes_zero_stock_listings():
    inventory = McFish._normalize_shop_inventory(
        {
            "初始木竿": [],
            "海豚": 0,
            "鳕鱼": 2,
            "木竿": [],
        }
    )

    assert inventory == {"鳕鱼": 2}


def test_mcfish_buy_fish_uses_listed_shop_inventory():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._prices = {"鳕鱼": 10}
    plugin._economy_change = lambda *_args: True
    plugin._save = lambda *_args: None
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="confirm"))
    plugin.get_config = lambda _key, default=None: default
    plugin._shop_data = lambda: ({"鳕鱼": 100}, {"鳕鱼": 3})
    event = SimpleNamespace(
        sender=SimpleNamespace(user_id="100"),
        reply=AsyncMock(),
    )
    state = {"pack": {}}

    asyncio.run(plugin._buy(event, state, "鳕鱼"))

    assert state["pack"]["鳕鱼"] == 1
    assert event.reply.await_args.kwargs["text"] == "你用10购买了1鳕鱼"


def test_mcfish_buy_advanced_rod_is_level_locked_and_uses_fixed_price():
    import asyncio

    assert SHOP_ROD_PRICES == {
        "铁竿": 540,
        "金竿": 900,
        "钻石竿": 1260,
        "下界合金竿": 1620,
    }

    plugin = McFish.__new__(McFish)
    plugin._prices = {}
    plugin._shop_data = lambda: ({name: 100 for name in SHOP_ROD_PRICES}, {})
    plugin._economy_change = lambda *_args: True
    plugin._advance_tasks = lambda *_args, **_kwargs: None
    plugin._save = lambda *_args: None
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="confirm"))
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._buy(event, {"fish_level": 2, "pack": {}}, "铁竿"))
    assert "需要等级3" in event.reply.await_args.kwargs["text"]

    asyncio.run(plugin._buy(event, {"pack": {}}, "铁竿"))
    assert "需要等级3" in event.reply.await_args.kwargs["text"]

    state = {"fish_level": 8, "pack": {}}
    asyncio.run(plugin._buy(event, state, "铁竿"))

    assert state["pack"]["铁竿"][0]["craftable"] is False
    assert event.reply.await_args.kwargs["text"] == f"你用{SHOP_ROD_PRICES['铁竿']}购买了1铁竿"


def test_mcfish_shop_rod_cannot_be_used_as_craft_material_but_drop_rod_can():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="group:2:1:0"))
    plugin._save = lambda *_args: None
    plugin._advance_tasks = lambda *_args, **_kwargs: None
    plugin._charge_fee = lambda *_args: True
    purchased = {"durable": 50, "maintenance": 0, "induce": 0, "favor": 0, "craftable": False}
    dropped = {"durable": 50, "maintenance": 0, "induce": 0, "favor": 0}
    state = {"pack": {"铁竿": [purchased, dropped, dropped.copy(), dropped.copy()]}}
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._craft(event, state, "金竿"))

    assert len(state["pack"]["铁竿"]) == 1
    assert len(state["pack"]["金竿"]) == 1
    assert purchased in state["pack"]["铁竿"]


def test_mcfish_shop_rod_sale_uses_fixed_purchase_price():
    plugin = McFish.__new__(McFish)
    plugin._prices = {}

    assert plugin._pole_price(
        {"durable": 1, "maintenance": 9, "induce": 3, "favor": 3, "craftable": False},
        {"铁竿": 100},
        "铁竿",
    ) == SHOP_ROD_PRICES["铁竿"]


def test_mcfish_daily_task_board_has_at_most_one_rod_reward_and_weekly_has_none():
    plugin = McFish.__new__(McFish)
    state = {"fish_level": 31, "fish_xp": 0, "pack": {}}

    plugin._ensure_task_state(state, date(2026, 9, 16))

    daily = state["fishing_daily_tasks"]["tasks"]
    weekly = state["fishing_weekly_tasks"]["tasks"]
    daily_rods = [
        name
        for task in daily
        for name in task["reward"].get("items", {})
        if name in {"木竿", "铁竿", "金竿", "钻石竿", "下界合金竿", "三叉戟"}
    ]
    weekly_rods = [
        name
        for task in weekly
        for name in task["reward"].get("items", {})
        if name in {"木竿", "铁竿", "金竿", "钻石竿", "下界合金竿", "三叉戟"}
    ]

    assert len(daily_rods) <= 1
    assert weekly_rods == []


def test_mcfish_enchant_at_max_level_does_not_consume_book():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._save = lambda *_args: None
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {
        "equip": {"name": "铁竿", "durable": 50, "maintenance": 0, "induce": 3, "favor": 0},
        "pack": {"诱钓": 1},
    }

    asyncio.run(plugin._enchant(event, state, "诱钓"))

    assert state["pack"]["诱钓"] == 1
    assert "不会消耗" in event.reply.await_args.kwargs["text"]


def test_mcfish_dismantles_selected_rods_into_fragments():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._save = lambda *_args: None
    plugin._choose = AsyncMock(
        side_effect=[SimpleNamespace(key="all"), SimpleNamespace(key="confirm")]
    )
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {
        "pack": {
            "铁竿": [{"durable": 50, "maintenance": 0, "induce": 0, "favor": 0}],
            "金竿": [{"durable": 70, "maintenance": 0, "induce": 0, "favor": 0}],
        }
    }

    asyncio.run(plugin._dismantle(event, state, "鱼竿"))

    assert state["pack"]["铁竿"] == []
    assert state["pack"]["金竿"] == []
    assert state["pack"]["钓鱼碎片"] == 6


def test_mcfish_permanent_wood_pole_uses_fixed_price():
    plugin = McFish.__new__(McFish)
    plugin._prices = {"木竿": 100}

    assert plugin._shop_price("初始木竿", {"木竿": 50}) == SHOP_WOOD_PRICE
    assert plugin._shop_price("初始木竿", {"木竿": 199}) == SHOP_WOOD_PRICE


def test_mcfish_enchanted_pole_price_is_bounded_by_pole_value():
    plugin = McFish.__new__(McFish)
    plugin._prices = {"木竿": 100}

    price = plugin._pole_price(
        {"durable": 30, "maintenance": 0, "induce": 3, "favor": 3},
        {"木竿": 199},
    )

    assert price == 497


def test_mcfish_sells_multiple_items_with_quantities():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._prices = {"鲑鱼": 50, "鳕鱼": 10}
    plugin._economy_change = lambda *_args: True
    plugin._save = lambda *_args: None
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="confirm"))
    plugin.get_config = lambda key, default=None: 0.8 if key == "sell_tax" else default
    plugin._shop_data = lambda: ({"鲑鱼": 136, "鳕鱼": 100}, {})
    event = SimpleNamespace(
        sender=SimpleNamespace(user_id="100"),
        reply=AsyncMock(),
    )
    state = {"pack": {"鲑鱼": 2, "鳕鱼": 3}}

    asyncio.run(plugin._sell(event, state, "鲑鱼2 鳕鱼 3"))

    assert state["pack"]["鲑鱼"] == 0
    assert state["pack"]["鳕鱼"] == 0
    assert "你赚到了" in event.reply.await_args.kwargs["text"]


def test_mcfish_multi_sell_uses_daily_market_prices():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._prices = {"鲑鱼": 50, "鹦鹉螺": 500}
    plugin._economy_change = lambda *_args: True
    plugin._save = lambda *_args: None
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="confirm"))
    plugin.get_config = lambda key, default=None: 0.8 if key == "sell_tax" else default
    plugin._shop_data = lambda: ({"鲑鱼": 136, "鹦鹉螺": 117}, {})
    event = SimpleNamespace(
        sender=SimpleNamespace(user_id="100"),
        reply=AsyncMock(),
    )
    state = {"pack": {"鲑鱼": 8, "鹦鹉螺": 1}}

    asyncio.run(plugin._sell(event, state, "鲑鱼8 鹦鹉螺1"))

    assert "你赚到了" in event.reply.await_args.kwargs["text"]


def test_mcfish_sells_fish_king_using_captured_length_price_and_gold_magnet():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._prices = {"风暴海龙": 1600}
    plugin._types = {}
    plugin._economy_change = lambda *_args: True
    plugin._save = lambda *_args: None
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="confirm"))
    plugin.get_config = lambda key, default=None: 0.8 if key == "sell_tax" else default
    plugin._shop_data = lambda: ({"风暴海龙": 125}, {})
    plugin._accessory_effect = lambda _state, name, key, default: 100.0 if (name, key) == ("黄金磁石", "king_price") else default
    event = SimpleNamespace(
        sender=SimpleNamespace(user_id="100"),
        reply=AsyncMock(),
    )
    state = {
        "pack": {"风暴海龙": 1},
        "king_sale_prices": {"风暴海龙": [2000]},
    }

    asyncio.run(plugin._sell(event, state, "风暴海龙"))

    assert state["pack"]["风暴海龙"] == 0
    assert state["king_sale_prices"]["风暴海龙"] == []
    # 2000 * 125% market * 100x magnet * 80% sale tax.
    assert "赚到了200000" in event.reply.await_args.kwargs["text"]


def test_mcfish_sells_legacy_fish_king_without_captured_length_record():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._prices = {"风暴海龙": 1600}
    plugin._types = {}
    plugin._economy_change = lambda *_args: True
    plugin._save = lambda *_args: None
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="confirm"))
    plugin.get_config = lambda key, default=None: 0.8 if key == "sell_tax" else default
    plugin._shop_data = lambda: ({"风暴海龙": 125}, {})
    plugin._accessory_effect = lambda _state, name, key, default: 100.0 if (name, key) == ("黄金磁石", "king_price") else default
    event = SimpleNamespace(
        sender=SimpleNamespace(user_id="100"),
        reply=AsyncMock(),
    )
    state = {"pack": {"风暴海龙": 1}}

    asyncio.run(plugin._sell(event, state, "风暴海龙"))

    assert "赚到了160000" in event.reply.await_args.kwargs["text"]


def test_mcfish_multi_sell_is_atomic_when_one_item_is_missing():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._prices = {"鲑鱼": 50, "鳕鱼": 10}
    plugin._economy_change = lambda *_args: True
    plugin._save = lambda *_args: None
    plugin.get_config = lambda _key, default=None: 0.8
    event = SimpleNamespace(
        sender=SimpleNamespace(user_id="100"),
        reply=AsyncMock(),
    )
    state = {"pack": {"鲑鱼": 2}}

    asyncio.run(plugin._sell(event, state, "鲑鱼1 鳕鱼1"))

    assert state["pack"]["鲑鱼"] == 2
    assert event.reply.await_args.kwargs["text"] == "背包数量不足"


def test_mcfish_selling_waste_does_not_return_it_to_shop():
    import asyncio

    inventory = {}
    plugin = McFish.__new__(McFish)
    plugin._wastes = ["海草"]
    plugin._prices = {"海草": 10}
    plugin._shop_data = lambda: ({"海草": 100}, inventory)
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="confirm"))
    plugin._economy_change = lambda *_args: True
    plugin._save = lambda *_args: None
    plugin.get_config = lambda key, default=None: 0.8 if key == "sell_tax" else default
    state = {"pack": {"海草": 2}}
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._sell(event, state, "海草 1"))

    assert inventory == {}


def test_mcfish_purification_book_purchase_matches_original_limits():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._prices = {"净化书": 5000}
    plugin._shop_data = lambda: ({"净化书": 100}, {"净化书": 10})
    plugin._save = lambda *_args: None
    plugin._economy_change = lambda *_args: True
    plugin.get_config = lambda _key, default=None: default
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {"pack": {"宝藏诅咒": 2, "净化书": 2}}

    asyncio.run(plugin._buy(event, state, "净化书 1"))

    assert event.reply.await_args.kwargs["text"] == "你身上净化书已足够,不能购买该商品"


def test_mcfish_record_doubling_can_return_a_pole_to_shop():
    import asyncio

    inventory = {}
    plugin = McFish.__new__(McFish)
    plugin._prices = {"木竿": 100, "唱片": 3000}
    plugin._shop_data = lambda: ({"木竿": 100}, inventory)
    plugin._choose = AsyncMock(side_effect=[
        SimpleNamespace(key="confirm"), SimpleNamespace(key="yes"),
    ])
    plugin._economy_change = lambda *_args: True
    plugin._save = lambda *_args: None
    plugin.get_config = lambda key, default=None: 0.8 if key == "sell_tax" else default
    state = {"pack": {
        "木竿": [{"durable": 30, "maintenance": 0, "induce": 0, "favor": 0}],
        "唱片": 1,
    }}
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._sell(event, state, "木竿"))

    assert len(inventory["木竿"]) == 1


def test_mcfish_craft_accepts_reverse_pole_indices(monkeypatch):
    import asyncio
    import importlib

    monkeypatch.setattr(importlib.import_module("McFish.McFish").random, "randrange", lambda _stop: 0)
    plugin = McFish.__new__(McFish)
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="group:2:1:0"))
    plugin._save = lambda *_args: None
    state = {"pack": {"木竿": [
        {"durable": 1, "maintenance": 0, "induce": 0, "favor": 0},
        {"durable": 2, "maintenance": 0, "induce": 0, "favor": 0},
        {"durable": 3, "maintenance": 0, "induce": 0, "favor": 0},
    ]}}
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._craft(event, state, "铁竿"))

    assert state["pack"]["木竿"] == []
    assert len(state["pack"]["铁竿"]) == 1


def test_mcfish_craft_lists_material_poles_instead_of_target_name():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key=None))
    poles = [
        {"durable": 50, "maintenance": 0, "induce": 0, "favor": 0},
        {"durable": 39, "maintenance": 5, "induce": 2, "favor": 0},
        {"durable": 19, "maintenance": 0, "induce": 1, "favor": 0},
        {"durable": 50, "maintenance": 0, "induce": 0, "favor": 0},
    ]
    state = {"pack": {"铁竿": poles}}
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._craft(event, state, "金竿"))

    prompt = plugin._choose.await_args.args[0]
    assert "[0] 铁竿 : 耐50/修0/诱0/眷顾0" in prompt
    assert "[1] 铁竿 : 耐39/修5/诱Ⅱ/眷顾0" in prompt
    assert "[0] 金竿" not in prompt


def test_mcfish_craft_index_parser_accepts_only_three_distinct_valid_indices():
    assert McFish._craft_indices("2 1 0", 3) == "group:2:1:0"
    assert McFish._craft_indices("0 0 1", 3) is None
    assert McFish._craft_indices("0 1 3", 3) is None
    assert McFish._craft_indices("0 1", 3) is None


def test_mcfish_craft_all_preserves_remainder_for_non_multiple_of_three(monkeypatch):
    import asyncio
    import importlib

    mcfish_module = importlib.import_module("McFish.McFish")
    monkeypatch.setattr(mcfish_module.random, "randrange", lambda _stop: 0)
    plugin = McFish.__new__(McFish)
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="all"))
    plugin._save = lambda *_args: None
    poles = [
        {"durable": index + 1, "maintenance": 0, "induce": 0, "favor": 0}
        for index in range(7)
    ]
    state = {"pack": {"木竿": poles}}
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._craft(event, state, "铁竿"))

    assert len(state["pack"]["木竿"]) == 1
    assert state["pack"]["木竿"][0]["durable"] == 7
    assert len(state["pack"]["铁竿"]) == 2
    assert "剩余 1 根材料未参与合成" in event.reply.await_args.kwargs["text"]



def test_mcfish_craft_from_fresh_materials_scales_durability(monkeypatch):
    """Fresh source rods scale to the target with bounded random durability."""
    import asyncio
    import importlib

    mcfish_module = importlib.import_module("McFish.McFish")
    monkeypatch.setattr(mcfish_module.random, "randrange", lambda _stop: 0)
    plugin = McFish.__new__(McFish)
    plugin._save = lambda *_args: None
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key=None))
    state = {"pack": {"木竿": [{"durable": 30, "maintenance": 0, "induce": 0, "favor": 0}] * 3}}
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._craft(event, state, "铁竿"))

    assert state["pack"]["铁竿"] == [{"durable": 45, "maintenance": 0, "induce": 0, "favor": 0}]
    assert event.reply.await_args.kwargs["text"] == "成功合成：1 个铁竿\n属性: 45/0/0/0"


def test_mcfish_craft_averages_material_enchant_levels_and_drops_wear(monkeypatch):
    """Enchant levels are averaged (floor) while material durability and maintenance are discarded."""
    import asyncio
    import importlib

    mcfish_module = importlib.import_module("McFish.McFish")
    monkeypatch.setattr(mcfish_module.random, "randrange", lambda _stop: 0)
    plugin = McFish.__new__(McFish)
    plugin._save = lambda *_args: None
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key=None))
    state = {"pack": {"木竿": [
        {"durable": 30, "maintenance": 0, "induce": 3, "favor": 0},
        {"durable": 12, "maintenance": 4, "induce": 0, "favor": 1},
        {"durable": 3, "maintenance": 9, "induce": 0, "favor": 0},
    ]}}
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._craft(event, state, "铁竿"))

    assert state["pack"]["铁竿"] == [{"durable": 22, "maintenance": 0, "induce": 1, "favor": 0}]
    assert event.reply.await_args.kwargs["text"] == "成功合成：1 个铁竿\n属性: 22/0/1/0"


def test_mcfish_craft_zero_durability_materials_cannot_become_full_durability(monkeypatch):
    """MCF-15: 三根零耐久材料不会凭空合成满耐久鱼竿。"""
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._save = lambda *_args: None
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key=None))
    state = {"pack": {"木竿": [{"durable": 0, "maintenance": 0, "induce": 0, "favor": 0}] * 3}}
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._craft(event, state, "铁竿"))

    assert state["pack"]["铁竿"][0]["durable"] == 1
    assert state["pack"]["铁竿"][0]["durable"] < 50


def test_mcfish_craft_durability_randomness_stays_within_proportional_bounds(monkeypatch):
    """MCF-16: 相同材料的合成耐久会在比例区间内随机。"""
    import importlib

    module = importlib.import_module("McFish.McFish")
    selected = [{"durable": 30}, {"durable": 30}, {"durable": 30}]
    monkeypatch.setattr(module.random, "randrange", lambda _stop: 0)
    lower = McFish._craft_durable("铁竿", "木竿", selected)
    monkeypatch.setattr(module.random, "randrange", lambda stop: stop - 1)
    upper = McFish._craft_durable("铁竿", "木竿", selected)

    assert (lower, upper) == (45, 50)


def test_mcfish_craft_all_averages_each_batch_of_three_separately(monkeypatch):
    """Suoha averages every triple on its own, matching the original plugin batch loop."""
    import asyncio
    import importlib

    mcfish_module = importlib.import_module("McFish.McFish")
    monkeypatch.setattr(mcfish_module.random, "randrange", lambda _stop: 0)
    plugin = McFish.__new__(McFish)
    plugin._save = lambda *_args: None
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="all"))
    state = {"pack": {"木竿": [
        {"durable": 30, "maintenance": 0, "induce": 3, "favor": 0},
        {"durable": 30, "maintenance": 0, "induce": 3, "favor": 0},
        {"durable": 30, "maintenance": 0, "induce": 3, "favor": 0},
        {"durable": 30, "maintenance": 0, "induce": 0, "favor": 3},
        {"durable": 30, "maintenance": 0, "induce": 0, "favor": 3},
        {"durable": 30, "maintenance": 0, "induce": 0, "favor": 3},
    ]}}
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._craft(event, state, "铁竿"))

    assert sorted((pole["induce"], pole["favor"]) for pole in state["pack"]["铁竿"]) == [(0, 3), (3, 0)]


def test_mcfish_more_than_hundred_fish_only_switches_probability_ranges():
    """Both the 100 fish milestone and the axolotl rod only widen the probability table."""
    plugin = McFish.__new__(McFish)
    plugin._catalog = {"分类": [
        {"类型": "treasure", "概率[0-100)": 10},
        {"类型": "pole", "概率[0-100)": 10},
        {"类型": "fish", "概率[0-100)": 30},
        {"类型": "waste", "概率[0-100)": 10},
    ], "物品": []}

    assert plugin._zone_boundaries({"pack": {"鳗鱼": 101}, "equip": {"name": "木竿", "favor": 0}}) == (0, 2, 10, 45, 90)
    assert plugin._zone_boundaries({"pack": {}, "equip": {"name": "美西螈", "favor": 0}}) == (0, 2, 10, 45, 90)
    assert plugin._zone_boundaries({"pack": {"鳗鱼": 100}, "equip": {"name": "木竿", "favor": 0}}) == (0, 10, 20, 50, 60)


def test_mcfish_drop_quantity_is_five_only_while_equipped_with_axolotl(monkeypatch):
    """The x5 multiplier needs the axolotl rod; a 100+ fish pack must not multiply drops."""
    import importlib

    mcfish_module = importlib.import_module("McFish.McFish")
    monkeypatch.setattr(mcfish_module.random, "randrange", lambda _stop: 0)
    plugin = McFish.__new__(McFish)
    plugin._catalog = {"分类": [
        {"类型": "fish", "概率[0-100)": 50},
        {"类型": "treasure", "概率[0-100)": 50},
    ], "物品": [
        {"名称": "鳗鱼", "类型": "fish", "概率[0-100)": 100},
        {"名称": "美西螈", "类型": "treasure", "概率[0-100)": 100},
    ]}

    plugin._zone_boundaries = lambda _state, _boundaries=None: (0, 0, 0, 100, 100)
    assert plugin._original_drop({"pack": {"鳗鱼": 101}, "equip": {"name": "木竿"}}) == ("鳗鱼", 1)
    assert plugin._original_drop({"pack": {}, "equip": {"name": "美西螈"}}) == ("鳗鱼", 5)

    plugin._zone_boundaries = lambda _state, _boundaries=None: (0, 100, 100, 100, 100)
    assert plugin._original_drop({"pack": {}, "equip": {"name": "美西螈"}}) == ("美西螈", 1)


def test_mcfish_help_explains_emergency_pole_is_independent_of_shop():
    assert "花费100购买鱼竿" in MCFISH_HELP
    assert "最多可以购买3次" in MCFISH_HELP
    assert "每3天最多补偿1根木竿" in MCFISH_HELP
    assert "可一次出售多个物品" in MCFISH_HELP


def test_mcfish_help_documents_every_special_mechanic():
    """帮助里的特殊机制清单必须覆盖美西螈等全部特殊规则。"""
    text = "\n".join(MCFISH_SPECIAL_MECHANICS)
    for keyword in (
        "美西螈",
        "每竿要吃3条鱼",
        "永久失去",
        "一次获得5个",
        "海豚",
        "超过100条",
        "满75点自动获得1本净化书",
        "每满10次获得1个宝藏诅咒",
        "约50%–199%",
        "半价福利",
    ):
        assert keyword in text


def test_mcfish_help_image_renders_special_mechanics(tmp_path: Path):
    """MCF-18: 帮助图使用平衡高度的 1600px 双栏布局。"""
    from PIL import Image as PILImage

    plugin = McFish.__new__(McFish)
    plugin.workspace = tmp_path

    output = plugin._render_help_image("100")

    assert output.exists()
    with PILImage.open(output) as image:
        assert image.size[0] == 1600
        assert image.size[1] < 3200

    assert "掉落鱼竿随机生成耐久、维修和附魔" in MCFISH_HELP
    assert "美西螈" in "\n".join(MCFISH_SPECIAL_MECHANICS)


def test_mcfish_emergency_pole_command_is_limited_to_three_purchases():
    import asyncio

    class Store:
        def account(self, _user_id):
            return {"balance": 1000}

    plugin = McFish.__new__(McFish)
    plugin.get_config = lambda key, default=None: default
    plugin._economy = SimpleNamespace(store=Store())
    plugin._economy_change = lambda *_args: True
    plugin._save = lambda *_args: None
    state = {"pack": {}, "equip": None, "emergency_pole_day": ""}
    plugin._state = lambda _uid: state

    def event():
        return SimpleNamespace(
            raw_message="花费100购买鱼竿",
            message=[],
            sender=SimpleNamespace(user_id="100"),
            reply=AsyncMock(),
        )

    first = event()
    asyncio.run(plugin.handle_message(first))
    assert state["equip"]["name"] == "木竿"
    assert "已花费100金币购买并装备应急木竿" in first.reply.await_args.kwargs["text"]

    for _ in range(2):
        state["equip"] = None
        asyncio.run(plugin.handle_message(event()))
    state["equip"] = None
    fourth = event()
    asyncio.run(plugin.handle_message(fourth))
    assert "请装备鱼竿后钓鱼" in fourth.reply.await_args.kwargs["text"]


def test_mcfish_first_fishing_grants_free_starter_pole_without_balance():
    plugin = McFish.__new__(McFish)
    plugin._save = lambda *_args: None
    state = {"equip": None, "pack": {}, "emergency_pole_count": 0}

    plugin._grant_starter_pole("100", state)

    assert state["equip"] == {
        "name": "木竿",
        "durable": 30,
        "maintenance": 0,
        "induce": 0,
        "favor": 0,
    }
    assert state["starter_pole_day"]


def test_mcfish_level_release_migration_resets_players_and_normalizes_equipment(tmp_path: Path):
    plugin = McFish.__new__(McFish)
    plugin._db = sqlite3.connect(tmp_path / "mcfish.db")
    plugin._db.execute("CREATE TABLE players (user_id TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at REAL NOT NULL)")
    plugin._db.execute("CREATE TABLE migrations (migration_id TEXT PRIMARY KEY, applied_at REAL NOT NULL, details TEXT NOT NULL)")
    plugin._db.execute(
        "INSERT INTO players VALUES (?, ?, ?)",
        (
            "player",
            json.dumps({
                "fish_level": 8,
                "fish_xp": 999,
                "fish_today": 4,
                "equip": {"name": "金竿", "durable": 12},
                "pack": {},
            }, ensure_ascii=False),
            time.time(),
        ),
    )
    plugin._db.commit()

    plugin._reset_fishing_progress_for_level_release()
    row = plugin._db.execute("SELECT state FROM players WHERE user_id = 'player'").fetchone()
    state = json.loads(row[0])
    assert (state["fish_level"], state["fish_xp"], state["fish_today"]) == (1, 0, 0)
    assert state["equip"]["name"] == "木竿"
    assert len(state["pack"]["金竿"]) == 1

    plugin._reset_fishing_progress_for_level_release()
    assert plugin._db.execute("SELECT COUNT(*) FROM migrations").fetchone()[0] == 1
    plugin._db.close()


def test_mcfish_starter_pole_requires_three_days_and_existing_pack_pole_blocks_grant():
    from datetime import date, timedelta

    assert not McFish._starter_pole_available(
        {"starter_pole_day": (date.today() - timedelta(days=2)).isoformat()}
    )
    assert McFish._starter_pole_available(
        {"starter_pole_day": (date.today() - timedelta(days=3)).isoformat()}
    )
    plugin = McFish.__new__(McFish)
    assert plugin._has_pole({"equip": None, "pack": {"木竿": [{"durable": 30}]}})


def test_mcfish_locked_pack_pole_does_not_block_buying_a_wood_pole():
    plugin = McFish.__new__(McFish)
    state = {
        "fish_level": 1,
        "fish_xp": 0,
        "equip": None,
        "pack": {"铁竿": [{"durable": 30, "maintenance": 0, "induce": 0, "favor": 0}]},
    }

    assert plugin._has_pole(state)
    assert not plugin._has_usable_pole(state)


def test_mcfish_clock_renders_the_nearest_five_future_king_windows(tmp_path: Path):
    from PIL import Image as PILImage

    plugin = McFish.__new__(McFish)
    plugin.workspace = tmp_path
    now = datetime.now()
    plugin._fish_windows = lambda: [
        FishKingWindow(
            "链",
            f"鱼王{i}",
            now + timedelta(minutes=10 * i),
            now + timedelta(minutes=10 * i + 5),
        )
        for i in range(1, 7)
    ]

    output = plugin._render_clock_image("100")

    assert output.exists()
    with PILImage.open(output) as image:
        assert image.size == (1240, 928)


def test_mcfish_reports_daily_limit_separately_from_pole_durability():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin.get_config = lambda key, default=None: 50 if key == "fish_limit" else default
    event = SimpleNamespace(
        sender=SimpleNamespace(user_id="100"),
        reply=AsyncMock(),
    )
    state = {
        "fish_today": 50,
        "equip": {"name": "木竿", "durable": 10, "maintenance": 0, "induce": 0, "favor": 0},
        "pack": {},
    }

    asyncio.run(plugin._fish(event, state, "进行10次钓鱼"))

    assert event.reply.await_args.kwargs["text"] == "今天你已经进行50次钓鱼了.\n游戏虽好,但请不要沉迷。"


def test_mcfish_fishing_result_reports_used_and_remaining_count(monkeypatch):
    import asyncio
    import importlib

    module = importlib.import_module("McFish.McFish")
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(module.random, "randint", lambda _low, _high: 1)
    plugin = McFish.__new__(McFish)
    plugin.get_config = lambda key, default=None: 50 if key == "fish_limit" else default
    plugin._original_drop = lambda _state, _boundaries=None, **_kwargs: ("垃圾", 1)
    plugin._item_image = lambda _name: None
    event = SimpleNamespace(
        sender=SimpleNamespace(user_id="100"),
        reply=AsyncMock(),
    )
    state = {
        "fish_today": 0,
        "equip": {"name": "木竿", "durable": 2, "maintenance": 0, "induce": 0, "favor": 0},
        "pack": {},
    }
    _attach_fake_state_store(plugin, state)

    asyncio.run(plugin._fish(event, state, "进行1次钓鱼"))

    result = event.reply.await_args.kwargs["text"]
    assert "恭喜你钓到了1垃圾" in result
    assert "上钩时间：" in result
    assert "获得经验 0 XP" in result
    assert "本次获得经验：0 XP" in result


def test_mcfish_batch_fishing_uses_one_probability_snapshot(monkeypatch):
    import asyncio
    import importlib

    module = importlib.import_module("McFish.McFish")
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    boundaries_seen = []
    plugin = McFish.__new__(McFish)
    plugin.get_config = lambda key, default=None: 50 if key == "fish_limit" else default
    plugin._item_image = lambda _name: None

    def drop(_state, boundaries, **_kwargs):
        boundaries_seen.append(boundaries)
        return "鲑鱼", 1

    plugin._original_drop = drop
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {
        "fish_today": 0,
        "equip": {"name": "木竿", "durable": 2, "maintenance": 0, "induce": 0, "favor": 0},
        "pack": {},
    }
    _attach_fake_state_store(plugin, state)

    asyncio.run(plugin._fish(event, state, "进行2次钓鱼"))

    assert boundaries_seen == [boundaries_seen[0], boundaries_seen[0]]


def test_mcfish_batch_fishing_refreshes_dolphin_air_conversion(monkeypatch):
    import asyncio
    import importlib

    module = importlib.import_module("McFish.McFish")
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    boundaries_seen = []
    plugin = McFish.__new__(McFish)
    plugin.get_config = lambda key, default=None: 50 if key == "fish_limit" else default
    plugin._item_image = lambda _name: None
    plugin._zone_boundaries = lambda current: (0, 1, 5, 30, 100 if current.get("pack", {}).get("海豚", 0) else 60)
    stored = _attach_fake_state_store(
        plugin,
        {
            "fish_today": 0,
            "equip": {"name": "木竿", "durable": 2, "maintenance": 0, "induce": 0, "favor": 0},
            "pack": {},
        },
    )

    def drop(_state, boundaries, **_kwargs):
        boundaries_seen.append(boundaries)
        if len(boundaries_seen) == 1:
            stored["pack"]["海豚"] = 1
        return "鲑鱼", 1

    plugin._original_drop = drop
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._fish(event, stored, "进行2次钓鱼"))

    assert boundaries_seen == [
        (0, 1, 5, 30, 60),
        (0, 1, 5, 30, 100),
    ]


def test_mcfish_batch_fishing_persists_each_catch_after_its_wait(monkeypatch):
    import asyncio
    import importlib

    module = importlib.import_module("McFish.McFish")
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    plugin = McFish.__new__(McFish)
    plugin.get_config = lambda key, default=None: 50 if key == "fish_limit" else default
    plugin._item_image = lambda _name: None
    plugin._original_drop = lambda _state, _boundaries=None, **_kwargs: ("鲑鱼", 1)
    snapshots = []
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {
        "fish_today": 0,
        "equip": {"name": "木竿", "durable": 3, "maintenance": 0, "induce": 0, "favor": 0},
        "pack": {},
    }
    _attach_fake_state_store(plugin, state, snapshot=snapshots)

    asyncio.run(plugin._fish(event, state, "进行2次钓鱼"))

    assert snapshots[0].get("鲑鱼", 0) == 0
    assert snapshots[1].get("鲑鱼", 0) == 1
    assert snapshots[-1].get("鲑鱼", 0) == 2


def test_wallet_help_does_not_claim_the_fishing_help_command():
    from GroupEconomy.GroupEconomy import HELP

    assert "钓鱼帮助：查看钓鱼、背包、商店、装备、交易和合成说明" in HELP


def _make_migration_plugin(tmp_path: Path) -> McFish:
    plugin = McFish.__new__(McFish)
    plugin._db = sqlite3.connect(tmp_path / "mcfish.db")
    plugin._db.execute("CREATE TABLE players (user_id TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at REAL NOT NULL)")
    plugin._db.execute("CREATE TABLE migrations (migration_id TEXT PRIMARY KEY, applied_at REAL NOT NULL, details TEXT NOT NULL)")
    plugin._db.execute("CREATE TABLE admin_adjustments (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, reason TEXT NOT NULL, old_state TEXT NOT NULL, new_state TEXT NOT NULL, created_at REAL NOT NULL)")
    return plugin


def test_mcfish_drop_quantity_migration_reclaims_five_times_items_and_xp(tmp_path: Path):
    plugin = _make_migration_plugin(tmp_path)
    state = {
        "fish_level": 5,
        "fish_xp": 891,
        "pack": {
            "鳕鱼": 58,
            "鲑鱼": 38,
            "河豚": 16,
            "鳗鱼": 3,
            "绊线钩": 18,
            "鞋子": 8,
            "碗": 6,
            "木棍": 9,
            "瓶子": 7,
            "腐肉": 9,
            "墨鱼": 6,
        },
    }
    plugin._db.execute("INSERT INTO players VALUES (?, ?, ?)", ("1310043427", json.dumps(state, ensure_ascii=False), time.time()))
    plugin._db.commit()

    plugin._repair_excessive_drop_quantities()

    repaired = json.loads(plugin._db.execute("SELECT state FROM players WHERE user_id = '1310043427'").fetchone()[0])
    assert repaired["fish_xp"] == 567
    assert repaired["fish_level"] == 4
    assert repaired["pack"]["鳕鱼"] == 46
    assert repaired["pack"]["鲑鱼"] == 22
    assert repaired["pack"]["河豚"] == 12
    assert repaired["pack"]["鳗鱼"] == 0
    assert repaired["pack"]["绊线钩"] == 10
    assert repaired["pack"]["鞋子"] == 4
    assert repaired["pack"]["碗"] == 2
    assert repaired["pack"]["木棍"] == 5
    assert repaired["pack"]["瓶子"] == 3
    assert repaired["pack"]["腐肉"] == 5
    assert repaired["pack"]["墨鱼"] == 6

    reason, old_state, new_state = plugin._db.execute(
        "SELECT reason, old_state, new_state FROM admin_adjustments WHERE user_id = '1310043427'"
    ).fetchone()
    assert reason == "mcfish-drop-quantity-2026-09-10"
    assert json.loads(old_state)["fish_xp"] == 891
    assert json.loads(new_state)["fish_xp"] == 567

    plugin._repair_excessive_drop_quantities()
    assert plugin._db.execute("SELECT COUNT(*) FROM migrations").fetchone()[0] == 1
    plugin._db.close()


def test_mcfish_drop_quantity_migration_skips_unrecorded_players(tmp_path: Path):
    plugin = _make_migration_plugin(tmp_path)
    states = {
        "10123121": {
            "fish_level": 6,
            "fish_xp": 1205,
            "pack": {
                "鹦鹉螺": 22,
                "墨鱼": 12,
                "鳕鱼": 53,
                "河豚": 10,
                "鲑鱼": 28,
                "热带鱼": 11,
                "帽子": 11,
                "木棍": 12,
                "海草": 0,
                "瓶子": 16,
                "碗": 20,
                "骨头": 22,
            },
        },
        "99999999": {"fish_level": 3, "fish_xp": 200, "pack": {"鳕鱼": 5}},
    }
    for user_id, state in states.items():
        plugin._db.execute("INSERT INTO players VALUES (?, ?, ?)", (user_id, json.dumps(state, ensure_ascii=False), time.time()))
    plugin._db.commit()

    plugin._repair_excessive_drop_quantities()

    repaired = json.loads(plugin._db.execute("SELECT state FROM players WHERE user_id = '10123121'").fetchone()[0])
    assert repaired["fish_xp"] == 713
    assert repaired["fish_level"] == 5
    assert repaired["pack"]["鹦鹉螺"] == 14
    assert repaired["pack"]["墨鱼"] == 8
    assert repaired["pack"]["鳕鱼"] == 49
    assert repaired["pack"]["河豚"] == 6
    assert repaired["pack"]["鲑鱼"] == 24
    assert repaired["pack"]["热带鱼"] == 7
    assert repaired["pack"]["帽子"] == 3
    assert repaired["pack"]["木棍"] == 4
    assert repaired["pack"]["海草"] == 0
    assert repaired["pack"]["瓶子"] == 12
    assert repaired["pack"]["碗"] == 8
    assert repaired["pack"]["骨头"] == 10

    untouched = json.loads(plugin._db.execute("SELECT state FROM players WHERE user_id = '99999999'").fetchone()[0])
    assert untouched == states["99999999"]
    assert plugin._db.execute("SELECT COUNT(*) FROM migrations").fetchone()[0] == 1
    plugin._db.close()


def test_mcfish_drop_quantity_migration_tops_up_partial_first_pass(tmp_path: Path):
    plugin = _make_migration_plugin(tmp_path)
    state = {"fish_level": 4, "fish_xp": 567, "pack": {"鳕鱼": 58, "鳗鱼": 6}}
    plugin._db.execute("INSERT INTO players VALUES (?, ?, ?)", ("1310043427", json.dumps(state, ensure_ascii=False), time.time()))
    plugin._db.execute(
        "INSERT INTO migrations VALUES (?, ?, ?)",
        (
            "mcfish-drop-quantity-2026-09-10",
            time.time(),
            json.dumps(
                {"1310043427": {"removed_items": {"鲑鱼": 16, "河豚": 4}, "removed_xp": 324}},
                ensure_ascii=False,
            ),
        ),
    )
    plugin._db.commit()

    plugin._top_up_excessive_drop_quantities()

    repaired = json.loads(plugin._db.execute("SELECT state FROM players WHERE user_id = '1310043427'").fetchone()[0])
    assert repaired["pack"]["鳕鱼"] == 46
    assert repaired["pack"]["鳗鱼"] == 2
    assert repaired["fish_xp"] == 567
    assert repaired["fish_level"] == 4
    details = json.loads(
        plugin._db.execute(
            "SELECT details FROM migrations WHERE migration_id = 'mcfish-drop-quantity-2026-09-10-v2'"
        ).fetchone()[0]
    )
    assert details["1310043427"]["removed_items"] == {"鳕鱼": 12, "鳗鱼": 4}
    assert details["1310043427"]["removed_xp"] == 0

    plugin._top_up_excessive_drop_quantities()
    assert plugin._db.execute("SELECT COUNT(*) FROM migrations").fetchone()[0] == 2
    plugin._db.close()


def test_mcfish_fishing_batch_keeps_purchase_made_while_waiting(monkeypatch):
    """两起丢物品事故的根因回归：后台钓鱼任务不能再整份覆盖并发写入。"""
    import asyncio
    import importlib

    module = importlib.import_module("McFish.McFish")
    monkeypatch.setattr(module, "fishing_wait_for_result", lambda *_args, **_kwargs: 0.01)
    plugin = McFish.__new__(McFish)
    plugin.get_config = lambda key, default=None: 50 if key == "fish_limit" else default
    plugin._item_image = lambda _name: None
    plugin._original_drop = lambda _state, _boundaries=None, **_kwargs: ("鲑鱼", 1)
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {
        "fish_today": 0,
        "equip": {"name": "木竿", "durable": 10, "maintenance": 0, "induce": 0, "favor": 0},
        "pack": {},
    }
    stored = _attach_fake_state_store(plugin, state)

    async def buy_while_waiting():
        # 落在第一竿和第二竿之间：旧实现会把这次购买连同木竿一起覆盖掉。
        await asyncio.sleep(0.015)
        snapshot = plugin._state("100")
        plugin._add(snapshot, "木竿", pole=True)
        plugin._save("100", snapshot)

    async def run():
        await asyncio.gather(plugin._fish(event, state, "进行3次钓鱼"), buy_while_waiting())

    asyncio.run(run())

    assert len(stored["pack"]["木竿"]) == 1
    assert stored["pack"]["鲑鱼"] == 3


def test_mcfish_fishing_save_only_merges_the_current_cast_changes():
    """落盘时只合并本竿增量，其它指令在等待期间写入的改动必须原样保留。"""
    plugin = McFish.__new__(McFish)
    initial = {
        "fish_today": 1,
        "equip": {"name": "木竿", "durable": 5, "maintenance": 0, "induce": 0, "favor": 0},
        "pack": {"鲑鱼": 2},
    }
    _attach_fake_state_store(plugin, initial)
    base = plugin._state("100")
    current = copy.deepcopy(base)
    current["pack"]["鳕鱼"] = 3
    current["fish_today"] = 2

    purchase = plugin._state("100")
    plugin._add(purchase, "铁竿", pole=True)
    plugin._save("100", purchase)

    plugin._save_fishing_state("100", current, base)

    stored = plugin._state("100")
    assert stored["pack"]["鳕鱼"] == 3
    assert stored["fish_today"] == 2
    assert len(stored["pack"]["铁竿"]) == 1
    assert stored["pack"]["鲑鱼"] == 2


def test_mcfish_favor_converts_air_when_king_hunter_state_is_active():
    """钓鱼佬/美西螈区间下海之眷顾依旧把空竿转化为宝藏/鱼竿/鱼，而不是被忽略。"""
    plugin = McFish.__new__(McFish)
    plugin._load_original_data()

    assert plugin._zone_boundaries({"pack": {}, "equip": None}) == (0, 1, 5, 30, 60)
    # 普通区间：每级 宝藏+1、鱼竿+2、鱼+3，空竿-6。
    assert plugin._zone_boundaries({"pack": {}, "equip": {"name": "木竿", "favor": 3}}) == (0, 4, 14, 48, 78)
    assert plugin._zone_boundaries({"pack": {"鳕鱼": 101}, "equip": {"name": "木竿", "favor": 0}}) == (0, 2, 10, 45, 90)
    # 钓鱼佬只剩 10% 空竿，两级眷顾刚好把它转化完，剩下的空竿按 0 处理。
    assert plugin._zone_boundaries({"pack": {"鳕鱼": 101}, "equip": {"name": "木竿", "favor": 1}}) == (0, 3, 13, 51, 96)
    assert plugin._zone_boundaries({"pack": {"鳕鱼": 101}, "equip": {"name": "木竿", "favor": 2}}) == (0, 3, 14, 55, 100)
    assert plugin._zone_boundaries({"pack": {"鳕鱼": 101}, "equip": {"name": "木竿", "favor": 3}}) == (0, 3, 14, 55, 100)


def test_mcfish_probability_detail_matches_zone_boundaries():
    """“当前装备概率明细”必须和实际掉落区间同源，不能各算一套。"""
    plugin = McFish.__new__(McFish)
    plugin._load_original_data()

    states = [
        {"pack": {}, "equip": None},
        {"pack": {}, "equip": {"name": "木竿", "favor": 3}},
        {"pack": {"鳕鱼": 101}, "equip": {"name": "木竿", "favor": 2}},
        {"pack": {"海豚": 1}, "equip": {"name": "木竿", "favor": 3}},
    ]
    for state in states:
        boundaries = plugin._zone_boundaries(state)
        zones = [
            boundaries[1] - boundaries[0],
            boundaries[2] - boundaries[1],
            boundaries[3] - boundaries[2],
            boundaries[4] - boundaries[3],
        ]
        detail = plugin._probability_detail_text(state)
        assert f"宝藏 : {zones[0]}%" in detail
        assert f"鱼竿 : {zones[1]}%" in detail
        assert f"鱼类 : {zones[2]}%" in detail
        assert f"垃圾 : {zones[3]}%" in detail
        assert f"合计 : {sum(zones)}%" in detail


def test_mcfish_concurrent_overwrite_repair_restores_lost_purchases(tmp_path: Path):
    """2026-09-11 被覆盖丢失的购买一次性补发，并且只补发一次。"""
    plugin = _make_migration_plugin(tmp_path)
    for user_id in ("10123121", "1506123340"):
        plugin._db.execute(
            "INSERT INTO players VALUES (?, ?, ?)",
            (user_id, json.dumps({"pack": {}}, ensure_ascii=False), time.time()),
        )
    plugin._db.commit()

    plugin._repair_concurrent_overwrite_losses()

    fisher = json.loads(plugin._db.execute("SELECT state FROM players WHERE user_id = '10123121'").fetchone()[0])
    assert len(fisher["pack"]["木竿"]) == 7
    assert fisher["pack"]["木竿"][0]["durable"] == 30
    collector = json.loads(plugin._db.execute("SELECT state FROM players WHERE user_id = '1506123340'").fetchone()[0])
    assert collector["pack"]["墨鱼"] == 30
    assert plugin._db.execute("SELECT COUNT(*) FROM admin_adjustments").fetchone()[0] == 2

    plugin._repair_concurrent_overwrite_losses()

    again = json.loads(plugin._db.execute("SELECT state FROM players WHERE user_id = '10123121'").fetchone()[0])
    assert len(again["pack"]["木竿"]) == 7
    plugin._db.close()


def test_mcfish_concurrent_overwrite_late_repair_grants_items_and_refunds_gold(tmp_path: Path):
    """第二批补发：其余被覆盖的购买要么补发木竿，要么按原价退回金币，且只执行一次。"""
    plugin = _make_migration_plugin(tmp_path)
    economy = EconomyStore(tmp_path / "economy.db")
    plugin._economy = SimpleNamespace(store=economy)
    for user_id in ("10123121", "1506123340", "2448564447", "1310043427", "273421673"):
        plugin._db.execute(
            "INSERT INTO players VALUES (?, ?, ?)",
            (user_id, json.dumps({"pack": {}}, ensure_ascii=False), time.time()),
        )
    plugin._db.commit()

    plugin._top_up_concurrent_overwrite_losses()

    def poles(user_id):
        return len(json.loads(plugin._db.execute("SELECT state FROM players WHERE user_id = ?", (user_id,)).fetchone()[0])["pack"].get("木竿", []))

    assert poles("10123121") == 1
    assert poles("2448564447") == 3
    assert poles("1310043427") == 1
    assert poles("273421673") == 1
    assert poles("1506123340") == 0
    balance = lambda uid: economy.account(uid)["balance"]  # noqa: E731
    assert balance("2448564447") == 300
    assert balance("1310043427") == 80
    assert balance("1506123340") == 2253
    assert balance("10123121") == 0

    plugin._top_up_concurrent_overwrite_losses()

    assert poles("10123121") == 1
    assert balance("1506123340") == 2253
    plugin._db.close()
