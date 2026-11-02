"""Shared effect labels for McFish panels.

One place decides how an effect key is written, so the gear panel, the text
fallback and the talent tree never show ambiguous shorthand like「稀有 +5」。
"""

from __future__ import annotations

# key -> 面板上的完整名称
EFFECT_LABELS = {
    "wait": "咬钩速度",
    "air": "空竿率",
    "material": "材料产出概率",
    "valuable": "贵重品概率",
    "rarity": "稀有鱼权重",
    "double_fish": "双倍鱼概率",
    "hidden": "隐藏鱼触发率",
    "box": "钥匙与箱子概率",
    "king_bite": "鱼王咬钩率",
    "bait_save": "鱼饵不消耗概率",
    "food": "食物回复次数",
    "legendary": "隐藏鱼与常驻鱼王概率",
    "fragment": "碎片概率",
    "daily": "每日钓鱼次数",
    "fish_price": "鱼售价",
    "fees": "修复与合成手续费",
    "fp": "钓鱼力",
    "rod_power": "鱼竿力加成",
}

# 数值单位不是百分比的项
FLAT_UNITS = {"fragment": "点", "daily": "次", "fp": "点"}

# 数值越大表示“降低”的项，显示负号
DOWNWARD = {"air", "fees"}


def effect_label(key: str) -> str:
    return EFFECT_LABELS.get(key, key)


def format_effect(key: str, value: float) -> str:
    label = effect_label(key)
    unit = FLAT_UNITS.get(key)
    if unit:
        return f"{label} +{value:g}{unit}"
    sign = "-" if key in DOWNWARD else "+"
    return f"{label} {sign}{abs(float(value)) * 100:g}%"
