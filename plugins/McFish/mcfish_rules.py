"""Pure gameplay rules for McFish.

The plugin keeps persistence and message delivery outside this module so the
time, strategy and pity rules can be tested without a running bot.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

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

HOOK_SKILL_BONUS = {
    "precision_hookset": 0.00,
    "powerful_hookset": 0.08,
    "patient_control": 0.12,
    "burst_reel": 0.18,
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
    "月影巨鲤": {"chain": "河湾链", "zone": "bay", "kind": "prerequisite", "path": ("4", "4", "4"), "requires": None, "length": (45, 110), "base_price": 180, "asset_id": "mcfish_moon_carp_king"},
    "河湾巨鲤": {"chain": "河湾链", "zone": "bay", "kind": "final", "path": ("3", "3"), "requires": "月影巨鲤", "length": (80, 180), "base_price": 420, "asset_id": "mcfish_river_carp_king"},
    "深海黑鳍": {"chain": "深海链", "zone": "deep", "kind": "prerequisite", "path": ("4", "4", "4"), "requires": None, "length": (60, 150), "base_price": 260, "asset_id": "mcfish_deep_blackfin_king"},
    "深海灯笼鱼": {"chain": "深海链", "zone": "deep", "kind": "final", "path": ("3", "3", "3"), "requires": "深海黑鳍", "length": (100, 260), "base_price": 680, "asset_id": "mcfish_deep_lanternfish_king"},
    "遗迹铜鳞": {"chain": "遗迹链", "zone": "ruin", "kind": "prerequisite", "path": ("3", "3"), "requires": None, "length": (70, 190), "base_price": 360, "asset_id": "mcfish_ruin_copper_scale_king"},
    "遗迹金鳞鱼": {"chain": "遗迹链", "zone": "ruin", "kind": "final", "path": ("4", "4", "4"), "requires": "遗迹铜鳞", "length": (120, 320), "base_price": 980, "asset_id": "mcfish_ruin_gold_scale_king"},
    "风暴幼龙": {"chain": "风暴链", "zone": "storm", "kind": "prerequisite", "path": ("4", "4"), "requires": None, "length": (90, 220), "base_price": 520, "asset_id": "mcfish_storm_whelp_king"},
    "风暴海龙": {"chain": "风暴链", "zone": "storm", "kind": "final", "path": ("4", "4", "4", "4"), "requires": "风暴幼龙", "length": (180, 500), "base_price": 1600, "asset_id": "mcfish_storm_seadragon_king"},
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
    "幽光鳐": {"zone": "deep", "price": 800, "asset_id": "mcfish_luminous_ray"},
    "星砂鳗": {"zone": "ruin", "price": 1000, "asset_id": "mcfish_starsand_eel"},
    "云潮鲨": {"zone": "storm", "price": 1500, "asset_id": "mcfish_cloudtide_shark"},
}

CHAIN_RULES = {
    "河湾链": {"days": (0, 1), "overlap": 9, "first_length": (27, 41), "final_length": (23, 37)},
    "深海链": {"days": (1, 2, 3), "overlap": 11, "first_length": (31, 47), "final_length": (25, 43)},
    "遗迹链": {"days": (3, 4), "overlap": 9, "first_length": (23, 37), "final_length": (29, 45)},
    "风暴链": {"days": (5, 6), "overlap": 13, "first_length": (29, 43), "final_length": (23, 35)},
}

ROD_UNLOCK_LEVELS = {
    "木竿": 0,
    "铁竿": 3,
    "金竿": 8,
    "钻石竿": 15,
    "下界合金竿": 25,
    "三叉戟": 35,
}

ROD_LEVELS = tuple(ROD_UNLOCK_LEVELS)
POLE_FRAGMENT_COSTS = {
    "木竿": 12,
    "铁竿": 24,
    "金竿": 56,
    "钻石竿": 120,
    "下界合金竿": 240,
}
POLE_DISMANTLE_FRAGMENTS = {
    "木竿": 1,
    "铁竿": 2,
    "金竿": 4,
    "钻石竿": 8,
    "下界合金竿": 16,
    "三叉戟": 32,
}
MIN_FISH_WAIT_SECONDS = 30
BASE_FISH_WAIT_SECONDS = 180
FISHING_ZONES = ("bay", "deep", "ruin", "storm")
FISHING_ZONE_LABELS = {"bay": "河湾", "deep": "深海", "ruin": "遗迹水域", "storm": "风暴海域"}
CHAIN_ZONES = {"河湾链": "bay", "深海链": "deep", "遗迹链": "ruin", "风暴链": "storm"}

FISH_ZONE_WEIGHTS = {
    "bay": ("鳕鱼", "鲑鱼", "热带鱼", "河豚", "鹦鹉螺", "墨鱼"),
    "deep": ("鳕鱼", "鲑鱼", "河豚", "鹦鹉螺", "墨鱼", "鳗鱼", "深海鱼"),
    "ruin": ("热带鱼", "河豚", "鹦鹉螺", "墨鱼", "鳗鱼", "水晶虾", "银鱼"),
    "storm": ("鲑鱼", "热带鱼", "河豚", "墨鱼", "银鱼", "金鳞鱼"),
}

FISHING_XP = {
    "鳕鱼": 5,
    "鲑鱼": 8,
    "热带鱼": 12,
    "河豚": 18,
    "鹦鹉螺": 25,
    "墨鱼": 30,
    "鲤鱼": 10,
    "鳗鱼": 16,
    "水晶虾": 24,
    "深海鱼": 32,
    "银鱼": 40,
    "金鳞鱼": 50,
    "幽光鳐": 80,
    "星砂鳗": 100,
    "云潮鲨": 120,
}

FISH_ROD_WEIGHTS = {
    "木竿": (30, 25, 15, 10, 8, 6, 4, 2, 0, 0, 0, 0),
    "铁竿": (24, 20, 14, 10, 8, 7, 6, 4, 3, 2, 1, 1),
    "金竿": (18, 16, 13, 10, 9, 8, 7, 5, 4, 4, 3, 3),
    "钻石竿": (14, 12, 10, 9, 9, 9, 8, 7, 7, 6, 5, 4),
    "下界合金竿": (10, 9, 8, 8, 8, 8, 8, 8, 9, 9, 8, 7),
    "三叉戟": (8, 8, 8, 8, 8, 8, 8, 8, 9, 10, 9, 8),
}

# Values are (bite rate, hook success rate), indexed by rod level.
FISH_KING_PROBABILITIES = {
    "月影巨鲤": ((.03, .20), (.05, .25), (.08, .35), (.12, .45), (.16, .55), (.20, .60)),
    "河湾巨鲤": ((.005, .15), (.01, .20), (.02, .30), (.03, .40), (.04, .50), (.05, .58)),
    "深海黑鳍": ((.03, .18), (.05, .23), (.08, .33), (.12, .43), (.16, .53), (.20, .60)),
    "深海灯笼鱼": ((.005, .15), (.01, .20), (.02, .30), (.03, .40), (.04, .50), (.05, .58)),
    "遗迹铜鳞": ((.03, .15), (.05, .20), (.08, .30), (.12, .40), (.16, .50), (.20, .58)),
    "遗迹金鳞鱼": ((.005, .10), (.01, .15), (.02, .25), (.03, .35), (.04, .45), (.05, .55)),
    "风暴幼龙": ((.03, .12), (.05, .18), (.08, .28), (.12, .38), (.16, .48), (.20, .58)),
    "风暴海龙": ((.005, .05), (.01, .10), (.02, .18), (.03, .28), (.04, .40), (.05, .50)),
}

FISH_KING_UNLOCKED_PROBABILITIES = {
    "河湾巨鲤": ((.08, .40), (.12, .45), (.18, .50), (.25, .55), (.32, .60), (.40, .65)),
    "深海灯笼鱼": ((.08, .40), (.12, .45), (.18, .50), (.25, .55), (.32, .60), (.40, .65)),
    "遗迹金鳞鱼": ((.08, .35), (.12, .40), (.18, .45), (.25, .50), (.32, .55), (.40, .60)),
    "风暴海龙": ((.08, .30), (.12, .35), (.18, .40), (.25, .45), (.32, .52), (.40, .58)),
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

# (base seconds, random extra seconds). The result's profile controls the
# next hidden timestamp, so common low-value fish resolve faster than rare fish.
FISH_WAIT_PROFILES = {
    "鳕鱼": (30, 20),
    "鲑鱼": (40, 25),
    "热带鱼": (50, 30),
    "河豚": (65, 35),
    "鹦鹉螺": (80, 40),
    "墨鱼": (90, 45),
    "鲤鱼": (45, 25),
    "鳗鱼": (60, 35),
    "水晶虾": (75, 40),
    "深海鱼": (90, 45),
    "银鱼": (110, 50),
    "金鳞鱼": (130, 60),
    "幽光鳐": (180, 90),
    "星砂鳗": (210, 100),
    "云潮鲨": (240, 120),
    **{name: (300, 300) for name in FISH_KINGS},
}


def level_for_xp(xp: int) -> int:
    """Return the fishing level from cumulative XP using an increasing curve."""
    xp = max(0, int(xp))
    level = 1
    while level < 1000 and xp >= xp_required_for_level(level + 1):
        level += 1
    return level


def xp_required_for_level(level: int) -> int:
    """Return cumulative XP required to reach a level (level 1 starts at 0)."""
    level = max(1, int(level))
    steps = level - 1
    return 100 * steps + 25 * steps * (steps - 1)


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


def fish_zone_weights(zone: str, rod_name: str) -> dict[str, int]:
    names = FISH_ZONE_WEIGHTS.get(zone, FISH_ZONE_WEIGHTS["bay"])
    all_names = ("鳕鱼", "鲑鱼", "热带鱼", "河豚", "鹦鹉螺", "墨鱼", "鲤鱼", "鳗鱼", "水晶虾", "深海鱼", "银鱼", "金鳞鱼")
    values = FISH_ROD_WEIGHTS.get(rod_name, FISH_ROD_WEIGHTS["木竿"])
    return {name: values[all_names.index(name)] for name in names}


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


def hook_skill_bonus(skill: str) -> float:
    return HOOK_SKILL_BONUS.get(skill, 0.0)


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
    """Generate irregular, overlapping windows for one Monday-based week."""
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


def path_progress(state: dict, king_name: str, strategy: str) -> dict:
    paths = state.setdefault("king_paths", {})
    progress = paths.setdefault(king_name, {"counts": {}, "guaranteed": False})
    number = next((number for number, value in STRATEGY_BY_NUMBER.items() if value == strategy), None)
    if number:
        progress["counts"][number] = int(progress["counts"].get(number, 0)) + 1
    required = FISH_KINGS[king_name]["path"]
    progress["guaranteed"] = all(int(progress["counts"].get(number, 0)) >= required.count(number) for number in set(required))
    return progress


def king_success_rate(base: float, *, guaranteed: bool = False, tough_tackle: bool | int = False) -> float:
    value = 0.95 if guaranteed else base
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


def hidden_fish_trigger(*, zone: str, hidden_lure: bool, rng: random.Random | None = None) -> str | None:
    """Hidden lure only works in the matching fishing zone."""
    if not hidden_lure:
        return None
    candidates = [name for name, item in HIDDEN_FISH.items() if item["zone"] == zone]
    if not candidates or (rng or random).random() >= 0.045:
        return None
    return (rng or random).choice(candidates)
