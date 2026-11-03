"""Equipment sets for McFish.

Every set starts at its own level and can be upgraded to 20 with the same
material ladder.  Same level means the same per-piece FP; sets differ only by
their specialisation and the piece count needed to trigger it.
"""

from __future__ import annotations

MAX_LEVEL = 20

SLOT_LABELS = {
    "head": "头盔",
    "body": "胸甲",
    "legs": "腿甲",
    "necklace": "项链",
    "ring1": "戒指1",
    "ring2": "戒指2",
}
GEAR_SLOTS = ("head", "body", "legs")
JEWELRY_SLOTS = ("necklace", "ring1", "ring2")

# 套装定义。slots 使用 "ring" 表示戒指（可装两枚）。
SETS = {
    "布": {
        "start": 1, "material": "纤维", "craft_gold": 2000,
        "craft_items": {"纤维": 10, "木头": 5},
        "slots": {"head": "木头头盔", "body": "木头胸甲", "legs": "木头裤子",
                  "ring": "木制戒指"},
        "spec": "续航",
        "effects": {2: {"bait_save": 0.05}, 4: {"food": 0.10}},
        "craftable": True,
    },
    "铜": {
        "start": 3, "material": "铜矿石", "craft_gold": 6000,
        "craft_items": {"铜矿石": 8, "废弃零件": 4},
        "slots": {"head": "铜制头盔", "body": "铜制胸甲", "legs": "铜制裤子",
                  "necklace": "铜制四芒星项链"},
        "spec": "手感",
        "effects": {2: {"wait": 0.05}, 4: {"air": 0.03}},
        "craftable": True,
    },
    "锡": {
        "start": 5, "material": "锡矿石", "craft_gold": 12000,
        "craft_items": {"锡矿石": 10, "海绵": 6},
        "slots": {"head": "青铜头盔", "body": "青铜胸甲", "legs": "青铜裤子",
                  "ring": "石头戒指"},
        "spec": "采集",
        "effects": {2: {"material": 0.08}, 4: {"valuable": 0.04}},
        "craftable": True,
    },
    "铁": {
        "start": 7, "material": "铁矿石", "craft_gold": 22000,
        "craft_items": {"铁矿石": 12, "废弃零件": 8},
        "slots": {"head": "铁制头盔", "body": "铁制胸甲", "legs": "铁制裤子",
                  "necklace": "大块铁项链"},
        "spec": "稳定",
        "effects": {2: {"air": 0.04}, 4: {"rarity": 0.04}},
        "craftable": True,
    },
    "猩红": {
        "start": 10, "material": "猩红金属矿石", "craft_gold": 32000,
        "craft_items": {"猩红金属矿石": 15, "金矿石": 8},
        "slots": {"head": "猩红金属头盔", "body": "猩红金属胸甲", "legs": "猩红金属裤子",
                  "necklace": "大猩红金属块项链"},
        "spec": "稀有",
        "effects": {2: {"rarity": 0.06}, 4: {"double_fish": 0.06}},
        "craftable": True,
    },
    "幻光": {
        "start": 12, "material": "幻光金属矿石", "craft_gold": 42000,
        "craft_items": {"幻光金属矿石": 18, "金矿石": 10},
        "slots": {"head": "幻光金属头盔", "body": "幻光金属胸甲", "legs": "幻光金属裤子",
                  "necklace": "幻光金属项链", "ring": "幻光金属戒指"},
        "spec": "碎片",
        "effects": {3: {"fragment": 2}, 5: {"hidden": 0.08}},
        "craftable": True,
    },
    "星河": {
        "start": 14, "material": "星河金属矿石", "craft_gold": 52000,
        "craft_items": {"星河金属矿石": 20, "星尘": 3},
        "slots": {"head": "星河金属头盔", "body": "星河金属躯干", "legs": "星河金属裤子",
                  "necklace": "游牧民项链"},
        "spec": "探索",
        "effects": {2: {"box": 0.10}, 4: {"king_bite": 0.05}},
        "craftable": True,
    },
    "灿阳": {
        "start": 16, "material": "灿阳金属矿石", "craft_gold": 65000,
        "craft_items": {"灿阳金属矿石": 20, "熔接合金": 5, "星尘": 3},
        "slots": {"head": "灿阳金属骑士盔", "body": "灿阳金属胸甲", "legs": "灿阳金属腿甲",
                  "necklace": "上古宝石项链"},
        "spec": "珍宝",
        "effects": {2: {"valuable": 0.10}, 4: {"legendary": 0.10}},
        "craftable": True,
    },
    "海钓": {
        "start": 13, "material": "冰晶", "craft_gold": 0, "craft_items": {},
        "slots": {"head": "潜水头盔", "body": "海藻披风", "legs": "脚蹼",
                  "necklace": "尼普顿项链", "ring": "金鱼戒指"},
        "spec": "鱼王",
        "effects": {2: {"king_bite": 0.03}, 5: {"double_fish": 0.08}},
        "craftable": False,
    },
    "雇佣兵": {
        "start": 10, "material": "废弃零件", "craft_gold": 0, "craft_items": {},
        "slots": {"head": "雇佣兵头带", "body": "雇佣兵背心", "legs": "雇佣兵裤子"},
        "spec": "材料",
        "effects": {2: {"material": 0.06}, 3: {"fragment": 2}},
        "craftable": False,
    },
    "忍者": {
        "start": 17, "material": "幻光金属矿石", "craft_gold": 0, "craft_items": {},
        "slots": {"head": "忍者头巾", "body": "忍者服饰", "legs": "忍者护腿"},
        "spec": "手感",
        "effects": {2: {"wait": 0.06}, 3: {"air": 0.05}},
        "craftable": False,
    },
    "圣骑士": {
        "start": 13, "material": "铁矿石", "craft_gold": 0, "craft_items": {},
        "slots": {"head": "圣骑士面具", "body": "圣骑士胸甲", "legs": "圣骑士裤子"},
        "spec": "碎片",
        "effects": {1: {"fragment": 3}, 3: {"fragment": 6}},
        "craftable": False,
    },
    "神谕": {
        "start": 20, "material": "鱼王鳞片", "craft_gold": 0, "craft_items": {},
        "slots": {"necklace": "神谕卡组"},
        "spec": "珍宝",
        "effects": {1: {"rarity": 0.08, "daily": 5}},
        "craftable": False,
    },
}

SET_LABELS = {
    "布": "木头套装",
    "铜": "铜制套装",
    "锡": "青铜套装",
    "铁": "铁制套装",
    "猩红": "猩红金属套装",
    "幻光": "幻光金属套装",
    "星河": "星河金属套装",
    "灿阳": "灿阳金属套装",
    "雇佣兵": "雇佣兵套装",
    "海钓": "海钓套装",
    "圣骑士": "圣骑士套装",
    "忍者": "忍者套装",
    "神谕": "神谕套装",
}


SET_ICONS = {
    "圣骑士": "圣骑士面具",
    "神谕": "神谕卡组",
}


# 旧装备名迁移到 wiki 同名装备；旧存档读取时统一改写。
GEAR_ALIASES = {
    "布制头盔": "木头头盔",
    "布制胸甲": "木头胸甲",
    "布制护腿": "木头裤子",
    "布制项链": "木制戒指",
    "布制戒指": "木制戒指",
    "铜制项链": "铜制四芒星项链",
    "铜制戒指": "铜制四芒星项链",
    "锡制头盔": "青铜头盔",
    "锡制胸甲": "青铜胸甲",
    "锡制护腿": "青铜裤子",
    "锡制项链": "石头戒指",
    "锡制戒指": "石头戒指",
    "铁制项链": "大块铁项链",
    "铁制戒指": "大块铁项链",
    "猩红金属项链": "大猩红金属块项链",
    "猩红金属戒指": "大猩红金属块项链",
    "星河金属项链": "游牧民项链",
    "星河金属戒指": "游牧民项链",
    "灿阳金属项链": "上古宝石项链",
    "灿阳金属戒指": "上古宝石项链",
    "神谕之环": "神谕卡组",
}


def set_icon(name: str, data: dict) -> str:
    if name in SET_ICONS:
        return SET_ICONS[name]
    for piece in (data.get("slots") or {}).values():
        if piece:
            return piece
    return f"{name}头盔"


def set_label(name: str) -> str:
    return SET_LABELS.get(name, f"{name}套装")


# 装备名 -> (套装, 部位)；部位里的 "ring" 可放入 ring1/ring2。
PIECE_INFO: dict[str, dict] = {}
for _set_name, _set in SETS.items():
    for _slot, _piece in _set["slots"].items():
        PIECE_INFO[_piece] = {"set": _set_name, "slot": _slot, "start": _set["start"]}


def fp_for_level(level: int) -> int:
    """Single-piece fishing power: level 1 → 1, level 20 → 16."""
    return max(0, round(max(0, int(level)) * 0.8))


def piece_info(name: str) -> dict | None:
    return PIECE_INFO.get(name)


def set_of(name: str) -> dict | None:
    info = PIECE_INFO.get(name)
    return SETS.get(info["set"]) if info else None


# 单件合成系数与额外材料：头/胸/腿/项链/戒指分别使用不同的成本结构。
SLOT_CRAFT = {
    "head": (0.8, {"废弃零件": 1}),
    "body": (1.0, {"废弃零件": 2}),
    "legs": (0.9, {"纤维": 2}),
    "necklace": (0.6, {"纤维": 3}),
    "ring": (0.5, {"纤维": 2}),
}


def craft_recipe(name: str) -> dict | None:
    info = PIECE_INFO.get(name)
    if not info:
        return None
    set_def = SETS[info["set"]]
    if not set_def.get("craftable"):
        return None
    factor, extra = SLOT_CRAFT.get(info["slot"], (1.0, {}))
    items = {
        item: max(1, round(int(count) * factor))
        for item, count in set_def.get("craft_items", {}).items()
    }
    for item, count in extra.items():
        items[item] = items.get(item, 0) + int(count)
    gold = max(1, int(set_def.get("craft_gold", 0) * factor))
    return {
        "level": int(info["start"]),
        "gold": gold,
        "items": items,
    }


# 升级消耗：(目标等级下界, 金币, 本位材料数量, 额外材料)
_UPGRADE_BANDS = (
    (2, 450, 2, {}),
    (5, 750, 2, {"铜矿石": 5}),
    (10, 1300, 3, {"铁矿石": 8}),
    (15, 2200, 4, {"猩红金属矿石": 10, "熔接合金": 2}),
    (18, 3200, 5, {"星河金属矿石": 8}),
    (20, 5400, 6, {"灿阳金属矿石": 12, "星尘": 3}),
)


def upgrade_cost(name: str, target_level: int) -> tuple[int, dict[str, int]] | None:
    """Return (gold, materials) for upgrading to ``target_level``."""
    info = PIECE_INFO.get(name)
    if not info or not 2 <= int(target_level) <= MAX_LEVEL:
        return None
    set_def = SETS[info["set"]]
    band = _UPGRADE_BANDS[0]
    for candidate in _UPGRADE_BANDS:
        if int(target_level) >= candidate[0]:
            band = candidate
    _lower, gold, base_count, extra = band
    items = {str(set_def["material"]): base_count}
    for item, count in extra.items():
        items[item] = items.get(item, 0) + count
    return int(gold), items


def make_piece(name: str, level: int | None = None) -> dict | None:
    info = PIECE_INFO.get(name)
    if not info:
        return None
    value = int(info["start"] if level is None else level)
    value = max(1, min(MAX_LEVEL, value))
    return {"name": name, "set": info["set"], "slot": info["slot"], "level": value}


def worn_sets(equipment: dict, jewelry: dict) -> dict[str, dict]:
    """Group worn pieces by set: count and average level."""
    groups: dict[str, dict] = {}
    for slots in (equipment or {}, jewelry or {}):
        for item in slots.values():
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", ""))
            info = PIECE_INFO.get(name)
            if not info:
                continue
            group = groups.setdefault(info["set"], {"count": 0, "levels": []})
            group["count"] += 1
            group["levels"].append(int(item.get("level", info["start"])))
    result = {}
    for set_name, group in groups.items():
        levels = group["levels"]
        result[set_name] = {
            "count": group["count"],
            "avg_level": sum(levels) / len(levels) if levels else 0,
        }
    return result


def scaled_effects(equipment: dict, jewelry: dict) -> dict[str, float]:
    """Aggregate set bonuses, scaled by average worn level / 20."""
    effects: dict[str, float] = {}
    for set_name, worn in worn_sets(equipment, jewelry).items():
        set_def = SETS.get(set_name)
        if not set_def:
            continue
        scale = max(0.0, min(1.0, worn["avg_level"] / MAX_LEVEL))
        for needed, bonus in set_def["effects"].items():
            if worn["count"] < int(needed):
                continue
            for key, value in bonus.items():
                effects[key] = effects.get(key, 0.0) + float(value) * scale
    return effects
