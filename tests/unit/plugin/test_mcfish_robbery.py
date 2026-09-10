from __future__ import annotations

import sys
import json
import sqlite3
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins"))

from GroupEconomy.store import EconomyStore  # noqa: E402
from McFish.McFish import MCFISH_HELP, SHOP_WOOD_PRICE, FishKingWindow, McFish  # noqa: E402
from Robbery.Robbery import Robbery  # noqa: E402


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
    assert pack_image.size[0] == 1020
    assert pack_image.getpixel((500, 15))[:3] == (255, 255, 255)
    assert PILImage.open(store_path).size[0] >= 1000
    assert pack_path.exists() and store_path.exists()


def test_mcfish_help_contains_original_commands_and_rules():
    assert "钓鱼背包" in MCFISH_HELP
    assert "进行n次钓鱼" in MCFISH_HELP
    assert "宝藏诅咒" in MCFISH_HELP
    assert "合成成功率90%" in MCFISH_HELP
    assert "三叉戟 : 可使1次钓鱼视为3次钓鱼。耐久上限:301" in MCFISH_HELP


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


def test_mcfish_original_drop_uses_original_probability_intervals(monkeypatch):
    import importlib

    plugin = McFish.__new__(McFish)
    plugin._load_original_data()
    state = {"pack": {}, "equip": None}
    rolls = iter((0, 0))
    module = importlib.import_module("McFish.McFish")
    monkeypatch.setattr(module.random, "randrange", lambda _limit: next(rolls))

    assert plugin._original_drop(state) == ("诱钓", 1)


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

    assert PILImage.open(output).size[0] == 1020


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

    assert PILImage.open(output).size[0] == 1020
    entries = []
    value = {"durable": 70, "maintenance": 0, "induce": 0, "favor": 0}
    entries.append(f"金竿({value['durable']}/{value['maintenance']}/{value['induce']}/{value['favor']})")
    assert entries == ["金竿(70/0/0/0)"]


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


def test_mcfish_help_explains_emergency_pole_is_independent_of_shop():
    assert "花费100购买鱼竿" in MCFISH_HELP
    assert "最多可以购买3次" in MCFISH_HELP
    assert "每3天最多补偿1根木竿" in MCFISH_HELP
    assert "可一次出售多个物品" in MCFISH_HELP


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
        assert image.size == (1200, 880)


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
    plugin._original_drop = lambda _state, _boundaries=None: ("垃圾", 1)
    plugin._item_image = lambda _name: None
    plugin._save = lambda *_args: None
    event = SimpleNamespace(
        sender=SimpleNamespace(user_id="100"),
        reply=AsyncMock(),
    )
    state = {
        "fish_today": 0,
        "equip": {"name": "木竿", "durable": 2, "maintenance": 0, "induce": 0, "favor": 0},
        "pack": {},
    }

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
    plugin._save = lambda *_args: None

    def drop(_state, boundaries):
        boundaries_seen.append(boundaries)
        return "鲑鱼", 1

    plugin._original_drop = drop
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {
        "fish_today": 0,
        "equip": {"name": "木竿", "durable": 2, "maintenance": 0, "induce": 0, "favor": 0},
        "pack": {"鲑鱼": 100},
    }

    asyncio.run(plugin._fish(event, state, "进行2次钓鱼"))

    assert boundaries_seen == [boundaries_seen[0], boundaries_seen[0]]


def test_mcfish_batch_fishing_persists_each_catch_after_its_wait(monkeypatch):
    import asyncio
    import importlib

    module = importlib.import_module("McFish.McFish")
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    plugin = McFish.__new__(McFish)
    plugin.get_config = lambda key, default=None: 50 if key == "fish_limit" else default
    plugin._item_image = lambda _name: None
    plugin._original_drop = lambda _state, _boundaries=None: ("鲑鱼", 1)
    snapshots = []

    def save(_uid, state):
        snapshots.append(dict(state.get("pack", {})))

    plugin._save = save
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {
        "fish_today": 0,
        "equip": {"name": "木竿", "durable": 3, "maintenance": 0, "induce": 0, "favor": 0},
        "pack": {},
    }

    asyncio.run(plugin._fish(event, state, "进行2次钓鱼"))

    assert snapshots[0].get("鲑鱼", 0) == 0
    assert snapshots[1].get("鲑鱼", 0) == 1
    assert snapshots[-1].get("鲑鱼", 0) == 2


def test_wallet_help_does_not_claim_the_fishing_help_command():
    from GroupEconomy.GroupEconomy import HELP

    assert "钓鱼帮助：查看钓鱼、背包、商店、装备、交易和合成说明" in HELP
