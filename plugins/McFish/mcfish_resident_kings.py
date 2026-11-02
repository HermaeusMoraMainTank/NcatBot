"""常驻鱼王（P7）。

常驻鱼王不占用每周限时窗口：钓鱼力达到门槛后，每次抛竿都有一次独立咬钩判定，
咬钩与上钩在同一次结算里完成，不需要在聊天里回复技能。每条常驻鱼王每天最多
成功钓起 1 条，因此它更像长期藏品而不是限时爆发目标。

限时鱼王仍由 ``mcfish_rules.FISH_KINGS`` 描述，二者共用 ``king_stats`` 战绩，
但常驻鱼王不产出「鱼王鳞片」，工坊材料来源保持唯一。
"""

from __future__ import annotations

import random

RESIDENT_KING_DAILY_LIMIT = 1

# 咬钩率按“每次抛竿”计；上钩率是咬钩后的成功率。
RESIDENT_KINGS = {
    "宝石蟹": {
        "zone": "bay", "fp": 30, "bite": 0.012, "hook": 0.75,
        "length": (28, 70), "base_price": 900, "xp": 800,
        "asset_id": "mcfish_resident_jewel_crab",
        "trigger": "水面下闪过一层细碎的光，像有什么东西在沙底翻动。",
        "disappear": "沙底的光点散开，水面恢复平静。",
        "success": "成功钓起常驻鱼王【宝石蟹】！长度：{length} cm，售价：{price}。",
    },
    "白色珊瑚蝾螈": {
        "zone": "deep", "fp": 70, "bite": 0.011, "hook": 0.72,
        "length": (35, 90), "base_price": 1300, "xp": 1000,
        "asset_id": "mcfish_resident_coral_axolotl",
        "trigger": "深处的珊瑚丛轻轻晃动，白影从缝隙里探出半个身子。",
        "disappear": "白影缩回珊瑚缝隙，晃动停止。",
        "success": "成功钓起常驻鱼王【白色珊瑚蝾螈】！长度：{length} cm，售价：{price}。",
    },
    "王冠鱿鱼": {
        "zone": "ruin", "fp": 110, "bite": 0.010, "hook": 0.70,
        "length": (40, 110), "base_price": 1800, "xp": 1200,
        "asset_id": "mcfish_resident_crown_squid",
        "trigger": "遗迹的石柱之间浮起一团暗影，影子里有一圈金边在转。",
        "disappear": "暗影沉回石柱之间，金边熄灭。",
        "success": "成功钓起常驻鱼王【王冠鱿鱼】！长度：{length} cm，售价：{price}。",
    },
    "星辰水母": {
        "zone": "storm", "fp": 150, "bite": 0.0095, "hook": 0.68,
        "length": (45, 130), "base_price": 2400, "xp": 1400,
        "asset_id": "mcfish_resident_star_jellyfish",
        "trigger": "浪谷里升起一片半透明的伞盖，边缘缀着会灭的星点。",
        "disappear": "伞盖沉进浪谷，星点接连熄灭。",
        "success": "成功钓起常驻鱼王【星辰水母】！长度：{length} cm，售价：{price}。",
    },
    "星光鹦鹉螺": {
        "zone": "magma", "fp": 190, "bite": 0.009, "hook": 0.65,
        "length": (50, 150), "base_price": 3200, "xp": 1600,
        "asset_id": "mcfish_resident_starlight_nautilus",
        "trigger": "熔岩边缘的暗红水里，一枚螺旋壳缓慢上浮，壳面有光在走。",
        "disappear": "螺旋壳沉回暗红的水里，光痕消失。",
        "success": "成功钓起常驻鱼王【星光鹦鹉螺】！长度：{length} cm，售价：{price}。",
    },
    "黑色钢化海胆": {
        "zone": "frost", "fp": 215, "bite": 0.008, "hook": 0.62,
        "length": (55, 170), "base_price": 4000, "xp": 1800,
        "asset_id": "mcfish_resident_tempered_urchin",
        "trigger": "冰层下的水忽然变暗，一团带刺的黑影贴着底部移动。",
        "disappear": "黑影沉进冰层下的暗处，水面重新变亮。",
        "success": "成功钓起常驻鱼王【黑色钢化海胆】！长度：{length} cm，售价：{price}。",
    },
    "宇宙形体": {
        "zone": "glow", "fp": 240, "bite": 0.007, "hook": 0.60,
        "length": (60, 190), "base_price": 5000, "xp": 2000,
        "asset_id": "mcfish_resident_cosmic_form",
        "trigger": "洞窟的荧光向一处收拢，水里浮出一个没有固定轮廓的形体。",
        "disappear": "荧光重新散开，形体失去轮廓。",
        "success": "成功钓起常驻鱼王【宇宙形体】！长度：{length} cm，售价：{price}。",
    },
    "托尼斯鱼王": {
        "zone": "astral", "fp": 265, "bite": 0.0065, "hook": 0.58,
        "length": (70, 220), "base_price": 6200, "xp": 2400,
        "asset_id": "mcfish_resident_tonis_king",
        "trigger": "碎裂的星光聚成一条巨大的轮廓，轮廓的边缘不断剥落又重组。",
        "disappear": "轮廓散成星屑，落回深渊。",
        "success": "成功钓起常驻鱼王【托尼斯鱼王】！长度：{length} cm，售价：{price}。",
    },
    "裂缝灯鱼": {
        "zone": "chrono", "fp": 275, "bite": 0.006, "hook": 0.55,
        "length": (80, 260), "base_price": 8000, "xp": 3000,
        "asset_id": "mcfish_resident_rift_lanternfish",
        "trigger": "洄流的水面裂开一道细缝，缝里游出一盏不灭的灯。",
        "disappear": "细缝合拢，灯光被水带走。",
        "success": "成功钓起常驻鱼王【裂缝灯鱼】！长度：{length} cm，售价：{price}。",
    },
}

RESIDENT_KING_NAMES = tuple(RESIDENT_KINGS)
RESIDENT_KING_ZONES = {item["zone"]: name for name, item in RESIDENT_KINGS.items()}
RESIDENT_KING_XP = {name: int(item["xp"]) for name, item in RESIDENT_KINGS.items()}

# 鱼饵只提高常驻鱼王的咬钩概率，不再提供“必定钓上”的机制。
BAIT_RESIDENT_KING_MULTIPLIER = {
    "隐秘诱饵": 2.0,
    "万能鱼饵": 1.5,
}


def resident_king_for_zone(zone: str) -> str | None:
    return RESIDENT_KING_ZONES.get(str(zone))


def resident_king_fp(name: str) -> int:
    return int(RESIDENT_KINGS.get(name, {}).get("fp", 0))


def bait_multiplier(consumables: dict | None) -> float:
    if not consumables:
        return 1.0
    multiplier = 1.0
    for name, value in BAIT_RESIDENT_KING_MULTIPLIER.items():
        if int(consumables.get(name, 0)) > 0:
            multiplier = max(multiplier, float(value))
    return multiplier


def bite_chance(
    name: str,
    *,
    gear_bonus: float = 0.0,
    clue_multiplier: float | None = None,
    bait_bonus: float = 1.0,
) -> float:
    """Per-cast bite probability for a resident king."""
    entry = RESIDENT_KINGS.get(name)
    if not entry:
        return 0.0
    value = float(entry["bite"]) * max(1.0, float(clue_multiplier or 1.0))
    value *= max(1.0, float(bait_bonus))
    value *= 1.0 + max(0.0, float(gear_bonus))
    return min(1.0, value)


def hook_chance(name: str, *, tough_points: int = 0) -> float:
    entry = RESIDENT_KINGS.get(name)
    if not entry:
        return 0.0
    value = float(entry["hook"]) + max(0, int(tough_points)) / 100
    return min(0.95, value)


def price_for(name: str, length: int, *, gold_magnet: float = 1.0) -> int:
    entry = RESIDENT_KINGS.get(name)
    if not entry:
        return 0
    low, high = entry["length"]
    base = int(entry["base_price"]) * int(length) / ((low + high) / 2)
    return int(base * max(1.0, float(gold_magnet)))


def roll_length(name: str, rng=None) -> int:
    low, high = RESIDENT_KINGS.get(name, {}).get("length", (1, 1))
    roller = rng or random
    return int(roller.randint(int(low), int(high)))
