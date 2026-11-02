from __future__ import annotations

import asyncio
import json
import random
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "plugins"))

from McFish import mcfish_content as C  # noqa: E402
from McFish.McFish import McFish  # noqa: E402

EQUIPMENT_DROPS = {"潜水头盔", "海藻披风", "雇佣兵头带", "忍者头巾"}


def test_waste_split_sums_to_100_for_every_zone():
    assert set(C.WASTE_SPLIT) == {
        "bay", "deep", "ruin", "storm", "magma", "frost", "glow", "astral", "chrono"
    }
    for zone, parts in C.WASTE_SPLIT.items():
        assert sum(parts) == 100, zone
        assert C.waste_split(zone) == parts


def test_roll_waste_sub_follows_the_zone_boundaries():
    junk, material, food, _valuable = C.WASTE_SPLIT["bay"]
    assert C.roll_waste_sub("bay", 0) == "junk"
    assert C.roll_waste_sub("bay", junk - 1) == "junk"
    assert C.roll_waste_sub("bay", junk) == "material"
    assert C.roll_waste_sub("bay", junk + material) == "food"
    assert C.roll_waste_sub("bay", junk + material + food) == "valuable"
    assert C.roll_waste_sub("bay", 99) == "valuable"
    # 未知鱼点回退到河湾
    assert C.roll_waste_sub("unknown", 99) == "valuable"


def test_zone_material_food_and_valuable_lists_roll_their_own_items():
    rng = random.Random(3)
    for zone in ("bay", "chrono"):
        material_names = {name for name, _low, _high in C.ZONE_MATERIALS[zone]}
        for _ in range(50):
            name, quantity = C.roll_material(zone, rng)
            assert name in material_names
            assert quantity >= 1
            assert C.roll_food(zone, rng)[0] in C.ZONE_FOODS[zone]
            assert C.roll_valuable(zone, rng)[0] in C.ZONE_VALUABLES[zone]


def test_box_tables_sum_to_100_and_reference_known_items():
    for box, table in C.BOX_LOOT.items():
        assert sum(weight for _name, weight, _low, _high in table) == 100, box
        for name, weight, low, high in table:
            assert weight > 0 and 1 <= low <= high
            assert (
                name in C.ITEM_PRICES
                or name in C.KEY_NAMES.values()
                or name in C.CARD_NAMES
                or name in EQUIPMENT_DROPS
            ), name
    for tier, box_name in C.BOX_NAMES.items():
        assert C.box_key_name(box_name) == C.KEY_NAMES[tier]
    assert C.box_key_name("未知箱子") is None


def test_roll_box_loot_always_returns_a_table_entry():
    rng = random.Random(11)
    for _ in range(100):
        name, quantity = C.roll_box_loot("上锁的铜制箱子", rng)
        assert name in {entry[0] for entry in C.BOX_LOOT["上锁的铜制箱子"]}
        assert quantity >= 1


def test_card_roll_uses_pity_and_ignores_unknown_zones():
    name, _rate, pity = C.CARDS["bay"]
    assert C.roll_card("bay", pity - 1) == name
    assert C.roll_card("unknown") is None
    assert C.roll_card("bay", 0, rng=random.Random(0)) in (None, name)


def test_content_items_are_registered_with_prices_and_types():
    plugin = McFish.__new__(McFish)
    plugin._load_original_data()

    assert plugin._prices["纤维"] == 2
    assert plugin._prices["绿色海藻"] == 30
    assert plugin._prices["上古宝石"] == 3500
    assert plugin._types["纤维"] == "material"
    assert plugin._types["绿色海藻"] == "food"
    assert plugin._types["上古宝石"] == "valuable"
    assert plugin._types["铜钥匙"] == "key"
    assert plugin._types["上锁的铜制箱子"] == "box"
    assert plugin._types["克己"] == "card"
    # 钥匙、箱子、卡牌不可出售
    for name in ("铜钥匙", "上锁的铜制箱子", "克己"):
        assert name not in plugin._prices


def test_original_drop_splits_waste_into_material(monkeypatch):
    import importlib

    module = importlib.import_module("McFish.McFish")
    plugin = McFish.__new__(McFish)
    plugin._load_original_data()
    # 第一次 roll 落入 waste，第二次 roll 落入材料的 18% 区间
    rolls = iter((0, 80))
    monkeypatch.setattr(module.random, "randrange", lambda _limit: next(rolls))

    name, quantity = plugin._original_drop(
        {"pack": {}, "equip": {"name": "木竿"}, "fishing_zone": "bay"},
        (0, 0, 0, 0, 100),
    )

    assert name in {entry[0] for entry in C.ZONE_MATERIALS["bay"]}
    assert quantity >= 1


def test_original_drop_can_yield_a_key(monkeypatch):
    import importlib

    module = importlib.import_module("McFish.McFish")
    plugin = McFish.__new__(McFish)
    plugin._load_original_data()
    # waste 子类落到“贵重”，再用 0.0 命中 10% 钥匙/箱子与钥匙分支
    rolls = iter((0, 99))
    monkeypatch.setattr(module.random, "randrange", lambda _limit: next(rolls))
    monkeypatch.setattr(module.random, "random", lambda: 0.0)

    name, quantity = plugin._original_drop(
        {"pack": {}, "equip": {"name": "木竿"}, "fishing_zone": "bay"},
        (0, 0, 0, 0, 100),
    )

    assert name == "铜钥匙"
    assert quantity == 1


def test_open_box_consumes_the_key_and_grants_loot(monkeypatch):
    import importlib

    module = importlib.import_module("McFish.McFish")
    monkeypatch.setattr(module, "roll_box_loot", lambda _box: ("鱼卵琥珀", 2))
    plugin = McFish.__new__(McFish)
    plugin._save = lambda _uid, _state: None
    state = {"pack": {"上锁的铜制箱子": 1, "铜钥匙": 2}}
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._open_box(event, state, "上锁的铜制箱子"))

    assert state["pack"]["上锁的铜制箱子"] == 0
    assert state["pack"]["铜钥匙"] == 1
    assert state["pack"]["鱼卵琥珀"] == 2
    assert event.reply.await_args.kwargs["text"] == "上锁的铜制箱子：鱼卵琥珀 ×2"


def test_eat_food_restores_used_attempts_and_stops_at_zero():
    plugin = McFish.__new__(McFish)
    plugin._save = lambda _uid, _state: None
    state = {"pack": {"绿色海藻": 3}, "fish_today": 10, "fish_level": 10, "food_today": 0}
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._eat_food(event, state, "绿色海藻 2"))

    assert state["pack"]["绿色海藻"] == 1
    assert state["fish_today"] == 8
    assert state["food_today"] == 2
    assert "恢复 2 次" in event.reply.await_args.kwargs["text"]

    state["fish_today"] = 0
    asyncio.run(plugin._eat_food(event, state, "绿色海藻 1"))
    assert state["pack"]["绿色海藻"] == 1
    assert "今日已用 0/" in event.reply.await_args.kwargs["text"]


def test_eat_food_restores_current_zone_quota_too():
    plugin = McFish.__new__(McFish)
    plugin._save = lambda _uid, _state: None
    state = {
        "pack": {"绿色海藻": 1},
        "fish_today": 10,
        "fish_level": 10,
        "food_today": 0,
        "fishing_zone": "bay",
        "zone_fish_today": {"bay": 10},
    }
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._eat_food(event, state, "绿色海藻 1"))

    assert state["fish_today"] == 9
    assert state["zone_fish_today"]["bay"] == 9


def test_eat_food_respects_the_daily_restore_cap():
    plugin = McFish.__new__(McFish)
    plugin._save = lambda _uid, _state: None
    state = {"pack": {"绿色海藻": 5}, "fish_today": 10, "fish_level": 10, "food_today": 14}
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._eat_food(event, state, "绿色海藻 5"))

    assert state["food_today"] == 15
    assert state["fish_today"] == 9
    assert state["pack"]["绿色海藻"] == 4


def test_card_text_shows_collection_and_pity():
    plugin = McFish.__new__(McFish)
    text = plugin._card_text({"cards": {"克己": 1}, "card_pity": {"deep": 249}})

    assert "神谕卡片 1/9" in text
    assert "克己（河湾）：已获得" in text
    assert "灵光（深海）：保底 249/250" in text


def test_adjusted_waste_split_applies_gear_bonuses_from_junk():
    junk, material, food, valuable = C.adjusted_waste_split("bay", 0.5, 0.5)
    assert material == round(18 * 1.5)
    assert valuable == round(2 * 1.5)
    assert junk == 100 - material - food - valuable
    assert C.roll_waste_sub("bay", 70, material_bonus=0.5) == "material"


def test_gear_air_bonus_converts_air_into_fish():
    from McFish.mcfish_equipment import make_piece

    plugin = McFish.__new__(McFish)
    plugin._load_original_data()
    base = plugin._zone_boundaries({"pack": {}, "equipment": {}, "jewelry": {}})
    geared = plugin._zone_boundaries(
        {
            "pack": {},
            "equipment": {
                "head": make_piece("铜制头盔", 20),
                "body": make_piece("铜制胸甲", 20),
                "legs": make_piece("铜制裤子", 20),
            },
            "jewelry": {"necklace": make_piece("铜制四芒星项链", 20)},
        }
    )
    # 铜套 4 件在 20 级时提供 3% 空竿转化，鱼区间应更宽
    assert (geared[3] - geared[2]) > (base[3] - base[2])

def test_state_migrates_old_card_names_to_wiki_cards():
    plugin = McFish.__new__(McFish)
    plugin._db = sqlite3.connect(":memory:")
    plugin._db.execute("CREATE TABLE players (user_id TEXT PRIMARY KEY, state TEXT)")
    plugin._db.execute(
        "INSERT INTO players VALUES (?, ?)",
        ("100", json.dumps({"pack": {}, "cards": {"潮信": 1, "克己": 1}}, ensure_ascii=False)),
    )
    plugin._db.commit()

    state = plugin._state("100")

    assert state["cards"]["克己"] == 1
    assert state["cards"]["灵光"] == 1
    assert "潮信" not in state["cards"]
