"""Shared effect labels (P7): panels must spell effects out, not abbreviate them."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "plugins"))

from McFish import mcfish_talents as T
from McFish.McFish import McFish
from McFish.mcfish_effects import format_effect


def test_percent_effects_spell_out_their_meaning():
    assert format_effect("rarity", 0.05) == "稀有鱼权重 +5%"
    assert format_effect("valuable", 0.08) == "贵重品概率 +8%"
    assert format_effect("material", 0.06) == "材料产出概率 +6%"
    assert format_effect("hidden", 0.10) == "隐藏鱼触发率 +10%"
    assert format_effect("king_bite", 0.05) == "鱼王咬钩率 +5%"
    assert format_effect("legendary", 0.10) == "隐藏鱼与常驻鱼王概率 +10%"


def test_downward_effects_use_a_minus_sign():
    assert format_effect("air", 0.03) == "空竿率 -3%"
    assert format_effect("fees", 0.06) == "修复与合成手续费 -6%"


def test_flat_effects_show_their_unit():
    assert format_effect("fragment", 2) == "碎片概率 +2点"
    assert format_effect("daily", 5) == "每日钓鱼次数 +5次"
    assert format_effect("fp", 4) == "钓鱼力 +4点"


def test_talent_tree_has_no_abbreviated_labels():
    state = {"fish_level": 60, "talents": {"levels": {"advantage": 2, "steady": 1, "handy": 1}}}
    text = "\n".join(T.tree_lines(state))
    for vague in ("稀有 +", "贵重 +", "材料 +", "隐藏鱼 +", "碎片 +", "手续费 +"):
        assert vague not in text
    assert "稀有鱼权重 +5%" in text
    assert "空竿率 -3%" in text


def test_gear_text_uses_the_same_explicit_labels():
    plugin = McFish.__new__(McFish)
    state = {"fish_level": 20, "equipment": {}, "jewelry": {}, "gear_bag": []}
    # 用 4 件灿阳直接验证 legendary 的写法，而不是手写一份标签表。
    from McFish.mcfish_equipment import make_piece

    state["equipment"] = {
        "head": {**make_piece("灿阳金属骑士盔"), "level": 20},
        "body": {**make_piece("灿阳金属胸甲"), "level": 20},
        "legs": {**make_piece("灿阳金属腿甲"), "level": 20},
    }
    state["jewelry"] = {
        "necklace": {**make_piece("上古宝石项链"), "level": 20},
    }
    text = plugin._gear_text(state)
    assert "稀有鱼权重" in text or "贵重品概率" in text
    assert "legendary" not in text
    assert "rarity" not in text
