from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "plugins"))

from McFish import mcfish_talents as T  # noqa: E402
from McFish.McFish import McFish  # noqa: E402


def test_point_curve_gives_twenty_five_points_at_level_100():
    assert T.total_points(1) == 0
    assert T.total_points(5) == 1
    assert T.total_points(99) == 19
    assert T.total_points(100) == 25
    state = {"fish_level": 100, "talents": {"levels": {"keen": 2, "sense": 3}}}
    assert T.used_points(state) == 5
    assert T.free_points(state) == 20


def test_can_learn_checks_level_prerequisite_and_points():
    state = {"fish_level": 10, "talents": {"levels": {}}}
    ok, reason = T.can_learn(state, "advantage")
    assert not ok and "等级 20" in reason

    state["fish_level"] = 20
    ok, reason = T.can_learn(state, "treasure")
    assert not ok and "垂钓者的优势" in reason

    assert T.learn(state, "advantage")[0]
    assert T.learn(state, "advantage")[0]
    ok, _reason = T.can_learn(state, "treasure")
    assert ok

    empty = {"fish_level": 5, "talents": {"levels": {}}}
    assert T.learn(empty, "keen")[0]
    ok, reason = T.can_learn(empty, "keen")
    assert not ok and "天赋点" in reason


def test_effects_skip_power_nodes_and_summary_uses_labels():
    state = {"fish_level": 50, "talents": {"levels": {"keen": 2, "sense": 1}}}
    effects = T.effects(state)
    assert effects["wait"] == 0.08
    assert "fp" not in effects
    lines = T.summary_lines(state)
    assert any("敏锐触觉 2/5" in line for line in lines)
    assert any("钓鱼天赋 可用" in line for line in lines)


def test_reset_cost_is_fixed():
    state = {"talents": {"reset_count": 0}}
    assert T.reset_cost(state) == 20000
    state["talents"]["reset_count"] = 3
    assert T.reset_cost(state) == 20000
    state["talents"]["reset_count"] = 99
    assert T.reset_cost(state) == 20000

    T.reset(state)
    assert state["talents"]["levels"] == {}
    assert state["talents"]["reset_count"] == 100


def test_talent_commands_learn_and_reset():
    plugin = McFish.__new__(McFish)
    plugin._save = lambda _uid, _state: None
    plugin._economy = None
    state = {"fish_level": 50, "pack": {}}
    plugin._state = lambda _uid: state

    def event(text):
        return SimpleNamespace(
            sender=SimpleNamespace(user_id="100"), raw_message=text, reply=AsyncMock()
        )

    learn_event = event("学习 钓感")
    asyncio.run(plugin._handle_message(learn_event))
    assert state["talents"]["levels"]["sense"] == 1
    assert "钓感：1/5" in learn_event.reply.await_args.kwargs["text"]

    panel_event = event("钓鱼天赋")
    asyncio.run(plugin._handle_message(panel_event))
    assert "钓鱼天赋 可用" in panel_event.reply.await_args.kwargs["text"]

    reset_event = event("重置钓鱼天赋")
    asyncio.run(plugin._handle_message(reset_event))
    assert state["talents"]["levels"] == {}
    assert state["talents"]["reset_count"] == 1


def test_gear_effects_merge_talent_bonuses():
    plugin = McFish.__new__(McFish)
    state = {"talents": {"levels": {"keen": 1}}, "equipment": {}, "jewelry": {}}
    assert plugin._gear_effects(state)["wait"] == 0.04


def test_talent_tree_shows_branch_connectors_and_lock_reasons():
    state = {"fish_level": 50, "talents": {"levels": {"keen": 2}}}
    text = "\n".join(T.tree_lines(state))
    assert "钓鱼天赋 可用" in text
    assert "A 手感" in text
    assert "    └ 稳线 0/3" in text
    assert "        └ 轻巧抛竿 0/3" in text
    assert "锁定：需要 稳线 2 级" in text
    assert "（前置 敏锐触觉 2）" in text


def test_talent_nodes_stay_locked_until_prerequisite_level_is_met():
    state = {"fish_level": 50, "talents": {"levels": {}}}
    assert T.node_status(state, "light_cast") == "prereq_lock"
    ok, reason = T.can_learn(state, "light_cast")
    assert not ok and "稳线" in reason
    # 点满前置后同一节点才变成可加点
    state["talents"]["levels"] = {"keen": 2, "steady": 2}
    assert T.node_status(state, "light_cast") == "ready"


def test_sanitize_refunds_points_from_illegal_allocations():
    state = {"fish_level": 100, "talents": {"levels": {"light_cast": 3, "handy": 2}}}
    before_free = T.free_points(state)
    refunded = T.sanitize(state)

    assert refunded == 5
    assert state["talents"]["levels"] == {}
    assert T.used_points(state) == 0
    assert T.free_points(state) == before_free + 5


def test_sanitize_clamps_over_max_ranks_without_eating_points():
    state = {"fish_level": 100, "talents": {"levels": {"keen": 9}}}
    refunded = T.sanitize(state)
    assert refunded == 4
    assert state["talents"]["levels"] == {"keen": 5}
    assert T.total_points(100) == 25
    assert T.free_points(state) == 20


def test_state_heals_illegal_talent_points_on_read():
    class FakeDB:
        def __init__(self, state: dict):
            self.payload = json.dumps(state, ensure_ascii=False)

        def execute(self, _sql, _params=()):
            return SimpleNamespace(fetchone=lambda: (self.payload,))

    plugin = McFish.__new__(McFish)
    plugin._db = FakeDB(
        {"pack": {}, "fish_level": 100, "talents": {"levels": {"deep_eye": 3}}}
    )

    state = plugin._state("100")

    assert state["talents"]["levels"] == {}
    assert state["talent_refunded"] == 3
    assert T.free_points(state) == T.total_points(100)


def test_handy_talent_discounts_repair_and_craft_fees():
    plugin = McFish.__new__(McFish)
    base = {"talents": {"levels": {}}, "equipment": {}, "jewelry": {}}
    handy = {"talents": {"levels": {"keen": 2, "steady": 2, "light_cast": 0, "handy": 3}},
             "equipment": {}, "jewelry": {}}
    fee = 100000
    assert plugin._discounted_fee(base, fee) == fee
    assert plugin._discounted_fee(handy, fee) == round(fee * (1.0 - 0.18))
    assert plugin._discounted_fee(handy, 0) == 0
    assert plugin._discounted_fee(handy, 1) == 1


def test_light_cast_talent_saves_baits_without_changing_consumption_cap(monkeypatch):
    import importlib

    module = importlib.import_module("McFish.McFish")
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    plugin = McFish.__new__(McFish)
    plugin.get_config = lambda key, default=None: 50 if key == "fish_limit" else default
    plugin._item_image = lambda _name: None
    plugin._original_drop = lambda _state, _boundaries=None, **_kwargs: ("鲑鱼", 1)
    plugin._fish_windows = list
    plugin._gear_effects = lambda _state: {"bait_save": 0.5}
    monkeypatch.setattr(module.random, "random", lambda: 0.0)  # 每次都触发节省
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    stored = {
        "fish_today": 0,
        "equip": {"name": "木竿", "durable": 4, "maintenance": 0, "induce": 0, "favor": 0},
        "pack": {},
        "active_consumables": {"魔法鱼饵": 3},
        "fishing_zone": "bay",
    }
    plugin._state = lambda _uid: stored
    plugin._save = lambda _uid, _value: None

    asyncio.run(plugin._fish(event, stored, "进行2次钓鱼"))

    assert stored["active_consumables"]["魔法鱼饵"] == 3
