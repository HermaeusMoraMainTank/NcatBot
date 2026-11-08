from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "plugins"))

from McFish.McFish import McFish  # noqa: E402
from McFish.mcfish_equipment import (  # noqa: E402
    PIECE_INFO,
    SETS,
    craft_recipe,
    dismantle_materials,
    fp_for_level,
    make_piece,
    piece_info,
    scaled_effects,
    upgrade_cost,
    worn_sets,
)


def test_all_sets_have_the_designed_starting_levels():
    assert set(SETS) == {
        "布", "铜", "锡", "铁", "猩红", "幻光", "星河", "灿阳",
        "海钓", "雇佣兵", "忍者", "圣骑士", "神谕",
    }
    starts = {name: spec["start"] for name, spec in SETS.items()}
    assert starts == {
        "布": 1, "铜": 3, "锡": 5, "铁": 7, "猩红": 10, "幻光": 12, "星河": 14,
        "灿阳": 16, "海钓": 13, "雇佣兵": 10, "忍者": 17, "圣骑士": 13, "神谕": 20,
    }
    assert fp_for_level(1) == 1
    assert fp_for_level(10) == 8
    assert fp_for_level(20) == 16
    assert piece_info("铜制头盔") == {"set": "铜", "slot": "head", "start": 3}
    assert piece_info("未知装备") is None


def test_craft_recipe_only_exists_for_metal_sets():
    recipe = craft_recipe("铜制头盔")
    assert recipe == {"level": 3, "gold": 4800, "items": {"铜矿石": 6, "废弃零件": 4}}
    assert craft_recipe("潜水头盔") is None
    assert craft_recipe("木制戒指")["level"] == 1


def test_upgrade_cost_uses_the_shared_ladder():
    assert upgrade_cost("铜制头盔", 2) == (450, {"铜矿石": 2})
    assert upgrade_cost("铜制头盔", 5) == (750, {"铜矿石": 7})
    assert upgrade_cost("铜制头盔", 20) == (
        5400, {"铜矿石": 6, "灿阳金属矿石": 12, "星尘": 3}
    )
    assert upgrade_cost("铜制头盔", 21) is None


def test_worn_sets_and_scaled_effects():
    equipment = {
        "head": {"name": "铜制头盔", "level": 10},
        "body": {"name": "铜制胸甲", "level": 10},
        "legs": None,
    }
    worn = worn_sets(equipment, {})
    assert worn["铜"] == {"count": 2, "avg_level": 10.0}
    # 铜套 2 件：咬钩速度 +5% × 平均等级 10/20 = 2.5%
    assert scaled_effects(equipment, {}) == {"wait": 0.025}


def test_make_piece_starts_at_the_set_level_and_clamps():
    piece = make_piece("灿阳金属骑士盔")
    assert piece["level"] == 16
    assert piece["set"] == "灿阳"
    assert make_piece("灿阳金属骑士盔", 99)["level"] == 20
    assert make_piece("未知装备") is None
    assert "铜制四芒星项链" in PIECE_INFO


def _gear_plugin():
    plugin = McFish.__new__(McFish)
    plugin._save = lambda _uid, _state: None
    plugin._economy = None
    return plugin


def _event():
    return SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())


def test_craft_gear_consumes_materials_and_adds_to_bag():
    plugin = _gear_plugin()
    state = {"pack": {"铜矿石": 8, "废弃零件": 4}}
    event = _event()

    asyncio.run(plugin._craft_gear(event, state, "铜制头盔"))

    assert state["pack"]["铜矿石"] == 2
    assert state["pack"]["废弃零件"] == 0
    assert state["gear_bag"][0]["name"] == "铜制头盔"
    assert state["gear_bag"][0]["level"] == 3
    assert "已合成 铜制头盔（3级）" in event.reply.await_args.kwargs["text"]


def test_craft_gear_reports_missing_materials():
    plugin = _gear_plugin()
    state = {"pack": {"铜矿石": 2}}
    event = _event()

    asyncio.run(plugin._craft_gear(event, state, "铜制头盔"))

    assert "材料不足" in event.reply.await_args.kwargs["text"]
    assert state.get("gear_bag", []) == []


def test_equipping_two_rings_uses_both_ring_slots():
    plugin = _gear_plugin()
    state = {"pack": {}, "gear_bag": [make_piece("木制戒指"), make_piece("木制戒指")]}

    asyncio.run(plugin._equip_gear(_event(), state, "木制戒指"))
    asyncio.run(plugin._equip_gear(_event(), state, "木制戒指"))

    assert state["jewelry"]["ring1"]["name"] == "木制戒指"
    assert state["jewelry"]["ring2"]["name"] == "木制戒指"
    assert state["gear_bag"] == []


def test_equip_gear_can_consume_equipment_dropped_into_pack():
    plugin = _gear_plugin()
    state = {"pack": {"潜水头盔": 1}, "equipment": {}, "jewelry": {}, "gear_bag": []}

    asyncio.run(plugin._equip_gear(_event(), state, "潜水头盔"))

    assert state["pack"]["潜水头盔"] == 0
    assert state["equipment"]["head"]["name"] == "潜水头盔"
    assert state["equipment"]["head"]["set"] == "海钓"


def test_upgrade_gear_uses_the_next_level_cost():
    plugin = _gear_plugin()
    state = {
        "pack": {"铜矿石": 2},
        "equipment": {"head": make_piece("铜制头盔")},
    }
    event = _event()

    asyncio.run(plugin._upgrade_gear(event, state, "铜制头盔"))

    assert state["equipment"]["head"]["level"] == 4
    assert state["pack"]["铜矿石"] == 0
    assert "4级" in event.reply.await_args.kwargs["text"]


def test_unequip_gear_moves_the_piece_back_to_the_bag():
    plugin = _gear_plugin()
    state = {"pack": {}, "equipment": {"head": make_piece("铜制头盔")}, "gear_bag": []}
    event = _event()

    asyncio.run(plugin._unequip_gear(event, state, "头盔"))

    assert state["equipment"] == {}
    assert state["gear_bag"][0]["name"] == "铜制头盔"
    assert "已卸下" in event.reply.await_args.kwargs["text"]


def test_gear_text_lists_slots_sets_and_fp():
    plugin = _gear_plugin()
    state = {
        "fish_level": 10,
        "equipment": {"head": make_piece("铜制头盔"), "body": make_piece("铜制胸甲")},
        "jewelry": {},
        "gear_bag": [],
    }

    text = plugin._gear_text(state)

    assert "头盔：铜制头盔（铜制套装，3级，FP 2）" in text
    assert "套装：铜制套装 2件" in text
    assert "套装效果：咬钩速度 +" in text
    assert "装备 FP：" in text


def test_gear_command_routes_to_the_panel(tmp_path: Path):
    from PIL import Image as PILImage

    plugin = _gear_plugin()
    plugin.workspace = tmp_path
    state = {"fish_level": 10, "equipment": {}, "jewelry": {}, "gear_bag": [], "pack": {}}
    plugin._state = lambda _uid: state
    plugin._send_original_message = AsyncMock()
    event = SimpleNamespace(
        sender=SimpleNamespace(user_id="100"), raw_message="钓鱼装备", reply=AsyncMock()
    )

    asyncio.run(plugin._handle_message(event))

    message = plugin._send_original_message.await_args.args[1]
    segment = list(message)[0]
    image_path = Path(str(segment.file))
    assert image_path.name == "gear_100.png"
    with PILImage.open(image_path) as image:
        assert image.size[0] == 1440
        assert image.size[1] > 400


def test_craft_list_command_renders_recipe_card_image(tmp_path: Path):
    from PIL import Image as PILImage

    plugin = _gear_plugin()
    plugin.workspace = tmp_path
    state = {"fish_level": 10, "equipment": {}, "jewelry": {}, "gear_bag": [], "pack": {}}
    plugin._state = lambda _uid: state
    plugin._send_original_message = AsyncMock()
    event = SimpleNamespace(
        sender=SimpleNamespace(user_id="100"), raw_message="合成渔具", reply=AsyncMock()
    )

    asyncio.run(plugin._handle_message(event))

    message = plugin._send_original_message.await_args.args[1]
    image_path = Path(str(list(message)[0].file))
    assert image_path.name == "craft_100.png"
    with PILImage.open(image_path) as image:
        assert image.size[0] == 1600


def test_gear_catalog_command_renders_sets_effects_and_sources(tmp_path: Path):
    from PIL import Image as PILImage

    plugin = _gear_plugin()
    plugin.workspace = tmp_path
    plugin._state = lambda _uid: {"pack": {}, "equipment": {}, "jewelry": {}}
    plugin._send_original_message = AsyncMock()
    event = SimpleNamespace(
        sender=SimpleNamespace(user_id="100"), raw_message="装备图鉴", reply=AsyncMock()
    )

    asyncio.run(plugin._handle_message(event))

    message = plugin._send_original_message.await_args.args[1]
    image_path = Path(str(list(message)[0].file))
    assert image_path.name == "gearcatalog_slots_100.png"
    with PILImage.open(image_path) as image:
        assert image.size[0] == 1600
        # 按部位展示全部装备，高度必须能容纳 5 个部位与饰品区。
        assert image.size[1] > 3000


def test_catalog_uses_wiki_icons_for_paladin_and_oracle_sets():
    from McFish.mcfish_equipment import SETS, set_icon

    assert set_icon("圣骑士", SETS["圣骑士"]) == "圣骑士面具"
    assert set_icon("神谕", SETS["神谕"]) == "神谕卡组"
    plugin = McFish.__new__(McFish)
    for name in ("圣骑士面具", "圣骑士胸甲", "神谕卡组"):
        assert plugin._item_image(name) is not None


def test_every_equipment_piece_has_a_dedicated_icon():
    plugin = McFish.__new__(McFish)
    missing = [name for name in PIECE_INFO if plugin._item_image(name) is None]
    assert missing == []

def test_state_migrates_old_gear_names_to_wiki_equipment():
    import json
    import sqlite3

    plugin = McFish.__new__(McFish)
    plugin._db = sqlite3.connect(":memory:")
    plugin._db.execute("CREATE TABLE players (user_id TEXT PRIMARY KEY, state TEXT)")
    plugin._db.execute(
        "INSERT INTO players VALUES (?, ?)",
        (
            "100",
            json.dumps(
                {
                    "pack": {},
                    "equipment": {"head": {"name": "布制头盔", "set": "布", "slot": "head", "level": 5}},
                    "jewelry": {"necklace": {"name": "铜制项链", "set": "铜", "slot": "necklace", "level": 3}},
                    "gear_bag": [{"name": "神谕之环", "set": "神谕", "slot": "necklace", "level": 20}],
                },
                ensure_ascii=False,
            ),
        ),
    )
    plugin._db.commit()

    state = plugin._state("100")

    assert state["equipment"]["head"]["name"] == "木头头盔"
    assert state["jewelry"]["necklace"]["name"] == "铜制四芒星项链"
    assert state["gear_bag"][0]["name"] == "神谕卡组"


def test_craft_recipes_differ_by_slot():
    head = craft_recipe("铜制头盔")
    body = craft_recipe("铜制胸甲")
    legs = craft_recipe("铜制裤子")
    necklace = craft_recipe("铜制四芒星项链")

    assert head["items"] != body["items"]
    assert body["gold"] > legs["gold"] > head["gold"] > necklace["gold"]
    assert "废弃零件" in body["items"]
    assert "纤维" in legs["items"]
    assert "纤维" in necklace["items"]

def test_gear_catalog_set_overview_is_still_available(tmp_path: Path):
    from PIL import Image as PILImage

    plugin = _gear_plugin()
    plugin.workspace = tmp_path
    plugin._state = lambda _uid: {"pack": {}, "equipment": {}, "jewelry": {}}
    plugin._send_original_message = AsyncMock()
    event = SimpleNamespace(
        sender=SimpleNamespace(user_id="100"), raw_message="装备图鉴 套装", reply=AsyncMock()
    )

    asyncio.run(plugin._handle_message(event))

    message = plugin._send_original_message.await_args.args[1]
    image_path = Path(str(list(message)[0].file))
    assert image_path.name == "gearcatalog_100.png"
    with PILImage.open(image_path) as image:
        assert image.size[0] == 1600
        assert image.size[1] > 2000


def test_dismantle_materials_returns_half_of_craft_and_upgrade_investment():
    materials = dismantle_materials("铜制头盔", 5)

    recipe = craft_recipe("铜制头盔")
    totals = dict(recipe["items"])
    for target in (4, 5):
        _gold, items = upgrade_cost("铜制头盔", target)
        for item, count in items.items():
            totals[item] = totals.get(item, 0) + count
    assert materials == {
        item: max(1, count // 2) for item, count in totals.items()
    }
    assert dismantle_materials("潜水头盔") == {}


def test_dismantle_gear_command_returns_materials_and_removes_item():
    plugin = McFish.__new__(McFish)
    plugin._save = lambda *_args: None
    plugin._charge_fee = lambda *_args: True
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="confirm"))
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {
        "gear_bag": [make_piece("铜制头盔", 3)],
        "pack": {},
        "equipment": {},
        "jewelry": {},
        "talents": {},
    }

    asyncio.run(plugin._dismantle_gear(event, state, "铜制头盔"))

    assert state["gear_bag"] == []
    for item, count in dismantle_materials("铜制头盔", 3).items():
        assert state["pack"][item] == count
    assert "已分解 铜制头盔" in event.reply.await_args.kwargs["text"]
    assert "100 金币" in event.reply.await_args.kwargs["text"]
