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

    plugin._independent_box_key_drop = lambda _effects, _zone: None
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
    # 贵重品位替换：0.0 命中 10% 钥匙/箱子与钥匙分支；独立判定单独关掉。
    plugin._independent_box_key_drop = lambda _effects, _zone: None
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


def test_food_talent_rank_one_still_adds_value_to_one_restore_food():
    plugin = McFish.__new__(McFish)
    plugin._save = lambda _uid, _state: None
    state = {
        "pack": {"绿色海藻": 4},
        "fish_today": 10,
        "fish_level": 50,
        "food_today": 0,
        "talents": {"levels": {"cooking": 1}},
    }
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._eat_food(event, state, "绿色海藻 4"))

    # 1 × 1.25 × 4 = 5 次：小数累计后第 4 个食物回复 2 次。
    assert state["pack"]["绿色海藻"] == 0
    assert state["food_today"] == 5
    assert state["fish_today"] == 5


def test_food_talent_fraction_carries_between_meals():
    plugin = McFish.__new__(McFish)
    plugin._save = lambda _uid, _state: None
    state = {
        "pack": {"绿色海藻": 4},
        "fish_today": 12,
        "fish_level": 50,
        "food_today": 0,
        "talents": {"levels": {"cooking": 1}},
    }
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._eat_food(event, state, "绿色海藻 2"))
    assert state["food_today"] == 2
    assert state["food_carry"] == 0.5

    asyncio.run(plugin._eat_food(event, state, "绿色海藻 2"))
    assert state["food_today"] == 5
    assert state["food_carry"] == 0.0


def test_food_talent_raises_the_daily_restore_cap():
    plugin = McFish.__new__(McFish)
    plugin._save = lambda _uid, _state: None
    state = {
        "pack": {"蓝色海藻": 5},
        "fish_today": 40,
        "fish_level": 60,
        "food_today": 15,
        "talents": {"levels": {"cooking": 3}},
    }
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())

    asyncio.run(plugin._eat_food(event, state, "蓝色海藻 5"))

    # 上限 15 × 1.75 = 26：还能补 11 次，3 × 1.75 = 5.25 次/个。
    assert state["food_today"] == 26
    assert state["fish_today"] == 29
    assert state["pack"]["蓝色海藻"] == 2


def test_card_text_shows_collection_and_pity():
    plugin = McFish.__new__(McFish)
    text = plugin._card_text({"cards": {"克己": 1}, "card_pity": {"deep": 249}})

    assert "神谕卡片 1/9" in text
    assert "克己（河湾）：已获得" in text
    assert "灵光（深海）：保底 249/250" in text


def test_adjusted_waste_split_applies_gear_bonuses_from_junk():
    base = C.waste_split("bay")
    junk, material, food, valuable = C.adjusted_waste_split("bay", 0.5, 0.5)
    assert material == round(base[1] * 1.5)
    assert valuable == round(base[3] * 1.5)
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

def test_independent_box_and_key_roll_uses_the_zone_tier(monkeypatch):
    import importlib

    module = importlib.import_module("McFish.McFish")
    plugin = McFish.__new__(McFish)
    rolls = iter([0.0, 0.012, 0.5])
    monkeypatch.setattr(module.random, "random", lambda: next(rolls))

    assert plugin._independent_box_key_drop({}, "ruin") == ("上锁的铁制箱子", 1)
    assert plugin._independent_box_key_drop({}, "ruin") == ("铁钥匙", 1)
    assert plugin._independent_box_key_drop({}, "ruin") is None


def test_independent_box_roll_scales_with_the_box_bonus(monkeypatch):
    import importlib

    module = importlib.import_module("McFish.McFish")
    plugin = McFish.__new__(McFish)
    rolls = iter([0.011, 0.011])
    monkeypatch.setattr(module.random, "random", lambda: next(rolls))

    # 满级寻宝直觉 +24%：箱子 1.24% / 钥匙 0.62%，0.011 命中箱子；
    # 没有加成时 0.011 落在钥匙区间。
    assert plugin._independent_box_key_drop({"box": 0.24}, "bay") == ("上锁的铜制箱子", 1)
    assert plugin._independent_box_key_drop({}, "bay") == ("铜钥匙", 1)


def test_key_recipes_cover_every_key_tier():
    from McFish.mcfish_content import KEY_NAMES, KEY_RECIPES

    assert set(KEY_RECIPES) == set(KEY_NAMES.values())
    for key, (gold, items) in KEY_RECIPES.items():
        assert gold > 0
        assert items
        for material, count in items.items():
            assert material in C.MATERIAL_NAMES
            assert count >= 1


def test_craft_key_consumes_materials_and_fee():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._save = lambda *_args: None
    plugin._discounted_fee = lambda _state, fee: fee
    plugin._charge_fee = lambda *_args: True
    plugin._advance_tasks = lambda _state, spend=0: None
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {"pack": {"铜矿石": 3}}

    asyncio.run(plugin._craft_key(event, state, "铜钥匙"))

    assert state["pack"]["铜矿石"] == 1
    assert state["pack"]["铜钥匙"] == 1
    assert "成功合成铜钥匙" in event.reply.await_args.kwargs["text"]


def test_craft_key_reports_missing_materials():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._save = lambda *_args: None
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {"pack": {"铜矿石": 1}}

    asyncio.run(plugin._craft_key(event, state, "铜钥匙"))

    message = event.reply.await_args.kwargs["text"]
    assert "合成铜钥匙材料不足" in message
    assert "铜矿石：已有1/2个，还差1个" in message
    assert int(state["pack"].get("铜钥匙", 0)) == 0


def test_craft_command_dispatches_key_recipes():
    import asyncio

    plugin = McFish.__new__(McFish)
    plugin._ensure_accessory_state = lambda _state: None
    plugin._craft_key = AsyncMock()
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {"pack": {}}

    asyncio.run(plugin._craft(event, state, "灿阳金属钥匙"))

    plugin._craft_key.assert_awaited_once()
