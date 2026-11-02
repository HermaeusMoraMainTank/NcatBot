"""Pure gameplay rules for McFish.

The plugin keeps persistence and message delivery outside this module so the
time, strategy and pity rules can be tested without a running bot.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from .mcfish_resident_kings import RESIDENT_KING_XP

STRATEGY_BY_NUMBER = {
    "1": "direct_reel",
    "2": "small_to_large",
    "3": "humble_bait",
    "4": "ambitious_bait",
}

BITE_SKILLS = {
    "1": "hold_target",       # 保留当前目标，进入上钩判定
    "2": "mooch_post",         # 牺牲前置鱼王，尝试后置鱼王
    "3": "give_up",            # 放弃当前机会
    "4": "give_up",            # 没有后置鱼王时的保守收线
}

HOOK_SKILLS = {
    "1": "precision_hookset",
    "2": "powerful_hookset",
    "3": "patient_control",
    "4": "burst_reel",
}

# 上钩技能用倍率而不是固定百分点：基础率越高，倍率带来的绝对提升越大，
# 这样四个技能在任意鱼竿档位的期望收益都接近，区别只剩体型分布与路径推进。
HOOK_SKILL_RATE_MULTIPLIER = {
    "precision_hookset": 1.00,
    "powerful_hookset": 1.10,
    "patient_control": 1.24,
    "burst_reel": 1.45,
}

# Lower bounds inside each king's configured length range. Higher-risk hooksets
# produce larger fish, so they remain useful when the path is already complete.
HOOK_SKILL_SIZE_FLOORS = {
    "precision_hookset": 0.55,
    "powerful_hookset": 0.40,
    "patient_control": 0.20,
    "burst_reel": 0.00,
}
EQUIPMENT = {
    "财富浮标": {"kind": "permanent", "fragments": 120, "uses": None, "effects": {"treasure_rate": 1.35, "king_clue_rate": 1.20}},
    "坚韧钓组": {"kind": "permanent", "fragments": 160, "uses": None, "effects": {"king_hook_success_points": 12, "minimum_success": 5}},
    "黄金磁石": {"kind": "permanent", "fragments": 220, "uses": None, "effects": {"treasure_quality": 1, "treasure_price": 1.25, "fish_price": 1.08, "king_price": 10.0}},
    "魔法鱼饵": {"kind": "consumable", "fragments": 24, "uses": 10, "effects": {"wait_rate": 0.85, "rare_weight": 1.25}},
    "幸运海星": {"kind": "permanent", "fragments": 300, "uses": None, "effects": {"fragment_points": 8}},
    "隐秘诱饵": {"kind": "consumable", "fragments": 42, "uses": 6, "effects": {"hidden_clue_rate": 1.50}},
    "万能鱼饵": {"kind": "consumable", "fragments": 50, "uses": 5, "effects": {"ordinary_limit_bypass": 1}},
}
ACCESSORY_MAX_LEVEL = 3
ACCESSORY_SLOT_UNLOCKS = {
    3: {"level": 10, "fragments": 80},
    4: {"level": 20, "fragments": 180},
}
ACCESSORY_UPGRADE_COSTS = {
    "财富浮标": (60, 120),
    "坚韧钓组": (80, 160),
    "黄金磁石": (110, 220),
    "幸运海星": (150, 300),
}
PERMANENT_ACCESSORY_EFFECTS = {
    "财富浮标": {
        1: {"treasure_rate": 1.35, "king_clue_rate": 1.20},
        2: {"treasure_rate": 1.50, "king_clue_rate": 1.28},
        3: {"treasure_rate": 1.65, "king_clue_rate": 1.36},
    },
    "坚韧钓组": {
        1: {"king_hook_success_points": 12, "minimum_success": 5},
        2: {"king_hook_success_points": 16, "minimum_success": 5},
        3: {"king_hook_success_points": 20, "minimum_success": 5},
    },
    "黄金磁石": {
        1: {"treasure_quality": 1, "treasure_price": 1.25, "fish_price": 1.08, "king_price": 10.0},
        2: {"treasure_quality": 2, "treasure_price": 1.35, "fish_price": 1.12, "king_price": 30.0},
        3: {"treasure_quality": 3, "treasure_price": 1.50, "fish_price": 1.16, "king_price": 100.0},
    },
    "幸运海星": {
        1: {"fragment_points": 8},
        2: {"fragment_points": 12},
        3: {"fragment_points": 16},
    },
}
# Gold fees are deliberately separate from fragment costs: fragments gate access,
# while the fee provides a meaningful sink for established players.
EQUIPMENT_CRAFT_FEES = {
    "财富浮标": 30000,
    "坚韧钓组": 50000,
    "黄金磁石": 80000,
    "幸运海星": 120000,
    "魔法鱼饵": 100,
    "隐秘诱饵": 160,
    "万能鱼饵": 220,
}

FISH_KINGS = {
    "月影巨鲤": {"chain": "河湾链", "zone": "bay", "kind": "prerequisite", "path": ("4", "4", "4"), "requires": None, "length": (45, 110), "base_price": 60, "asset_id": "mcfish_moon_carp_king"},
    "河湾巨鲤": {"chain": "河湾链", "zone": "bay", "kind": "final", "path": ("3", "3"), "requires": "月影巨鲤", "length": (80, 180), "base_price": 140, "asset_id": "mcfish_river_carp_king"},
    "深海黑鳍": {"chain": "深海链", "zone": "deep", "kind": "prerequisite", "path": ("4", "4", "4"), "requires": None, "length": (60, 150), "base_price": 90, "asset_id": "mcfish_deep_blackfin_king"},
    "深海灯笼鱼": {"chain": "深海链", "zone": "deep", "kind": "final", "path": ("3", "3", "3"), "requires": "深海黑鳍", "length": (100, 260), "base_price": 230, "asset_id": "mcfish_deep_lanternfish_king"},
    "遗迹铜鳞": {"chain": "遗迹链", "zone": "ruin", "kind": "prerequisite", "path": ("3", "3"), "requires": None, "length": (70, 190), "base_price": 120, "asset_id": "mcfish_ruin_copper_scale_king"},
    "遗迹金鳞鱼": {"chain": "遗迹链", "zone": "ruin", "kind": "final", "path": ("4", "4", "4"), "requires": "遗迹铜鳞", "length": (120, 320), "base_price": 330, "asset_id": "mcfish_ruin_gold_scale_king"},
    "风暴幼龙": {"chain": "风暴链", "zone": "storm", "kind": "prerequisite", "path": ("4", "4"), "requires": None, "length": (90, 220), "base_price": 170, "asset_id": "mcfish_storm_whelp_king"},
    "风暴海龙": {"chain": "风暴链", "zone": "storm", "kind": "final", "path": ("4", "4", "4", "4"), "requires": "风暴幼龙", "length": (180, 500), "base_price": 540, "asset_id": "mcfish_storm_seadragon_king"},
}

FISH_KING_PROMPTS = {
    "月影巨鲤": {
        "trigger": "水面下的月光忽明忽暗……",
        "disappear": "水面下的月光沉回黑暗……",
        "success": "钓起了水底月影的主人！成功钓起鱼王【月影巨鲤】！长度：{length} cm，售价：{price}。",
    },
    "河湾巨鲤": {
        "trigger": "河面传来沉重的拍击声，波纹层层散开……",
        "disappear": "沉重的拍击声戛然而止……",
        "success": "钓起了河湾的古老霸主！成功钓起鱼王【河湾巨鲤】！长度：{length} cm，售价：{price}。",
    },
    "深海黑鳍": {
        "trigger": "深处亮起一点幽蓝的光……",
        "disappear": "幽蓝的光熄灭了……",
        "success": "钓起了深渊里的漆黑闪光！成功钓起鱼王【深海黑鳍】！长度：{length} cm，售价：{price}。",
    },
    "深海灯笼鱼": {
        "trigger": "黑暗中有庞大的影子缓缓游过，光点随之摇晃……",
        "disappear": "庞大的影子隐回黑暗，光点不再摇晃……",
        "success": "钓起了提灯游进深渊的主人！成功钓起鱼王【深海灯笼鱼】！长度：{length} cm，售价：{price}。",
    },
    "遗迹铜鳞": {
        "trigger": "水中闪过一缕暗红色的光……",
        "disappear": "暗红色的光沉入水中……",
        "success": "钓起了遗迹里的铜色闪光！成功钓起鱼王【遗迹铜鳞】！长度：{length} cm，售价：{price}。",
    },
    "遗迹金鳞鱼": {
        "trigger": "水底传来金属般的回响，碎金般的鳞光浮起……",
        "disappear": "金属回响远去，碎金般的鳞光熄灭……",
        "success": "钓起了遗迹深处最耀眼的金光！成功钓起鱼王【遗迹金鳞鱼】！长度：{length} cm，售价：{price}。",
    },
    "风暴幼龙": {
        "trigger": "远处水面开始剧烈翻涌，风里带着细小电鸣……",
        "disappear": "翻涌的水面恢复平静，电鸣散去……",
        "success": "钓起了风暴的小小主宰！成功钓起鱼王【风暴幼龙】！长度：{length} cm，售价：{price}。",
    },
    "风暴海龙": {
        "trigger": "雷光映出巨大的轮廓，海面被压出深谷般的浪痕……",
        "disappear": "雷光散去，巨大的轮廓沉入深海……",
        "success": "钓起了驾驭风暴的巨影！成功钓起鱼王【风暴海龙】！长度：{length} cm，售价：{price}。",
    },
}

HIDDEN_FISH = {
    # 隐藏鱼也使用 wiki 鱼名；它们与普通池里的同名鱼共享图标和售价。
    "灰色沙丘曳尾鱼": {"zone": "deep", "price": 800, "asset_id": "mcfish_gray_dune_tail"},
    "魂灵面纱": {"zone": "ruin", "price": 1000, "asset_id": "mcfish_soul_veil"},
    "岩石凶鲨": {"zone": "storm", "price": 1500, "asset_id": "mcfish_rock_shark"},
}

CHAIN_RULES = {
    "河湾链": {"days": (0, 1), "overlap": 9, "first_length": (27, 41), "final_length": (23, 37)},
    "深海链": {"days": (1, 2, 3), "overlap": 11, "first_length": (31, 47), "final_length": (25, 43)},
    "遗迹链": {"days": (3, 4), "overlap": 9, "first_length": (23, 37), "final_length": (29, 45)},
    "风暴链": {"days": (5, 6), "overlap": 13, "first_length": (29, 43), "final_length": (23, 35)},
}

ROD_UNLOCK_LEVELS = {
    "木竿": 0,
    "铜竿": 3,
    "锡竿": 8,
    "铁竿": 15,
    "猩红竿": 25,
    "幻光竿": 35,
    "星河竿": 48,
    "灿阳竿": 55,
    "三叉戟": 35,
}

ROD_LEVELS = tuple(ROD_UNLOCK_LEVELS)
POLE_FRAGMENT_COSTS = {
    "木竿": 12,
    "铜竿": 24,
    "锡竿": 56,
    "铁竿": 120,
    "猩红竿": 240,
    "幻光竿": 360,
    "星河竿": 500,
    "灿阳竿": 680,
}
POLE_DISMANTLE_FRAGMENTS = {
    "木竿": 1,
    "铜竿": 2,
    "锡竿": 4,
    "铁竿": 8,
    "猩红竿": 16,
    "幻光竿": 24,
    "星河竿": 32,
    "灿阳竿": 42,
    "三叉戟": 48,
}
MIN_FISH_WAIT_SECONDS = 30
BASE_FISH_WAIT_SECONDS = 180
FISHING_ZONES = ("bay", "deep", "ruin", "storm", "magma", "frost", "glow", "astral", "chrono")
FISHING_ZONE_LABELS = {
    "bay": "河湾",
    "deep": "深海",
    "ruin": "遗迹水域",
    "storm": "风暴海域",
    "magma": "熔岩深渊",
    "frost": "极地冰海",
    "glow": "荧光洞窟",
    "astral": "星陨深渊",
    "chrono": "时之洄流",
}
FISHING_ZONE_ALIASES = {
    "河湾": "bay", "bay": "bay",
    "深海": "deep", "deep": "deep",
    "遗迹": "ruin", "遗迹水域": "ruin", "ruin": "ruin",
    "风暴": "storm", "风暴海域": "storm", "storm": "storm",
    "熔岩": "magma", "熔岩深渊": "magma", "magma": "magma",
    "极地": "frost", "极地冰海": "frost", "frost": "frost",
    "荧光": "glow", "荧光洞窟": "glow", "glow": "glow",
    "星陨": "astral", "星陨深渊": "astral", "astral": "astral",
    "洄流": "chrono", "时之洄流": "chrono", "chrono": "chrono",
}
CHAIN_ZONES = {"河湾链": "bay", "深海链": "deep", "遗迹链": "ruin", "风暴链": "storm"}

# 鱼类名称与图标全部取自 Core Keeper WIKI（鱼类页）；每个鱼点 4 条普通鱼 + 1 条常驻鱼王。
FISH_ZONE_WEIGHTS = {
    # 河湾 = wiki 泥土生态区 + 黏土洞窟（黏土没有常驻鱼王，合并进河湾）
    "bay": (
        "岩石凶鲨", "吸血鳗", "恶魔蠕虫",
        "橙色洞穴孔雀鱼", "蓝色洞穴孔雀鱼", "绿色水泡头", "黄色水泡头",
    ),
    # 深海 = 沉没之海
    "deep": ("追踪鱼", "银色飞镖", "金色飞镖", "粉色珊瑚蝾螈"),
    # 遗迹水域 = 被遗忘的废墟
    "ruin": ("刃鳍鱼", "粉色的宫殿鱼", "蓝绿色的宫殿鱼"),
    # 风暴海域 = 埃齐欧斯荒野
    "storm": ("蔚蓝色羽毛鱼", "翠绿色羽毛鱼", "魂灵面纱"),
    # 熔岩深渊 = 熔融采石场
    "magma": ("暗色噬岩鱼", "亮色噬岩鱼", "碧绿龙鱼", "古老龙鱼"),
    # 极地冰海 = 霉菌生态区（普通鱼只有 2 条，常驻鱼王是黑色钢化海胆）
    "frost": ("霉菌鲨鱼", "腐烂的鱼"),
    # 荧光洞窟 = 荧光边境
    "glow": ("莹润深潜鱼", "灿烂深潜鱼", "蓝晶棱角鱼", "金晶棱角鱼"),
    # 星陨深渊 = 起源荒漠
    "astral": ("硬皮尖刺鱼", "砂皮尖刺鱼", "灰色沙丘曳尾鱼", "褐色沙丘曳尾鱼"),
    # 时之洄流 = 通道
    "chrono": ("陆地三叶虫", "石三叶虫", "绿角皮科鱼", "粉角皮科鱼"),
}

# P2/P3 用等级作为鱼点门槛；P7 起鱼点改用钓鱼力门槛，本表保留给旧存档与旧测试对照。
FISHING_ZONE_LEVELS = {
    "bay": 1,
    "deep": 10,
    "ruin": 22,
    "storm": 32,
    "magma": 42,
    "frost": 55,
    "glow": 68,
    "astral": 80,
    "chrono": 92,
}

# P7：鱼点门槛按钓鱼力（FP），等级只通过 FP 间接影响能否进入。
FISHING_ZONE_POWER = {
    "bay": 0,
    "deep": 20,
    "ruin": 50,
    "storm": 80,
    "magma": 110,
    "frost": 135,
    "glow": 160,
    "astral": 190,
    "chrono": 220,
}

# P7 经济压缩：出售税按鱼点递增，高段鱼点的单竿收益不再按旧版 20% 固定税。
FISHING_ZONE_SELL_TAX = {
    "bay": 0.20,
    "deep": 0.22,
    "ruin": 0.25,
    "storm": 0.28,
    "magma": 0.32,
    "frost": 0.34,
    "glow": 0.37,
    "astral": 0.40,
    "chrono": 0.45,
}

# 单鱼点每日次数上限：全局上限仍然生效，鱼点上限用于压高段产出。
FISHING_ZONE_DAILY_CAPS = {
    "bay": 60,
    "deep": 55,
    "ruin": 50,
    "storm": 45,
    "magma": 40,
    "frost": 40,
    "glow": 35,
    "astral": 35,
    "chrono": 30,
}

# 常驻与前置鱼王不吃黄金磁石的 10/30/100 倍率，避免可反复刷取的鱼王成为金矿；
# 后置鱼王仍保留 10/30/100。
KING_MAGNET_CAP = {10.0: 3.0, 30.0: 5.0, 100.0: 8.0}


def capped_king_sale_price(base: int, *, gold_magnet: bool | float = False) -> int:
    raw = 10.0 if gold_magnet is True else float(gold_magnet or 1.0)
    if raw <= 1.0:
        multiplier = 1.0
    else:
        multiplier = KING_MAGNET_CAP.get(raw, min(raw, 8.0))
    return int(base * multiplier)


# 同一鱼点内按稀有度递增的钓鱼力偏移，顺序与 FISH_ZONE_WEIGHTS 的鱼一致。
# 河湾合并了泥土 + 黏土，共 7 条；其余鱼点 2–4 条，取前 N 档。
FISH_POWER_OFFSETS = (0, 0, 20, 45, 60, 75, 90)
# 超出 min_fp 越多，该鱼种权重越高；上限按稀有度分档（普通/稀有/史诗/传说）。
FISH_WEIGHT_CAPS = (1.4, 1.7, 1.9, 2.2)
FISH_WEIGHT_POWER_SCALE = 150

FISHING_XP = {
    # 河湾（泥土 + 黏土）
    "岩石凶鲨": 5, "吸血鳗": 8, "恶魔蠕虫": 12,
    "橙色洞穴孔雀鱼": 18, "蓝色洞穴孔雀鱼": 18, "绿色水泡头": 22, "黄色水泡头": 24,
    # 深海（沉没之海）
    "追踪鱼": 26, "银色飞镖": 32, "金色飞镖": 38, "粉色珊瑚蝾螈": 40,
    # 遗迹水域（被遗忘的废墟）
    "刃鳍鱼": 40, "粉色的宫殿鱼": 45, "蓝绿色的宫殿鱼": 50,
    # 风暴海域（埃齐欧斯荒野）
    "蔚蓝色羽毛鱼": 55, "翠绿色羽毛鱼": 56, "魂灵面纱": 60,
    # 熔岩深渊（熔融采石场）
    "暗色噬岩鱼": 60, "亮色噬岩鱼": 62, "碧绿龙鱼": 68, "古老龙鱼": 75,
    # 极地冰海（霉菌生态区）
    "霉菌鲨鱼": 80, "腐烂的鱼": 80,
    # 荧光洞窟（荧光边境）
    "莹润深潜鱼": 90, "灿烂深潜鱼": 100, "蓝晶棱角鱼": 100, "金晶棱角鱼": 120,
    # 星陨深渊（起源荒漠）
    "硬皮尖刺鱼": 120, "砂皮尖刺鱼": 120, "灰色沙丘曳尾鱼": 120, "褐色沙丘曳尾鱼": 140,
    # 时之洄流（通道）
    "陆地三叶虫": 140, "石三叶虫": 160, "绿角皮科鱼": 170, "粉角皮科鱼": 200,
}

# 按价格档位给每把鱼竿一组权重；所有鱼种都走同一张表，避免手调表与新鱼脱节。
DEFAULT_FISH_WEIGHT = 1
# 档位：1 <200，2 200–399，3 400–699，4 700–999，5 ≥1000。
_FISH_PRICE_TIERS = {
    "岩石凶鲨": 1, "吸血鳗": 1, "恶魔蠕虫": 1,
    "橙色洞穴孔雀鱼": 1, "蓝色洞穴孔雀鱼": 2, "绿色水泡头": 2, "黄色水泡头": 2,
    "追踪鱼": 2, "银色飞镖": 2, "金色飞镖": 3, "粉色珊瑚蝾螈": 3,
    "刃鳍鱼": 3, "粉色的宫殿鱼": 3, "蓝绿色的宫殿鱼": 3,
    "蔚蓝色羽毛鱼": 3, "翠绿色羽毛鱼": 3, "魂灵面纱": 3,
    "暗色噬岩鱼": 3, "亮色噬岩鱼": 3, "碧绿龙鱼": 3, "古老龙鱼": 4,
    "霉菌鲨鱼": 4, "腐烂的鱼": 4,
    "莹润深潜鱼": 4, "灿烂深潜鱼": 5, "蓝晶棱角鱼": 5, "金晶棱角鱼": 5,
    "硬皮尖刺鱼": 5, "砂皮尖刺鱼": 5, "灰色沙丘曳尾鱼": 5, "褐色沙丘曳尾鱼": 5,
    "陆地三叶虫": 5, "石三叶虫": 5, "绿角皮科鱼": 5, "粉角皮科鱼": 5,
}
_ROD_TIER_WEIGHTS = {
    "木竿": (12, 3, 1, 0, 0),
    "铜竿": (10, 5, 2, 1, 0),
    "锡竿": (8, 6, 3, 1, 0),
    "铁竿": (7, 6, 4, 2, 1),
    "猩红竿": (6, 6, 5, 3, 1),
    "幻光竿": (5, 6, 6, 4, 2),
    "星河竿": (5, 5, 6, 5, 3),
    "灿阳竿": (4, 5, 6, 6, 4),
    "三叉戟": (4, 5, 6, 6, 5),
}
FISH_ROD_WEIGHTS: dict[str, dict[str, int]] = {
    rod: {} for rod in _ROD_TIER_WEIGHTS
}
for _fish_name, _tier in _FISH_PRICE_TIERS.items():
    for _rod_name, _tier_row in _ROD_TIER_WEIGHTS.items():
        FISH_ROD_WEIGHTS[_rod_name][_fish_name] = _tier_row[_tier - 1]

# Values are (bite rate, hook success rate), indexed by rod level (9 tiers).
FISH_KING_PROBABILITIES = {
    "月影巨鲤": ((.015, .20), (.025, .25), (.035, .35), (.05, .45), (.065, .55), (.08, .60), (.09, .62), (.10, .64), (.11, .66)),
    "河湾巨鲤": ((.003, .15), (.005, .20), (.01, .30), (.015, .40), (.02, .50), (.025, .58), (.028, .60), (.03, .62), (.033, .64)),
    "深海黑鳍": ((.015, .18), (.025, .23), (.04, .33), (.06, .43), (.08, .53), (.10, .60), (.11, .62), (.12, .64), (.13, .66)),
    "深海灯笼鱼": ((.003, .15), (.005, .20), (.01, .30), (.015, .40), (.02, .50), (.025, .58), (.028, .60), (.03, .62), (.033, .64)),
    "遗迹铜鳞": ((.015, .15), (.025, .20), (.04, .30), (.06, .40), (.08, .50), (.10, .58), (.11, .60), (.12, .62), (.13, .64)),
    "遗迹金鳞鱼": ((.003, .10), (.005, .15), (.01, .25), (.015, .35), (.02, .45), (.025, .55), (.028, .57), (.03, .59), (.033, .61)),
    "风暴幼龙": ((.015, .12), (.025, .18), (.04, .28), (.06, .38), (.08, .48), (.10, .58), (.11, .60), (.12, .62), (.13, .64)),
    "风暴海龙": ((.003, .05), (.005, .10), (.01, .18), (.015, .28), (.02, .40), (.025, .50), (.028, .53), (.03, .56), (.033, .59)),
}

FISH_KING_UNLOCKED_PROBABILITIES = {
    "河湾巨鲤": ((.03, .40), (.04, .45), (.055, .50), (.07, .55), (.09, .60), (.11, .65), (.12, .67), (.135, .69), (.15, .71)),
    "深海灯笼鱼": ((.03, .40), (.04, .45), (.055, .50), (.07, .55), (.09, .60), (.11, .65), (.12, .67), (.135, .69), (.15, .71)),
    "遗迹金鳞鱼": ((.03, .35), (.04, .40), (.055, .45), (.07, .50), (.09, .55), (.11, .60), (.12, .62), (.135, .64), (.15, .66)),
    "风暴海龙": ((.03, .30), (.04, .35), (.055, .40), (.07, .45), (.09, .52), (.11, .58), (.12, .61), (.135, .64), (.15, .67)),
}

FISH_KING_XP = {
    "月影巨鲤": 300,
    "河湾巨鲤": 500,
    "深海黑鳍": 350,
    "深海灯笼鱼": 650,
    "遗迹铜鳞": 400,
    "遗迹金鳞鱼": 750,
    "风暴幼龙": 450,
    "风暴海龙": 900,
}
# 常驻鱼王的经验与限时鱼王共用一张查询表（fishing_xp 只查这一处）。
FISH_KING_XP.update(RESIDENT_KING_XP)

# (base seconds, random extra seconds). The result's profile controls the
# next hidden timestamp, so common low-value fish resolve faster than rare fish.
FISH_WAIT_PROFILES = {
    # 河湾（泥土 + 黏土）
    "岩石凶鲨": (30, 20), "吸血鳗": (35, 20), "恶魔蠕虫": (45, 25),
    "橙色洞穴孔雀鱼": (55, 30), "蓝色洞穴孔雀鱼": (65, 35),
    "绿色水泡头": (75, 40), "黄色水泡头": (85, 45),
    # 深海（沉没之海）
    "追踪鱼": (60, 30), "银色飞镖": (80, 40), "金色飞镖": (100, 50), "粉色珊瑚蝾螈": (120, 60),
    # 遗迹水域（被遗忘的废墟）
    "刃鳍鱼": (80, 40), "粉色的宫殿鱼": (100, 50), "蓝绿色的宫殿鱼": (120, 60),
    # 风暴海域（埃齐欧斯荒野）
    "蔚蓝色羽毛鱼": (100, 50), "翠绿色羽毛鱼": (120, 60), "魂灵面纱": (140, 70),
    # 熔岩深渊（熔融采石场）
    "暗色噬岩鱼": (120, 60), "亮色噬岩鱼": (140, 70), "碧绿龙鱼": (160, 80), "古老龙鱼": (180, 90),
    # 极地冰海（霉菌生态区）
    "霉菌鲨鱼": (150, 75), "腐烂的鱼": (180, 90),
    # 荧光洞窟（荧光边境）
    "莹润深潜鱼": (160, 80), "灿烂深潜鱼": (180, 90),
    "蓝晶棱角鱼": (200, 100), "金晶棱角鱼": (220, 110),
    # 星陨深渊（起源荒漠）
    "硬皮尖刺鱼": (180, 90), "砂皮尖刺鱼": (200, 100),
    "灰色沙丘曳尾鱼": (220, 110), "褐色沙丘曳尾鱼": (240, 120),
    # 时之洄流（通道）
    "陆地三叶虫": (200, 100), "石三叶虫": (220, 110),
    "绿角皮科鱼": (240, 120), "粉角皮科鱼": (260, 130),
    **{name: (300, 300) for name in FISH_KINGS},
}


MAX_FISHING_LEVEL = 100


def level_for_xp(xp: int) -> int:
    """Return the fishing level from cumulative XP using an increasing curve."""
    xp = max(0, int(xp))
    level = 1
    while level < MAX_FISHING_LEVEL and xp >= xp_required_for_level(level + 1):
        level += 1
    return level


def xp_required_for_level(level: int) -> int:
    """Return cumulative XP required to reach a level (level 1 starts at 0).

    P7 曲线 B：1–35 级沿用旧曲线（老存档不跳级），36–60 级每级 1200+40×(L-36)，
    61–100 级每级 2600+60×(L-61)。100 级累计 224,250 经验。
    """
    level = max(1, int(level))
    if level <= 35:
        steps = level - 1
        return 100 * steps + 25 * steps * (steps - 1)
    total = 100 * 34 + 25 * 34 * 33
    if level > 35:
        count = min(level, 60) - 35
        total += count * 1200 + 40 * (count * (count - 1) // 2)
    if level > 60:
        count = min(level, MAX_FISHING_LEVEL) - 60
        total += count * 2600 + 60 * (count * (count - 1) // 2)
    return total


def daily_attempt_limit(level: int) -> int:
    level = max(0, int(level))
    if level < 5:
        return 50
    if level < 10:
        return 60
    if level < 20:
        return 70
    if level < 30:
        return 80
    return 100


def fishing_wait_seconds(induce: int = 0, wait_rate: float = 1.0) -> int:
    """Return the generic fallback hidden wait, with a 30-second floor."""
    return max(MIN_FISH_WAIT_SECONDS, int(BASE_FISH_WAIT_SECONDS * float(wait_rate) / (max(0, int(induce)) + 1)))


def fishing_wait_for_result(
    name: str,
    *,
    induce: int = 0,
    wait_rate: float = 1.0,
    rng: random.Random | None = None,
) -> int:
    """Return a fluctuating hidden wait based on the resolved fish tier."""
    base, fluctuation = FISH_WAIT_PROFILES.get(name, (90, 45))
    roller = rng or random
    value = base + roller.randint(0, fluctuation)
    return max(MIN_FISH_WAIT_SECONDS, int(value * float(wait_rate) / (max(0, int(induce)) + 1)))


def parse_count_token(value: str) -> int | None:
    """Parse Arabic or common Chinese integer tokens used by fishing commands."""
    value = value.strip()
    if not value:
        return 1
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if all(char in digits or char in "十百千" for char in value):
        total = 0
        section = 0
        number = 0
        units = {"十": 10, "百": 100, "千": 1000}
        for char in value:
            if char in digits:
                number = digits[char]
            else:
                section += (number or 1) * units[char]
                number = 0
        return section + number
    return None


def rod_unlock_level(rod_name: str) -> int:
    return ROD_UNLOCK_LEVELS.get(rod_name, 0)


def rod_level_index(rod_name: str) -> int:
    return max(0, min(len(ROD_LEVELS) - 1, ROD_LEVELS.index(rod_name))) if rod_name in ROD_LEVELS else 0


def rod_drop_allowed(current_rod: str, dropped_rod: str) -> bool:
    """Allow the equipped rod to catch itself, lower rods, or the next tier."""
    if current_rod not in ROD_LEVELS or dropped_rod not in ROD_LEVELS:
        return True
    return rod_level_index(dropped_rod) <= rod_level_index(current_rod) + 1


def fish_king_probabilities(king_name: str, rod_name: str, *, prerequisite_caught: bool = False) -> tuple[float, float]:
    king = FISH_KINGS.get(king_name)
    if king and king["kind"] == "final" and not prerequisite_caught:
        return 0.0, 0.0
    table = FISH_KING_UNLOCKED_PROBABILITIES.get(king_name) if prerequisite_caught else None
    table = table or FISH_KING_PROBABILITIES.get(king_name)
    if not table:
        return 0.0, 0.0
    return table[rod_level_index(rod_name)]


def fishing_xp(name: str) -> int:
    """Return XP for a successfully caught fish; misses and unknown items give 0."""
    return FISH_KING_XP.get(name, FISHING_XP.get(name, 0))


def daily_fishing_zone(day: datetime | None = None) -> str:
    value = (day or datetime.now()).date().toordinal()
    return FISHING_ZONES[value % len(FISHING_ZONES)]


def fishing_zone_label(zone: str) -> str:
    return FISHING_ZONE_LABELS.get(zone, zone)


def resolve_fishing_zone(text: str) -> str | None:
    """Map a user-typed spot name to its internal id; unknown names return None."""
    return FISHING_ZONE_ALIASES.get((text or "").strip())


def fishing_zone_unlock_level(zone: str) -> int:
    return int(FISHING_ZONE_LEVELS.get(zone, 1))


def fishing_zone_required_power(zone: str) -> int:
    """P7 的鱼点门槛：进入鱼点需要的钓鱼力。"""
    return int(FISHING_ZONE_POWER.get(zone, 0))


def fishing_zone_unlocked(zone: str, power: int) -> bool:
    return int(power) >= fishing_zone_required_power(zone)


def default_fishing_zone_for_power(power: int) -> str:
    """Return the highest spot the current fishing power can enter."""
    unlocked = [zone for zone in FISHING_ZONES if fishing_zone_unlocked(zone, power)]
    return unlocked[-1] if unlocked else FISHING_ZONES[0]


def default_fishing_zone(level: int) -> str:
    """Legacy level-based lookup kept for old saves and display."""
    unlocked = [zone for zone in FISHING_ZONES if fishing_zone_unlock_level(zone) <= int(level)]
    return unlocked[-1] if unlocked else FISHING_ZONES[0]


def fish_min_power(zone: str, name: str) -> int:
    """稀有条目在鱼点门槛之上再加一档钓鱼力要求。"""
    names = FISH_ZONE_WEIGHTS.get(zone, ())
    index = names.index(name) if name in names else 0
    offset = FISH_POWER_OFFSETS[min(index, len(FISH_POWER_OFFSETS) - 1)]
    return fishing_zone_required_power(zone) + offset


def format_duration(seconds: float) -> str:
    """Format a non-negative countdown without exposing fractional seconds."""
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}秒"
    hours, remainder = divmod(total, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes}分" if minutes else f"{hours}小时"
    return f"{minutes}分"


def fish_zone_weights(zone: str, rod_name: str, power: int | None = None) -> dict[str, float]:
    names = FISH_ZONE_WEIGHTS.get(zone, FISH_ZONE_WEIGHTS["bay"])
    weights = FISH_ROD_WEIGHTS.get(rod_name, FISH_ROD_WEIGHTS["木竿"])
    result: dict[str, float] = {name: float(weights.get(name, DEFAULT_FISH_WEIGHT)) for name in names}
    if power is not None:
        # 钓鱼力不足的稀有条目不进入池子；至少保留该鱼点最基础的一条鱼。
        allowed = [name for name in names if fish_min_power(zone, name) <= int(power)]
        if allowed:
            result = {name: result[name] for name in allowed}
        # 超出 min_fp 越多，稀有鱼权重越高，上限按稀有度分档。
        scaled: dict[str, float] = {}
        for index, name in enumerate(names):
            if name not in result:
                continue
            cap = FISH_WEIGHT_CAPS[min(index, len(FISH_WEIGHT_CAPS) - 1)]
            bonus = max(0.0, (int(power) - fish_min_power(zone, name)) / FISH_WEIGHT_POWER_SCALE)
            scaled[name] = round(result[name] * min(cap, 1.0 + bonus), 4)
        result = scaled
    # 高段鱼点里的鱼在低级鱼竿上权重可能全是 0；补一个保底权重，
    # 避免抽取和概率面板出现空池/除零。
    if not any(result.values()):
        return {name: float(DEFAULT_FISH_WEIGHT) for name in result}
    return result


@dataclass(frozen=True)
class FishKingWindow:
    chain: str
    king_name: str
    starts_at: datetime
    ends_at: datetime

    def contains(self, value: datetime) -> bool:
        return self.starts_at <= value < self.ends_at


def strategy_from_reply(text: str) -> str | None:
    """Accept only one of the four numeric replies."""
    return STRATEGY_BY_NUMBER.get(text.strip())


def bite_skill_from_reply(text: str) -> str | None:
    return BITE_SKILLS.get(text.strip())


def hook_skill_from_reply(text: str) -> str | None:
    return HOOK_SKILLS.get(text.strip())


def hook_skill_rate_multiplier(skill: str) -> float:
    return HOOK_SKILL_RATE_MULTIPLIER.get(skill, 1.0)


def king_length_for_hook(king_name: str, hook_skill: str, *, rng: random.Random | None = None) -> int:
    """Return a king length whose lower bound depends on the hookset skill."""
    king = FISH_KINGS.get(king_name)
    if not king:
        return 0
    lower, upper = king["length"]
    floor = HOOK_SKILL_SIZE_FLOORS.get(hook_skill, 0.0)
    minimum = int(lower + (upper - lower) * floor)
    return (rng or random).randint(minimum, upper)


def generate_week_windows(week_start: datetime, seed: int | None = None) -> list[FishKingWindow]:
    """Generate irregular, overlapping windows for one Monday-based week.

    鱼点改为玩家自选后，窗口不再需要落在“该鱼点的轮换日”上；玩家可以在窗口内
    主动切到对应鱼点。九个鱼点也超过了七天，强行对齐会让部分鱼王整周没有窗口。
    """
    rng = random.Random(seed)
    result: list[FishKingWindow] = []
    for chain, rule in CHAIN_RULES.items():
        day = rng.choice(rule["days"])
        first_start = rng.randint(9 * 60 + 7, 19 * 60 + 23) // 7 * 7
        first_minutes = rng.randint(*rule["first_length"])
        final_minutes = rng.randint(*rule["final_length"])
        earliest_final = first_start + max(8, rule["overlap"] - final_minutes)
        latest_final = first_start + first_minutes - rule["overlap"]
        final_start = rng.randint(earliest_final, latest_final)
        first_at = week_start + timedelta(days=day, minutes=first_start)
        final_at = week_start + timedelta(days=day, minutes=final_start)
        first_name = next(name for name, item in FISH_KINGS.items() if item["chain"] == chain and item["kind"] == "prerequisite")
        final_name = next(name for name, item in FISH_KINGS.items() if item["chain"] == chain and item["kind"] == "final")
        result.extend((FishKingWindow(chain, first_name, first_at, first_at + timedelta(minutes=first_minutes)), FishKingWindow(chain, final_name, final_at, final_at + timedelta(minutes=final_minutes))))
    return result


def active_window(windows: Iterable[FishKingWindow], at: datetime) -> FishKingWindow | None:
    return next((window for window in windows if window.contains(at)), None)


def scheduled_attempts(
    start: datetime,
    waits: Iterable[timedelta],
    *,
    include_start: bool = True,
) -> list[datetime]:
    """Return one ordered virtual timestamp per rod."""
    values = []
    current = start
    for wait in waits:
        if not include_start:
            current += wait
        values.append(current)
        if include_start:
            current += wait
    return values


def fragment_roll(misses: int, *, rng: random.Random | None = None, rate: float = 0.08) -> tuple[int, int]:
    """Return ``(quantity, new_misses)``; the thirteenth miss is guaranteed."""
    roller = rng or random
    if misses >= 12 or roller.random() < rate:
        return roller.randint(1, 2), 0
    return 0, misses + 1


def fragment_jackpot(*, rng: random.Random | None = None, rate: float = 0.001) -> bool:
    """A 0.1% independent jackpot that awards 50 fragments."""
    return (rng or random).random() < rate


def path_requirements(king_name: str) -> dict[str, int]:
    required: dict[str, int] = {}
    for number in FISH_KINGS.get(king_name, {}).get("path", ()):
        required[number] = required.get(number, 0) + 1
    return required


def path_counts(state: dict, king_name: str) -> dict[str, int]:
    paths = state.setdefault("king_paths", {})
    progress = paths.setdefault(king_name, {"counts": {}})
    counts = progress.get("counts")
    if not isinstance(counts, dict):
        counts = progress["counts"] = {}
    progress.pop("guaranteed", None)
    return counts


def path_is_satisfied(state: dict, king_name: str) -> bool:
    """Whether the weekly path is complete, which enables the guaranteed hookset."""
    required = path_requirements(king_name)
    if not required:
        return False
    counts = path_counts(state, king_name)
    return all(int(counts.get(number, 0)) >= need for number, need in required.items())


def advance_path(state: dict, king_name: str, strategy: str) -> dict[str, int]:
    """Record one successful hookset choice; a failed hook never advances the path."""
    number = next((number for number, value in STRATEGY_BY_NUMBER.items() if value == strategy), None)
    counts = path_counts(state, king_name)
    if number:
        counts[number] = int(counts.get(number, 0)) + 1
    return counts


def consume_path(state: dict, king_name: str) -> None:
    """A guaranteed hookset spends the path, so the player must rebuild it."""
    path_counts(state, king_name).clear()


def king_success_rate(
    base: float,
    *,
    guaranteed: bool = False,
    tough_tackle: bool | int = False,
    rate_multiplier: float = 1.0,
) -> float:
    value = 0.95 if guaranteed else float(base) * max(0.0, float(rate_multiplier))
    if tough_tackle:
        value += 0.12 if isinstance(tough_tackle, bool) else int(tough_tackle) / 100
    return min(0.95, max(0.05, value))


def king_bite_rate(base: float, *, wealth_bobber: bool | float = False, hidden_lure: bool = False, hidden_target: bool = False) -> float:
    """Apply clue modifiers without changing windows or prerequisite checks."""
    wealth_multiplier = 1.20 if wealth_bobber is True else float(wealth_bobber or 1.0)
    value = base * wealth_multiplier
    if hidden_lure and hidden_target:
        value *= 1.50
    return min(1.0, value)


def treasure_sale_price(base: int, *, gold_magnet: bool | float = False) -> int:
    """黄金磁石 raises treasure value by exactly 25 percent."""
    multiplier = 1.25 if gold_magnet is True else float(gold_magnet or 1.0)
    return int(base * multiplier)


def fish_sale_price(base: int, *, gold_magnet: bool | float = False) -> int:
    """Apply the smaller Gold Magnet bonus to ordinary fish sale value."""
    multiplier = 1.08 if gold_magnet is True else float(gold_magnet or 1.0)
    return int(base * multiplier)


def king_sale_price(base: int, *, gold_magnet: bool | float = False) -> int:
    """Apply the larger Gold Magnet bonus to fish-king sale value."""
    multiplier = 10.0 if gold_magnet is True else float(gold_magnet or 1.0)
    return int(base * multiplier)


def hidden_fish_trigger(
    *,
    zone: str,
    hidden_lure: bool,
    rng: random.Random | None = None,
    rate: float = 0.045,
) -> str | None:
    """Hidden lure only works in the matching fishing zone."""
    if not hidden_lure:
        return None
    candidates = [name for name, item in HIDDEN_FISH.items() if item["zone"] == zone]
    if not candidates or (rng or random).random() >= rate:
        return None
    return (rng or random).choice(candidates)
