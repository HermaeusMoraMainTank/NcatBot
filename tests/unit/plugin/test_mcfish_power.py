from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "plugins"))

from McFish.McFish import McFish  # noqa: E402
from McFish.mcfish_power import (  # noqa: E402
    ROD_POWER,
    equipment_power,
    fishing_power,
    jewelry_power,
    power_breakdown,
    power_summary,
    rod_power,
    talent_power,
)
from McFish.mcfish_rules import resolve_fishing_zone  # noqa: E402


def test_rod_power_matches_the_nine_tier_ladder():
    assert ROD_POWER == {
        "木竿": 0,
        "铜竿": 8,
        "锡竿": 16,
        "铁竿": 26,
        "猩红竿": 38,
        "幻光竿": 50,
        "星河竿": 62,
        "灿阳竿": 74,
        "三叉戟": 90,
    }
    assert rod_power({"equip": {"name": "三叉戟"}}) == 90
    assert rod_power({"equip": {"name": "未知道具"}}) == 0
    assert rod_power({}) == 0


def test_fishing_power_sums_level_rod_equipment_jewelry_and_talents():
    state = {
        "fish_level": 35,
        "equip": {"name": "铜竿"},
        "equipment": {"head": {"fp": 5}, "body": {"fp": 4}, "legs": {"fp": 3}},
        "jewelry": {"necklace": {"fp": 2}, "ring1": {"fp": 1}, "ring2": {"fp": 1}},
        "talents": {"levels": {"sense": 2, "rod_mastery": 1}},
        "fp_bonus": 2,
    }
    assert equipment_power(state) == 12
    assert jewelry_power(state) == 4
    # 钓感 2×4 = 8；竿术 1×5% × 鱼竿 8 = 0
    assert talent_power(state) == 8
    assert fishing_power(state) == 35 + 8 + 12 + 4 + 8 + 2


def test_equipment_power_ignores_malformed_slots():
    assert equipment_power({"equipment": {"head": {"fp": 4}, "body": 9, "legs": None}}) == 4
    assert equipment_power({"equipment": []}) == 0
    assert jewelry_power({"jewelry": {"necklace": {"fp": -3}}}) == 0


def test_level_power_has_a_floor_and_summary_skips_zero_sources():
    assert fishing_power({"fish_level": 0}) == 1
    assert power_summary({"fish_level": 1}) == "1（等级 1）"
    summary = power_summary({"fish_level": 10, "equip": {"name": "锡竿"}})
    assert summary == "26（等级 10 + 鱼竿 16）"


def test_power_breakdown_totals_to_fishing_power():
    state = {
        "fish_level": 42,
        "equip": {"name": "铁竿"},
        "equipment": {"head": {"fp": 7}},
        "jewelry": {"necklace": {"fp": 3}},
        "talents": {"levels": {"sense": 3, "rod_mastery": 2}},
    }
    assert sum(value for _, value in power_breakdown(state)) == fishing_power(state)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("河湾", "bay"),
        (" 深海 ", "deep"),
        ("遗迹", "ruin"),
        ("遗迹水域", "ruin"),
        ("storm", "storm"),
        ("风暴海域", "storm"),
        ("火星", None),
        ("", None),
    ],
)
def test_resolve_fishing_zone_accepts_labels_and_ids(text, expected):
    assert resolve_fishing_zone(text) == expected


class _FakeDB:
    def __init__(self, state: dict):
        self._state = state

    def execute(self, _sql, _params=()):
        payload = json.dumps(self._state, ensure_ascii=False)
        return SimpleNamespace(fetchone=lambda: (payload,))


def test_state_keeps_the_player_chosen_spot_across_days():
    plugin = McFish.__new__(McFish)
    plugin._db = _FakeDB(
        {
            "pack": {},
            "fish_level": 20,
            "fish_day": "2020-01-01",
            "fish_today": 7,
            "fishing_zone": "deep",
            "fishing_zone_day": "2020-01-01",
        }
    )

    state = plugin._state("100")

    assert state["fishing_zone"] == "deep"
    assert state["fish_today"] == 0
    assert state["equipment"] == {}
    assert state["jewelry"] == {}
    assert state["talents"] == {}


def _command_plugin(state: dict):
    plugin = McFish.__new__(McFish)
    plugin._state = lambda _uid: state
    plugin._save = lambda _uid, _value: None
    return plugin


def _command_event(text: str):
    return SimpleNamespace(
        sender=SimpleNamespace(user_id="100"),
        raw_message=text,
        reply=AsyncMock(),
    )


def test_forward_command_switches_the_spot():
    state = {"fishing_zone": "bay", "fish_level": 20, "pack": {}}
    plugin = _command_plugin(state)
    event = _command_event("前往 深海")

    asyncio.run(plugin._handle_message(event))

    assert state["fishing_zone"] == "deep"
    assert event.reply.await_args.kwargs["text"] == "当前鱼点：深海"


def test_forward_command_rejects_unknown_spot():
    state = {"fishing_zone": "bay", "pack": {}}
    plugin = _command_plugin(state)
    event = _command_event("前往 火星")

    asyncio.run(plugin._handle_message(event))

    assert state["fishing_zone"] == "bay"
    assert event.reply.await_args.kwargs["text"] == "未知鱼点：火星"


def test_power_and_spot_commands_render_system_summaries():
    state = {"fish_level": 35, "equip": {"name": "铜竿"}, "fishing_zone": "bay", "pack": {}}
    plugin = _command_plugin(state)

    power_event = _command_event("钓鱼力")
    asyncio.run(plugin._handle_message(power_event))
    power_text = power_event.reply.await_args.kwargs["text"]
    assert power_text.splitlines()[0] == "钓鱼力 43"
    assert "等级 35" in power_text
    assert "鱼竿 8" in power_text

    spot_event = _command_event("钓鱼鱼点")
    asyncio.run(plugin._handle_message(spot_event))
    spot_text = spot_event.reply.await_args.kwargs["text"]
    assert "当前鱼点：河湾" in spot_text
    assert "活跃鱼点：" in spot_text
    assert "前往 深海" in spot_text


def test_rod_ladder_migration_maps_legacy_pole_names(tmp_path):
    plugin = McFish.__new__(McFish)
    plugin._db = sqlite3.connect(tmp_path / "mcfish.db")
    plugin._db.execute("CREATE TABLE players (user_id TEXT PRIMARY KEY, state TEXT, updated_at REAL)")
    plugin._db.execute("CREATE TABLE shop (day TEXT PRIMARY KEY, discounts TEXT, inventory TEXT, custom_prices TEXT)")
    plugin._db.execute("CREATE TABLE migrations (migration_id TEXT PRIMARY KEY, applied_at REAL, details TEXT)")
    legacy = {
        "pack": {
            "木竿": [{"durable": 30}],
            "铁竿": [{"durable": 40}],
            "金竿": [{"durable": 60}],
            "钻石竿": [{"durable": 90}],
            "下界合金竿": [{"durable": 140}],
        },
        "equip": {"name": "金竿", "durable": 10},
    }
    plugin._db.execute("INSERT INTO players VALUES (?, ?, ?)", ("1", json.dumps(legacy, ensure_ascii=False), 0.0))
    plugin._db.execute(
        "INSERT INTO shop VALUES (?, ?, ?, ?)",
        (
            "2026-11-01",
            json.dumps({"金竿": 100}, ensure_ascii=False),
            json.dumps({"铁竿": [{"durable": 5}]}, ensure_ascii=False),
            json.dumps({"金竿": 500}, ensure_ascii=False),
        ),
    )
    plugin._db.commit()

    plugin._migrate_rod_ladder()

    row = json.loads(plugin._db.execute("SELECT state FROM players WHERE user_id='1'").fetchone()[0])
    assert set(row["pack"]) == {"木竿", "铜竿", "锡竿", "铁竿", "猩红竿"}
    assert row["equip"]["name"] == "锡竿"
    discounts, inventory, custom = plugin._db.execute(
        "SELECT discounts, inventory, custom_prices FROM shop"
    ).fetchone()
    assert json.loads(discounts) == {"锡竿": 100}
    assert "铜竿" in json.loads(inventory)
    assert json.loads(custom) == {"锡竿": 500}
    assert plugin._db.execute(
        "SELECT 1 FROM migrations WHERE migration_id = 'mcfish-rod-ladder-2026-11-01'"
    ).fetchone()

    # 幂等：第二次执行不能把迁移后新建的“铁竿”再改成铜竿
    plugin._migrate_rod_ladder()
    row = json.loads(plugin._db.execute("SELECT state FROM players WHERE user_id='1'").fetchone()[0])
    assert "铁竿" in row["pack"]


def test_zone_unlocks_follow_fishing_power_and_default_picks_highest():
    from McFish.mcfish_rules import (
        default_fishing_zone_for_power,
        fishing_zone_required_power,
        fishing_zone_unlocked,
        fish_min_power,
    )

    assert fishing_zone_required_power("bay") == 0
    assert fishing_zone_required_power("deep") == 20
    assert fishing_zone_required_power("ruin") == 50
    assert fishing_zone_required_power("storm") == 80
    assert fishing_zone_required_power("magma") == 110
    assert fishing_zone_required_power("frost") == 135
    assert fishing_zone_required_power("glow") == 160
    assert fishing_zone_required_power("astral") == 190
    assert fishing_zone_required_power("chrono") == 220
    assert not fishing_zone_unlocked("deep", 19)
    assert fishing_zone_unlocked("deep", 20)
    assert default_fishing_zone_for_power(0) == "bay"
    assert default_fishing_zone_for_power(20) == "deep"
    assert default_fishing_zone_for_power(49) == "deep"
    assert default_fishing_zone_for_power(50) == "ruin"
    assert default_fishing_zone_for_power(220) == "chrono"
    # 鱼点内 4 条鱼按顺序逐档加钓鱼力要求
    assert fish_min_power("bay", "岩石凶鲨") == 0
    assert fish_min_power("bay", "吸血鳗") == 0
    assert fish_min_power("bay", "恶魔蠕虫") == 20
    assert fish_min_power("bay", "橙色洞穴孔雀鱼") == 45
    assert fish_min_power("chrono", "粉角皮科鱼") == 265


def test_fishing_power_raises_rare_fish_weights_and_blocks_unmet_entries():
    from McFish.mcfish_rules import fish_min_power, fish_zone_weights

    assert fish_min_power("storm", "魂灵面纱") == 100
    below = fish_zone_weights("storm", "灿阳竿", power=95)
    assert "魂灵面纱" not in below
    at_gate = fish_zone_weights("storm", "灿阳竿", power=100)
    high = fish_zone_weights("storm", "灿阳竿", power=400)

    def share(weights):
        total = sum(weights.values())
        return weights["魂灵面纱"] / total

    assert at_gate["魂灵面纱"] > 0
    assert share(high) > share(at_gate)
    assert high["魂灵面纱"] > at_gate["魂灵面纱"]


def test_fishing_power_shortens_the_hidden_wait(monkeypatch):
    import importlib

    module = importlib.import_module("McFish.McFish")
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    rates = []

    def fake_wait(_name, *, induce=0, wait_rate=1.0):
        rates.append(wait_rate)
        return 0.01

    monkeypatch.setattr(module, "fishing_wait_for_result", fake_wait)
    plugin = McFish.__new__(McFish)
    plugin.get_config = lambda key, default=None: 50 if key == "fish_limit" else default
    plugin._item_image = lambda _name: None
    plugin._original_drop = lambda _state, _boundaries=None, **_kwargs: ("垃圾", 1)
    plugin._fish_windows = list
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {
        "fish_today": 0,
        "fish_level": 50,
        "fish_xp": 0,
        "equip": {"name": "木竿", "durable": 3, "maintenance": 0, "induce": 0, "favor": 0},
        "pack": {},
        "fishing_zone": "bay",
    }
    plugin._state = lambda _uid: state
    plugin._save = lambda _uid, _value: None

    asyncio.run(plugin._fish(event, state, "进行1次钓鱼"))

    # FP = 等级 50 + 木竿 0 = 50，等待倍率 1/(1 + 50/100)。
    assert abs(rates[-1] - 1.0 / 1.5) < 1e-9


def test_legacy_level_thresholds_still_resolve_for_old_saves():
    from McFish.mcfish_rules import default_fishing_zone, fishing_zone_unlock_level

    assert fishing_zone_unlock_level("bay") == 1
    assert fishing_zone_unlock_level("chrono") == 92
    assert default_fishing_zone(1) == "bay"
    assert default_fishing_zone(10) == "deep"
    assert default_fishing_zone(99) == "chrono"


def test_forward_command_blocks_a_locked_spot():
    state = {"fishing_zone": "bay", "fish_level": 5, "pack": {}}
    plugin = _command_plugin(state)
    event = _command_event("前往 深海")

    asyncio.run(plugin._handle_message(event))

    assert state["fishing_zone"] == "bay"
    assert event.reply.await_args.kwargs["text"] == "深海：需要钓鱼力 20，当前 5"


def test_state_falls_back_when_the_stored_spot_is_locked():
    plugin = McFish.__new__(McFish)
    plugin._db = _FakeDB(
        {
            "pack": {},
            "fish_level": 5,
            "fish_day": "2020-01-01",
            "fishing_zone": "storm",
            "fishing_zone_day": "2020-01-01",
        }
    )

    state = plugin._state("100")

    assert state["fishing_zone"] == "bay"
