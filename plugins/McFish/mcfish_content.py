"""Non-fish content for McFish: materials, food, valuables, boxes and cards.

Data lives here so the drop pipeline, the shop and the tests share one source
of truth.  Prices are sell prices; box/keys/cards are intentionally not
sellable and therefore not listed in ``ITEM_PRICES``.
"""

from __future__ import annotations

import random

# waste 大类的内部拆分（百分比，顺序：垃圾 / 材料 / 食物 / 贵重品）。
WASTE_SPLIT = {
    "bay": (74, 18, 6, 2),
    "deep": (72, 19, 6, 3),
    "ruin": (70, 20, 6, 4),
    "storm": (68, 20, 7, 5),
    "magma": (65, 21, 8, 6),
    "frost": (64, 21, 8, 7),
    "glow": (62, 22, 8, 8),
    "astral": (60, 22, 8, 10),
    "chrono": (58, 22, 8, 12),
}

# (材料, 最少, 最多)
ZONE_MATERIALS = {
    "bay": (("纤维", 1, 3), ("木头", 1, 3), ("铜矿石", 1, 2)),
    "deep": (("海绵", 1, 2), ("铜矿石", 1, 2), ("废弃零件", 1, 2), ("锡矿石", 1, 2)),
    "ruin": (("铜矿石", 1, 2), ("铁矿石", 1, 3), ("废弃零件", 1, 3)),
    "storm": (("金矿石", 1, 2), ("废弃零件", 1, 3), ("猩红金属矿石", 1, 2)),
    "magma": (("熔接合金", 1, 2), ("金矿石", 1, 3), ("猩红金属矿石", 1, 3)),
    "frost": (("海绵", 1, 3), ("冰晶", 1, 2), ("幻光金属矿石", 1, 2)),
    "glow": (("幻光金属矿石", 1, 3), ("星河金属矿石", 1, 2), ("冰晶", 1, 2)),
    "astral": (("星河金属矿石", 1, 3), ("星尘", 1, 2), ("灿阳金属矿石", 1, 2)),
    "chrono": (("时砂", 1, 2), ("星尘", 1, 2), ("上古遗物碎片", 1, 1)),
}

ZONE_FOODS = {
    "bay": ("绿色海藻", "黄色海藻"),
    "deep": ("黄色海藻", "灰色海藻"),
    "ruin": ("灰色海藻", "红色海藻"),
    "storm": ("红色海藻", "苍白海藻"),
    "magma": ("深蓝色海藻",),
    "frost": ("蓝色海藻", "水晶海藻"),
    "glow": ("水晶海藻", "蓝色海藻"),
    "astral": ("深蓝色海藻",),
    "chrono": ("水晶海藻",),
}

# 卖品（贵重品）。首饰在 P5 之前也先按卖品登记，避免掉落链断掉。
ZONE_VALUABLES = {
    "bay": ("鱼卵琥珀", "金色海星", "铜制四芒星项链", "洞穴孔雀鱼项链"),
    "deep": ("巨型鱿鱼眼球", "光滑贝壳", "金色水母", "新月项链", "气泡珍珠项链"),
    "ruin": ("水胆水晶", "毒蛇石", "穴居者香水", "金鱼戒指", "菊石项链"),
    "storm": ("羽毛鱼的鳞片", "穴居者奖章", "遗失的船桨", "海沫戒指", "骨制项链"),
    "magma": ("上古宝石", "星光碎片", "核心虹膜", "大熔接金属块项链", "上古宝石项链"),
    "frost": ("霉菌贝壳", "巨型细菌", "仪式长笛", "数据石板", "海螺壳项链", "海洋之心项链", "脚蹼"),
    "glow": (
        "催化宝石", "水晶骨头", "三叠纪化石", "晶莹木种子", "太阳稻米种子",
        "月亮玉米种子", "幻光金属项链", "玻璃珠项链", "尼普顿项链",
    ),
    "astral": ("破损的葫芦", "破损的玩具船", "上古鱼钩", "贵族戒指", "双环戒指", "傀儡戒指"),
    "chrono": ("硫磺芽", "潘多拉矿石", "遗失的船桨", "游牧民项链", "灵魂勋章"),
}

ITEM_PRICES = {
    # 材料（只用于合成，售价压得很低）
    "纤维": 2, "木头": 2, "废弃零件": 3, "铜矿石": 4, "锡矿石": 5, "铁矿石": 8,
    "海绵": 6, "金矿石": 12, "猩红金属矿石": 18, "幻光金属矿石": 26,
    "星河金属矿石": 36, "灿阳金属矿石": 50, "熔接合金": 80, "冰晶": 60,
    "星尘": 120, "时砂": 200, "上古遗物碎片": 300,
    # 食物
    "绿色海藻": 30, "黄色海藻": 45, "灰色海藻": 60, "红色海藻": 75,
    "苍白海藻": 90, "蓝色海藻": 120, "水晶海藻": 180, "深蓝色海藻": 220,
    # 贵重品
    "鱼卵琥珀": 200, "金色海星": 600,
    "巨型鱿鱼眼球": 700, "光滑贝壳": 900, "金色水母": 1200,
    "水胆水晶": 1500, "毒蛇石": 1800, "穴居者香水": 2200,
    "羽毛鱼的鳞片": 1600, "穴居者奖章": 2000, "遗失的船桨": 2600,
    "上古宝石": 3500, "星光碎片": 5000, "核心虹膜": 6000,
    "霉菌贝壳": 2600, "巨型细菌": 3000, "仪式长笛": 3800, "数据石板": 4200,
    "催化宝石": 3000, "水晶骨头": 3500, "三叠纪化石": 4000,
    "晶莹木种子": 4500, "太阳稻米种子": 5000, "月亮玉米种子": 5500,
    "破损的葫芦": 1200, "破损的玩具船": 1800, "上古鱼钩": 5200,
    "硫磺芽": 6000, "潘多拉矿石": 8000,
    # 首饰（P5 之前按卖品处理）
    "铜制四芒星项链": 300, "洞穴孔雀鱼项链": 400, "新月项链": 900,
    "气泡珍珠项链": 1100, "金鱼戒指": 1400, "菊石项链": 1300,
    "海沫戒指": 2200, "骨制项链": 1800, "大熔接金属块项链": 3600,
    "上古宝石项链": 4200, "海螺壳项链": 3400, "海洋之心项链": 4500,
    "幻光金属项链": 3800, "玻璃珠项链": 2600, "贵族戒指": 4000,
    "双环戒指": 4400, "傀儡戒指": 4800, "游牧民项链": 5200, "灵魂勋章": 5600,
    # P5 之前的装备掉落先按贵重品登记，避免箱子产出未知物品
    "潜水头盔": 6000, "海藻披风": 5000, "脚蹼": 5500, "尼普顿项链": 6500,
    "雇佣兵头带": 3000, "忍者头巾": 7000,
}

FOOD_RESTORE = {
    "绿色海藻": 1, "黄色海藻": 1, "灰色海藻": 2, "红色海藻": 2,
    "苍白海藻": 2, "蓝色海藻": 3, "水晶海藻": 3, "深蓝色海藻": 3,
}

CATEGORY_TYPES = {
    "material": "material",
    "food": "food",
    "valuable": "valuable",
}

MATERIAL_NAMES = frozenset(
    name for table in ZONE_MATERIALS.values() for name, _low, _high in table
)

# 每个鱼点对应的钥匙/箱子档位。
ZONE_BOX_TIER = {
    "bay": "铜",
    "deep": "铁",
    "ruin": "铁",
    "storm": "猩红金属",
    "magma": "猩红金属",
    "frost": "幻光金属",
    "glow": "幻光金属",
    "astral": "星河金属",
    "chrono": "灿阳金属",
}

KEY_NAMES = {tier: f"{tier}钥匙" for tier in ("铜", "铁", "猩红金属", "幻光金属", "星河金属", "灿阳金属")}
BOX_NAMES = {tier: f"上锁的{tier}制箱子" for tier in ("铜", "铁")}
BOX_NAMES.update({tier: f"上锁的{tier}箱子" for tier in ("猩红金属", "幻光金属", "星河金属", "灿阳金属")})

# 单抽掉落表：(物品, 权重, 最少, 最多)
BOX_LOOT = {
    "上锁的铜制箱子": (
        ("鱼卵琥珀", 30, 1, 1), ("铜制四芒星项链", 20, 1, 1), ("铜矿石", 20, 3, 5),
        ("纤维", 15, 5, 8), ("绿色海藻", 10, 1, 2), ("克己", 2, 1, 1), ("铜钥匙", 3, 1, 1),
    ),
    "上锁的铁制箱子": (
        ("光滑贝壳", 25, 1, 1), ("新月项链", 15, 1, 1), ("铁矿石", 20, 3, 5),
        ("海绵", 15, 2, 4), ("灰色海藻", 10, 1, 2), ("金鱼戒指", 5, 1, 1),
        ("铁钥匙", 7, 1, 1), ("灵光", 3, 1, 1),
    ),
    "上锁的猩红金属箱子": (
        ("穴居者奖章", 22, 1, 1), ("海沫戒指", 15, 1, 1), ("猩红金属矿石", 20, 3, 5),
        ("金矿石", 15, 2, 3), ("红色海藻", 10, 1, 2), ("雇佣兵头带", 6, 1, 1),
        ("猩红金属钥匙", 7, 1, 1), ("实体", 5, 1, 1),
    ),
    "上锁的幻光金属箱子": (
        ("水胆水晶", 20, 1, 1), ("海螺壳项链", 15, 1, 1), ("幻光金属矿石", 20, 3, 5),
        ("冰晶", 15, 2, 3), ("水晶海藻", 8, 1, 2), ("潜水头盔", 7, 1, 1),
        ("幻光金属钥匙", 10, 1, 1), ("坚忍", 5, 1, 1),
    ),
    "上锁的星河金属箱子": (
        ("上古宝石", 18, 1, 1), ("大熔接金属块项链", 15, 1, 1), ("星河金属矿石", 20, 3, 5),
        ("星尘", 15, 2, 3), ("忍者头巾", 10, 1, 1), ("星河金属钥匙", 12, 1, 1),
        ("大都会", 8, 1, 1), ("上古鱼钩", 2, 1, 1),
    ),
    "上锁的灿阳金属箱子": (
        ("星光碎片", 18, 1, 1), ("游牧民项链", 15, 1, 1), ("灿阳金属矿石", 20, 3, 5),
        ("时砂", 15, 2, 3), ("海藻披风", 10, 1, 1), ("幻光金属钥匙", 2, 1, 1),
        ("灿阳金属钥匙", 10, 1, 1), ("智慧", 10, 1, 1),
    ),
}

# 卡牌：鱼点 -> (卡名, 单竿掉率, 保底竿数)
# 卡名全部取自 wiki 的「神谕卡片」，图标与名称保持一致。
CARDS = {
    "bay": ("克己", 0.0030, 250),
    "deep": ("灵光", 0.0030, 250),
    "ruin": ("实体", 0.0025, 300),
    "storm": ("辉煌", 0.0025, 300),
    "magma": ("光芒", 0.0020, 350),
    "frost": ("坚忍", 0.0020, 350),
    "glow": ("启迪", 0.0015, 400),
    "astral": ("大都会", 0.0015, 400),
    "chrono": ("智慧", 0.0050, 200),
}

# 旧存档里的卡名迁移到 wiki 卡名，保留已收集进度。
CARD_ALIASES = {
    "潮信": "灵光",
    "残响": "实体",
    "风眼": "辉煌",
    "熔心": "光芒",
    "永冻": "坚忍",
    "微光": "启迪",
    "星轨": "大都会",
    "轮回": "智慧",
}

CARD_NAMES = tuple(item[0] for item in CARDS.values())


def waste_split(zone: str) -> tuple[int, int, int, int]:
    return WASTE_SPLIT.get(zone, WASTE_SPLIT["bay"])


def adjusted_waste_split(zone: str, material_bonus: float = 0.0, valuable_bonus: float = 0.0) -> tuple[int, int, int, int]:
    """Apply gear bonuses to the material/valuable shares, taking points from junk."""
    junk, material, food, valuable = waste_split(zone)
    material = min(100, round(material * (1.0 + max(0.0, material_bonus))))
    valuable = min(100, round(valuable * (1.0 + max(0.0, valuable_bonus))))
    junk = max(0, 100 - material - food - valuable)
    return junk, material, food, valuable


def roll_waste_sub(
    zone: str,
    roll: int,
    *,
    material_bonus: float = 0.0,
    valuable_bonus: float = 0.0,
) -> str:
    """Map a 0–99 roll inside the waste category to a sub category."""
    junk, material, food, valuable = adjusted_waste_split(zone, material_bonus, valuable_bonus)
    if roll < junk:
        return "junk"
    if roll < junk + material:
        return "material"
    if roll < junk + material + food:
        return "food"
    return "valuable"


def roll_material(zone: str, rng: random.Random | None = None) -> tuple[str, int]:
    roller = rng or random
    table = ZONE_MATERIALS.get(zone) or ZONE_MATERIALS["bay"]
    name, low, high = roller.choice(table)
    return name, roller.randint(low, high)


def roll_food(zone: str, rng: random.Random | None = None) -> tuple[str, int]:
    roller = rng or random
    names = ZONE_FOODS.get(zone) or ZONE_FOODS["bay"]
    return roller.choice(names), 1


def roll_valuable(zone: str, rng: random.Random | None = None) -> tuple[str, int]:
    roller = rng or random
    names = ZONE_VALUABLES.get(zone) or ZONE_VALUABLES["bay"]
    return roller.choice(names), 1


def roll_key_or_box(zone: str, rng: random.Random | None = None) -> tuple[str, int]:
    """10% 的贵重品位置会换成钥匙或箱子（各一半）。"""
    roller = rng or random
    tier = ZONE_BOX_TIER.get(zone, "铜")
    name = KEY_NAMES[tier] if roller.random() < 0.5 else BOX_NAMES[tier]
    return name, 1


def roll_box_loot(box_name: str, rng: random.Random | None = None) -> tuple[str, int]:
    roller = rng or random
    table = BOX_LOOT.get(box_name)
    if not table:
        return "垃圾", 1
    total = sum(entry[1] for entry in table)
    roll = roller.uniform(0, total)
    cursor = 0.0
    for name, weight, low, high in table:
        cursor += weight
        if roll <= cursor:
            return name, roller.randint(low, high)
    name, _weight, low, high = table[-1]
    return name, roller.randint(low, high)


def card_for_zone(zone: str) -> tuple[str, float, int] | None:
    return CARDS.get(zone)


def box_key_name(box_name: str) -> str | None:
    for tier, name in BOX_NAMES.items():
        if name == box_name:
            return KEY_NAMES[tier]
    return None


def roll_card(zone: str, misses: int = 0, rng: random.Random | None = None) -> str | None:
    """Pity-aware card roll; returns the card name or None."""
    entry = CARDS.get(zone)
    if not entry:
        return None
    name, rate, pity = entry
    roller = rng or random
    if misses + 1 >= pity or roller.random() < rate:
        return name
    return None
