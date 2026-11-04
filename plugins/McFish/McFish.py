from __future__ import annotations

import asyncio
import copy
import json
import math
import random
import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont
from ncatbot.core import registrar
from ncatbot.plugin import NcatBotPlugin
from ncatbot.types import At, Image, MessageArray, PlainText
from ncatbot.utils import get_log
from common.utils.CommonUtil import CommonUtil

from .mcfish_rules import (
    ACCESSORY_MAX_LEVEL,
    ACCESSORY_SLOT_UNLOCKS,
    ACCESSORY_UPGRADE_COSTS,
    EQUIPMENT,
    EQUIPMENT_CRAFT_FEES,
    FISH_KINGS,
    FISH_KING_PROMPTS,
    HIDDEN_FISH,
    FishKingWindow,
    active_window,
    daily_attempt_limit,
    daily_fishing_zone,
    default_fishing_zone,
    default_fishing_zone_for_power,
    fish_min_power,
    fishing_zone_required_power,
    fishing_zone_unlocked,
    FISH_ZONE_WEIGHTS,
    FISHING_ZONE_DAILY_CAPS,
    FISHING_ZONE_SELL_TAX,
    capped_king_sale_price,
    KING_MAGNET_CAP,
    fishing_zone_label,
    fishing_zone_unlock_level,
    fish_zone_weights,
    fish_king_probabilities,
    fishing_xp,
    fishing_wait_for_result,
    format_duration,
    advance_path,
    bite_skill_from_reply,
    consume_path,
    hook_skill_from_reply,
    hook_skill_rate_multiplier,
    king_length_for_hook,
    fragment_roll,
    fragment_jackpot,
    generate_week_windows,
    king_success_rate,
    PERMANENT_ACCESSORY_EFFECTS,
    king_bite_rate,
    level_for_xp,
    POLE_FRAGMENT_COSTS,
    POLE_DISMANTLE_FRAGMENTS,
    xp_required_for_level,
    rod_unlock_level,
    rod_drop_allowed,
    resolve_fishing_zone,
    FISHING_ZONE_LABELS,
    FISHING_ZONES,
    fish_sale_price,
    hidden_fish_trigger,
    king_sale_price,
    path_is_satisfied,
    parse_count_token,
    treasure_sale_price,
)
from .admin_server import DEFAULT_ADMIN_TOKEN, McFishAdminServer
from .admin_service import McFishAdminService
from .mcfish_content import (
    BOX_LOOT,
    BOX_NAMES,
    CARD_ALIASES,
    CARD_NAMES,
    CARDS,
    FOOD_RESTORE,
    ITEM_PRICES,
    KEY_NAMES,
    MATERIAL_NAMES,
    ZONE_FOODS,
    ZONE_MATERIALS,
    ZONE_VALUABLES,
    box_key_name,
    card_for_zone,
    roll_box_loot,
    roll_card,
    roll_food,
    roll_key_or_box,
    roll_material,
    roll_valuable,
    roll_waste_sub,
    waste_split,
)
from .mcfish_effects import format_effect
from .mcfish_equipment import (
    GEAR_ALIASES,
    GEAR_SLOTS,
    JEWELRY_SLOTS,
    PIECE_INFO,
    SETS,
    SLOT_LABELS,
    set_icon,
    set_label,
    craft_recipe,
    fp_for_level,
    make_piece,
    piece_info,
    scaled_effects,
    upgrade_cost,
    worn_sets,
)
from .mcfish_help import revision_page
from .mcfish_power import ROD_POWER, fishing_power, power_breakdown, power_summary
from .mcfish_resident_kings import (
    RESIDENT_KING_DAILY_LIMIT,
    RESIDENT_KINGS,
    RESIDENT_KING_NAMES,
    bait_multiplier as resident_king_bait_multiplier,
    bite_chance as resident_king_bite_chance,
    hook_chance as resident_king_hook_chance,
    price_for as resident_king_price,
    resident_king_for_zone,
    resident_king_fp,
    roll_length as resident_king_length,
)
from .mcfish_talents import (
    TALENTS,
    can_learn as can_learn_talent,
    effects as talent_effects,
    free_points as talent_free_points,
    learn as learn_talent,
    reset as reset_talents,
    reset_cost as talent_reset_cost,
    sanitize as sanitize_talents,
    talent_by_label,
    total_points as talent_total_points,
    tree_lines as talent_tree_lines,
)
from .mcfish_workshop import (
    ORACLE,
    RECIPES,
    TRIDENT,
    can_claim,
    mark_claimed,
    mark_delivered,
    progress as workshop_progress,
    recipe_of,
    recipe_unlocked,
    status_text as workshop_status,
    trident_unlocked,
)


LOG = get_log("McFish")
# 限时鱼王与常驻鱼王的合并视图：展示、价格与出售走同一张表，
# 窗口、路径与工坊仍然只认 FISH_KINGS。
KING_INFO = {**FISH_KINGS, **RESIDENT_KINGS}
BLOCKED_GROUP_ID = "853963912"
ITEM_GRANT_USERS = {"273421673"}

# 用于区分“状态里没有这个键”和“键的值就是 None”。
_MISSING = object()


class _McFishEvent:
    """Keep the upstream message style without NcatBot's default sender mention."""

    def __init__(self, event, plugin):
        self._event = event
        self._plugin = plugin

    def __getattr__(self, name):
        return getattr(self._event, name)

    async def reply(self, *args, **kwargs):
        if _is_blocked_group(self._event):
            return None
        kwargs.setdefault("at_sender", False)
        positional_text = args[0] if args and isinstance(args[0], str) else None
        text = kwargs.get("text", positional_text)
        if "rtf" in kwargs:
            kwargs["rtf"] = await self._plugin._message_array_as_images(
                self._event, kwargs["rtf"]
            )
        elif text is not None and getattr(self._plugin, "workspace", None):
            if positional_text is not None:
                args = args[1:]
            kwargs.pop("text", None)
            output = await asyncio.to_thread(
                self._plugin._render_text_image,
                str(self._event.sender.user_id),
                str(text),
            )
            kwargs["rtf"] = MessageArray([Image(file=str(output))])
        outgoing = kwargs.get("rtf")
        try:
            return await self._event.reply(*args, **kwargs)
        finally:
            self._plugin._cleanup_message_images(outgoing)


def _is_blocked_group(event) -> bool:
    return str(getattr(event, "group_id", "")) == BLOCKED_GROUP_ID


def _same_conversation(incoming, source_event) -> bool:
    incoming = getattr(incoming, "data", incoming)
    source_user = getattr(source_event, "user_id", None) or getattr(
        getattr(source_event, "sender", None), "user_id", ""
    )
    incoming_user = getattr(incoming, "user_id", None) or getattr(
        getattr(incoming, "sender", None), "user_id", ""
    )
    if str(incoming_user) != str(source_user):
        return False
    source_group = getattr(source_event, "group_id", None)
    return source_group is None or str(getattr(incoming, "group_id", "")) == str(source_group)


POLES = {
    "木竿": (30, 100),
    "铜竿": (45, 240),
    "锡竿": (60, 480),
    "铁竿": (80, 900),
    "猩红竿": (105, 1600),
    "幻光竿": (130, 2600),
    "星河竿": (155, 4200),
    "灿阳竿": (185, 6800),
    "三叉戟": (301, 4000),
}
# 只保留错别字别名。旧档位改名（铁→铜、金→锡、钻石→铁、下界合金→猩红）
# 由一次性迁移完成，不能放进别名表——否则迁移后新建的“铁竿”会被再次改名。
POLE_ALIASES = {
    "木杆": "木竿",
    "铜杆": "铜竿",
    "锡杆": "锡竿",
    "铁杆": "铁竿",
    "猩红杆": "猩红竿",
    "幻光杆": "幻光竿",
    "星河杆": "星河竿",
    "灿阳杆": "灿阳竿",
}
# 旧档位 → 新档位。仅用于一次性迁移，不参与运行时归一化。
LEGACY_ROD_NAMES = {
    "铁竿": "铜竿",
    "金竿": "锡竿",
    "钻石竿": "铁竿",
    "下界合金竿": "猩红竿",
}
# Fishing drops use their own attribute roll. Shop, exchange, and crafting rods
# intentionally continue to use their existing fixed or inherited attributes.
POLE_DROP_ATTRIBUTE_LIMITS = {
    "maintenance": 10,
    "induce": 3,
    "favor": 2,
}
FISH = {
    # 河湾（泥土 + 黏土）
    "岩石凶鲨": 10, "吸血鳗": 50, "恶魔蠕虫": 100,
    "橙色洞穴孔雀鱼": 180, "蓝色洞穴孔雀鱼": 220, "绿色水泡头": 260, "黄色水泡头": 260,
    # 深海（沉没之海）
    "追踪鱼": 300, "银色飞镖": 380, "金色飞镖": 400, "粉色珊瑚蝾螈": 400,
    # 遗迹水域（被遗忘的废墟）
    "刃鳍鱼": 420, "粉色的宫殿鱼": 450, "蓝绿色的宫殿鱼": 500,
    # 风暴海域（埃齐欧斯荒野）
    "蔚蓝色羽毛鱼": 550, "翠绿色羽毛鱼": 560, "魂灵面纱": 600,
    # 熔岩深渊（熔融采石场）
    "暗色噬岩鱼": 600, "亮色噬岩鱼": 620, "碧绿龙鱼": 680, "古老龙鱼": 750,
    # 极地冰海（霉菌生态区）
    "霉菌鲨鱼": 800, "腐烂的鱼": 800,
    # 荧光洞窟（荧光边境）
    "莹润深潜鱼": 900, "灿烂深潜鱼": 1000, "蓝晶棱角鱼": 1000, "金晶棱角鱼": 1200,
    # 星陨深渊（起源荒漠）
    "硬皮尖刺鱼": 1200, "砂皮尖刺鱼": 1200, "灰色沙丘曳尾鱼": 1200, "褐色沙丘曳尾鱼": 1400,
    # 时之洄流（通道）
    "陆地三叶虫": 1400, "石三叶虫": 1600, "绿角皮科鱼": 1700, "粉角皮科鱼": 2000,
}
# 旧鱼名 → wiki 鱼名。用于任务池改写、旧存档迁移和商店库存清理。
FISH_RENAMES = {
    "鳕鱼": "刃鳍鱼", "鲑鱼": "追踪鱼", "鲤鱼": "岩石凶鲨", "晨雾鲈": "吸血鳗", "荷鲤": "吸血鳗",
    "热带鱼": "粉色的宫殿鱼", "鳗鱼": "蓝绿色的宫殿鱼", "深海鱼": "莹润深潜鱼",
    "潮汐鲷": "灿烂深潜鱼", "鹦鹉螺": "灿烂深潜鱼",
    "水晶虾": "陆地三叶虫", "银鱼": "石三叶虫", "墨鱼": "绿角皮科鱼",
    "壁画鳗": "粉角皮科鱼", "沉钟鱼": "粉角皮科鱼",
    "河豚": "蔚蓝色羽毛鱼", "风蚀鲣": "翠绿色羽毛鱼", "金鳞鱼": "银色飞镖",
    "云帆鱼": "金色飞镖", "雷纹旗鱼": "金色飞镖",
    "熔岩鲈": "恶魔蠕虫", "赤铁鱼": "腐烂的鱼", "硫火鳗": "暗色噬岩鱼",
    "黑曜石鲷": "亮色噬岩鱼", "岩浆鳐": "亮色噬岩鱼",
    "冰鳍鲑": "霉菌鲨鱼", "霜纹鳕": "硬皮尖刺鱼", "极光鳟": "砂皮尖刺鱼",
    "冰晶水母": "魂灵面纱", "白鲸鳗": "魂灵面纱",
    "荧光鳍": "橙色洞穴孔雀鱼", "晶簇虾": "蓝色洞穴孔雀鱼", "幽蓝鳐": "黄色水泡头",
    "灯芯鱼": "蓝晶棱角鱼", "辉光龙鱼": "蓝晶棱角鱼",
    "星屑鲱": "绿色水泡头", "陨铁鲷": "灰色沙丘曳尾鱼", "银河鳗": "褐色沙丘曳尾鱼",
    "星环鳐": "金晶棱角鱼", "星核鲸": "金晶棱角鱼",
    "逆流鲑": "石三叶虫", "沙漏鱼": "碧绿龙鱼", "时砂鳗": "古老龙鱼",
    "轮回鲟": "粉色珊瑚蝾螈", "永恒之影": "粉色珊瑚蝾螈",
    "幽光鳐": "灰色沙丘曳尾鱼", "星砂鳗": "魂灵面纱", "云潮鲨": "岩石凶鲨",
    "白色珊瑚螈": "白色珊瑚蝾螈",
}
ITEMS = {"诱钓": 1000, "海之眷顾": 2500, "唱片": 3000, "美西螈": 3000, "海豚": 1000, "净化书": 5000, "宝藏诅咒": 0, "垃圾": 10}
SHOP_EQUIPMENT_PRICES = {
    "魔法鱼饵": 1800,
    "隐秘诱饵": 2600,
    "万能鱼饵": 3200,
    "诱钓": 500,
    "海之眷顾": 1000,
}
SHOP_EQUIPMENT_STOCK = {
    "魔法鱼饵": 5,
    "隐秘诱饵": 3,
    "万能鱼饵": 2,
    "诱钓": 5,
    "海之眷顾": 3,
}
SHOP_WOOD_PRICE = 180
SHOP_ROD_PRICES = {
    "铜竿": 600,
    "锡竿": 1600,
    "铁竿": 4000,
    "猩红竿": 9000,
    "幻光竿": 16000,
    "星河竿": 24000,
    "灿阳竿": 36000,
}
AXOLOTL = "美西螈"
UPGRADES = {
    "铜竿": "木竿",
    "锡竿": "铜竿",
    "铁竿": "锡竿",
    "猩红竿": "铁竿",
    "幻光竿": "猩红竿",
    "星河竿": "幻光竿",
    "灿阳竿": "星河竿",
    "三叉戟": "灿阳竿",
}

# 任务无需接取。任务池按难度和钓鱼等级分档，周期开始时抽取并保存任务快照。
# 低档任务仍可能出现在高等级玩家的任务板上，但不领取就不会产生消耗。
TASK_STATE_VERSION = 5
PERMANENT_TASK_STATE_VERSION = 1
TASK_DIFFICULTY_LABELS = {"simple": "简单", "medium": "中等", "complex": "复杂"}
TASK_LEVEL_TIERS = (
    {"id": "1-5", "label": "1-5级", "min": 1, "max": 5},
    {"id": "6-10", "label": "5-10级", "min": 6, "max": 10},
    {"id": "11-20", "label": "10-20级", "min": 11, "max": 20},
    {"id": "21-30", "label": "20-30级", "min": 21, "max": 30},
    {"id": "31+", "label": "30级+", "min": 31, "max": 9999},
)

# 每个池条目同时提供每日/每周版本，实际任务由 _task_definitions 组合抽取。
TASK_POOL = (
    {
        "id": "starter_cod_supply", "type": "delivery", "difficulty": "simple", "level_tier": "1-5", "label": "河湾基础补给",
        "requirements": {"daily": {"鳕鱼": 6}, "weekly": {"鳕鱼": 18}},
        "rewards": {"daily": {"xp": 20, "items": {"鳕鱼": 10}}, "weekly": {"xp": 70, "items": {"鳕鱼": 30}}},
    },
    {
        "id": "starter_salmon_order", "type": "delivery", "difficulty": "simple", "level_tier": "1-5", "label": "河湾鲜鱼单",
        "requirements": {"daily": {"鲑鱼": 4}, "weekly": {"鲑鱼": 12}},
        "rewards": {"daily": {"xp": 25, "gold": 35}, "weekly": {"xp": 80, "gold": 120}},
    },
    {
        "id": "starter_mixed_order", "type": "delivery", "difficulty": "medium", "level_tier": "1-5", "label": "小型混合订单",
        "requirements": {"daily": {"鳕鱼": 10, "鲑鱼": 4}, "weekly": {"鳕鱼": 30, "鲑鱼": 12}},
        "rewards": {"daily": {"xp": 45, "fragments": 4}, "weekly": {"xp": 140, "fragments": 12}},
    },
    {
        "id": "starter_purification", "type": "delivery", "difficulty": "complex", "level_tier": "1-5", "label": "初级净化供奉",
        "requirements": {"daily": {"鳕鱼": 20, "鲑鱼": 8}, "weekly": {"鳕鱼": 50, "鲑鱼": 20}},
        "rewards": {"daily": {"xp": 80, "items": {"魔法鱼饵": 1, "木竿": 1}}, "weekly": {"xp": 220, "items": {"魔法鱼饵": 2}}},
    },
    {
        "id": "apprentice_cod_order", "type": "delivery", "difficulty": "simple", "level_tier": "6-10", "label": "熟练渔户鳕鱼单",
        "requirements": {"daily": {"鳕鱼": 12}, "weekly": {"鳕鱼": 36}},
        "rewards": {"daily": {"xp": 60, "gold": 70}, "weekly": {"xp": 180, "gold": 240}},
    },
    {
        "id": "apprentice_mixed_order", "type": "delivery", "difficulty": "medium", "level_tier": "6-10", "label": "熟练渔户混合单",
        "requirements": {"daily": {"鲑鱼": 15, "热带鱼": 5}, "weekly": {"鲑鱼": 45, "热带鱼": 15}},
        "rewards": {"daily": {"xp": 120, "fragments": 8}, "weekly": {"xp": 320, "fragments": 22}},
    },
    {
        "id": "apprentice_purification", "type": "delivery", "difficulty": "complex", "level_tier": "6-10", "label": "进阶净化供奉",
        "requirements": {"daily": {"鳕鱼": 30, "鲑鱼": 15}, "weekly": {"鳕鱼": 75, "鲑鱼": 35}},
        "rewards": {"daily": {"xp": 180, "items": {"隐秘诱饵": 1, "铜竿": 1}}, "weekly": {"xp": 480, "items": {"隐秘诱饵": 2}}},
    },
    {
        "id": "expert_salmon_order", "type": "delivery", "difficulty": "simple", "level_tier": "11-20", "label": "专家鲑鱼订单",
        "requirements": {"daily": {"鲑鱼": 15}, "weekly": {"鲑鱼": 45}},
        "rewards": {"daily": {"xp": 120, "gold": 150}, "weekly": {"xp": 360, "gold": 500}},
    },
    {
        "id": "expert_mixed_order", "type": "delivery", "difficulty": "medium", "level_tier": "11-20", "label": "专家混合订单",
        "requirements": {"daily": {"鳕鱼": 25, "鲑鱼": 10}, "weekly": {"鳕鱼": 75, "鲑鱼": 30}},
        "rewards": {"daily": {"xp": 220, "fragments": 14, "gold": 100}, "weekly": {"xp": 650, "fragments": 35, "gold": 320}},
    },
    {
        "id": "expert_purification", "type": "delivery", "difficulty": "complex", "level_tier": "11-20", "label": "净化仪式",
        "requirements": {"daily": {"鳕鱼": 40, "鲑鱼": 20}, "weekly": {"鳕鱼": 100, "鲑鱼": 50}},
        "rewards": {"daily": {"xp": 350, "items": {"净化书": 1, "锡竿": 1}}, "weekly": {"xp": 1000, "items": {"净化书": 2}}},
    },
    {
        "id": "master_cod_order", "type": "delivery", "difficulty": "simple", "level_tier": "21-30", "label": "大师鳕鱼订单",
        "requirements": {"daily": {"鳕鱼": 25}, "weekly": {"鳕鱼": 75}},
        "rewards": {"daily": {"xp": 200, "gold": 280}, "weekly": {"xp": 600, "gold": 900}},
    },
    {
        "id": "master_mixed_order", "type": "delivery", "difficulty": "medium", "level_tier": "21-30", "label": "大师混合订单",
        "requirements": {"daily": {"鲑鱼": 35, "热带鱼": 15}, "weekly": {"鲑鱼": 105, "热带鱼": 45}},
        "rewards": {"daily": {"xp": 380, "fragments": 25, "items": {"万能鱼饵": 1}}, "weekly": {"xp": 1100, "fragments": 65, "items": {"万能鱼饵": 2}}},
    },
    {
        "id": "master_purification", "type": "delivery", "difficulty": "complex", "level_tier": "21-30", "label": "大师净化仪式",
        "requirements": {"daily": {"鳕鱼": 70, "鲑鱼": 35}, "weekly": {"鳕鱼": 200, "鲑鱼": 100}},
        "rewards": {"daily": {"xp": 650, "items": {"净化书": 1, "隐秘诱饵": 1, "铁竿": 1}}, "weekly": {"xp": 1900, "items": {"净化书": 3}}},
    },
    {
        "id": "legend_cod_order", "type": "delivery", "difficulty": "simple", "level_tier": "31+", "label": "传说鳕鱼订单",
        "requirements": {"daily": {"鳕鱼": 35}, "weekly": {"鳕鱼": 105}},
        "rewards": {"daily": {"xp": 320, "gold": 450}, "weekly": {"xp": 950, "gold": 1400}},
    },
    {
        "id": "legend_mixed_order", "type": "delivery", "difficulty": "medium", "level_tier": "31+", "label": "传说混合订单",
        "requirements": {"daily": {"鳕鱼": 50, "鲑鱼": 25}, "weekly": {"鳕鱼": 150, "鲑鱼": 75}},
        "rewards": {"daily": {"xp": 600, "fragments": 45, "items": {"万能鱼饵": 1}}, "weekly": {"xp": 1800, "fragments": 110, "items": {"万能鱼饵": 3}}},
    },
    {
        "id": "legend_purification", "type": "delivery", "difficulty": "complex", "level_tier": "31+", "label": "传说净化仪式",
        "requirements": {"daily": {"鳕鱼": 100, "鲑鱼": 50}, "weekly": {"鳕鱼": 300, "鲑鱼": 150}},
        "rewards": {"daily": {"xp": 1200, "items": {"净化书": 2, "猩红竿": 1}}, "weekly": {"xp": 3600, "items": {"净化书": 5}}},
    },
    {
        "id": "maintenance", "type": "spend", "difficulty": "medium", "level_tier": "1-5", "label": "渔具维护",
        "requirements": {"weekly": {"金币": 300}},
        "rewards": {"weekly": {"xp": 50, "fragments": 3}},
    },
    {
        "id": "maintenance_apprentice", "type": "spend", "difficulty": "medium", "level_tier": "6-10", "label": "熟练渔具维护",
        "requirements": {"weekly": {"金币": 700}},
        "rewards": {"weekly": {"xp": 140, "fragments": 8, "items": {"魔法鱼饵": 1}}},
    },
    {
        "id": "maintenance_expert", "type": "spend", "difficulty": "medium", "level_tier": "11-20", "label": "专家渔具维护",
        "requirements": {"weekly": {"金币": 1200}},
        "rewards": {"weekly": {"xp": 260, "fragments": 16, "items": {"隐秘诱饵": 1}}},
    },
    {
        "id": "maintenance_master", "type": "spend", "difficulty": "medium", "level_tier": "21-30", "label": "大师渔具维护",
        "requirements": {"weekly": {"金币": 1800}},
        "rewards": {"weekly": {"xp": 450, "fragments": 25, "items": {"万能鱼饵": 1}}},
    },
    {
        "id": "maintenance_legend", "type": "spend", "difficulty": "medium", "level_tier": "31+", "label": "传说渔具维护",
        "requirements": {"weekly": {"金币": 2600}},
        "rewards": {"weekly": {"xp": 700, "fragments": 40, "items": {"万能鱼饵": 2}}},
    },
)
PERMANENT_TASK = {
    "id": "permanent_all_fish_kings",
    "pool_id": "permanent_all_fish_kings",
    "type": "collection",
    "difficulty": "milestone",
    "difficulty_label": "里程碑",
    "level_tier": "永久",
    "level_tier_label": "永久",
    "label": "鱼王全收集",
    # 任务 60 级解锁；鱼竿本身的使用门槛保留 35，兼容已经持有三叉戟的老玩家。
    "level_required": 60,
    "requirements": {name: 1 for name in FISH_KINGS},
    "reward": {"items": {"三叉戟": 1}},
}
def _rename_task_fish(pool) -> None:
    """把任务池里的旧鱼名改写为 wiki 鱼名。"""
    for task in pool:
        requirements = task.get("requirements") or {}
        for period in ("daily", "weekly"):
            values = requirements.get(period)
            if isinstance(values, dict):
                requirements[period] = {FISH_RENAMES.get(name, name): count for name, count in values.items()}
        reward = (task.get("reward") or {}).get("items")
        if isinstance(reward, dict):
            task["reward"]["items"] = {FISH_RENAMES.get(name, name): count for name, count in reward.items()}


_rename_task_fish(TASK_POOL)

TASK_KIND_LABELS = {"daily": "每日任务", "weekly": "每周任务", "permanent": "常驻任务"}

# 2026-09-10 数据修复：移植时把“背包里鱼的总数超过 100 只会改变概率区间”误写成“一次获得 5 个”，
# 下面两位玩家因此多领了物品和经验。清单来自服务器日志里的逐竿上钩记录（09-09 完整日志 + 09-10
# 17:53 的十连结果）：每一次计入 ×5 的上钩都多发了 4 个物品，经验差额与捕获记录完全吻合
# （891-4×81=567、1205-4×123=713）。
EXCESSIVE_DROP_REPAIRS = {
    "1310043427": {
        "excess_xp": 324,
        "items": {
            "鳕鱼": 12,
            "鲑鱼": 16,
            "河豚": 4,
            "鳗鱼": 4,
            "绊线钩": 8,
            "鞋子": 4,
            "碗": 4,
            "木棍": 4,
            "瓶子": 4,
            "腐肉": 4,
        },
    },
    "10123121": {
        "excess_xp": 492,
        "items": {
            "鹦鹉螺": 8,
            "墨鱼": 4,
            "鳕鱼": 4,
            "河豚": 4,
            "鲑鱼": 4,
            "热带鱼": 4,
            "帽子": 8,
            "木棍": 8,
            "海草": 4,
            "瓶子": 4,
            "碗": 12,
            "骨头": 12,
        },
    },
}


# 2026-09-11 数据修复：后台钓鱼任务在等待期间持有整份内存快照、每竿结束再整份写回，
# 玩家在等待期间的购买会被覆盖丢失。账本（GroupEconomy ledger）与 fishing_attempts
# 的时间轴对比结果：
# - 10123121：01:16:00（-540，3 根木竿）落在 01:15:17-01:30:15 任务区间内；
#   01:59:50（-720，4 根木竿）落在 01:54:29-02:08:11 区间内。他当天共买 13 根木竿，
#   其中 6 根合成了现在背包里的 2 根铁竿，被覆盖丢失的正好是这 3+4=7 根。
# - 1506123340：07:56:40（-8500，20 条墨鱼）与 07:56:54（-4250，10 条墨鱼）
#   都落在 07:54:19-08:10:28 任务区间内，30 条墨鱼全部被覆盖。
CONCURRENT_OVERWRITE_REPAIRS = {
    "10123121": {"木竿": 7},
    "1506123340": {"墨鱼": 30},
}


# 2026-09-11 补发（第二批）：同一个“后台任务整份覆盖存档”bug 在更早的时间段还吞掉了
# 下面这些购买。逐条来自 GroupEconomy 账本与 mcfish.fishing_attempts 任务区间的时间重叠：
# - 10123121：09-10 21:46（-180，1 根木竿）落在 21:45:51-21:59:42 的十连区间内。
# - 2448564447：09-10 18:50（-540，3 根木竿）落在 18:50:38-18:56:08；09-11 03:38（-300）
#   落在 03:37:25-03:52:24，购买物品无法唯一还原，按原价补回金币。
# - 1310043427：09-10 17:54（-180，1 根木竿）落在 17:53:30-18:07:33；09-08 00:20（-80）
#   落在 00:17:43-00:34:01，按原价补回金币。
# - 273421673：09-10 17:32（-180，1 根木竿）落在 17:27:34-17:36:28。
# - 1506123340：09-08 的 -405 / -1740 / -108 分别落在当天的两次十连区间内，购买物品无法
#   唯一还原，共补回 2253 金币。
CONCURRENT_OVERWRITE_LATE_REPAIRS = {
    "10123121": {"items": {"木竿": 1}, "gold": 0},
    "2448564447": {"items": {"木竿": 3}, "gold": 300},
    "1310043427": {"items": {"木竿": 1}, "gold": 80},
    "273421673": {"items": {"木竿": 1}, "gold": 0},
    "1506123340": {"items": {}, "gold": 2253},
}


MCFISH_COMMAND_ROWS_BASE = (
    "开始：钓鱼 / 进行钓鱼 / 进行n次钓鱼（支持中文数量，单次最多10竿）",
    "查询：钓鱼看板 / 钓鱼商店 / 钓鱼背包 / 钓鱼时钟 / 钓鱼概率 [鱼点/全部] / 钓鱼图鉴 [名称] / 装备图鉴 [套装] / 查看上次钓鱼结果",
    "任务：钓鱼任务 / 领取钓鱼任务 编号",
    "鱼点：钓鱼鱼点（鱼点与常驻鱼王） / 前往 深海（切换鱼点）",
    "买卖：购买xxx [数量] / 出售xxx [数量] / 出售所有垃圾（可一次出售多个物品）",
    "旧系统：装备鱼竿或饰品、卸下饰品、购买饰品槽位/解锁饰品槽位、升级财富浮标/升级坚韧钓组/升级黄金磁石/升级幸运海星、修复鱼竿/维修鱼竿、附魔诱钓或海之眷顾",
    "合成与分解：合成鱼竿、合成财富浮标、合成坚韧钓组、合成黄金磁石、合成幸运海星、合成魔法鱼饵、合成隐秘诱饵、合成万能鱼饵、分解鱼竿、消除宝藏诅咒、当前装备概率明细",
)
MCFISH_COMMAND_ROWS_SYSTEMS = (
    "钓鱼力：钓鱼力（查看构成与等待倍率）",
    "收集：钓鱼卡牌 / 开箱 铜制箱子 / 吃海藻 绿色海藻 3",
    "装备：钓鱼装备 / 合成渔具 铜制头盔 / 装备渔具 铜制头盔 / 强化渔具 铜制头盔 / 卸下渔具 头盔",
    "工坊：钓鱼工坊 [配方] / 交付 三叉戟 鱼王鳞片 1 / 领取 三叉戟 / 领取 神谕卡组",
    "天赋：钓鱼天赋（树状加点与前置） / 学习 钓感 / 重置钓鱼天赋",
    "帮助：钓鱼帮助 或 查看钓鱼规则（速查卡） / 钓鱼帮助 改版 1-8 / 钓鱼帮助 鱼点/装备/鱼竿/掉落/任务/机制/全部",
    "管理员：钓鱼发放 @用户 物品 数量 / 钓鱼充值 @用户 物品 数量",
)
MCFISH_COMMAND_ROWS = MCFISH_COMMAND_ROWS_BASE + MCFISH_COMMAND_ROWS_SYSTEMS


MCFISH_HELP = """一款钓鱼模拟器
----------指令----------
""" + "\n".join(f"- {row}" for row in MCFISH_COMMAND_ROWS) + """

规则V6.4:
- 每竿独立结算并按隐藏上钩时间发送结果；同一玩家已有任务时不能重复开始，其他玩家不受影响。
- 普通鱼直接结算；鱼王有咬钩和上钩两个阶段，鱼王窗口只在对应鱼点和时间内生效。
- 钓鱼力 = 等级 + 鱼竿 + 装备 + 首饰 + 饰品 + 天赋 + 临时加成；鱼点由玩家自选，跨天不会重置。
- 钓鱼力同时影响等待与稀有度：等待×1/(1+FP/100)；鱼种权重=基础×min(上限,1+(FP−min_fp)/150)，上限按稀有度1.4/1.7/1.9/2.2。
- 鱼点门槛按钓鱼力：河湾0 / 深海20 / 遗迹水域50 / 风暴海域80 / 熔岩深渊110 / 极地冰海135 / 荧光洞窟160 / 星陨深渊190 / 时之洄流220；每个鱼点 2–7 条普通鱼，按顺序额外要求 +0/+0/+20/+45/+60/+75/+90 钓鱼力。
- 单鱼点每日次数：河湾60 / 深海55 / 遗迹50 / 风暴45 / 熔岩40 / 极地40 / 荧光35 / 星陨35 / 时之洄流30；与全局每日次数同时生效。
- 出售税按鱼点递增：河湾20% / 深海22% / 遗迹25% / 风暴28% / 熔岩32% / 极地34% / 荧光37% / 星陨40% / 时之洄流45%；鱼竿、宝藏与非鱼点物品按20%。
- 常驻与前置鱼王的黄金磁石售价封顶 ×3/×5/×8，后置鱼王为 ×10/×30/×100。
- 常驻鱼王每个鱼点 1 条，钓鱼力达标后每竿独立判定，咬钩与上钩同一次结算，每日最多成功 1 条，不产出鱼王鳞片。
- 天赋为树状：每条分支必须先满足前置等级才能继续加；不合法的旧加点会在读取存档时清除，点数退回可用池，不会丢失。
- 鱼王咬钩后不回复默认保留目标，只有回复2才会尝试以小钓大、回复3才会放弃；上钩阶段1/2/3/4为上钩率×1.00/1.10/1.24/1.45，成功率越高的技能鱼体越小。
- 鱼王路径只在成功上钩时累计；满足路径后下一次上钩率锁定95%，保证成功后路径清空，且路径不跨周继承。
- 经验用于升级：1–35级沿用旧曲线；36–60级每级需要1200经验起（每级+40），61–100级每级需要2600经验起（每级+60），满级100级累计224250经验；每日次数按等级为50/60/70/80/100次，鱼竿按等级解锁。
- 无鱼竿且余额不足时每3天最多补偿1根木竿；有余额可发送“花费100购买鱼竿”，最多可以购买3次。
- 三叉戟：达到60级并成功捕获全部8条限时鱼王后，在工坊交付鱼王鳞片8个、时砂5个和7万金币领取。耐久上限301，不再额外增加抛竿次数，仍为1竿1次掉落；工坊奖励的三叉戟不会损坏，耐久归零后可直接支付金币修复。
- 合成鱼竿需要3根上一等级鱼竿，合成成功率90%；使用碎片兑换时必须确认，未明确选择不会默认消耗碎片；合成和修复会收取金币手续费。
- 碎片可兑换鱼竿或合成装备；普通鱼基础8%获得1–2个，第13次未获得时保底，鱼王成功必得5–9个。
- 饰品合成：发送“合成财富浮标/合成坚韧钓组/合成黄金磁石/合成幸运海星”，分别需要120/160/220/300个钓鱼碎片，并支付30000/50000/80000/120000金币手续费；永久饰品默认2格。
- 饰品槽位：发送“购买饰品槽位”解锁下一格；钓鱼等级10级并支付80个碎片解锁第3格，20级并支付180个碎片解锁第4格，最多4格。
- 饰品升级：发送“升级财富浮标 / 升级坚韧钓组 / 升级黄金磁石 / 升级幸运海星”升级，升级只消耗钓鱼碎片、不消耗金币，最高Ⅲ级；同名永久饰品不能重复装备。
- 饰品管理：发送“卸下财富浮标 / 卸下坚韧钓组 / 卸下黄金磁石 / 卸下幸运海星”或“卸下全部饰品”取消装备，卸下不会丢失饰品。
- 消耗品合成：魔法鱼饵需要24个碎片（100金币手续费，10竿），隐秘诱饵需要42个碎片（160金币手续费，6竿），万能鱼饵需要50个碎片（220金币手续费，5竿）；合成后发送“装备魔法鱼饵”等命令启用，每完成1竿消耗1次。
- 永久饰品效果：财富浮标Ⅰ/Ⅱ/Ⅲ级为宝藏×1.35/1.50/1.65、鱼王咬钩×1.20/1.28/1.36；坚韧钓组为上钩率+12/16/20个百分点；黄金磁石为宝藏品质+1/2/3档、宝藏售价×1.25/1.35/1.50、普通鱼售价×1.08/1.12/1.16、后置鱼王售价×10/30/100（前置与常驻鱼王封顶×3/×5/×8，钓起显示和出售结算均生效）；幸运海星为普通碎片概率16%/20%/24%。
- 掉落鱼竿随机生成耐久、维修和附魔；合成与修复的手续费由钱包实际扣除。
- 达到对应等级后，商店常驻出售铜竿/锡竿/铁竿/猩红竿/幻光竿/星河竿/灿阳竿，价格分别为600/1600/4000/9000/16000/24000/36000金币；商店购买鱼竿不能作为合成材料，掉落、任务奖励、碎片兑换和合成鱼竿仍可合成。
- 可出售：鱼、鱼王、鱼竿、贵重品、食物、垃圾、宝藏道具、消耗品；材料、钥匙、箱子、神谕卡片、装备、鱼王鳞片、钓鱼碎片不可出售。
- 每日和每周任务从简单/中等/复杂任务池按等级档位组合生成；发送“钓鱼任务”查看编号和奖励，发送“领取钓鱼任务 编号”领取奖励，任务跨日、跨周自动刷新。三叉戟改由工坊交付，不再作为任务奖励。
- 宝藏诅咒不能交易，可用净化书消除；鱼竿超过30支不能钓鱼，超过50支不能购买。"""


# 帮助图片里的特殊机制。每条只放进“特殊机制”卡片一次，避免和规则正文重复。
MCFISH_SPECIAL_MECHANICS = (
    "美西螈：宝藏掉落；装备后区间同样变为2/8/35/45；每竿要吃3条普通鱼（不含鱼王、海豚、材料等），鱼不够时永久失去；其他物品一次获得5个，鱼竿和美西螈除外。",
    "海豚：背包里放着海豚时，空竿区间全部并入垃圾区间。",
    "鱼数超过100条：区间变为宝藏2%、鱼竿8%、鱼35%、垃圾45%，只改变概率、不改变数量。",
    "净化书：每完成1竿累计1点祝福，满75点自动获得1本净化书，超出部分计入下一轮。",
    "交易诅咒：买卖鱼竿每满10次获得1个宝藏诅咒；买入价+10%、卖出价-10%；可用净化书1:1消除。",
    "商店折扣每天重新生成（约50%–199%），库存跨天延续；木竿固定180金币；出售按收购价80%结算，可用唱片翻倍。",
    "高级鱼竿按等级常驻出售：铜竿600、锡竿1600、铁竿4000、猩红竿9000、幻光竿16000、星河竿24000、灿阳竿36000金币；商店购买杆不能参与合成。",
    "半价福利：每买满20次非鱼竿物品获得3次半价，单次购买最多抵3件，鱼竿不参与。",
    "修复：消耗1根同名鱼竿和金币，维修度+1；耐久增加材料耐久的80%（鱼竿≤10支）或100%。",
    "附魔：诱钓、海之眷顾约50%成功，最高3级；修复时材料附魔有50%概率继承。",
    "合成：3根同等级鱼竿或碎片兑换均需金币手续费；鱼竿合成成功率90%，失败仍只扣一次手续费。",
)


class McFish(NcatBotPlugin):
    name = "McFish"
    version = "1.0.0"
    author = "NcatBot"
    description = "钓鱼模拟器"
    MAX_FISH_BATCH = 10

    async def on_load(self) -> None:
        self.init_defaults({
            "fish_limit": 50,
            "success_rate": 0.6,
            "starting_pole_price": 100,
            "sell_tax": 0.8,
            "admin_enabled": True,
            "standalone_admin_enabled": False,
            "admin_host": "127.0.0.1",
            "admin_port": 8766,
            "admin_token": DEFAULT_ADMIN_TOKEN,
        })
        self._economy = self.get_plugin("GroupEconomy")
        if self._economy is None:
            raise RuntimeError("McFish requires GroupEconomy")
        self._db = sqlite3.connect(str(Path(self.workspace) / "mcfish.db"), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS players (user_id TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at REAL NOT NULL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS shop (day TEXT PRIMARY KEY, discounts TEXT NOT NULL, inventory TEXT NOT NULL, custom_prices TEXT NOT NULL DEFAULT '{}')")
        shop_columns = {str(row[1]) for row in self._db.execute("PRAGMA table_info(shop)").fetchall()}
        if "custom_prices" not in shop_columns:
            self._db.execute("ALTER TABLE shop ADD COLUMN custom_prices TEXT NOT NULL DEFAULT '{}'")
        self._db.execute("CREATE TABLE IF NOT EXISTS admin_adjustments (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, reason TEXT NOT NULL, old_state TEXT NOT NULL, new_state TEXT NOT NULL, created_at REAL NOT NULL)")
        self._ensure_admin_adjustment_columns()
        self._db.execute("CREATE TABLE IF NOT EXISTS fishing_groups (group_id TEXT PRIMARY KEY, group_name TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS fishing_group_members (group_id TEXT NOT NULL, user_id TEXT NOT NULL, display_name TEXT NOT NULL DEFAULT '', last_seen_at REAL NOT NULL, PRIMARY KEY (group_id, user_id))")
        self._db.execute("CREATE TABLE IF NOT EXISTS migrations (migration_id TEXT PRIMARY KEY, applied_at REAL NOT NULL, details TEXT NOT NULL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS fish_windows (week_start TEXT NOT NULL, chain TEXT NOT NULL, king_name TEXT NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT NOT NULL, PRIMARY KEY (week_start, king_name))")
        self._db.execute("CREATE TABLE IF NOT EXISTS fishing_attempts (user_id TEXT NOT NULL, attempt_index INTEGER NOT NULL, scheduled_at TEXT NOT NULL, rod_id TEXT NOT NULL, bait_id TEXT NOT NULL, active_window_id TEXT, result_type TEXT NOT NULL, created_at REAL NOT NULL)")
        self._db.commit()
        self._user_locks: dict[str, asyncio.Lock] = {}
        self._fishing_tasks: dict[str, asyncio.Task] = {}
        self._closing = False
        CommonUtil.cleanup_old_files(
            Path(self.workspace) / "images", max_age_seconds=3600
        )
        CommonUtil.cleanup_backup_files([Path(self.workspace)], keep_seconds=86400)
        self.add_scheduled_task(
            "mcfish_temp_cleanup",
            "1h",
            callback=lambda: (
                CommonUtil.cleanup_old_files(
                    Path(self.workspace) / "images", max_age_seconds=3600
                ),
                CommonUtil.cleanup_backup_files(
                    [Path(self.workspace)], keep_seconds=86400
                ),
            ),
        )
        self._load_original_data()
        self._migrate_rod_ladder()
        self._migrate_workshop()
        self._migrate_wiki_fish()
        self._ensure_fish_windows()
        self._realign_king_windows()
        self._repair_abnormal_pole_trades()
        self._repair_abnormal_pole_inventory()
        self._repair_abnormal_equipment()
        self._reset_fishing_progress_for_level_release()
        self._repair_excessive_drop_quantities()
        self._top_up_excessive_drop_quantities()
        self._repair_concurrent_overwrite_losses()
        self._top_up_concurrent_overwrite_losses()
        self._admin_server = None
        self.admin_service = McFishAdminService(
            self,
            rod_names={"木竿", "铜竿", "锡竿", "铁竿", "猩红竿", "幻光竿", "星河竿", "灿阳竿", "三叉戟"},
            fixed_price_names={
                "木竿",
                "铜竿",
                "锡竿",
                "铁竿",
                "猩红竿",
                "幻光竿",
                "星河竿",
                "灿阳竿",
                "魔法鱼饵",
                "隐秘诱饵",
                "万能鱼饵",
            },
        )
        # 默认把面板并入统一后台（/api/mcfish/*），不再单独占一个端口。
        # admin_enabled 只表示面板是否对统一后台开放；
        # standalone_admin_enabled 才是旧的独立端口开关，默认关闭。
        if bool(self.get_config("standalone_admin_enabled", False)):
            self._admin_server = McFishAdminServer(
                self,
                host=str(self.get_config("admin_host", "127.0.0.1")),
                port=int(self.get_config("admin_port", 8766)),
                token=str(self.get_config("admin_token", DEFAULT_ADMIN_TOKEN) or DEFAULT_ADMIN_TOKEN),
            )
            try:
                await self._admin_server.start()
            except OSError as exc:
                LOG.error("McFish 独立管理界面启动失败，请检查 admin_host/admin_port：%s", exc)
                self._admin_server = None

    async def on_close(self) -> None:
        # 热更新会触发插件卸载。钓鱼任务跨等待时间运行，直接取消会让最后一竿
        # 和最终结果丢失；这里先停止新任务，再等现有任务完成自己的结算。
        self._closing = True
        tasks = [
            task
            for task in getattr(self, "_fishing_tasks", {}).values()
            if task is not None and not task.done()
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._fishing_tasks = {}
        if getattr(self, "_admin_server", None) is not None:
            await self._admin_server.stop()
        if hasattr(self, "_db"):
            self._db.close()

    def _ensure_admin_adjustment_columns(self) -> None:
        columns = {
            "operator_id": "TEXT NOT NULL DEFAULT ''",
            "action": "TEXT NOT NULL DEFAULT 'legacy'",
        }
        existing = {
            str(row[1])
            for row in self._db.execute("PRAGMA table_info(admin_adjustments)").fetchall()
        }
        for name, definition in columns.items():
            if name not in existing:
                self._db.execute(f"ALTER TABLE admin_adjustments ADD COLUMN {name} {definition}")
        self._db.commit()

    @staticmethod
    def _ensure_accessory_state(state: dict) -> None:
        state.setdefault("accessories", [])
        if not isinstance(state["accessories"], list):
            state["accessories"] = []
        levels = state.setdefault("accessory_levels", {})
        if not isinstance(levels, dict):
            state["accessory_levels"] = levels = {}
        slots = state.get("accessory_slots", 2)
        try:
            state["accessory_slots"] = max(2, min(4, int(slots)))
        except (TypeError, ValueError):
            state["accessory_slots"] = 2
        for name in PERMANENT_ACCESSORY_EFFECTS:
            if name in state["accessories"] or int(state.get("pack", {}).get(name, 0)) > 0:
                try:
                    levels[name] = max(1, min(ACCESSORY_MAX_LEVEL, int(levels.get(name, 1))))
                except (TypeError, ValueError):
                    levels[name] = 1

    def _accessory_level(self, state: dict, name: str) -> int:
        self._ensure_accessory_state(state)
        if name not in state["accessories"] and int(state.get("pack", {}).get(name, 0)) <= 0:
            return 0
        return int(state["accessory_levels"].get(name, 1))

    def _accessory_effect(self, state: dict, name: str, key: str, default):
        level = self._accessory_level(state, name)
        return PERMANENT_ACCESSORY_EFFECTS.get(name, {}).get(level, {}).get(key, default)

    def _state(self, user_id: str) -> dict:
        row = self._db.execute("SELECT state FROM players WHERE user_id = ?", (str(user_id),)).fetchone()
        state = json.loads(row[0]) if row else {}
        state.setdefault("pack", {})
        # 每次读取都兜底归一化旧鱼名，避免迁移未跑或被跳过后背包/图鉴/商店对不上新名字。
        self._rename_fish_state(state)
        for old_name, canonical_name in POLE_ALIASES.items():
            if old_name in state["pack"]:
                value = state["pack"].pop(old_name)
                state["pack"].setdefault(canonical_name, [])
                state["pack"][canonical_name].extend(
                    value if isinstance(value, list) else self._shop_poles(value, canonical_name)
                )
        if state.get("equip") and state["equip"].get("name") in POLE_ALIASES:
            state["equip"]["name"] = POLE_ALIASES[state["equip"]["name"]]
        state.setdefault("fish_today", 0)
        state.setdefault("fish_xp", 0)
        state.setdefault("fish_level", max(1, level_for_xp(int(state.get("fish_xp", 0)))))
        state.setdefault("fish_bless", 0)
        state.setdefault("fish_day", date.today().isoformat())
        state.setdefault("equip", None)
        state.setdefault("king_paths", {})
        state.setdefault("king_stats", {})
        if not isinstance(state.get("king_sale_prices"), dict):
            state["king_sale_prices"] = {}
        state.setdefault("fish_stats", {})
        state.setdefault("fragment_misses", 0)
        self._ensure_accessory_state(state)
        state.setdefault("active_consumables", {})
        state.setdefault("trade_day", date.today().isoformat())
        state.setdefault("trade_count", 0)
        state.setdefault("pole_trade_count", 0)
        state.setdefault("item_buy_count", 0)
        state.setdefault("half_price_count", 0)
        today = date.today().isoformat()
        state.setdefault("fishing_zone_day", today)
        level_now = int(state.get("fish_level", 1))
        state.setdefault("fishing_zone", default_fishing_zone(level_now))
        # 鱼点门槛按钓鱼力：装备/鱼竿变化后若已不达标，回到当前能进的最高点。
        if not fishing_zone_unlocked(str(state["fishing_zone"]), fishing_power(state)):
            state["fishing_zone"] = default_fishing_zone_for_power(fishing_power(state))
        # 装备与天赋在后续阶段写入；这里只保证旧存档有默认容器。
        state.setdefault("equipment", {})
        state.setdefault("jewelry", {})
        state.setdefault("talents", {})
        # 旧存档或异常写入可能留下“先点后置”的加点：清理并退还点数。
        refunded_talents = sanitize_talents(state)
        if refunded_talents:
            state["talent_refunded"] = int(state.get("talent_refunded", 0)) + refunded_talents
        state.setdefault("fp_bonus", 0)
        state.setdefault("cards", {})
        for old_card, new_card in CARD_ALIASES.items():
            if old_card in state["cards"]:
                state["cards"][new_card] = max(
                    int(state["cards"].get(new_card, 0)), int(state["cards"].pop(old_card))
                )
        state.setdefault("card_pity", {})
        state.setdefault("food_today", 0)
        state.setdefault("resident_king_today", {})
        state.setdefault("zone_fish_today", {})
        state.setdefault("item_zones", {})
        state.setdefault("gear_bag", [])
        for group in ("equipment", "jewelry"):
            slots = state.get(group) or {}
            for slot, item in list(slots.items()):
                if not isinstance(item, dict):
                    continue
                new_name = GEAR_ALIASES.get(str(item.get("name", "")))
                info = PIECE_INFO.get(new_name or "")
                if not info:
                    continue
                target_group = "equipment" if info["slot"] in GEAR_SLOTS else "jewelry"
                same_slot = info["slot"] == slot or (info["slot"] == "ring" and slot in ("ring1", "ring2"))
                if target_group == group and same_slot:
                    item["name"] = new_name
                    item["set"] = info["set"]
                    item["slot"] = info["slot"]
                else:
                    slots.pop(slot, None)
                    state["gear_bag"].append(
                        make_piece(new_name, int(item.get("level", info["start"])))
                    )
        migrated_bag = []
        for item in state["gear_bag"]:
            if isinstance(item, dict):
                new_name = GEAR_ALIASES.get(str(item.get("name", "")))
                info = PIECE_INFO.get(new_name or "")
                if info:
                    item = dict(item)
                    item["name"] = new_name
                    item["set"] = info["set"]
                    item["slot"] = info["slot"]
            migrated_bag.append(item)
        state["gear_bag"] = migrated_bag
        state.setdefault("workshop", {})
        state.setdefault("buy_times", 0)
        state.setdefault("emergency_pole_day", "")
        state.setdefault("emergency_pole_count", 1 if state.get("emergency_pole_day") else 0)
        state.setdefault("starter_pole_day", "")
        state.setdefault("last_fishing_result", None)
        if state["fish_day"] != today:
            state["fish_day"], state["fish_today"] = today, 0
            state["food_today"] = 0
            state["resident_king_today"] = {}
            state["zone_fish_today"] = {}
        # 鱼点由玩家自选，跨天只更新“活跃鱼点”记录，不再覆盖玩家选择。
        state["fishing_zone_day"] = today
        if state["trade_day"] != date.today().isoformat():
            state["trade_day"], state["trade_count"] = date.today().isoformat(), 0
            state["pole_trade_count"], state["item_buy_count"] = 0, 0
        # 鱼王路径不跨周继承：每周第一次读取状态时清空，避免一次性刷满后永久保证上钩。
        week_key = "{}-W{:02d}".format(*date.today().isocalendar()[:2])
        if state.get("king_paths_week") != week_key:
            state["king_paths_week"] = week_key
            state["king_paths"] = {}
        if state.get("talent_reset_week") != week_key:
            state["talent_reset_week"] = week_key
            if state.get("talents"):
                state["talents"]["reset_count"] = 0
        return state

    def _record_group_member(self, event) -> None:
        """Keep a lightweight group-to-player index for the admin panel."""
        group_id = getattr(event, "group_id", None)
        sender = getattr(event, "sender", None)
        user_id = getattr(sender, "user_id", None) or getattr(event, "user_id", None)
        if group_id is None or user_id is None or not hasattr(self, "_db"):
            return
        group_id = str(group_id)
        user_id = str(user_id)
        display_name = str(
            getattr(sender, "card", None)
            or getattr(sender, "nickname", None)
            or getattr(event, "display_name", None)
            or user_id
        )
        group_name = str(getattr(event, "group_name", None) or group_id)
        now = time.time()
        self._db.execute(
            "INSERT INTO fishing_groups(group_id, group_name, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(group_id) DO UPDATE SET group_name = excluded.group_name, updated_at = excluded.updated_at",
            (group_id, group_name, now),
        )
        self._db.execute(
            "INSERT INTO fishing_group_members(group_id, user_id, display_name, last_seen_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(group_id, user_id) DO UPDATE SET display_name = excluded.display_name, last_seen_at = excluded.last_seen_at",
            (group_id, user_id, display_name, now),
        )
        self._db.commit()

    def _ensure_fish_windows(self) -> None:
        """Persist one irregular schedule per week; this method never sends messages."""
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today - timedelta(days=today.weekday())
        for schedule_start in (week_start, week_start + timedelta(days=7)):
            key = schedule_start.date().isoformat()
            if self._db.execute("SELECT 1 FROM fish_windows WHERE week_start = ? LIMIT 1", (key,)).fetchone():
                continue
            for window in generate_week_windows(schedule_start, seed=schedule_start.toordinal()):
                self._db.execute(
                    "INSERT INTO fish_windows VALUES (?, ?, ?, ?, ?)",
                    (key, window.chain, window.king_name, window.starts_at.isoformat(), window.ends_at.isoformat()),
                )
        self._db.commit()

    def _rename_pole_keys(self, data: dict) -> tuple[dict, int]:
        """Copy a mapping over to the new rod names; lists (rod stacks) merge."""
        result: dict = {}
        renamed = 0
        for name, value in (data or {}).items():
            target = LEGACY_ROD_NAMES.get(name, name)
            if target != name:
                renamed += 1
            if isinstance(value, list) and isinstance(result.get(target), list):
                result[target].extend(value)
            else:
                result[target] = value
        return result, renamed

    def _migrate_rod_ladder(self) -> None:
        """P1: 旧五档鱼竿按相对档位映射到九档金属阶梯。"""
        migration_id = "mcfish-rod-ladder-2026-11-01"
        if self._db.execute("SELECT 1 FROM migrations WHERE migration_id = ?", (migration_id,)).fetchone():
            return
        touched_players = 0
        renamed_poles = 0
        for user_id, raw_state in self._db.execute("SELECT user_id, state FROM players").fetchall():
            state = json.loads(raw_state)
            pack, renamed = self._rename_pole_keys(state.get("pack") or {})
            equip = state.get("equip")
            equip_renamed = False
            if isinstance(equip, dict) and equip.get("name") in LEGACY_ROD_NAMES:
                equip["name"] = LEGACY_ROD_NAMES[equip["name"]]
                equip_renamed = True
            if not renamed and not equip_renamed:
                continue
            state["pack"] = pack
            self._db.execute(
                "UPDATE players SET state = ?, updated_at = ? WHERE user_id = ?",
                (json.dumps(state, ensure_ascii=False), time.time(), str(user_id)),
            )
            touched_players += 1
            renamed_poles += renamed
        shop_rows = 0
        for day, discounts, inventory, custom_prices in self._db.execute(
            "SELECT day, discounts, inventory, custom_prices FROM shop"
        ).fetchall():
            new_discounts, a = self._rename_pole_keys(json.loads(discounts))
            new_inventory, b = self._rename_pole_keys(json.loads(inventory))
            new_custom, c = self._rename_pole_keys(json.loads(custom_prices))
            if not (a or b or c):
                continue
            self._db.execute(
                "UPDATE shop SET discounts = ?, inventory = ?, custom_prices = ? WHERE day = ?",
                (
                    json.dumps(new_discounts, ensure_ascii=False),
                    json.dumps(new_inventory, ensure_ascii=False),
                    json.dumps(new_custom, ensure_ascii=False),
                    day,
                ),
            )
            shop_rows += 1
        self._db.execute(
            "INSERT INTO migrations(migration_id, applied_at, details) VALUES (?, ?, ?)",
            (
                migration_id,
                time.time(),
                json.dumps(
                    {"players": touched_players, "poles": renamed_poles, "shop_rows": shop_rows},
                    ensure_ascii=False,
                ),
            ),
        )
        self._db.commit()

    def _migrate_workshop(self) -> None:
        """P6: 鱼王全收集从常驻任务迁移到工坊；已领取的玩家标记为已领取。"""
        migration_id = "mcfish-workshop-2026-11-01"
        if self._db.execute("SELECT 1 FROM migrations WHERE migration_id = ?", (migration_id,)).fetchone():
            return
        touched = 0
        for user_id, raw_state in self._db.execute("SELECT user_id, state FROM players").fetchall():
            state = json.loads(raw_state)
            permanent = state.get("fishing_permanent_tasks") or {}
            claimed = PERMANENT_TASK["id"] in (permanent.get("claimed") or [])
            pack = state.get("pack") or {}
            equip = state.get("equip") or {}
            has_trident = bool(pack.get("三叉戟")) or equip.get("name") == "三叉戟"
            if not (claimed or has_trident):
                continue
            bucket = state.setdefault("workshop", {}).setdefault(
                "trident", {"delivered": {}, "claimed": False}
            )
            bucket["claimed"] = True
            if claimed and not has_trident:
                pack.setdefault("三叉戟", []).append(
                    {
                        "durable": POLES["三叉戟"][0],
                        "maintenance": 0,
                        "induce": 0,
                        "favor": 0,
                        "unbreakable": True,
                    }
                )
                state["pack"] = pack
            self._db.execute(
                "UPDATE players SET state = ?, updated_at = ? WHERE user_id = ?",
                (json.dumps(state, ensure_ascii=False), time.time(), str(user_id)),
            )
            touched += 1
        self._db.execute(
            "INSERT INTO migrations(migration_id, applied_at, details) VALUES (?, ?, ?)",
            (migration_id, time.time(), json.dumps({"players": touched}, ensure_ascii=False)),
        )
        self._db.commit()

    @staticmethod
    def _rename_fish_mapping(values: dict, mapping: dict) -> tuple[dict, int]:
        result: dict = {}
        changed = 0
        for name, value in values.items():
            target = mapping.get(name, name)
            if target != name:
                changed += 1
            if target in result and isinstance(value, (int, float)) and isinstance(result[target], (int, float)):
                result[target] += value
            else:
                result[target] = value
        return result, changed

    def _rename_fish_state(self, state: dict) -> int:
        changed = 0
        pack, count = self._rename_fish_mapping(state.get("pack") or {}, FISH_RENAMES)
        if pack:
            state["pack"] = pack
        changed += count
        stats = state.get("fish_stats")
        if isinstance(stats, dict):
            renamed: dict = {}
            for name, item in stats.items():
                target = FISH_RENAMES.get(name, name)
                if target != name:
                    changed += 1
                if target in renamed and isinstance(item, dict):
                    renamed[target]["count"] = int(renamed[target].get("count", 0)) + int(item.get("count", 0))
                    first = item.get("first_caught")
                    if first and (not renamed[target].get("first_caught") or first < renamed[target]["first_caught"]):
                        renamed[target]["first_caught"] = first
                else:
                    renamed[target] = dict(item) if isinstance(item, dict) else item
            state["fish_stats"] = renamed
        for key in ("king_stats", "king_sale_prices", "king_results", "resident_king_today"):
            values = state.get(key)
            if isinstance(values, dict):
                state[key], count = self._rename_fish_mapping(values, FISH_RENAMES)
                changed += count
        for kind in ("daily", "weekly"):
            bucket = state.get(f"fishing_{kind}_tasks")
            if not isinstance(bucket, dict):
                continue
            for task in bucket.get("tasks") or []:
                requirements = task.get("requirements")
                if isinstance(requirements, dict):
                    task["requirements"], task_changed = self._rename_fish_mapping(requirements, FISH_RENAMES)
                    changed += task_changed
            progress = bucket.get("progress")
            if isinstance(progress, dict):
                for task_id, values in list(progress.items()):
                    if isinstance(values, dict):
                        progress[task_id], task_changed = self._rename_fish_mapping(values, FISH_RENAMES)
                        changed += task_changed
        return changed

    def _migrate_wiki_fish(self) -> None:
        """P7: 把旧鱼名迁移到 Core Keeper WIKI 鱼名，覆盖背包、图鉴、任务和商店。"""
        migration_id = "mcfish-wiki-fish-2026-11-02"
        if self._db.execute("SELECT 1 FROM migrations WHERE migration_id = ?", (migration_id,)).fetchone():
            return
        touched_players = 0
        renamed_items = 0
        for user_id, raw_state in self._db.execute("SELECT user_id, state FROM players").fetchall():
            state = json.loads(raw_state)
            changed = self._rename_fish_state(state)
            if not changed:
                continue
            self._db.execute(
                "UPDATE players SET state = ?, updated_at = ? WHERE user_id = ?",
                (json.dumps(state, ensure_ascii=False), time.time(), str(user_id)),
            )
            touched_players += 1
            renamed_items += changed
        shop_rows = 0
        for day, discounts, inventory, custom_prices in self._db.execute(
            "SELECT day, discounts, inventory, custom_prices FROM shop"
        ).fetchall():
            new_discounts, a = self._rename_fish_mapping(json.loads(discounts), FISH_RENAMES)
            new_inventory, b = self._rename_fish_mapping(json.loads(inventory), FISH_RENAMES)
            new_custom, c = self._rename_fish_mapping(json.loads(custom_prices), FISH_RENAMES)
            if not (a or b or c):
                continue
            self._db.execute(
                "UPDATE shop SET discounts = ?, inventory = ?, custom_prices = ? WHERE day = ?",
                (
                    json.dumps(new_discounts, ensure_ascii=False),
                    json.dumps(new_inventory, ensure_ascii=False),
                    json.dumps(new_custom, ensure_ascii=False),
                    day,
                ),
            )
            shop_rows += 1
        self._db.execute(
            "INSERT INTO migrations(migration_id, applied_at, details) VALUES (?, ?, ?)",
            (
                migration_id,
                time.time(),
                json.dumps(
                    {"players": touched_players, "items": renamed_items, "shop_rows": shop_rows},
                    ensure_ascii=False,
                ),
            ),
        )
        self._db.commit()

    def _realign_king_windows(self) -> None:
        """Regenerate this and next week so every window lands on its own zone day."""
        migration_id = "mcfish-king-window-zone-2026-10-30"
        if self._db.execute("SELECT 1 FROM migrations WHERE migration_id = ?", (migration_id,)).fetchone():
            return
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today - timedelta(days=today.weekday())
        starts = (week_start, week_start + timedelta(days=7))
        for schedule_start in starts:
            key = schedule_start.date().isoformat()
            self._db.execute("DELETE FROM fish_windows WHERE week_start = ?", (key,))
            for window in generate_week_windows(schedule_start, seed=schedule_start.toordinal()):
                self._db.execute(
                    "INSERT INTO fish_windows VALUES (?, ?, ?, ?, ?)",
                    (key, window.chain, window.king_name, window.starts_at.isoformat(), window.ends_at.isoformat()),
                )
        self._db.execute(
            "INSERT INTO migrations(migration_id, applied_at, details) VALUES (?, ?, ?)",
            (migration_id, time.time(), json.dumps({"weeks": [item.date().isoformat() for item in starts]}, ensure_ascii=False)),
        )
        self._db.commit()

    def _reset_fishing_progress_for_level_release(self) -> None:
        """Initialize the level release once and normalize equipped rods."""
        migration_id = "mcfish-level-release-reset-2026-09-07-v2"
        if self._db.execute("SELECT 1 FROM migrations WHERE migration_id = ?", (migration_id,)).fetchone():
            return
        today = date.today().isoformat()
        rows = self._db.execute("SELECT user_id, state FROM players").fetchall()
        for user_id, raw_state in rows:
            state = json.loads(raw_state)
            state.setdefault("pack", {})
            equip = state.get("equip") or {}
            equip_name = equip.get("name")
            if equip_name and equip_name != "木竿":
                if equip_name in POLES:
                    state["pack"].setdefault(equip_name, []).append(dict(equip))
                elif equip_name == AXOLOTL:
                    state["pack"][equip_name] = int(state["pack"].get(equip_name, 0)) + 1
                state["equip"] = {"name": "木竿", "durable": 30, "maintenance": 0, "induce": 0, "favor": 0}
            state["fish_xp"] = 0
            state["fish_level"] = 1
            state["fish_today"] = 0
            state["fish_day"] = today
            self._db.execute(
                "UPDATE players SET state = ?, updated_at = ? WHERE user_id = ?",
                (json.dumps(state, ensure_ascii=False), time.time(), str(user_id)),
            )
        self._db.execute(
            "INSERT INTO migrations(migration_id, applied_at, details) VALUES (?, ?, ?)",
            (migration_id, time.time(), json.dumps({"users": len(rows), "level": 1, "fish_today": 0}, ensure_ascii=False)),
        )
        self._db.commit()

    def _fish_windows(self) -> list[FishKingWindow]:
        if not hasattr(self, "_db"):
            return []
        today = datetime.now().date()
        week_start = today - timedelta(days=today.weekday())
        next_week = week_start + timedelta(days=7)
        rows = self._db.execute(
            "SELECT chain, king_name, starts_at, ends_at FROM fish_windows WHERE week_start IN (?, ?)",
            (week_start.isoformat(), next_week.isoformat()),
        ).fetchall()
        return [FishKingWindow(chain, name, datetime.fromisoformat(start), datetime.fromisoformat(end)) for chain, name, start, end in rows]

    def _clock_text(self) -> str:
        now = datetime.now()
        windows = self._fish_windows()
        current = active_window(windows, now)
        upcoming = next((item for item in sorted(windows, key=lambda value: value.starts_at) if item.starts_at > now), None)
        next_zone_at = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        current_zone = daily_fishing_zone(now)
        next_zone = daily_fishing_zone(next_zone_at)
        zone_lines = (
            f"当前鱼点：{fishing_zone_label(current_zone)}\n"
            f"当前鱼点剩余：{format_duration((next_zone_at - now).total_seconds())}\n"
            f"下一鱼点：{fishing_zone_label(next_zone)}\n"
            f"下一鱼点开始：{next_zone_at:%m月%d日 %H:%M}（还有{format_duration((next_zone_at - now).total_seconds())}）\n"
            "下一鱼点持续：24小时"
        )
        if current:
            state = "水面出现异动"
            remaining = format_duration((current.ends_at - now).total_seconds())
            duration = format_duration((current.ends_at - current.starts_at).total_seconds())
            king_line = f"当前鱼王：{current.king_name}\n鱼王窗口剩余：{remaining}\n鱼王窗口持续：{duration}\n鱼王窗口结束：{current.ends_at:%m月%d日 %H:%M}"
        else:
            state = "水面平静"
            if upcoming:
                until = format_duration((upcoming.starts_at - now).total_seconds())
                duration = format_duration((upcoming.ends_at - upcoming.starts_at).total_seconds())
                king_line = f"下一条鱼王：{upcoming.king_name}\n距离开始：{until}\n鱼王窗口持续：{duration}\n鱼王窗口开始：{upcoming.starts_at:%m月%d日 %H:%M}\n鱼王窗口结束：{upcoming.ends_at:%m月%d日 %H:%M}"
            else:
                king_line = "下一条鱼王：本周已结束"
        return f"钓鱼时钟（北京时间）\n当前：{now:%m月%d日 %H:%M}\n{zone_lines}\n当前状态：{state}\n{king_line}"

    def _render_clock_image(self, uid: str) -> Path:
        """Render the queried clock and the five nearest future king windows."""
        now = datetime.now()
        windows = sorted(self._fish_windows(), key=lambda value: value.starts_at)
        current = active_window(windows, now)
        upcoming = [item for item in windows if item.starts_at > now][:5]
        next_zone_at = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        current_zone = fishing_zone_label(daily_fishing_zone(now))
        next_zone = fishing_zone_label(daily_fishing_zone(next_zone_at))

        width = 1240
        margin = 42
        gap = 22
        card_width = (width - margin * 2 - gap * 2) // 3
        row_height = 78
        table_top = 390
        table_height = 124 + max(1, len(upcoming)) * row_height
        height = table_top + table_height + 42
        image = PILImage.new("RGBA", (width, height), (244, 248, 249, 255))
        draw = ImageDraw.Draw(image)
        header_height = 170
        draw.rounded_rectangle((0, 0, width, header_height), radius=26, fill=(33, 68, 82, 255))
        draw.rectangle((0, header_height - 20, width, header_height), fill=(33, 68, 82, 255))
        draw.text((margin, 32), "钓鱼时钟", fill=(250, 252, 252, 255), font=self._font(64, bold=True))
        draw.text((margin, 112), "活跃鱼点与鱼王窗口", fill=(190, 222, 225, 255), font=self._font(25))
        draw.text((width - margin, 116), f"北京时间 {now:%m月%d日 %H:%M}", fill=(220, 234, 235, 255), font=self._font(23), anchor="ra")

        card_y = header_height + 28
        card_titles = ("活跃鱼点", "下一活跃鱼点", "当前状态")
        card_values = (
            (current_zone, "今日持续至 24:00"),
            (next_zone, f"{next_zone_at:%m月%d日 %H:%M} 开始"),
            ("水面出现异动" if current else "水面平静", current.king_name if current else "当前没有鱼王窗口"),
        )
        accents = ((38, 144, 158), (78, 116, 176), (61, 145, 112) if current else (145, 155, 160))
        for index, (title, values, accent) in enumerate(zip(card_titles, card_values, accents)):
            x = margin + index * (card_width + gap)
            self._card(draw, (x, card_y, x + card_width, card_y + 142))
            draw.rounded_rectangle((x, card_y, x + 9, card_y + 142), radius=4, fill=(*accent, 255))
            draw.text((x + 28, card_y + 20), title, fill=(92, 112, 117, 255), font=self._font(23))
            draw.text((x + 28, card_y + 57), values[0], fill=(40, 61, 68, 255), font=self._font(33, bold=True))
            draw.text((x + 28, card_y + 106), values[1], fill=(92, 112, 117, 255), font=self._font(20))

        table_y = table_top
        self._card(draw, (margin, table_y, width - margin, table_y + table_height))
        draw.text((margin + 28, table_y + 22), "最近 5 次鱼王窗口", fill=(40, 61, 68, 255), font=self._font(34, bold=True))
        draw.text((width - margin - 28, table_y + 30), "按开始时间排序", fill=(92, 112, 117, 255), font=self._font(20), anchor="ra")
        header_y = table_y + 96
        columns = ((margin + 30, "序号"), (margin + 125, "鱼王"), (margin + 410, "开始"), (margin + 665, "结束"), (width - margin - 30, "距离开始"))
        for x, label in columns:
            draw.text((x, header_y), label, fill=(92, 112, 117, 255), font=self._font(21), anchor="rs" if x == width - margin - 30 else "ls")
        draw.line((margin + 26, table_y + 119, width - margin - 26, table_y + 119), fill=(205, 220, 222, 255), width=2)
        if upcoming:
            for index, window in enumerate(upcoming):
                y = table_y + 137 + index * row_height
                if index % 2 == 0:
                    draw.rounded_rectangle((margin + 20, y - 14, width - margin - 20, y + 46), radius=10, fill=(246, 250, 250, 255))
                until = format_duration((window.starts_at - now).total_seconds())
                draw.text((margin + 34, y), str(index + 1), fill=(70, 83, 88, 255), font=self._font(25))
                draw.text((margin + 125, y), f"{window.king_name}·{fishing_zone_label(FISH_KINGS.get(window.king_name, {}).get('zone', ''))}", fill=(40, 61, 68, 255), font=self._font(20, bold=True))
                draw.text((margin + 410, y), f"{window.starts_at:%m月%d日 %H:%M}", fill=(70, 83, 88, 255), font=self._font(23))
                draw.text((margin + 665, y), f"{window.ends_at:%H:%M}", fill=(70, 83, 88, 255), font=self._font(23))
                draw.text((width - margin - 34, y), until, fill=(38, 144, 158, 255), font=self._font(23, bold=True), anchor="ra")
        else:
            draw.text((margin + 30, table_y + 146), "本周暂无后续鱼王窗口", fill=(92, 112, 117, 255), font=self._font(25))

        output = Path(self.workspace) / "images" / f"clock_{uid}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, "PNG")
        return output

    def _render_help_image(self, uid: str, page: str = "") -> Path:
        """Render fishing help; the quick card is the default entry point."""
        width = 1600
        margin = 56
        gap = 32
        column_width = (width - margin * 2 - gap) // 2
        title_font = self._font(78, bold=True)
        subtitle_font = self._font(28)
        section_font = self._font(34, bold=True)
        body_font = self._font(25)
        small_font = self._font(22)
        measure = ImageDraw.Draw(PILImage.new("RGB", (1, 1)))

        def wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
            result: list[str] = []
            current = ""
            for char in text:
                if char == "\n":
                    result.append(current)
                    current = ""
                    continue
                candidate = current + char
                if current and measure.textbbox((0, 0), candidate, font=font)[2] > max_width:
                    result.append(current)
                    current = char
                else:
                    current = candidate
            if current:
                result.append(current)
            return result or [""]

        equipment_effects = {
            "财富浮标": "Ⅰ/Ⅱ/Ⅲ级：宝藏×1.35/1.50/1.65，鱼王咬钩×1.20/1.28/1.36",
            "坚韧钓组": "Ⅰ/Ⅱ/Ⅲ级：鱼王上钩率+12/16/20个百分点，最低成功率5%",
            "黄金磁石": "Ⅰ/Ⅱ/Ⅲ级：宝藏品质+1/2/3档；宝藏售价×1.25/1.35/1.50；普通鱼售价×1.08/1.12/1.16；后置鱼王售价×10/30/100，前置与常驻鱼王封顶×3/5/8",
            "魔法鱼饵": "等待时间×0.85，稀有鱼权重×1.25",
            "幸运海星": "Ⅰ/Ⅱ/Ⅲ级：普通碎片概率16%/20%/24%",
            "隐秘诱饵": "对应鱼点隐藏鱼触发率×1.50",
            "万能鱼饵": "每竿兜底一个普通鱼点或鱼饵限制",
        }
        rod_rows = []
        for name, (durability, price) in POLES.items():
            special = "  · 1竿1次掉落  · 工坊交付" if name == "三叉戟" else ""
            rod_rows.append(
                f"{name}  · 耐久{durability}  · 均价{price}金币  · Lv{rod_unlock_level(name)}{special}"
            )
        equipment_rows = []
        for name, info in EQUIPMENT.items():
            kind_text = "永久" if info["kind"] == "permanent" else f"{info['uses']}竿消耗品"
            equipment_rows.append(
                f"{name}  · {kind_text}  · {info['fragments']}碎片  · 手续费{EQUIPMENT_CRAFT_FEES[name]}金币  · {equipment_effects[name]}"
            )
        sections = [
            (
                "指令入口",
                list(MCFISH_COMMAND_ROWS_BASE),
                (38, 144, 164),
            ),
            (
                "新系统与帮助",
                list(MCFISH_COMMAND_ROWS_SYSTEMS),
                (176, 119, 64),
            ),
            (
                "核心循环",
                [
                    "每竿独立结算并按隐藏上钩时间发送结果；完成后显示上钩时间、结果和经验。",
                    "普通鱼直接结算；鱼王分咬钩和上钩两个阶段，回复选项会影响后续机会和鱼体大小。",
                    "鱼王窗口每周生成，只在对应鱼点和时间内生效；常驻鱼王随时可判定。",
                ],
                (86, 122, 174),
            ),
            (
                "等级与经验",
                [
                    "经验用于升级：1–35级沿用旧曲线（1→2级100点，之后每级+50）；36–60级每级1200点起（每级+40），61–100级每级2600点起（每级+60）；满级100级累计224250点。",
                    "等级上限100；每5级获得1个天赋点，100级额外获得5个（满级共25点）；每日次数按等级为50/60/70/80/100次。",
                    "鱼王成功获得300–900经验，隐藏鱼80–120经验，普通鱼5–50经验；脱钩不获得捕获经验。",
                    "同一玩家已有钓鱼任务时不能重复开始，其他玩家不受影响。",
                ],
                (112, 96, 164),
            ),
            (
                "任务与金币循环",
                [
                    "任务池分简单/中等/复杂，并按1-5、5-10、10-20、20-30、30+级分档；高等级任务目标更大，奖励会增加经验、金币、碎片、消耗品或鱼竿。",
                    "每日/每周任务从任务池排列组合生成；任务板可能保留1-5级低档任务，不想领取不会产生任何消耗。",
                    "达到60级并捕获全部8条限时鱼王后，工坊交付鱼王鳞片8个、时砂5个和7万金币可领取三叉戟。",
                    "修复鱼竿和合成会按等级收取金币手续费，消费任务只统计成功扣款。",
                ],
                (176, 119, 64),
            ),
            ("鱼竿与升级", rod_rows, (239, 145, 76)),
            (
                "碎片与装备",
                [
                    "碎片来源：普通鱼基础8%得1–2个，第13次保底；稀有鱼/宝藏22%得2–4个；鱼王成功必得5–9个。",
                    "另有0.1%碎片宝藏，额外获得50个碎片；碎片可兑换鱼竿或合成装备。",
                    "合成方式：发送“合成财富浮标”等对应命令；永久饰品需碎片和金币手续费，默认2格。",
                    "槽位扩展：10级+80碎片解锁第3格，20级+180碎片解锁第4格；升级不消耗金币。",
                    "饰品管理：发送“升级财富浮标”升级，发送“卸下财富浮标”取消装备；同名永久饰品不能重复装备。",
                    "鱼竿也可分解回收碎片：木1、铜2、锡4、铁8、猩红16、幻光24、星河32、灿阳42、三叉戟48；分解前会列出属性并确认。",
                    "消耗品合成后发送“装备魔法鱼饵”等命令启用，每完成1竿消耗1次；剩余次数可在钓鱼背包查看。",
                    "商店常驻出售诱钓500金币（5个）和海之眷顾1000金币（3个）；附魔成功率严格为50%，满3级不会消耗附魔书。",
                    *equipment_rows,
                ],
                (112, 96, 164),
            ),
            (
                "掉落与概率",
                [
                    "基础区间：宝藏1% / 鱼竿4% / 普通鱼25% / 垃圾30% / 空竿40%。",
                    "鱼总数超过100条时：宝藏2% / 鱼竿8% / 普通鱼35% / 垃圾45%，只改变概率不改变数量。",
                    "鱼竿段内占比：木55% / 铜18% / 锡10% / 铁7% / 猩红5% / 幻光3% / 星河1% / 灿阳1%。",
                    "当前鱼竿最多只能钓到下一等级；越级区间转为空竿。掉落鱼竿随机生成耐久、维修和附魔。",
                    "海之眷顾每级把6%空竿转为宝藏1%、鱼竿2%、鱼3%；诱钓按等级缩短等待。",
                    "隐藏鱼（同名鱼种在另一鱼点出现）：灰色沙丘曳尾鱼（深海）、魂灵面纱（遗迹水域）、岩石凶鲨（风暴海域）；需对应鱼点和隐秘诱饵，4.5%触发。",
                    "垃圾段内部按鱼点分为垃圾/材料/食物/贵重；贵重10%为钥匙箱。",
                ],
                (49, 132, 190),
            ),
            (
                "钓鱼力、鱼点与鱼王",
                [
                    "钓鱼力：等级 + 鱼竿 + 装备 + 首饰 + 饰品 + 天赋；发送“钓鱼力”查看构成。",
                    "钓鱼力同时影响等待与稀有度：等待×1/(1+FP/100)；鱼种权重=基础×min(上限,1+(FP−min_fp)/150)，上限按稀有度1.4/1.7/1.9/2.2。",
                    "鱼点自选：钓鱼鱼点 查看，前往 深海 切换；跨天不重置。",
                    "鱼点门槛按钓鱼力：河湾0 / 深海20 / 遗迹50 / 风暴80 / 熔岩110 / 极地135 / 荧光160 / 星陨190 / 时之220；每个鱼点2–7条普通鱼，按顺序+0/+0/+20/+45/+60/+75/+90。",
                    "单鱼点每日次数：河湾60 / 深海55 / 遗迹50 / 风暴45 / 熔岩40 / 极地40 / 荧光35 / 星陨35 / 时之30；与全局次数同时生效。",
                    "出售税按鱼点递增：河湾20% / 深海22% / 遗迹25% / 风暴28% / 熔岩32% / 极地34% / 荧光37% / 星陨40% / 时之45%；鱼竿、宝藏与非鱼点物品按20%。",
                    "常驻与前置鱼王的黄金磁石售价封顶 ×3/×5/×8，后置鱼王为 ×10/×30/×100。",
                    "鱼王只在对应鱼点的窗口内出现；窗口期间需要身处该鱼点。",
                    "鱼王窗口每周重新生成，位于09:00–21:00的非整点时段；前置和后置窗口可能重合。",
                    "后置鱼王需要前置鱼王咬钩过一次；咬钩阶段回复2可牺牲前置鱼王尝试后置目标，不回复默认保留。",
                    "鱼王路径只在成功上钩时累计；满足条件后下一次上钩率锁定95%，成功后路径清空且每周重置。",
                    "常驻鱼王：每个鱼点1条，钓鱼力达标后每竿独立判定，咬钩与上钩在同一次结算内完成，每日最多成功1条，不产出鱼王鳞片。",
                    "财富浮标提高鱼王咬钩线索；隐秘诱饵、万能鱼饵提高常驻鱼王咬钩率，三者都只提高概率，不保证上钩。",
                    "改版说明按系统拆成8页：钓鱼帮助 改版 页码；每页一个系统，单页不超过12行。",
                    "发送钓鱼时钟查看当前环境和最近5次窗口，不会主动通知。",
                ],
                (61, 145, 112),
            ),
            ("特殊机制", list(MCFISH_SPECIAL_MECHANICS), (229, 112, 118)),
        ]

        page_titles = {
            "": ("指令入口", "新系统与帮助", "核心循环", "等级与经验"),
            "快速": ("指令入口", "新系统与帮助", "核心循环", "等级与经验"),
            "鱼点": ("钓鱼力、鱼点与鱼王",),
            "装备": ("碎片与装备",),
            "鱼竿": ("鱼竿与升级",),
            "掉落": ("掉落与概率",),
            "任务": ("任务与金币循环",),
            "机制": ("特殊机制",),
            "全部": tuple(section[0] for section in sections),
        }
        page_ids = {
            "": "quick", "快速": "quick", "鱼点": "spots", "装备": "gear", "鱼竿": "rods",
            "掉落": "drops", "任务": "tasks", "机制": "mechanics", "全部": "all",
        }
        page_key = page if page in page_titles else ""
        selected_titles = page_titles[page_key]
        sections = [section for section in sections if section[0] in selected_titles]

        single_section = len(sections) == 1
        content_width = width - margin * 2 if single_section else column_width

        def prepare_section(title: str, rows: list[str], accent: tuple[int, int, int]):
            wrapped_rows: list[list[str]] = []
            for row in rows:
                row_lines = wrap(f"• {row}", body_font, content_width - 48 - 28)
                wrapped_rows.append(row_lines)
            line_height = 34
            height = 82 + sum(len(lines) * line_height + 10 for lines in wrapped_rows) + 34
            return (title, wrapped_rows, accent, height)

        prepared = [prepare_section(*section) for section in sections]
        # Pack whole cards into two near-equal columns. There are only seven
        # cards, so exhaustive partitioning keeps the layout stable when one
        # section grows without adding a general-purpose layout dependency.
        header_height = 230
        footer_height = 92
        if single_section:
            left_sections = prepared
            right_sections = []
            column_height = sum(section[3] + 22 for section in left_sections)
        else:
            section_totals = [section[3] + 22 for section in prepared]
            total_height = sum(section_totals)
            best_mask = 1
            best_difference = total_height
            for mask in range(1, 1 << len(prepared)):
                if not mask & 1:
                    continue
                left_height = sum(
                    section_totals[index]
                    for index in range(len(prepared))
                    if mask & (1 << index)
                )
                difference = abs(total_height - left_height * 2)
                if difference < best_difference:
                    best_mask = mask
                    best_difference = difference
            left_sections = [
                section for index, section in enumerate(prepared) if best_mask & (1 << index)
            ]
            right_sections = [
                section for index, section in enumerate(prepared) if not best_mask & (1 << index)
            ]
            column_height = max(
                sum(section[3] + 22 for section in column)
                for column in (left_sections, right_sections)
            )
        height = header_height + column_height + footer_height
        image = PILImage.new("RGBA", (width, height), (244, 248, 249, 255))
        draw = ImageDraw.Draw(image)

        draw.rounded_rectangle((0, 0, width, header_height), radius=28, fill=(33, 68, 82, 255))
        draw.rectangle((0, header_height - 22, width, header_height), fill=(33, 68, 82, 255))
        draw.text((margin, 42), "钓鱼帮助", font=title_font, fill=(250, 252, 252, 255))
        draw.text((margin, 137), f"McFish  ·  指令、掉落、鱼王与装备规则  ·  {page_key or '快速'}", font=subtitle_font, fill=(190, 222, 225, 255))
        draw.text(
            (width - margin - 470, 148),
            f"{len(POLES)} 种鱼竿  ·  {len(EQUIPMENT)} 件装备  ·  规则 V6.4",
            font=small_font,
            fill=(220, 234, 235, 255),
        )
        for index, name in enumerate(("木竿", "铁竿", "灿阳竿")):
            icon = self._resource_image(name, (86, 86))
            if icon:
                image.alpha_composite(icon, (width - margin - 310 + index * 105, 38))

        def draw_section(section, x: int, y: int) -> int:
            title, wrapped_rows, accent, card_height = section
            draw.rounded_rectangle(
                (x, y, x + content_width, y + card_height),
                radius=18,
                fill=(255, 255, 255, 255),
                outline=(222, 231, 233, 255),
                width=2,
            )
            draw.rounded_rectangle((x, y, x + 10, y + card_height), radius=4, fill=(*accent, 255))
            draw.text((x + 30, y + 24), title, font=section_font, fill=(40, 61, 68, 255))
            draw.line((x + 30, y + 72, x + content_width - 28, y + 72), fill=(*accent, 100), width=2)
            current_y = y + 92
            for lines in wrapped_rows:
                for line_index, line in enumerate(lines):
                    draw.text((x + 30 + (28 if line_index else 0), current_y), line, font=body_font, fill=(70, 83, 88, 255))
                    current_y += 34
                current_y += 10
            return y + card_height + 22

        y_left = header_height + 28
        for section in left_sections:
            y_left = draw_section(section, margin, y_left)
        y_right = header_height + 28
        for section in right_sections:
            y_right = draw_section(section, margin + content_width + gap, y_right)

        footer_y = height - footer_height
        draw.line((margin, footer_y + 10, width - margin, footer_y + 10), fill=(184, 202, 205, 255), width=2)
        draw.text(
            (margin, footer_y + 30),
            "图片分页：钓鱼帮助 鱼点/装备/鱼竿/掉落/任务/机制/全部　·　文字改版：钓鱼帮助 改版 1-8",
            font=small_font,
            fill=(92, 112, 117, 255),
        )

        output = Path(self.workspace) / "images" / f"help_{uid}_{page_ids[page_key]}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, "PNG")
        return output

    def _save(self, user_id: str, state: dict) -> None:
        self._db.execute("INSERT OR REPLACE INTO players(user_id, state, updated_at) VALUES (?, ?, ?)", (str(user_id), json.dumps(state, ensure_ascii=False), time.time()))
        self._db.commit()

    @staticmethod
    def _apply_state_delta(target: dict, base: dict, current: dict) -> None:
        """把 current 相对 base 的改动合并进 target，用于避免整份覆盖并发写入。"""
        for key, value in current.items():
            old = base.get(key, _MISSING)
            if isinstance(value, dict) and isinstance(old, dict):
                slot = target.get(key)
                if not isinstance(slot, dict):
                    slot = target[key] = {}
                McFish._apply_state_delta(slot, old, value)
            elif isinstance(value, list) and isinstance(old, list):
                target_list = target.get(key)
                if not isinstance(target_list, list):
                    target_list = []
                target[key] = McFish._merge_list_delta(old, value, target_list)
            elif isinstance(value, bool) or isinstance(old, bool):
                if old != value:
                    target[key] = value
            elif isinstance(value, (int, float)) and isinstance(old, (int, float)):
                held = target.get(key)
                target[key] = (held if isinstance(held, (int, float)) else 0) + value - old
            elif old != value:
                target[key] = value
        for key in base:
            if key not in current:
                target.pop(key, None)

    @staticmethod
    def _merge_list_delta(base: list, current: list, target: list) -> list:
        """按元素计数合并列表改动，保留 target 中并发新增的鱼竿/装备。"""

        def key(item):
            if isinstance(item, (dict, list)):
                return json.dumps(item, ensure_ascii=False, sort_keys=True)
            return repr(item)

        base_counts: dict[str, int] = {}
        current_counts: dict[str, int] = {}
        for item in base:
            base_counts[key(item)] = base_counts.get(key(item), 0) + 1
        sample: dict[str, object] = {}
        for item in current:
            token = key(item)
            current_counts[token] = current_counts.get(token, 0) + 1
            sample.setdefault(token, item)
        result = list(target)
        tokens = [key(item) for item in result]
        for token in set(base_counts) | set(current_counts):
            delta = current_counts.get(token, 0) - base_counts.get(token, 0)
            if delta > 0:
                result.extend([sample[token]] * delta)
                tokens.extend([token] * delta)
            elif delta < 0:
                for _ in range(min(-delta, tokens.count(token))):
                    index = len(tokens) - 1 - tokens[::-1].index(token)
                    tokens.pop(index)
                    result.pop(index)
        return result

    def _save_fishing_state(self, uid: str, state: dict, base: dict) -> dict:
        """钓鱼任务落盘时先读回最新存档，只把本次改动合并进去。

        后台钓鱼任务会跨分钟持有内存快照，直接整份写回会覆盖玩家在等待期间完成的
        购买、合成、出售或装备操作（2026-09-11 的丢鱼竿事故根因）。这里改成
        “读回最新状态 + 合并本次增量”，返回新的基准状态供下一次结算使用。
        """
        latest = self._state(uid)
        self._apply_state_delta(latest, base, state)
        self._save(uid, latest)
        return copy.deepcopy(state)

    @staticmethod
    def _record_capture(state: dict, name: str, *, caught_at: datetime | None = None) -> None:
        stats = state.setdefault("fish_stats", {})
        item = stats.setdefault(name, {"count": 0, "first_caught": None})
        item["count"] = int(item.get("count", 0)) + 1
        if not item.get("first_caught"):
            item["first_caught"] = (caught_at or datetime.now()).isoformat(timespec="seconds")

    def _grant_fishing_fragments(self, state: dict, source: str) -> int:
        state["fragment_jackpot"] = False
        if source == "king":
            amount = random.randint(5, 9)
            state["fragment_misses"] = 0
        elif source == "rare":
            amount = random.randint(2, 4) if random.random() < 0.22 else 0
            state["fragment_misses"] = 0 if amount else int(state.get("fragment_misses", 0)) + 1
        else:
            if fragment_jackpot():
                state["fragment_jackpot"] = True
                state["fragment_misses"] = 0
                self._add(state, "钓鱼碎片", 50)
                return 50
            fragment_rate = 0.08 + self._accessory_effect(
                state, "幸运海星", "fragment_points", 0
            ) / 100 + self._gear_effects(state).get("fragment", 0) / 100
            amount, misses = fragment_roll(int(state.get("fragment_misses", 0)), rate=fragment_rate)
            state["fragment_misses"] = misses
        if amount:
            self._add(state, "钓鱼碎片", amount)
        return amount

    def _record_attempt(self, uid: str, attempt_index: int, scheduled_at: datetime, rod_id: str, bait_id: str, window: FishKingWindow | None, result_type: str) -> None:
        if not hasattr(self, "_db"):
            return
        self._db.execute(
            "INSERT INTO fishing_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (uid, attempt_index, scheduled_at.isoformat(), rod_id, bait_id, window.king_name if window else None, result_type, time.time()),
        )

    def _economy_change(self, user_id: str, delta: int, reason: str) -> bool:
        economy = self._plugin_loader.get_plugin("GroupEconomy") if hasattr(self, "_plugin_loader") else None
        if economy is not None:
            self._economy = economy
        if not getattr(self, "_economy", None):
            return True
        return bool(self._economy.store.change_balance(user_id, delta, reason=reason)["ok"])

    def _charge_fee(self, user_id: str, amount: int, reason: str) -> bool:
        """Charge a production wallet; isolated direct unit tests may omit GroupEconomy."""
        if not getattr(self, "_economy", None):
            return True
        return self._economy_change(user_id, -int(amount), reason)

    def _discounted_fee(self, state: dict, fee: int) -> int:
        """天赋「巧手」降低修复/合成金币手续费，最低仍收 1 金币。"""
        value = max(0, int(fee))
        if value <= 0:
            return 0
        reduction = min(0.6, float(self._gear_effects(state).get("fees", 0.0)))
        return max(1, int(round(value * (1.0 - reduction))))

    @staticmethod
    def _task_period(kind: str, today: date | None = None) -> str:
        current = today or date.today()
        if kind == "weekly":
            current -= timedelta(days=current.weekday())
        return current.isoformat()

    @staticmethod
    def _task_level_tier(level: int) -> dict:
        level = max(1, int(level))
        return next(tier for tier in TASK_LEVEL_TIERS if tier["min"] <= level <= tier["max"])

    @staticmethod
    def _task_pool_candidates(kind: str, *, tier_id: str | None = None, difficulty: str | None = None, task_type: str | None = None) -> list[dict]:
        return [
            task for task in TASK_POOL
            if kind in task.get("requirements", {})
            and (tier_id is None or task["level_tier"] == tier_id)
            and (difficulty is None or task["difficulty"] == difficulty)
            and (task_type is None or task["type"] == task_type)
        ]

    @staticmethod
    def _task_content_key(task: dict, kind: str | None = None) -> tuple:
        requirements = task.get("requirements", {})
        if kind and isinstance(requirements.get(kind), dict):
            requirements = requirements[kind]
        return (
            task.get("type"),
            tuple(sorted((str(name), int(number)) for name, number in requirements.items())),
        )

    @staticmethod
    def _materialize_task(pool_task: dict, kind: str) -> dict:
        return {
            "id": f"{kind}_{pool_task['id']}",
            "pool_id": pool_task["id"],
            "type": pool_task["type"],
            "difficulty": pool_task["difficulty"],
            "difficulty_label": TASK_DIFFICULTY_LABELS[pool_task["difficulty"]],
            "level_tier": pool_task["level_tier"],
            "level_tier_label": next(
                tier["label"] for tier in TASK_LEVEL_TIERS if tier["id"] == pool_task["level_tier"]
            ),
            "label": pool_task["label"],
            "requirements": copy.deepcopy(pool_task["requirements"][kind]),
            "reward": copy.deepcopy(pool_task["rewards"][kind]),
        }

    def _task_definitions(self, kind: str, period: str, state: dict) -> list[dict]:
        """Compose a period board from the task pool and freeze it in player state."""
        level = int(state.get("fish_level", level_for_xp(int(state.get("fish_xp", 0)))))
        current_tier = self._task_level_tier(level)
        period_number = date.fromisoformat(period).toordinal()
        used_ids: set[str] = set()
        used_contents: set[tuple] = set()

        # The low-tier slot stays visible for every player so the board can contain
        # cheap tasks that a high-level player may deliberately ignore.
        slots = [("simple", "1-5", None), ("medium", current_tier["id"], None), ("complex", current_tier["id"], None)]
        if kind == "weekly":
            slots.append(("medium", current_tier["id"], "spend"))
        candidate_sets: list[list[dict]] = []
        for index, (difficulty, tier_id, task_type) in enumerate(slots):
            candidates = self._task_pool_candidates(
                kind, tier_id=tier_id, difficulty=difficulty, task_type=task_type
            )
            if task_type is None and kind == "weekly":
                # Keep a future slot's only possible entry available, while
                # allowing the current slot to use any task type otherwise.
                reserved: set[str] = set()
                for future_type, future_tier, future_task_type in slots[index + 1:]:
                    if not future_task_type:
                        continue
                    future_candidates = self._task_pool_candidates(
                        kind,
                        tier_id=future_tier,
                        difficulty=future_type,
                        task_type=future_task_type,
                    )
                    if len(future_candidates) == 1:
                        reserved.add(future_candidates[0]["id"])
                candidates = [candidate for candidate in candidates if candidate["id"] not in reserved]
            if not candidates and task_type:
                candidates = self._task_pool_candidates(kind, tier_id=tier_id, difficulty=difficulty)
            if not candidates:
                candidates = self._task_pool_candidates(kind, difficulty=difficulty)
            candidate_sets.append(candidates)

        def choose(index: int) -> list[dict] | None:
            if index == len(candidate_sets):
                return []
            candidates = candidate_sets[index]
            start = (period_number + index) % len(candidates)
            ordered = candidates[start:] + candidates[:start]
            for candidate in ordered:
                content_key = self._task_content_key(candidate, kind)
                if candidate["id"] in used_ids or content_key in used_contents:
                    continue
                used_ids.add(candidate["id"])
                used_contents.add(content_key)
                remainder = choose(index + 1)
                if remainder is not None:
                    return [candidate, *remainder]
                used_ids.remove(candidate["id"])
                used_contents.remove(content_key)
            return None

        selected = choose(0)
        if selected is None:
            raise ValueError(f"{kind} task pool cannot create unique task contents")
        materialized = [self._materialize_task(task, kind) for task in selected]
        if kind == "daily":
            # Keep daily rod income bounded even if more rod-reward entries are added later.
            rod_rewards = [
                task for task in materialized
                if any(name in POLES for name in task.get("reward", {}).get("items", {}))
            ]
            for task in rod_rewards[1:]:
                task["reward"]["items"] = {
                    name: number
                    for name, number in task["reward"].get("items", {}).items()
                    if name not in POLES
                }
        return materialized

    @staticmethod
    def _task_progress_template(tasks: list[dict]) -> dict:
        return {
            task["id"]: {name: 0 for name in task.get("requirements", {})}
            for task in tasks
        }

    @staticmethod
    def _refresh_permanent_task_progress(state: dict, bucket: dict) -> None:
        progress = bucket.setdefault("progress", {})
        task = bucket["tasks"][0]
        task_progress = progress.setdefault(task["id"], {})
        king_stats = state.get("king_stats", {})
        for name in task.get("requirements", {}):
            task_progress[name] = min(1, int(king_stats.get(name, {}).get("caught", 0)))

    def _ensure_permanent_task_state(self, state: dict) -> None:
        key = "fishing_permanent_tasks"
        bucket = state.get(key)
        if (
            not isinstance(bucket, dict)
            or bucket.get("version") != PERMANENT_TASK_STATE_VERSION
            or bucket.get("period") != "永久"
            or not isinstance(bucket.get("tasks"), list)
            or len(bucket["tasks"]) != 1
        ):
            task = copy.deepcopy(PERMANENT_TASK)
            bucket = {
                "version": PERMANENT_TASK_STATE_VERSION,
                "period": "永久",
                "tasks": [task],
                "progress": self._task_progress_template([task]),
                "claimed": [],
            }
            state[key] = bucket
        bucket.setdefault("claimed", [])
        self._refresh_permanent_task_progress(state, bucket)

    def _ensure_task_state(self, state: dict, today: date | None = None) -> None:
        self._ensure_permanent_task_state(state)
        for kind in ("daily", "weekly"):
            period = self._task_period(kind, today)
            key = f"fishing_{kind}_tasks"
            bucket = state.get(key)
            if (
                isinstance(bucket, dict)
                and bucket.get("period") == period
                and isinstance(bucket.get("tasks"), list)
                and bucket.get("version") != TASK_STATE_VERSION
            ):
                pool_by_id = {task["id"]: task for task in TASK_POOL}
                upgraded_tasks = []
                for task in bucket["tasks"]:
                    pool_task = pool_by_id.get(task.get("pool_id"))
                    if pool_task is None or kind not in pool_task.get("rewards", {}):
                        upgraded_tasks = []
                        break
                    upgraded = copy.deepcopy(task)
                    upgraded["reward"] = copy.deepcopy(pool_task["rewards"][kind])
                    upgraded_tasks.append(upgraded)
                if upgraded_tasks:
                    bucket["version"] = TASK_STATE_VERSION
                    bucket["tasks"] = upgraded_tasks
                    bucket.setdefault("progress", self._task_progress_template(upgraded_tasks))
                    bucket.setdefault("claimed", [])
                    state[key] = bucket
            if (
                not isinstance(bucket, dict)
                or bucket.get("version") != TASK_STATE_VERSION
                or bucket.get("period") != period
                or not isinstance(bucket.get("tasks"), list)
            ):
                tasks = self._task_definitions(kind, period, state)
                state[key] = {
                    "version": TASK_STATE_VERSION,
                    "period": period,
                    "tasks": tasks,
                    "progress": self._task_progress_template(tasks),
                    "claimed": [],
                }
                continue
            task_ids = [task.get("id") for task in bucket["tasks"]]
            pool_ids = [task.get("pool_id") for task in bucket["tasks"]]
            content_keys = [self._task_content_key(task) for task in bucket["tasks"]]
            if (
                len(task_ids) != len(set(task_ids))
                or len(pool_ids) != len(set(pool_ids))
                or len(content_keys) != len(set(content_keys))
            ):
                repaired_tasks = self._task_definitions(kind, period, state)
                old_progress = bucket.get("progress", {})
                old_claimed = set(bucket.get("claimed", []))
                state[key] = {
                    "version": TASK_STATE_VERSION,
                    "period": period,
                    "tasks": repaired_tasks,
                    "progress": {
                        task["id"]: copy.deepcopy(old_progress.get(task["id"], {}))
                        for task in repaired_tasks
                    },
                    "claimed": [
                        task["id"] for task in repaired_tasks if task["id"] in old_claimed
                    ],
                }
                continue
            bucket.setdefault("progress", self._task_progress_template(bucket["tasks"]))
            bucket.setdefault("claimed", [])

    @staticmethod
    def _task_complete(task: dict, progress: dict) -> bool:
        return all(
            int(progress.get(name, 0)) >= int(target)
            for name, target in task.get("requirements", {}).items()
        )

    def _advance_tasks(
        self,
        state: dict,
        *,
        casts: int = 0,
        catches: dict[str, int] | None = None,
        spend: int = 0,
        king: int = 0,
    ) -> None:
        self._ensure_task_state(state)
        catches = catches or {}
        changes = {"casts": max(0, int(casts)), "spend": max(0, int(spend)), "king": max(0, int(king))}
        for kind in ("daily", "weekly"):
            bucket = state[f"fishing_{kind}_tasks"]
            for task in bucket["tasks"]:
                task_type = task.get("type")
                if task_type == "delivery":
                    continue
                progress = bucket["progress"].setdefault(task["id"], {})
                for name, target in task.get("requirements", {}).items():
                    if task_type == "casts" and name == "次数":
                        amount = changes["casts"]
                    elif task_type == "spend" and name == "金币":
                        amount = changes["spend"]
                    elif task_type == "king" and name == "鱼王":
                        amount = changes["king"]
                    else:
                        amount = max(0, int(catches.get(name, 0))) if task_type == "catch" else 0
                    if amount:
                        progress[name] = min(int(target), int(progress.get(name, 0)) + amount)

    @staticmethod
    def _task_reward_text(reward: dict) -> str:
        parts = []
        if int(reward.get("xp", 0)):
            parts.append(f"经验+{int(reward['xp'])}")
        if int(reward.get("gold", 0)):
            parts.append(f"{int(reward['gold'])}金币")
        if int(reward.get("fragments", 0)):
            parts.append(f"{int(reward['fragments'])}个钓鱼碎片")
        for name, number in reward.get("items", {}).items():
            if int(number):
                parts.append(f"{name}×{int(number)}")
        return "、".join(parts) or "无"

    def _task_board(self, state: dict) -> list[tuple[int, str, dict, dict]]:
        self._ensure_task_state(state)
        board = []
        index = 1
        for kind in ("daily", "weekly"):
            bucket = state[f"fishing_{kind}_tasks"]
            for task in bucket["tasks"]:
                board.append((index, kind, bucket, task))
                index += 1
        return board

    @staticmethod
    def _task_display_progress(state: dict, task: dict, progress: dict) -> dict:
        """Show current fish in the backpack without treating it as submitted."""
        display = {
            name: min(int(target), int(progress.get(name, 0)))
            for name, target in task.get("requirements", {}).items()
        }
        if task.get("type") == "collection":
            king_stats = state.get("king_stats", {})
            return {
                name: min(1, int(king_stats.get(name, {}).get("caught", 0)))
                for name in task.get("requirements", {})
            }
        if task.get("type") != "delivery":
            return display
        pack = state.get("pack", {})
        for name in task.get("requirements", {}):
            display[name] = max(display[name], int(pack.get(name, 0)))
        return display

    @staticmethod
    def _task_period_text(kind: str, period: str) -> str:
        if kind == "permanent":
            return "永久"
        start = date.fromisoformat(period)
        end = start if kind == "daily" else start + timedelta(days=6)
        return f"{start:%m月%d日}" if kind == "daily" else f"{start:%m月%d日}-{end:%m月%d日}"

    def _task_lines(self, state: dict, kind: str, start_index: int = 1) -> list[str]:
        self._ensure_task_state(state)
        bucket = state[f"fishing_{kind}_tasks"]
        tasks = bucket["tasks"]
        period_text = self._task_period_text(kind, bucket["period"])
        lines = [f"{TASK_KIND_LABELS[kind]}（{period_text}）"]
        for offset, task in enumerate(tasks):
            task_index = start_index + offset
            progress = bucket["progress"].setdefault(task["id"], {})
            display_progress = self._task_display_progress(state, task, progress)
            if task["id"] in bucket["claimed"]:
                status = "已领取"
            else:
                if task.get("type") == "collection":
                    collected = sum(
                        int(display_progress.get(name, 0))
                        for name in task.get("requirements", {})
                    )
                    total = len(task.get("requirements", {}))
                    missing = [
                        name for name, target in task.get("requirements", {}).items()
                        if int(display_progress.get(name, 0)) < int(target)
                    ]
                    status = f"已获得鱼王 {collected}/{total}（只检查获得记录，不上交鱼王）"
                    if missing:
                        status += "；还缺 " + "、".join(missing)
                else:
                    requirement_text = "、".join(
                        (
                            f"钓鱼次数 {display_progress.get(name, 0)}/{int(target)}"
                            if name == "次数" else
                            f"消费金币 {display_progress.get(name, 0)}/{int(target)}"
                            if name == "金币" else
                            f"{name} {display_progress.get(name, 0)}/{int(target)}"
                        )
                        for name, target in task.get("requirements", {}).items()
                    )
                    status = requirement_text
            tier_text = f"[{task.get('difficulty_label', TASK_DIFFICULTY_LABELS['simple'])}·{task.get('level_tier_label', task.get('level_tier', '未分档'))}]"
            lines.append(
                f"{task_index}. {tier_text}{task['label']}：{status} · 奖励 {self._task_reward_text(task['reward'])}"
            )
        return lines

    async def _show_tasks(self, event, state: dict) -> None:
        self._ensure_task_state(state)
        self._save(str(event.sender.user_id), state)
        daily_count = len(state["fishing_daily_tasks"]["tasks"])
        level = int(state.get("fish_level", 1))
        lines = [
            f"钓鱼任务（当前等级 {level}）",
            "操作：领取钓鱼任务 编号（每日/每周任务会检查并提交鱼获）",
            *self._task_lines(state, "daily", 1),
            "",
            *self._task_lines(state, "weekly", daily_count + 1),
        ]
        await event.reply(text="\n".join(lines))

    async def _claim_tasks(self, event, state: dict, task_index: int | None = None) -> None:
        uid = str(event.sender.user_id)
        self._ensure_task_state(state)
        board = self._task_board(state)
        if task_index is not None and not any(index == task_index for index, *_ in board):
            await event.reply(text=f"任务编号不存在，请发送“钓鱼任务”查看有效编号（1-{len(board)}）")
            return
        claimed: list[str] = []
        total_xp = 0
        total_gold = 0
        total_fragments = 0
        total_items: dict[str, int] = {}
        failed_delivery: list[str] = []
        for index, kind, bucket, task in board:
            if task_index is not None and index != task_index:
                continue
            progress = bucket["progress"].setdefault(task["id"], {})
            if task["id"] in bucket["claimed"]:
                continue
            if task.get("type") == "delivery":
                missing = {
                    name: int(target) - int(state.get("pack", {}).get(name, 0))
                    for name, target in task.get("requirements", {}).items()
                    if int(state.get("pack", {}).get(name, 0)) < int(target)
                }
                if missing:
                    failed_delivery.append(
                        f"任务{index}缺少：" + "、".join(f"{name}×{number}" for name, number in missing.items())
                    )
                    continue
            elif not self._task_complete(task, progress):
                continue
            reward = task["reward"]
            gold = int(reward.get("gold", 0))
            reason = f"mcfish_task_{kind}_{bucket['period']}_{task['id']}"
            if gold and not self._economy_change(uid, gold, reason):
                continue
            if task.get("type") == "delivery":
                for name, target in task.get("requirements", {}).items():
                    self._remove(state, name, int(target))
                    progress[name] = int(target)
            xp = int(reward.get("xp", 0))
            if xp:
                self._add_fishing_xp(state, xp)
                total_xp += xp
            fragments = int(reward.get("fragments", 0))
            if fragments:
                self._add(state, "钓鱼碎片", fragments)
            for name, number in reward.get("items", {}).items():
                number = int(number)
                if number:
                    if name in POLES:
                        for _ in range(number):
                            if kind == "permanent" and name == "三叉戟":
                                self._add(
                                    state,
                                    name,
                                    pole=True,
                                    pole_data={
                                        "durable": POLES[name][0],
                                        "maintenance": 0,
                                        "induce": 0,
                                        "favor": 0,
                                        "unbreakable": True,
                                    },
                                )
                            else:
                                self._add(state, name, pole=True)
                    else:
                        self._add(state, name, number)
                    total_items[name] = total_items.get(name, 0) + number
            bucket["claimed"].append(task["id"])
            claimed.append(task["label"])
            total_gold += gold
            total_fragments += fragments
        self._save(uid, state)
        if not claimed:
            suffix = f"任务{task_index}" if task_index is not None else ""
            if failed_delivery:
                await event.reply(text="\n".join(failed_delivery) + "\n鱼获不足，本次未领取成功")
            else:
                await event.reply(text=f"当前没有已完成且未领取的钓鱼任务{suffix}")
            return
        reward_text = []
        if total_xp:
            reward_text.append(f"经验+{total_xp}")
        if total_gold:
            reward_text.append(f"金币+{total_gold}")
        if total_fragments:
            reward_text.append(f"钓鱼碎片+{total_fragments}")
        reward_text.extend(f"{name}×{number}" for name, number in total_items.items())
        response = f"已领取：{'、'.join(claimed)}\n获得：{'、'.join(reward_text)}"
        if failed_delivery:
            response += "\n未领取：" + "；".join(failed_delivery)
        await event.reply(text=response)

    @staticmethod
    def _repair_fee(pole_name: str, durable: int) -> int:
        maximum = POLES[pole_name][0]
        missing = max(0, maximum - int(durable))
        return min(2000, max(20, POLES[pole_name][1] // 10 + missing // 5))

    @staticmethod
    def _craft_fee(name: str, mode: str, groups: int = 1) -> int:
        if mode == "fragments":
            base = max(20, POLE_FRAGMENT_COSTS[name] // 3) if name in POLE_FRAGMENT_COSTS else 20
        elif mode == "poles":
            base = max(20, POLES[name][1] // 10)
        else:
            base = EQUIPMENT_CRAFT_FEES[name]
        return base * max(1, int(groups))

    def _load_original_data(self) -> None:
        """读取 ZeroBot 原插件的物品表；资源缺失时保留内置兼容数据。"""
        path = Path(__file__).parent / "resources" / "articlesInfo.json"
        self._catalog = {"zones": [], "items": []}
        if path.is_file():
            try:
                self._catalog = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self.logger.warning("无法读取原 McFish 物品表: %s", path)
        self._prices = (
            dict(FISH)
            | {name: price for name, (_, price) in POLES.items()}
            | {name: int(item["base_price"]) for name, item in KING_INFO.items()}
            | ITEMS
            | SHOP_EQUIPMENT_PRICES
        )
        self._types = {}
        for name, item in HIDDEN_FISH.items():
            # 隐藏鱼与普通鱼共用同一个鱼种价格：不能用隐藏价格覆盖普通鱼。
            self._prices.setdefault(name, int(item["price"]))
            self._types[name] = "fish"
        self._wastes = ["垃圾"]
        self._thing_order = []
        for item in self._catalog.get("物品", []):
            name = str(item.get("名称", ""))
            if not name:
                continue
            if name != "宝藏诅咒" and name not in self._thing_order:
                self._thing_order.append(name)
            self._prices[name] = int(item.get("价格", self._prices.get(name, 0)))
            self._types[name] = str(item.get("类型", ""))
            if self._types[name] == "waste":
                self._wastes.append(name)
        # 鱼、鱼王、鱼竿的价格以运行时规则为准，旧目录只补缺，不能覆盖。
        for name, price in FISH.items():
            self._prices[name] = int(price)
        for name, item in KING_INFO.items():
            self._prices[name] = int(item["base_price"])
        for name, (_, price) in POLES.items():
            self._prices[name] = int(price)
        for name, item in HIDDEN_FISH.items():
            self._prices.setdefault(name, int(item["price"]))
        # P4：材料 / 食物 / 贵重品 / 钥匙箱子 / 卡牌。
        # 只登记价格与类型，不把钥匙、箱子和卡牌放进 _prices，保证它们不可出售。
        for name, price in ITEM_PRICES.items():
            self._prices.setdefault(name, price)
            if name not in self._types:
                self._types[name] = (
                    "material" if name in MATERIAL_NAMES
                    else "food" if name in FOOD_RESTORE
                    else "valuable"
                )
            if name not in self._thing_order:
                self._thing_order.append(name)
        for name in KEY_NAMES.values():
            self._types.setdefault(name, "key")
            if name not in self._thing_order:
                self._thing_order.append(name)
        for name in BOX_NAMES.values():
            self._types.setdefault(name, "box")
            if name not in self._thing_order:
                self._thing_order.append(name)
        for name in CARD_NAMES:
            self._types.setdefault(name, "card")
            if name not in self._thing_order:
                self._thing_order.append(name)
        valuable_names = {name for table in ZONE_VALUABLES.values() for name in table}
        for name in PIECE_INFO:
            # 兼作贵重品的首饰保持 valuable，可正常出售；纯装备部件归入 equipment。
            if name in valuable_names:
                self._types.setdefault(name, "valuable")
            else:
                self._types[name] = "equipment"
            if name not in self._thing_order:
                self._thing_order.append(name)
        self._types.setdefault("鱼王鳞片", "material")
        if "鱼王鳞片" not in self._thing_order:
            self._thing_order.append("鱼王鳞片")

    @staticmethod
    def _shop_custom_price_items(raw_prices: object, inventory: dict) -> dict[str, int]:
        if not isinstance(raw_prices, dict):
            return {}
        result: dict[str, int] = {}
        for raw_name, raw_price in raw_prices.items():
            name = str(raw_name)
            if name not in inventory and not (name == "木竿" and "初始木竿" in inventory):
                continue
            try:
                price = int(raw_price)
            except (TypeError, ValueError):
                continue
            if price > 0:
                result[name] = price
        return result

    def _shop_data(self) -> tuple[dict[str, int], dict[str, int]]:
        """返回当天价格倍率和跨天延续的库存。"""
        today = date.today().isoformat()
        prices = getattr(
            self,
            "_prices",
            dict(FISH) | {name: price for name, (_, price) in POLES.items()} | ITEMS,
        )
        prices.update(SHOP_EQUIPMENT_PRICES)
        prices.update(SHOP_ROD_PRICES)
        if not hasattr(self, "_db"):
            self._shop_custom_prices = {}
            discounts = {
                name: random.randint(50, 199)
                for name in prices
                if name not in ("垃圾", "宝藏诅咒") and name not in HIDDEN_FISH
            }
            discounts.update({"木竿": 100, **{name: 100 for name in SHOP_EQUIPMENT_PRICES}, **{name: 100 for name in SHOP_ROD_PRICES}})
            fish = random.choice(list(FISH))
            inventory = {"初始木竿": [{"durable": 30, "maintenance": 0, "induce": 0, "favor": 0}], fish: max(1, 100 - discounts.get(fish, 100))}
            inventory.update(SHOP_EQUIPMENT_STOCK)
            return discounts, inventory
        try:
            row = self._db.execute(
                "SELECT discounts, inventory, custom_prices FROM shop WHERE day = ?",
                (today,),
            ).fetchone()
            has_custom_prices = True
        except sqlite3.OperationalError as exc:
            if "no such column: custom_prices" not in str(exc):
                raise
            row = self._db.execute(
                "SELECT discounts, inventory FROM shop WHERE day = ?", (today,)
            ).fetchone()
            self._db.execute(
                "ALTER TABLE shop ADD COLUMN custom_prices TEXT NOT NULL DEFAULT '{}'"
            )
            self._db.commit()
            has_custom_prices = row is None
        if row:
            discounts = self._rename_shop_mapping(json.loads(row[0]))
            inventory = self._rename_shop_mapping(json.loads(row[1]))
            try:
                custom_prices = json.loads(row[2] or "{}") if has_custom_prices else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                custom_prices = {}
            self._shop_custom_prices = self._rename_shop_mapping(
                self._shop_custom_price_items(custom_prices, inventory)
            )
            listed_equipment = {name: name in inventory for name in SHOP_EQUIPMENT_STOCK}
            if "初始木竿" in inventory and isinstance(inventory["初始木竿"], int):
                inventory["初始木竿"] = self._shop_poles(inventory["初始木竿"], "木竿")
            inventory = self._normalize_shop_inventory(inventory)
            self._shop_custom_prices = {
                name: price
                for name, price in self._shop_custom_prices.items()
                if name in inventory or (name == "木竿" and "初始木竿" in inventory)
            }
            for name in inventory:
                if name != "初始木竿":
                    discounts.setdefault(name, 100)
            inventory.setdefault("初始木竿", [{"durable": 30, "maintenance": 0, "induce": 0, "favor": 0}])
            for name, stock in SHOP_EQUIPMENT_STOCK.items():
                if not listed_equipment[name]:
                    inventory[name] = stock
            discounts.setdefault("木竿", 100)
            discounts.update({name: 100 for name in SHOP_EQUIPMENT_PRICES})
            discounts.update({name: 100 for name in SHOP_ROD_PRICES})
            self._db.execute(
                "UPDATE shop SET inventory = ? WHERE day = ?",
                (json.dumps(inventory, ensure_ascii=False), today),
            )
            self._db.commit()
            return discounts, inventory
        try:
            previous = self._db.execute(
                "SELECT discounts, inventory, custom_prices FROM shop ORDER BY day DESC LIMIT 1"
            ).fetchone()
            has_custom_prices = True
        except sqlite3.OperationalError as exc:
            if "no such column: custom_prices" not in str(exc):
                raise
            previous = self._db.execute(
                "SELECT discounts, inventory FROM shop ORDER BY day DESC LIMIT 1"
            ).fetchone()
            self._db.execute(
                "ALTER TABLE shop ADD COLUMN custom_prices TEXT NOT NULL DEFAULT '{}'"
            )
            self._db.commit()
            has_custom_prices = True
        previous_inventory = json.loads(previous[1]) if previous else {}
        try:
            previous_custom_prices = (
                json.loads(previous[2] or "{}") if previous and has_custom_prices else {}
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            previous_custom_prices = {}
        if "初始木竿" in previous_inventory and isinstance(previous_inventory["初始木竿"], int):
            previous_inventory["初始木竿"] = self._shop_poles(previous_inventory["初始木竿"], "木竿")
        previous_inventory = self._normalize_shop_inventory(previous_inventory)
        discounts = {}
        for name in prices:
            if name in ("垃圾", "宝藏诅咒") or name in HIDDEN_FISH:
                continue
            count = previous_inventory.get(name, 0)
            count = len(count) if isinstance(count, list) else int(count or 0)
            discounts[name] = random.randint(90, 109) if count > 150 else random.randint(50, 199)
        discounts["木竿"] = 100
        discounts.update({name: 100 for name in SHOP_EQUIPMENT_PRICES})
        discounts.update({name: 100 for name in SHOP_ROD_PRICES})
        inventory = dict(previous_inventory)
        self._shop_custom_prices = self._shop_custom_price_items(
            previous_custom_prices, inventory
        )
        fish = random.choice(list(FISH))
        inventory[fish] = int(inventory.get(fish, 0)) + 100 - discounts.get(fish, 100)
        if inventory[fish] < 1:
            inventory[fish] = 100
        wood = self._shop_poles(inventory.get("初始木竿", []), "木竿")
        wood.append({"durable": 30, "maintenance": 0, "induce": 0, "favor": 0})
        inventory["初始木竿"] = wood if len(wood) <= 5 else wood[-1:]
        for name, stock in SHOP_EQUIPMENT_STOCK.items():
            inventory.setdefault(name, stock)
        for name in inventory:
            if name != "初始木竿":
                discounts.setdefault(name, 100)
        self._db.execute(
            "INSERT INTO shop(day, discounts, inventory, custom_prices) VALUES (?, ?, ?, ?)",
            (
                today,
                json.dumps(discounts, ensure_ascii=False),
                json.dumps(inventory, ensure_ascii=False),
                json.dumps(self._shop_custom_prices, ensure_ascii=False),
            ),
        )
        self._db.commit()
        return discounts, inventory

    @staticmethod
    def _rename_shop_mapping(mapping: dict) -> dict:
        """旧鱼名合并到新鱼名，避免商店里继续出现白色珊瑚螈这类历史数据。"""
        result: dict = {}
        for name, value in mapping.items():
            target = FISH_RENAMES.get(str(name), str(name))
            if target in result and isinstance(result[target], int) and isinstance(value, int):
                result[target] += value
            else:
                result.setdefault(target, value)
        return result

    @staticmethod
    def _normalize_shop_inventory(inventory: dict) -> dict:
        """Remove empty listings so the display and purchase lookup share one inventory."""
        normalized = dict(inventory)
        for name, value in list(normalized.items()):
            if name == "初始木竿" or name in POLES:
                poles = McFish._shop_poles(value, "木竿" if name == "初始木竿" else name)
                if poles:
                    normalized[name] = poles
                else:
                    normalized.pop(name, None)
            else:
                try:
                    if int(value or 0) <= 0:
                        normalized.pop(name, None)
                except (TypeError, ValueError):
                    normalized.pop(name, None)
        return normalized

    def _shop_price(self, name: str, discounts: dict[str, int]) -> int:
        custom_prices = getattr(self, "_shop_custom_prices", {})
        if name in custom_prices:
            return int(custom_prices[name])
        prices = getattr(self, "_prices", dict(FISH) | {pole: price for pole, (_, price) in POLES.items()} | ITEMS)
        if name == "初始木竿":
            return SHOP_WOOD_PRICE
        if name == "木竿":
            return SHOP_WOOD_PRICE
        if name in SHOP_EQUIPMENT_PRICES:
            return SHOP_EQUIPMENT_PRICES[name]
        if name in SHOP_ROD_PRICES:
            return SHOP_ROD_PRICES[name]
        return prices.get(name, ITEMS.get(name, 0)) * discounts.get(name, 100) // 100

    def _item_zone(self, name: str, state: dict | None = None) -> str | None:
        if state:
            recorded = (state.get("item_zones") or {}).get(name)
            if recorded:
                return str(recorded)
        zones: list[str] = []
        for zone, names in FISH_ZONE_WEIGHTS.items():
            if name in names:
                zones.append(zone)
        for table in (ZONE_VALUABLES, ZONE_FOODS):
            for zone, names in table.items():
                if name in names:
                    zones.append(zone)
        for zone, rows in ZONE_MATERIALS.items():
            if any(name == row[0] for row in rows):
                zones.append(zone)
        if name in KING_INFO:
            zones.append(str(KING_INFO[name].get("zone", "")))
        zones = [zone for zone in zones if zone]
        if not zones:
            return None
        return min(zones, key=lambda zone: FISHING_ZONE_SELL_TAX.get(zone, 0.20))

    @staticmethod
    def _record_item_zone(state: dict, name: str, zone: str) -> None:
        if zone:
            state.setdefault("item_zones", {})[name] = str(zone)

    def _sale_tax_factor(self, name: str, state: dict | None = None) -> float:
        zone = self._item_zone(name, state)
        if zone is not None:
            return 1.0 - FISHING_ZONE_SELL_TAX.get(zone, 0.20)
        return float(self.get_config("sell_tax", 0.8))

    def _pole_price(self, pole: dict, discounts: dict[str, int], name: str = "木竿") -> int:
        if pole.get("craftable", True) is False:
            # Shop rods are sold back below their fixed purchase price. Their
            # generated attributes must not create an arbitrage route.
            return self._shop_price(name, discounts)
        base_price = POLES[name][1]
        value = base_price - (POLES[name][0] - int(pole.get("durable", 0))) - int(pole.get("maintenance", 0)) * 2
        enchant_levels = int(pole.get("induce", 0)) + int(pole.get("favor", 0))
        value += base_price * min(3, max(0, enchant_levels)) // 2
        return max(0, value * discounts.get(name, 100) // 100)

    def _repair_abnormal_pole_trades(self) -> None:
        """Reverse the confirmed 2026-09-07 pole trades made with the bad pricing."""
        migration_id = "mcfish-pole-price-reversal-2026-09-07"
        if self._db.execute("SELECT 1 FROM migrations WHERE migration_id = ?", (migration_id,)).fetchone():
            return

        repairs = {
            "794383252": {
                "balance": 311,
                "poles": [],
                "remove_equipped_pole": True,
                "trade_count": 1,
            },
            "1908545345": {
                "balance": -7127,
                "poles": [
                    ("木竿", {"durable": 4, "maintenance": 6, "induce": 0, "favor": 0}),
                    ("木竿", {"durable": 3, "maintenance": 1, "induce": 2, "favor": 0}),
                    ("木竿", {"durable": 30, "maintenance": 1, "induce": 1, "favor": 1}),
                ],
                "trade_count": 3,
            },
            "786405040": {
                "balance": -3046,
                "poles": [("木竿", {"durable": 29, "maintenance": 8, "induce": 0, "favor": 1})],
                "trade_count": 1,
            },
            "3230201299": {
                "balance": -2605,
                "poles": [("木竿", {"durable": 28, "maintenance": 8, "induce": 2, "favor": 0})],
                "trade_count": 1,
            },
            "550500903": {
                "balance": -1510,
                "poles": [("猩红竿", {"durable": 1, "maintenance": 0, "induce": 0, "favor": 0})],
                "trade_count": 1,
            },
        }
        old_states: dict[str, str] = {}
        new_states: dict[str, str] = {}
        try:
            for user_id, repair in repairs.items():
                row = self._db.execute("SELECT state FROM players WHERE user_id = ?", (user_id,)).fetchone()
                if not row:
                    continue
                state = json.loads(row[0])
                old_states[user_id] = json.dumps(state, ensure_ascii=False, sort_keys=True)
                for pole_name, pole in repair["poles"]:
                    state.setdefault("pack", {}).setdefault(pole_name, []).append(pole)
                if repair.get("remove_equipped_pole") and (state.get("equip") or {}).get("name") in POLES:
                    state["equip"] = None
                state["trade_count"] = max(0, int(state.get("trade_count", 0)) - int(repair["trade_count"]))
                state["pole_trade_count"] = max(0, int(state.get("pole_trade_count", 0)) - int(repair["trade_count"]))
                new_states[user_id] = json.dumps(state, ensure_ascii=False, sort_keys=True)

            for user_id, repair in repairs.items():
                if user_id not in new_states:
                    continue
                if repair["balance"]:
                    balance_reason = f"{migration_id}:balance"
                    economy_store = getattr(self._economy, "store", None)
                    already_applied = economy_store is not None and economy_store._conn.execute(
                        "SELECT 1 FROM ledger WHERE user_id = ? AND reason = ? LIMIT 1",
                        (user_id, balance_reason),
                    ).fetchone()
                    if not already_applied and not self._economy_change(user_id, int(repair["balance"]), balance_reason):
                        raise RuntimeError(f"无法回滚用户 {user_id} 的鱼竿交易余额")
                self._db.execute(
                    "UPDATE players SET state = ?, updated_at = ? WHERE user_id = ?",
                    (new_states[user_id], time.time(), user_id),
                )

            row = self._db.execute("SELECT inventory FROM shop WHERE day = ?", (date.today().isoformat(),)).fetchone()
            if row:
                inventory = json.loads(row[0])
                initial_wood = self._shop_poles(inventory.get("初始木竿", []), "木竿")
                initial_wood.append({"durable": 30, "maintenance": 0, "induce": 0, "favor": 0})
                inventory["初始木竿"] = initial_wood
                for pole_name, pole in repairs["1908545345"]["poles"] + repairs["786405040"]["poles"] + repairs["3230201299"]["poles"]:
                    poles = self._shop_poles(inventory.get(pole_name, []), pole_name)
                    for index, candidate in enumerate(poles):
                        if candidate == pole:
                            del poles[index]
                            break
                    inventory[pole_name] = poles
                self._db.execute(
                    "UPDATE shop SET inventory = ? WHERE day = ?",
                    (json.dumps(inventory, ensure_ascii=False), date.today().isoformat()),
                )

            for user_id, old_state in old_states.items():
                self._db.execute(
                    "INSERT INTO admin_adjustments(user_id, reason, old_state, new_state, created_at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, migration_id, old_state, new_states[user_id], time.time()),
                )
            self._db.execute(
                "INSERT INTO migrations(migration_id, applied_at, details) VALUES (?, ?, ?)",
                (migration_id, time.time(), json.dumps({"users": sorted(new_states)}, ensure_ascii=False)),
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    def _repair_abnormal_pole_inventory(self) -> None:
        """Remove sold poles left duplicated by legacy name fields in shop rows."""
        migration_id = "mcfish-pole-price-reversal-inventory-cleanup-2026-09-07"
        if self._db.execute("SELECT 1 FROM migrations WHERE migration_id = ?", (migration_id,)).fetchone():
            return
        sold_poles = [
            ("木竿", {"durable": 30, "maintenance": 1, "induce": 1, "favor": 1}),
            ("木竿", {"durable": 29, "maintenance": 8, "induce": 0, "favor": 1}),
        ]
        row = self._db.execute("SELECT inventory FROM shop WHERE day = ?", (date.today().isoformat(),)).fetchone()
        if not row:
            return
        inventory = json.loads(row[0])
        for pole_name, expected in sold_poles:
            poles = self._shop_poles(inventory.get(pole_name, []), pole_name)
            for index, candidate in enumerate(poles):
                if all(int(candidate.get(key, 0)) == value for key, value in expected.items()):
                    del poles[index]
                    break
            inventory[pole_name] = poles
        self._db.execute(
            "UPDATE shop SET inventory = ? WHERE day = ?",
            (json.dumps(inventory, ensure_ascii=False), date.today().isoformat()),
        )
        self._db.execute(
            "INSERT INTO migrations(migration_id, applied_at, details) VALUES (?, ?, ?)",
            (migration_id, time.time(), json.dumps({"removed": sold_poles}, ensure_ascii=False)),
        )
        self._db.commit()

    def _repair_abnormal_equipment(self) -> None:
        """Finish the 2026-09-07 rollback for the user's retained equipped pole."""
        migration_id = "mcfish-pole-price-reversal-equipment-fix-2026-09-07"
        if self._db.execute("SELECT 1 FROM migrations WHERE migration_id = ?", (migration_id,)).fetchone():
            return

        user_id = "794383252"
        expected_equipment = {
            "name": "木竿",
            "durable": 3,
            "maintenance": 5,
            "induce": 3,
            "favor": 2,
        }
        row = self._db.execute("SELECT state FROM players WHERE user_id = ?", (user_id,)).fetchone()
        shop_row = self._db.execute(
            "SELECT inventory FROM shop WHERE day = ?", (date.today().isoformat(),)
        ).fetchone()
        if not row or not shop_row:
            return

        state = json.loads(row[0])
        equipment = state.get("equip") or {}
        if not all(equipment.get(key) == value for key, value in expected_equipment.items()):
            self._db.execute(
                "INSERT INTO migrations(migration_id, applied_at, details) VALUES (?, ?, ?)",
                (migration_id, time.time(), json.dumps({"removed": False}, ensure_ascii=False)),
            )
            self._db.commit()
            return

        old_state = json.dumps(state, ensure_ascii=False, sort_keys=True)
        state["equip"] = None
        new_state = json.dumps(state, ensure_ascii=False, sort_keys=True)
        inventory = json.loads(shop_row[0])
        initial_wood = self._shop_poles(inventory.get("初始木竿", []), "木竿")
        initial_wood.append({"durable": 30, "maintenance": 0, "induce": 0, "favor": 0})
        inventory["初始木竿"] = initial_wood
        try:
            self._db.execute(
                "UPDATE players SET state = ?, updated_at = ? WHERE user_id = ?",
                (new_state, time.time(), user_id),
            )
            self._db.execute(
                "UPDATE shop SET inventory = ? WHERE day = ?",
                (json.dumps(inventory, ensure_ascii=False), date.today().isoformat()),
            )
            self._db.execute(
                "INSERT INTO admin_adjustments(user_id, reason, old_state, new_state, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, migration_id, old_state, new_state, time.time()),
            )
            self._db.execute(
                "INSERT INTO migrations(migration_id, applied_at, details) VALUES (?, ?, ?)",
                (migration_id, time.time(), json.dumps({"removed": True}, ensure_ascii=False)),
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    def _reclaim_excessive_drops(self, migration_id: str, targets: dict[str, dict]) -> None:
        """Deduct the given items/XP from players and record the adjustment once per migration_id."""
        if self._db.execute("SELECT 1 FROM migrations WHERE migration_id = ?", (migration_id,)).fetchone():
            return
        details: dict[str, dict] = {}
        try:
            for user_id, repair in targets.items():
                row = self._db.execute("SELECT state FROM players WHERE user_id = ?", (user_id,)).fetchone()
                if not row:
                    continue
                state = json.loads(row[0])
                old_state = json.dumps(state, ensure_ascii=False, sort_keys=True)
                pack = state.setdefault("pack", {})
                removed: dict[str, int] = {}
                for name, amount in repair["items"].items():
                    target = FISH_RENAMES.get(name, name)
                    # 旧存档可能还停在旧鱼名上；优先扣新名，没有再从旧名扣。
                    key = target if int(pack.get(target, 0) or 0) else name
                    held = int(pack.get(key, 0) or 0)
                    deducted = min(held, int(amount))
                    if deducted:
                        pack[key] = held - deducted
                        removed[target] = int(removed.get(target, 0)) + deducted
                excess_xp = min(max(0, int(state.get("fish_xp", 0))), int(repair.get("excess_xp", 0)))
                state["fish_xp"] = max(0, int(state.get("fish_xp", 0)) - excess_xp)
                state["fish_level"] = max(1, level_for_xp(state["fish_xp"]))
                new_state = json.dumps(state, ensure_ascii=False, sort_keys=True)
                self._db.execute(
                    "UPDATE players SET state = ?, updated_at = ? WHERE user_id = ?",
                    (new_state, time.time(), user_id),
                )
                self._db.execute(
                    "INSERT INTO admin_adjustments(user_id, reason, old_state, new_state, created_at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, migration_id, old_state, new_state, time.time()),
                )
                details[user_id] = {
                    "removed_items": removed,
                    "removed_xp": excess_xp,
                    "fish_xp": state["fish_xp"],
                    "fish_level": state["fish_level"],
                }
            self._db.execute(
                "INSERT INTO migrations(migration_id, applied_at, details) VALUES (?, ?, ?)",
                (migration_id, time.time(), json.dumps(details, ensure_ascii=False)),
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    def _repair_excessive_drop_quantities(self) -> None:
        """Reclaim the items and XP granted while the >100 fish drop bug was live."""
        self._reclaim_excessive_drops("mcfish-drop-quantity-2026-09-10", EXCESSIVE_DROP_REPAIRS)

    def _top_up_excessive_drop_quantities(self) -> None:
        """补齐首次线上执行时键名写错而漏掉的 鳕鱼/鳗鱼 差额。"""
        migration_id = "mcfish-drop-quantity-2026-09-10-v2"
        row = self._db.execute(
            "SELECT details FROM migrations WHERE migration_id = ?",
            ("mcfish-drop-quantity-2026-09-10",),
        ).fetchone()
        if not row:
            self._reclaim_excessive_drops(migration_id, {})
            return
        recorded = json.loads(row[0])
        targets: dict[str, dict] = {}
        for user_id, repair in EXCESSIVE_DROP_REPAIRS.items():
            removed = (recorded.get(user_id) or {}).get("removed_items", {})
            missing = {
                FISH_RENAMES.get(name, name): int(amount) - int(removed.get(FISH_RENAMES.get(name, name), 0))
                for name, amount in repair["items"].items()
                if int(removed.get(FISH_RENAMES.get(name, name), 0)) < int(amount)
            }
            if missing:
                targets[user_id] = {"excess_xp": 0, "items": missing}
        self._reclaim_excessive_drops(migration_id, targets)

    def _repair_concurrent_overwrite_losses(self) -> None:
        """补发 2026-09-11 被后台钓鱼任务整份覆盖而丢失的购买物品。"""
        migration_id = "mcfish-concurrent-overwrite-2026-09-11"
        if self._db.execute("SELECT 1 FROM migrations WHERE migration_id = ?", (migration_id,)).fetchone():
            return
        details: dict[str, dict] = {}
        try:
            for user_id, granted in CONCURRENT_OVERWRITE_REPAIRS.items():
                row = self._db.execute("SELECT state FROM players WHERE user_id = ?", (user_id,)).fetchone()
                if not row:
                    continue
                state = json.loads(row[0])
                state.setdefault("pack", {})
                old_state = json.dumps(state, ensure_ascii=False, sort_keys=True)
                for name, amount in granted.items():
                    for _ in range(int(amount)):
                        self._add(state, name, pole=name in POLES)
                new_state = json.dumps(state, ensure_ascii=False, sort_keys=True)
                self._db.execute(
                    "UPDATE players SET state = ?, updated_at = ? WHERE user_id = ?",
                    (new_state, time.time(), user_id),
                )
                self._db.execute(
                    "INSERT INTO admin_adjustments(user_id, reason, old_state, new_state, created_at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, migration_id, old_state, new_state, time.time()),
                )
                details[user_id] = {name: int(amount) for name, amount in granted.items()}
            self._db.execute(
                "INSERT INTO migrations(migration_id, applied_at, details) VALUES (?, ?, ?)",
                (migration_id, time.time(), json.dumps(details, ensure_ascii=False)),
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    def _top_up_concurrent_overwrite_losses(self) -> None:
        """补发同一覆盖 bug 在其余时间段吞掉的购买（第二批）。"""
        migration_id = "mcfish-concurrent-overwrite-2026-09-11-v2"
        if self._db.execute("SELECT 1 FROM migrations WHERE migration_id = ?", (migration_id,)).fetchone():
            return
        details: dict[str, dict] = {}
        try:
            for user_id, repair in CONCURRENT_OVERWRITE_LATE_REPAIRS.items():
                row = self._db.execute("SELECT state FROM players WHERE user_id = ?", (user_id,)).fetchone()
                if not row:
                    continue
                state = json.loads(row[0])
                state.setdefault("pack", {})
                old_state = json.dumps(state, ensure_ascii=False, sort_keys=True)
                for name, amount in repair["items"].items():
                    for _ in range(int(amount)):
                        self._add(state, name, pole=name in POLES)
                new_state = json.dumps(state, ensure_ascii=False, sort_keys=True)
                self._db.execute(
                    "UPDATE players SET state = ?, updated_at = ? WHERE user_id = ?",
                    (new_state, time.time(), user_id),
                )
                self._db.execute(
                    "INSERT INTO admin_adjustments(user_id, reason, old_state, new_state, created_at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, migration_id, old_state, new_state, time.time()),
                )
                details[user_id] = {name: int(amount) for name, amount in repair["items"].items()}
                gold = int(repair["gold"])
                if gold:
                    self._economy_change(user_id, gold, "mcfish_overwrite_refund")
                    details[user_id]["退款金币"] = gold
            self._db.execute(
                "INSERT INTO migrations(migration_id, applied_at, details) VALUES (?, ?, ?)",
                (migration_id, time.time(), json.dumps(details, ensure_ascii=False)),
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    @staticmethod
    def _shop_poles(value, name: str) -> list[dict]:
        if isinstance(value, list):
            return value
        return [{"durable": POLES[name][0], "maintenance": 0, "induce": 0, "favor": 0} for _ in range(int(value or 0))]

    @staticmethod
    def _enchant_label(level: int) -> str:
        return ("0", "Ⅰ", "Ⅱ", "Ⅲ")[max(0, min(3, int(level)))]

    # 海之眷顾每级把 6% 的空竿转化为宝藏 1%、鱼竿 2%、鱼 3%。
    FAVOR_AIR_COST = 6
    FAVOR_SPLIT = (1, 2, 3)

    def _zone_boundaries(self, state: dict) -> tuple[int, int, int, int, int]:
        favor = int((state.get("equip") or {}).get("favor", 0))
        fish_count = sum(
            int(value) for name, value in state.get("pack", {}).items()
            if name in FISH and isinstance(value, int)
        )
        if fish_count > 100 or (state.get("equip") or {}).get("name") == AXOLOTL:
            zones = [2, 8, 35, 45]
        else:
            zone_probabilities = {
                str(info.get("类型")): int(info.get("概率[0-100)", 0))
                for info in getattr(self, "_catalog", {}).get("分类", [])
            }
            zones = [
                zone_probabilities.get("treasure", 0),
                zone_probabilities.get("pole", 0),
                zone_probabilities.get("fish", 0),
                zone_probabilities.get("waste", 0),
            ]
        if favor > 0:
            # 眷顾只消耗“空竿”：空竿不足时按剩余量折算，不会挤占垃圾区间。
            air = max(0, 100 - sum(zones))
            converted = min(favor * self.FAVOR_AIR_COST, air)
            split = [converted * part // self.FAVOR_AIR_COST for part in self.FAVOR_SPLIT]
            split[2] += converted - sum(split)
            for index, amount in enumerate(split):
                zones[index] += amount
        gear_air = self._gear_effects(state).get("air", 0.0)
        if gear_air > 0:
            air_left = max(0, 100 - sum(zones))
            converted = min(air_left, int(air_left * gear_air))
            zones[2] += converted
        boundaries = (
            0,
            zones[0],
            zones[0] + zones[1],
            zones[0] + zones[1] + zones[2],
            sum(zones),
        )
        if int(state.get("pack", {}).get("海豚", 0)) > 0:
            boundaries = (*boundaries[:4], 100)
        wealth_multiplier = self._accessory_effect(state, "财富浮标", "treasure_rate", 1.0)
        if wealth_multiplier > 1.0 and boundaries[1] > boundaries[0]:
            extra = (boundaries[1] - boundaries[0]) * (wealth_multiplier - 1.0)
            boundaries = tuple(
                min(100, value + (extra if index else 0))
                for index, value in enumerate(boundaries)
            )
        return boundaries

    def _original_drop(
        self,
        state: dict,
        boundaries: tuple[int, int, int, int, int] | None = None,
        rod_name: str | None = None,
        *,
        magic_bait: bool | None = None,
    ) -> tuple[str | None, int]:
        rod_name = rod_name or str((state.get("equip") or {}).get("name", "木竿"))
        if magic_bait is None:
            magic_bait = int(state.get("active_consumables", {}).get("魔法鱼饵", 0)) > 0
        catalog = getattr(self, "_catalog", {})
        zones = catalog.get("分类", [])
        items = catalog.get("物品", [])
        if not zones or not items:
            return None, 1
        axolotl = (state.get("equip") or {}).get("name") == AXOLOTL

        def drop_quantity(dropped: str) -> int:
            # 原插件只有装备美西螈时才一次获得 5 个（鱼竿与美西螈本身除外），鱼的总数超过100只改变概率区间。
            return 5 if axolotl and dropped not in POLES and dropped != AXOLOTL else 1
        boundaries = boundaries or self._zone_boundaries(state)
        effects = self._gear_effects(state)
        roll = random.randrange(100)
        zone = None
        # 原插件使用累计区间，剩余 60% 为“赛博空气”。
        if boundaries[0] <= roll < boundaries[1]:
            zone = "treasure"
        elif boundaries[1] <= roll < boundaries[2]:
            zone = "pole"
        elif boundaries[2] <= roll < boundaries[3]:
            zone = "fish"
        elif boundaries[3] <= roll < boundaries[4]:
            zone = "waste"
        if zone is None:
            return None, 1
        if zone == "waste":
            current_zone = str(state.get("fishing_zone", daily_fishing_zone()))
            sub = roll_waste_sub(
                current_zone,
                random.randrange(100),
                material_bonus=effects.get("material", 0.0),
                valuable_bonus=effects.get("valuable", 0.0),
            )
            if sub == "material":
                return roll_material(current_zone)
            if sub == "food":
                return roll_food(current_zone)
            if sub == "valuable":
                if random.random() < 0.10 + effects.get("box", 0.0):
                    return roll_key_or_box(current_zone)
                return roll_valuable(current_zone)
            # 垃圾继续走原 catalog 抽取
        candidates = [item for item in items if item.get("类型") == zone]
        if not candidates:
            return None, 1
        if zone == "fish" and "fishing_zone" in state:
            weights = fish_zone_weights(
                str(state.get("fishing_zone", daily_fishing_zone())),
                rod_name,
                power=fishing_power(state),
            )
            if magic_bait:
                for name in list(weights):
                    if FISH.get(name, 0) >= 500:
                        weights[name] = weights[name] * 1.25
            rarity = effects.get("rarity", 0.0)
            if rarity:
                weights = {
                    name: (weight * (1.0 + rarity) if FISH.get(name, 0) >= 500 else weight)
                    for name, weight in weights.items()
                }
            names = [name for name, weight in weights.items() if weight > 0]
            selected_name = random.choices(names, weights=[weights[name] for name in names], k=1)[0]
            return selected_name, drop_quantity(selected_name)
        if zone == "fish" and magic_bait:
            weights = [
                int(item.get("概率[0-100)", 0))
                * (1.25 if FISH.get(str(item.get("名称")), 0) >= 500 else 1.0)
                for item in candidates
            ]
            selected = random.choices(candidates, weights=weights, k=1)[0]
            quantity = drop_quantity(str(selected["名称"]))
            return str(selected["名称"]), quantity
        if zone == "pole" and rod_name != AXOLOTL:
            candidates = [
                item for item in candidates
                if rod_drop_allowed(rod_name, str(item.get("名称", "")))
            ]
            if not candidates:
                return None, 1
        roll = random.randrange(100)
        lower = 0
        for item in candidates:
            upper = lower + int(item.get("概率[0-100)", 0))
            if lower <= roll < upper:
                quantity = drop_quantity(str(item["名称"]))
                return str(item["名称"]), quantity
            lower = upper
        return None, 1

    def _item_image(self, name: str) -> Path | None:
        # 物品图标优先使用按名字存放的 wiki 资源；找不到时再回退到旧 asset_id 映射。
        direct = Path(__file__).parent / "resources" / f"{name}.png"
        if direct.is_file():
            return direct
        icon_ids = {
            "财富浮标": "mcfish_wealth_bobber",
            "坚韧钓组": "mcfish_tough_tackle",
            "黄金磁石": "mcfish_gold_magnet",
            "魔法鱼饵": "mcfish_magic_bait",
            "幸运海星": "mcfish_lucky_starfish",
            "隐秘诱饵": "mcfish_hidden_lure",
            "万能鱼饵": "mcfish_universal_bait",
            "钓鱼碎片": "mcfish_fishing_fragment",
            "水晶虾": "mcfish_crystal_shrimp",
            "深海鱼": "mcfish_deep_fish",
            "金鳞鱼": "mcfish_goldfish",
            "银鱼": "mcfish_silverfish",
            "鲤鱼": "mcfish_carp",
            "鳗鱼": "mcfish_eel",
            "垃圾": "mcfish_junk",
            **{name: item["asset_id"] for name, item in KING_INFO.items()},
            **{name: item["asset_id"] for name, item in HIDDEN_FISH.items()},
        }
        if name in icon_ids:
            path = Path(__file__).parent / "resources" / "icons" / f"{icon_ids[name]}.png"
            return path if path.is_file() else None
        filename = "book.png" if name in ("诱钓", "海之眷顾", "净化书", "宝藏诅咒") else f"{name}.png"
        path = Path(__file__).parent / "resources" / filename
        return path if path.is_file() else None

    @staticmethod
    def _random_pole_data(name: str) -> dict[str, int]:
        """Create the random attributes used only by a fishing rod drop."""
        return {
            "durable": random.randrange(POLES[name][0]) + 1,
            "maintenance": random.randrange(POLE_DROP_ATTRIBUTE_LIMITS["maintenance"]),
            "induce": random.randrange(POLE_DROP_ATTRIBUTE_LIMITS["induce"]),
            "favor": random.randrange(POLE_DROP_ATTRIBUTE_LIMITS["favor"]),
        }

    def _add(
        self,
        state: dict,
        name: str,
        number: int = 1,
        *,
        pole: bool = False,
        pole_data: dict | None = None,
        random_pole: bool = False,
        craftable: bool = True,
    ) -> None:
        if pole:
            value = dict(pole_data or {"durable": POLES[name][0], "maintenance": 0, "induce": 0, "favor": 0})
            if random_pole:
                value = self._random_pole_data(name)
            if not craftable:
                value["craftable"] = False
            state["pack"].setdefault(name, []).append(value)
        else:
            state["pack"][name] = int(state["pack"].get(name, 0)) + number

    def _remove(self, state: dict, name: str, number: int = 1, index: int | None = None) -> bool:
        if name in POLES:
            poles = state["pack"].get(name, [])
            if len(poles) < number:
                return False
            if index is None:
                del poles[:number]
            else:
                del poles[index:index + number]
            return True
        count = int(state["pack"].get(name, 0))
        if count < number:
            return False
        state["pack"][name] = count - number
        return True

    def _text(self, event) -> str:
        return "".join(str(getattr(seg, "text", "") or "") for seg in event.message or [])

    def _grantable_item_names(self) -> set[str]:
        """Return item names that can be represented in a player's fishing pack."""
        names = set(FISH) | set(KING_INFO) | set(HIDDEN_FISH)
        names.update(POLES)
        names.update(ITEMS)
        names.update(SHOP_EQUIPMENT_PRICES)
        names.update(EQUIPMENT)
        names.update(ITEM_PRICES)
        names.update(KEY_NAMES.values())
        names.update(BOX_NAMES.values())
        names.update(CARD_NAMES)
        names.update(PIECE_INFO)
        names.update(getattr(self, "_thing_order", ()))
        names.add("钓鱼碎片")
        return names

    def _grant_target_id(self, event) -> str | None:
        for segment in event.message or []:
            if isinstance(segment, At) and str(segment.user_id) != str(getattr(event, "self_id", "")):
                return str(segment.user_id)
        return None

    async def _grant_item(self, event, text: str) -> None:
        """Add a valid fishing item to another player's pack for the designated admin."""
        if str(event.sender.user_id) not in ITEM_GRANT_USERS:
            return

        target_id = self._grant_target_id(event)
        if not target_id:
            await event.reply(text="用法：钓鱼发放 @用户 物品 数量")
            return

        checker = getattr(getattr(self, "_economy", None), "_target_in_group", None)
        if checker is not None and getattr(event, "group_id", None) is not None:
            in_group = await checker(event, target_id)
            if in_group is not True:
                message = (
                    "找不到对方，发放目标必须是当前群成员"
                    if in_group is False
                    else "暂时无法确认对方是否在群内，请稍后再试"
                )
                await event.reply(text=message)
                return

        payload = self._text(event).strip() or text
        for prefix in ("钓鱼发放", "钓鱼充值"):
            if payload == prefix or payload.startswith(prefix + " "):
                payload = payload[len(prefix):].strip()
                break
        payload = re.sub(r"\[CQ:at,qq=\d+(?:,[^\]]+)?\]", "", payload)
        payload = " ".join(payload.split())
        match = re.fullmatch(r"(.+?)\s+(\d+)", payload)
        if not match or int(match.group(2)) <= 0:
            await event.reply(text="用法：钓鱼发放 @用户 物品 数量，数量必须是正整数")
            return

        requested_name, number_text = match.groups()
        name = POLE_ALIASES.get(requested_name.strip(), requested_name.strip())
        if name not in self._grantable_item_names():
            await event.reply(text=f"钓鱼背包不存在可发放物品：{requested_name.strip()}")
            return
        number = int(number_text)
        if number > 100000:
            await event.reply(text="单次发放数量不能超过 100000")
            return
        async with self._user_lock(target_id):
            target_state = self._state(target_id)
            old_state = json.dumps(target_state, ensure_ascii=False, sort_keys=True)
            if name in POLES:
                for _ in range(number):
                    self._add(target_state, name, pole=True)
            else:
                self._add(target_state, name, number)
            self._save(target_id, target_state)
            new_state = json.dumps(target_state, ensure_ascii=False, sort_keys=True)
            self._db.execute(
                "INSERT INTO admin_adjustments(user_id, reason, old_state, new_state, created_at) VALUES (?, ?, ?, ?, ?)",
                (target_id, "admin_item_grant", old_state, new_state, time.time()),
            )
            self._db.commit()
            held = target_state["pack"].get(name, []) if name in POLES else int(target_state["pack"].get(name, 0))
            held_count = len(held) if name in POLES else held
        await event.reply(text=f"已为 {target_id} 发放 {name} ×{number}，当前背包 {name}：{held_count}")

    def _cleanup_message_images(self, message) -> int:
        """删除本次发送产生的渲染图，避免 McFish 图片目录无限增长。"""
        workspace = getattr(self, "workspace", None)
        if not workspace:
            return 0
        rendered_root = Path(workspace) / "images"
        removed = 0
        for segment in message or []:
            path = getattr(segment, "file", None)
            if not path:
                continue
            try:
                candidate = Path(path)
                if candidate.resolve().is_relative_to(rendered_root.resolve()):
                    candidate.unlink()
                    removed += 1
            except (OSError, TypeError, ValueError):
                pass
        return removed

    async def _send_original_message(self, event, message: MessageArray) -> None:
        """Send a raw message chain, matching upstream ctx.Send without auto Reply/At."""
        if _is_blocked_group(event):
            return
        message = await self._message_array_as_images(event, message)
        api = getattr(getattr(self, "api", None), "qq", None)
        try:
            if api is not None and getattr(event, "group_id", None) is not None:
                await api.post_group_msg(group_id=event.group_id, rtf=message)
                return
            await event.reply(rtf=message, at_sender=False)
        finally:
            self._cleanup_message_images(message)

    async def _message_array_as_images(self, event, message: MessageArray) -> MessageArray:
        """Replace text segments in an outgoing McFish message with PNGs."""
        if not getattr(self, "workspace", None):
            return message
        segments = []
        for segment in message:
            if isinstance(segment, PlainText):
                output = await asyncio.to_thread(
                    self._render_text_image,
                    str(event.sender.user_id),
                    segment.text,
                )
                segments.append(Image(file=str(output)))
            else:
                segments.append(segment)
        return MessageArray(segments)

    def _render_text_image(self, uid: str, text: str) -> Path:
        body_font = self._font(34)
        wrapped: list[str] = []
        for line in str(text).splitlines() or [""]:
            wrapped.extend(self._wrap_text(line, body_font, 900))
        output = Path(self.workspace) / "images" / f"message_{uid}_{time.time_ns()}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        image = self._panel("钓鱼消息", wrapped, max(300, 180 + len(wrapped) * 52 + 70))
        image.save(output, "PNG")
        return output

    def _user_lock(self, user_id: str) -> asyncio.Lock:
        locks = getattr(self, "_user_locks", None)
        if locks is None:
            locks = self._user_locks = {}
        return locks.setdefault(str(user_id), asyncio.Lock())

    async def _start_fishing(self, event, state: dict, text: str) -> None:
        """Start fishing in the background so the message handler stays responsive."""
        uid = str(event.sender.user_id)
        if getattr(self, "_closing", False):
            await event.reply(text="插件正在热更新，请稍后再开始钓鱼", at_sender=False)
            return
        task = getattr(self, "_fishing_tasks", {}).get(uid)
        if task is not None and not task.done():
            await event.reply(text="你正在钓鱼，请稍后再试", at_sender=False)
            return

        async def run() -> None:
            try:
                await self._fish(event, state, text)
            except Exception:
                LOG.exception("后台钓鱼任务执行失败 (user=%s)", uid)
            finally:
                if self._fishing_tasks.get(uid) is asyncio.current_task():
                    self._fishing_tasks.pop(uid, None)

        if not hasattr(self, "_fishing_tasks"):
            self._fishing_tasks = {}
        self._fishing_tasks[uid] = asyncio.create_task(run())
        await asyncio.sleep(0)

    async def _show_last_fishing_result(self, event, state: dict) -> None:
        task = getattr(self, "_fishing_tasks", {}).get(str(event.sender.user_id))
        if task is not None and not task.done():
            await event.reply(text="你正在钓鱼，请稍后再试", at_sender=False)
            return
        result = state.get("last_fishing_result")
        if not isinstance(result, dict) or not result.get("text"):
            await event.reply(text="暂无上次钓鱼结果", at_sender=False)
            return
        image = result.get("image")
        if image and not Path(image).exists():
            image = None
        try:
            if image:
                await event.reply(rtf=MessageArray([PlainText(text=str(result["text"])), Image(file=str(image))]))
            else:
                await event.reply(text=str(result["text"]), at_sender=False)
        finally:
            if image:
                self._cleanup_message_images([Image(file=str(image))])

    async def _send_fishing_result(self, event, result_text: str, image: Path | None = None) -> None:
        try:
            if image:
                await event.reply(rtf=MessageArray([PlainText(text=result_text), Image(file=str(image))]))
            else:
                await event.reply(text=result_text)
        finally:
            if image:
                self._cleanup_message_images([Image(file=str(image))])

    def _has_pole(self, state: dict) -> bool:
        return bool(state.get("equip")) or any(
            name in POLES and isinstance(value, list) and value
            for name, value in state.get("pack", {}).items()
        )

    def _has_usable_pole(self, state: dict) -> bool:
        """Return whether the player owns a durable rod allowed at their level."""
        level = int(state.get("fish_level", level_for_xp(int(state.get("fish_xp", 0)))))
        equip = state.get("equip") or {}
        if equip:
            name = str(equip.get("name", ""))
            return name == AXOLOTL or (
                name in POLES
                and rod_unlock_level(name) <= level
                and int(equip.get("durable", 0)) > 0
            )
        for name, value in state.get("pack", {}).items():
            if name not in POLES or rod_unlock_level(name) > level or not isinstance(value, list):
                continue
            if any(int(pole.get("durable", 0)) > 0 for pole in value):
                return True
        return False

    @staticmethod
    def _starter_pole_available(state: dict, today: date | None = None) -> bool:
        last_day = str(state.get("starter_pole_day", ""))
        if not last_day:
            return True
        try:
            return (today or date.today()) - date.fromisoformat(last_day) >= timedelta(days=3)
        except ValueError:
            return True

    def _fish_count_text(self, state: dict) -> str:
        level = int(state.get("fish_level", level_for_xp(int(state.get("fish_xp", 0)))))
        limit = daily_attempt_limit(level)
        used = min(limit, int(state.get("fish_today", 0)))
        return f"钓鱼等级：{level}（经验{int(state.get('fish_xp', 0))}）\n今日钓鱼次数：{used}/{limit}，剩余：{max(0, limit - used)}次"

    @staticmethod
    def _add_fishing_xp(state: dict, amount: int) -> tuple[int, int]:
        old_level = int(state.get("fish_level", 0))
        state["fish_xp"] = int(state.get("fish_xp", 0)) + max(0, int(amount))
        state["fish_level"] = level_for_xp(state["fish_xp"])
        return old_level, state["fish_level"]

    @staticmethod
    def _probability_value(value: float) -> str:
        return f"{value:.2f}%"

    def _probability_catalog(self) -> tuple[dict[str, int], dict[str, int]]:
        raw_items = getattr(self, "_catalog", {}).get("物品", [])
        item_ranges: dict[str, int] = {}
        type_totals: dict[str, int] = {}
        for item in raw_items:
            item_type = str(item.get("类型", ""))
            item_ranges[str(item.get("名称", ""))] = int(item.get("概率[0-100)", 0))
            type_totals[item_type] = type_totals.get(item_type, 0) + int(item.get("概率[0-100)", 0))
        return item_ranges, type_totals

    def _probability_item_map(self, item_type: str, category_probability: int) -> dict[str, float]:
        item_ranges, type_totals = self._probability_catalog()
        total = type_totals.get(item_type, 0)
        if not total:
            return {}
        return {
            name: raw * category_probability / total
            for name, raw in item_ranges.items()
            if name and raw and self._item_type(name) == item_type
        }

    def _item_type(self, name: str) -> str:
        return next(
            (str(item.get("类型", "")) for item in getattr(self, "_catalog", {}).get("物品", []) if str(item.get("名称", "")) == name),
            "",
        )

    @staticmethod
    def _fish_count(state: dict) -> int:
        return sum(
            int(value) for name, value in state.get("pack", {}).items()
            if name in FISH and isinstance(value, int)
        )

    def _fish_probability_map(self, rod_name: str, zone: str, category_probability: int, *, magic_bait: bool = False, power: int | None = None) -> dict[str, float]:
        weights = fish_zone_weights(zone, rod_name, power=power)
        if magic_bait:
            for name in list(weights):
                if FISH.get(name, 0) >= 500:
                    weights[name] = weights[name] * 1.25
        total = sum(weight for weight in weights.values() if weight > 0)
        if not total:
            return {}
        return {
            name: category_probability * weight / total
            for name, weight in weights.items()
            if weight > 0
        }

    def _rod_probability_map(self, rod_name: str, category_probability: int) -> tuple[dict[str, float], float]:
        item_ranges, type_totals = self._probability_catalog()
        total = type_totals.get("pole", 0)
        if not total:
            return {}, 0.0
        values: dict[str, float] = {}
        blocked = 0.0
        for name in POLES:
            raw = item_ranges.get(name, 0)
            if not raw:
                values[name] = 0.0
            elif rod_name == AXOLOTL or rod_drop_allowed(rod_name, name):
                values[name] = raw * category_probability / total
            else:
                values[name] = 0.0
                blocked += raw * category_probability / total
        return values, blocked

    def _probability_sections(self, uid: str, state: dict) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """Build the two probability columns from the live drop rules."""
        boundaries = self._zone_boundaries(state)
        categories = tuple(boundaries[index + 1] - boundaries[index] for index in range(4))
        equip_name = str((state.get("equip") or {}).get("name", ""))
        rod_name = equip_name if equip_name in POLES or equip_name == AXOLOTL else "木竿"
        zone = str(state.get("fishing_zone", daily_fishing_zone()))
        fish_count = self._fish_count(state)
        magic_bait = int(state.get("active_consumables", {}).get("魔法鱼饵", 0)) > 0
        rod_values, blocked_pole = self._rod_probability_map(rod_name, categories[1])
        treasure_values = self._probability_item_map("treasure", categories[0])
        waste_values = self._probability_item_map("waste", categories[3])
        fish_values = self._fish_probability_map(
            rod_name, zone, categories[2], magic_bait=magic_bait, power=fishing_power(state)
        )
        air = max(0, 100 - sum(categories)) + blocked_pole

        current: list[tuple[str, str]] = [
            ("heading", "第一部分 当前用户概率"),
            ("body", f"用户：{uid}    鱼竿：{equip_name or '未装备（按木竿限制计算）'}"),
            ("body", f"鱼点：{FISHING_ZONE_LABELS.get(zone, zone)}    鱼总数：{fish_count}    {'强化区间' if fish_count > 100 or equip_name == AXOLOTL else '基础区间'}"),
            ("body", f"钓鱼力：{power_summary(state)}"),
            ("body", f"饰品槽位：{len(state.get('accessories', []))}/{state.get('accessory_slots', 2)}    饰品：{'、'.join(f'{name}{self._enchant_label(self._accessory_level(state, name))}' for name in state.get('accessories', [])) or '无'}    鱼饵：{'、'.join(name for name, count in state.get('active_consumables', {}).items() if int(count) > 0) or '无'}"),
            ("body", f"大类：宝藏 {categories[0]}% / 鱼竿 {categories[1]}% / 普通鱼 {categories[2]}% / 垃圾 {categories[3]}% / 空竿 {air:.2f}%"),
            ("body", f"垃圾内部：垃圾 {waste_split(zone)[0]}% / 材料 {waste_split(zone)[1]}% / 食物 {waste_split(zone)[2]}% / 贵重 {waste_split(zone)[3]}%"),
            ("heading", "鱼竿"),
        ]
        current.extend(
            ("body", f"{name}：{self._probability_value(value)}" + ("（不可越级）" if value == 0 and name != "三叉戟" and name != rod_name else ""))
            for name, value in rod_values.items()
        )
        current.append(("heading", "宝藏"))
        current.extend(("body", f"{name}：{self._probability_value(value)}") for name, value in treasure_values.items())
        current.append(("heading", "当前鱼点普通鱼"))
        current.extend(("body", f"{name}：{self._probability_value(value)}") for name, value in fish_values.items())
        excluded_fish = [
            name for name in FISH_ZONE_WEIGHTS.get(zone, ())
            if fish_min_power(zone, name) > fishing_power(state)
        ]
        if excluded_fish:
            current.append(("note", "钓鱼力不足、当前不会出现的鱼：" + "、".join(
                f"{name}（{fish_min_power(zone, name)}）" for name in excluded_fish
            )))
        current.append(("heading", "垃圾"))
        current.extend(("body", f"{name}：{self._probability_value(value)}") for name, value in waste_values.items())
        hidden_names = [name for name, item in HIDDEN_FISH.items() if item["zone"] == zone]
        current.append(("heading", "隐藏鱼与鱼王"))
        if int(state.get("active_consumables", {}).get("隐秘诱饵", 0)) > 0 and hidden_names:
            current.append(("body", "隐秘诱饵：" + "、".join(f"{name} 4.50%" for name in hidden_names)))
        else:
            current.append(("body", "隐藏鱼：当前未满足隐秘诱饵与鱼点条件"))
        king_lines = []
        for king_name, king in FISH_KINGS.items():
            if king["zone"] != zone:
                continue
            required = king.get("requires")
            ready = not required or int(state.get("king_stats", {}).get(required, {}).get("bitten", 0)) > 0
            base = int(king.get("base_price", 0))
            if not ready:
                king_lines.append(
                    f"{king_name}（后置）：前置未咬钩，暂不判定 / 基础价 {base} / 磁石 ×10·×30·×100"
                )
                continue
            bite, hook = fish_king_probabilities(king_name, rod_name, prerequisite_caught=ready)
            wealth_multiplier = self._accessory_effect(state, "财富浮标", "king_clue_rate", 1.0)
            if wealth_multiplier > 1.0:
                bite = king_bite_rate(bite, wealth_bobber=wealth_multiplier)
            tough_points = self._accessory_effect(state, "坚韧钓组", "king_hook_success_points", 0)
            if tough_points:
                hook = king_success_rate(hook, tough_tackle=tough_points)
            role = "后置" if king.get("kind") == "final" else "前置"
            cap = "×10·×30·×100" if king.get("kind") == "final" else "×3·×5·×8"
            king_lines.append(
                f"{king_name}（{role}）：咬钩 {bite * 100:.2f}% / 基础上钩 {hook * 100:.2f}% / 基础价 {base} / 磁石 {cap}"
            )
        current.extend(("body", line) for line in king_lines or ["鱼王：当前鱼点没有对应鱼王"])
        resident_name = resident_king_for_zone(zone)
        if resident_name:
            resident_gear = self._gear_effects(state)
            resident_bite = resident_king_bite_chance(
                resident_name,
                gear_bonus=float(resident_gear.get("king_bite", 0.0)) + float(resident_gear.get("legendary", 0.0)),
                clue_multiplier=self._accessory_effect(state, "财富浮标", "king_clue_rate", 1.0),
                bait_bonus=resident_king_bait_multiplier(state.get("active_consumables", {})),
            )
            resident_hook = resident_king_hook_chance(
                resident_name,
                tough_points=self._accessory_effect(state, "坚韧钓组", "king_hook_success_points", 0),
            )
            current.append((
                "body",
                f"常驻鱼王 {resident_name}：每竿咬钩 {resident_bite * 100:.2f}% / 上钩 {resident_hook * 100:.2f}%"
                f"（钓鱼力 {resident_king_fp(resident_name)}，每日上限 {RESIDENT_KING_DAILY_LIMIT} 条，磁石 ×3·×5·×8）",
            ))
        current.append(("note", "鱼王概率仅在对应窗口内生效；最终上钩率还受回复技能和路径影响。常驻鱼王没有窗口，每次抛竿独立判定。前置与常驻鱼王黄金磁石封顶 ×3/×5/×8，后置鱼王 ×10/×30/×100。"))

        standard_categories = (1, 4, 25, 30)
        enhanced_categories = (2, 8, 35, 45)
        all_lines: list[tuple[str, str]] = [
            ("heading", "第二部分 所有鱼竿概率"),
            ("note", "鱼竿/宝藏/垃圾显示为：基础区间 / 强化区间（鱼总数超过100条时）。普通鱼按鱼点列出，强化区间为表中概率×1.4。"),
            ("heading", "鱼竿掉落（越级部分为空竿）"),
        ]
        for candidate in POLES:
            base_values, _ = self._rod_probability_map(candidate, standard_categories[1])
            enhanced_values, _ = self._rod_probability_map(candidate, enhanced_categories[1])
            details = "、".join(
                f"{name}{self._probability_value(base_values[name])}/{self._probability_value(enhanced_values[name])}"
                for name in POLES
                if base_values[name] or name == "三叉戟"
            )
            _, base_blocked = self._rod_probability_map(candidate, standard_categories[1])
            _, enhanced_blocked = self._rod_probability_map(candidate, enhanced_categories[1])
            all_lines.append(("body", f"{candidate}：{details}；额外空竿 {self._probability_value(base_blocked)}/{self._probability_value(enhanced_blocked)}"))
        all_lines.append(("heading", "宝藏"))
        all_treasures = self._probability_item_map("treasure", standard_categories[0])
        enhanced_treasures = self._probability_item_map("treasure", enhanced_categories[0])
        all_lines.append(("body", "、".join(f"{name}{self._probability_value(all_treasures[name])}/{self._probability_value(enhanced_treasures[name])}" for name in all_treasures)))
        all_lines.append(("heading", "垃圾"))
        all_wastes = self._probability_item_map("waste", standard_categories[3])
        enhanced_wastes = self._probability_item_map("waste", enhanced_categories[3])
        all_lines.append(("body", "、".join(f"{name}{self._probability_value(all_wastes[name])}/{self._probability_value(enhanced_wastes[name])}" for name in all_wastes)))
        all_lines.append(("heading", "普通鱼（河湾/深海/遗迹/风暴 · 全鱼竿对照）"))
        for candidate_zone in ("bay", "deep", "ruin", "storm"):
            all_lines.append(("body", f"{FISHING_ZONE_LABELS[candidate_zone]}："))
            for candidate in POLES:
                values = self._fish_probability_map(candidate, candidate_zone, standard_categories[2])
                all_lines.append(("body", f"{candidate}：" + "、".join(f"{name}{self._probability_value(value)}" for name, value in values.items())))
        all_lines.append(("note", "熔岩深渊及之后的鱼点概率见“钓鱼概率 鱼点”（按当前鱼竿显示每竿出现率）。"))
        all_lines.append(("heading", "鱼王（咬钩率 / 基础上钩率，基础价，磁石上限）"))
        for king_name, king in FISH_KINGS.items():
            values = []
            for candidate in POLES:
                bite, hook = fish_king_probabilities(king_name, candidate, prerequisite_caught=True)
                values.append(f"{candidate}{bite * 100:.1f}/{hook * 100:.1f}")
            role = "后置" if king.get("kind") == "final" else "前置"
            cap = "×10/×30/×100" if king.get("kind") == "final" else "×3/×5/×8"
            all_lines.append((
                "body",
                f"{king_name}（{role}，基础价 {int(king.get('base_price', 0))}，磁石 {cap}）："
                + "、".join(values),
            ))
        all_lines.append(("heading", "常驻鱼王（每竿咬钩率 / 上钩率）"))
        for resident in RESIDENT_KING_NAMES:
            entry = RESIDENT_KINGS[resident]
            all_lines.append((
                "body",
                f"{fishing_zone_label(entry['zone'])}·{resident}：钓鱼力 {entry['fp']} 起，"
                f"咬钩 {float(entry['bite']) * 100:.2f}% / 上钩 {float(entry['hook']) * 100:.2f}%，"
                f"参考售价 {int(entry['base_price'])}",
            ))
        all_lines.append(("note", "常驻鱼王不占用每周窗口，每日上限 1 条，不产出鱼王鳞片。"))
        all_lines.extend([
            ("heading", "隐藏鱼与不可直接钓出的装备"),
            ("body", "隐藏鱼（同名鱼种在另一鱼点出现）：深海 灰色沙丘曳尾鱼 4.50%；遗迹水域 魂灵面纱 4.50%；风暴海域 岩石凶鲨 4.50%；均需对应鱼点和隐秘诱饵。"),
            ("body", "财富浮标、坚韧钓组、黄金磁石、幸运海星、魔法鱼饵、隐秘诱饵、万能鱼饵：直接钓出概率均为0，需要合成或商店获得。"),
            ("note", "鱼王必须在对应鱼点和鱼王窗口内；后置行显示前置已咬钩后的概率，未满足前置条件时为0。前置鱼王磁石上限 ×3/×5/×8，后置鱼王 ×10/×30/×100。"),
        ])
        return current, all_lines

    def _zone_fish_entries(self, state: dict) -> list[tuple[str, str]]:
        """每个鱼点会出什么鱼、所需钓鱼力，以及当前鱼竿下的每竿出现率。"""
        rod_name = str((state.get("equip") or {}).get("name", "木竿"))
        power = fishing_power(state)
        boundaries = self._zone_boundaries(state)
        fish_category = boundaries[3] - boundaries[2]
        lines: list[tuple[str, str]] = []
        half = (len(FISHING_ZONES) + 1) // 2
        for part, spots in enumerate((FISHING_ZONES[:half], FISHING_ZONES[half:]), start=1):
            lines.append(("heading", f"各鱼点鱼种与钓鱼力门槛（{part}/2）"))
            for spot in spots:
                names = FISH_ZONE_WEIGHTS.get(spot, ())
                weights = fish_zone_weights(spot, rod_name, power=power)
                total = sum(value for value in weights.values() if value > 0)
                detail_parts = []
                for name in names:
                    share = weights.get(name, 0) / total if total else 0
                    detail_parts.append(
                        f"{name}{fish_min_power(spot, name)}（{share * fish_category:.2f}%）"
                    )
                lines.append((
                    "body",
                    f"{fishing_zone_label(spot)}（进入 {fishing_zone_required_power(spot)}）：" + "、".join(detail_parts),
                ))
        lines.append(("note", "数字为该鱼种进入掉落池所需钓鱼力；括号内为当前鱼竿下的每竿出现率，不含空竿与其他大类。"))
        return lines

    def _probability_current_tables(self, uid: str, state: dict) -> list[dict]:
        boundaries = self._zone_boundaries(state)
        categories = tuple(boundaries[index + 1] - boundaries[index] for index in range(4))
        equip_name = str((state.get("equip") or {}).get("name", ""))
        rod_name = equip_name if equip_name in POLES or equip_name == AXOLOTL else "木竿"
        zone = str(state.get("fishing_zone", daily_fishing_zone()))
        fish_count = self._fish_count(state)
        power = fishing_power(state)
        magic_bait = int(state.get("active_consumables", {}).get("魔法鱼饵", 0)) > 0
        rod_values, blocked_pole = self._rod_probability_map(rod_name, categories[1])
        treasure_values = self._probability_item_map("treasure", categories[0])
        waste_values = self._probability_item_map("waste", categories[3])
        fish_values = self._fish_probability_map(
            rod_name, zone, categories[2], magic_bait=magic_bait, power=power
        )
        air = max(0, 100 - sum(categories)) + blocked_pole
        accessories = "、".join(
            f"{name}{self._enchant_label(self._accessory_level(state, name))}"
            for name in state.get("accessories", [])
        ) or "无"
        baits = "、".join(
            name for name, count in state.get("active_consumables", {}).items() if int(count) > 0
        ) or "无"
        summary = [
            ("用户", str(uid), "鱼竿", equip_name or "未装备（按木竿计算）"),
            ("鱼点", FISHING_ZONE_LABELS.get(zone, zone), "鱼总数", f"{fish_count}（{'强化区间' if fish_count > 100 or equip_name == AXOLOTL else '基础区间'}）"),
            ("钓鱼力", power_summary(state), "", ""),
            ("饰品槽位", f"{len(state.get('accessories', []))}/{state.get('accessory_slots', 2)}", "鱼饵", baits),
            ("饰品", accessories, "", ""),
            ("大类", f"宝藏 {categories[0]}% / 鱼竿 {categories[1]}% / 普通鱼 {categories[2]}% / 垃圾 {categories[3]}% / 空竿 {air:.2f}%", "", ""),
            ("垃圾内部", f"垃圾 {waste_split(zone)[0]}% / 材料 {waste_split(zone)[1]}% / 食物 {waste_split(zone)[2]}% / 贵重 {waste_split(zone)[3]}%", "", ""),
        ]

        tables: list[dict] = [{
            "title": "鱼竿掉落",
            "headers": ["鱼竿", "概率", "状态"],
            "rows": [
                [
                    name,
                    self._probability_value(value),
                    "当前" if name == rod_name else "不可越级" if value == 0 and name != "三叉戟" else "可掉落",
                ]
                for name, value in rod_values.items()
            ],
            "weights": [2, 1, 1.5],
            "aligns": ["l", "r", "l"],
            "icons": True,
        }, {
            "title": "宝藏概率",
            "headers": ["宝藏", "概率"],
            "rows": [[name, self._probability_value(value)] for name, value in treasure_values.items()],
            "weights": [2.2, 1],
            "aligns": ["l", "r"],
            "icons": True,
        }, {
            "title": "当前鱼点普通鱼",
            "headers": ["鱼", "出现率", "所需 FP", "状态"],
            "rows": [
                [
                    name,
                    self._probability_value(fish_values.get(name, 0)),
                    str(fish_min_power(zone, name)),
                    "可钓" if fish_min_power(zone, name) <= power else "FP 不足",
                ]
                for name in FISH_ZONE_WEIGHTS.get(zone, ())
            ],
            "weights": [2.2, 1.2, 1, 1],
            "aligns": ["l", "r", "r", "l"],
            "icons": True,
            "note": "出现率为每竿概率，不含空竿与其他大类。",
        }, {
            "title": "垃圾概率",
            "headers": ["垃圾", "概率"],
            "rows": [[name, self._probability_value(value)] for name, value in waste_values.items()],
            "weights": [2.2, 1],
            "aligns": ["l", "r"],
            "icons": True,
        }]

        hidden_names = [name for name, item in HIDDEN_FISH.items() if item["zone"] == zone]
        if hidden_names:
            active = int(state.get("active_consumables", {}).get("隐秘诱饵", 0)) > 0
            tables.append({
                "title": "隐藏鱼",
                "headers": ["隐藏鱼", "概率", "条件"],
                "rows": [[name, "4.50%" if active else "0.00%", "隐秘诱饵" if active else "需隐秘诱饵"] for name in hidden_names],
                "weights": [2, 1, 2],
                "aligns": ["l", "r", "l"],
                "icons": True,
            })

        king_rows = []
        for king_name, king in FISH_KINGS.items():
            if king["zone"] != zone:
                continue
            required = king.get("requires")
            ready = not required or int(state.get("king_stats", {}).get(required, {}).get("bitten", 0)) > 0
            base = int(king.get("base_price", 0))
            role = "后置" if king.get("kind") == "final" else "前置"
            cap = "×10/×30/×100" if king.get("kind") == "final" else "×3/×5/×8"
            if not ready:
                king_rows.append([king_name, role, "—", "—", str(base), cap, "前置未咬钩"])
                continue
            bite, hook = fish_king_probabilities(king_name, rod_name, prerequisite_caught=ready)
            wealth_multiplier = self._accessory_effect(state, "财富浮标", "king_clue_rate", 1.0)
            if wealth_multiplier > 1.0:
                bite = king_bite_rate(bite, wealth_bobber=wealth_multiplier)
            tough_points = self._accessory_effect(state, "坚韧钓组", "king_hook_success_points", 0)
            if tough_points:
                hook = king_success_rate(hook, tough_tackle=tough_points)
            king_rows.append([king_name, role, f"{bite * 100:.2f}%", f"{hook * 100:.2f}%", str(base), cap, "可判定"])
        if king_rows:
            tables.append({
                "title": "限时鱼王",
                "headers": ["鱼王", "类型", "咬钩", "基础上钩", "基础价", "磁石上限", "状态"],
                "rows": king_rows,
                "weights": [1.8, 0.9, 1, 1.1, 1, 1.8, 1.2],
                "aligns": ["l", "l", "r", "r", "r", "l", "l"],
                "icons": True,
                "note": "概率只在对应鱼王窗口内生效；最终上钩率还受回复技能和路径影响。",
            })

        resident_name = resident_king_for_zone(zone)
        if resident_name:
            resident_gear = self._gear_effects(state)
            resident_bite = resident_king_bite_chance(
                resident_name,
                gear_bonus=float(resident_gear.get("king_bite", 0.0)) + float(resident_gear.get("legendary", 0.0)),
                clue_multiplier=self._accessory_effect(state, "财富浮标", "king_clue_rate", 1.0),
                bait_bonus=resident_king_bait_multiplier(state.get("active_consumables", {})),
            )
            resident_hook = resident_king_hook_chance(
                resident_name,
                tough_points=self._accessory_effect(state, "坚韧钓组", "king_hook_success_points", 0),
            )
            tables.append({
                "title": "常驻鱼王",
                "headers": ["鱼王", "每竿咬钩", "上钩", "钓鱼力", "每日上限", "参考售价"],
                "rows": [[
                    resident_name,
                    f"{resident_bite * 100:.2f}%",
                    f"{resident_hook * 100:.2f}%",
                    str(resident_king_fp(resident_name)),
                    str(RESIDENT_KING_DAILY_LIMIT),
                    str(int(RESIDENT_KINGS[resident_name]["base_price"])),
                ]],
                "weights": [2, 1.2, 1, 1, 1, 1.2],
                "aligns": ["l", "r", "r", "r", "r", "r"],
                "icons": True,
                "note": "常驻鱼王也属于该鱼点鱼类，但按鱼王规则单独结算，不占用每周窗口。",
            })
        return summary, tables

    def _probability_zone_tables(self, state: dict) -> list[dict]:
        rod_name = str((state.get("equip") or {}).get("name", "木竿"))
        power = fishing_power(state)
        boundaries = self._zone_boundaries(state)
        fish_category = boundaries[3] - boundaries[2]
        tables = []
        for zone in FISHING_ZONES:
            weights = fish_zone_weights(zone, rod_name, power=power)
            total = sum(value for value in weights.values() if value > 0)
            rows = []
            for name in FISH_ZONE_WEIGHTS.get(zone, ()):
                share = weights.get(name, 0) / total if total else 0
                gate = fish_min_power(zone, name)
                rows.append([
                    name,
                    "普通鱼",
                    str(gate),
                    f"{share * fish_category:.2f}%" if gate <= power else "0.00%",
                    "—",
                ])
            resident = resident_king_for_zone(zone)
            if resident:
                resident_gear = self._gear_effects(state)
                bite = resident_king_bite_chance(
                    resident,
                    gear_bonus=float(resident_gear.get("king_bite", 0.0)) + float(resident_gear.get("legendary", 0.0)),
                    clue_multiplier=self._accessory_effect(state, "财富浮标", "king_clue_rate", 1.0),
                    bait_bonus=resident_king_bait_multiplier(state.get("active_consumables", {})),
                )
                hook = resident_king_hook_chance(
                    resident,
                    tough_points=self._accessory_effect(state, "坚韧钓组", "king_hook_success_points", 0),
                )
                rows.append([
                    resident,
                    "常驻鱼王",
                    str(resident_king_fp(resident)),
                    f"{bite * 100:.2f}%",
                    f"{hook * 100:.2f}%",
                ])
            tables.append({
                "title": f"{fishing_zone_label(zone)}（进入 FP {fishing_zone_required_power(zone)}）",
                "headers": ["鱼", "类型", "所需 FP", "出现率 / 咬钩", "上钩"],
                "rows": rows,
                "weights": [2, 1, 1, 1.6, 1],
                "aligns": ["l", "l", "r", "r", "r"],
                "icons": True,
                "note": "出现率为每竿概率；常驻鱼王为每竿咬钩/上钩。",
            })
        return tables

    def _probability_all_tables(self) -> list[dict]:
        tables = []
        rod_rows = []
        for candidate in POLES:
            values, blocked = self._rod_probability_map(candidate, 1)
            rod_rows.append([candidate, *[self._probability_value(values[name]) for name in POLES], self._probability_value(blocked)])
        tables.append({
            "title": "鱼竿掉落（基础区间）",
            "headers": ["当前竿", *POLES, "额外空竿"],
            "rows": rod_rows,
            "weights": [1.2, *([1] * len(POLES)), 1.1],
            "aligns": ["l", *(["r"] * len(POLES)), "r"],
            "icons": False,
        })

        treasures = self._probability_item_map("treasure", 1)
        enhanced_treasures = self._probability_item_map("treasure", 2)
        tables.append({
            "title": "宝藏",
            "headers": ["宝藏", "基础", "强化"],
            "rows": [[name, self._probability_value(value), self._probability_value(enhanced_treasures.get(name, 0))] for name, value in treasures.items()],
            "weights": [2.2, 1, 1],
            "aligns": ["l", "r", "r"],
            "icons": True,
        })

        wastes = self._probability_item_map("waste", 30)
        enhanced_wastes = self._probability_item_map("waste", 45)
        tables.append({
            "title": "垃圾",
            "headers": ["垃圾", "基础", "强化"],
            "rows": [[name, self._probability_value(value), self._probability_value(enhanced_wastes.get(name, 0))] for name, value in wastes.items()],
            "weights": [2.2, 1, 1],
            "aligns": ["l", "r", "r"],
            "icons": True,
        })

        for zone in ("bay", "deep", "ruin", "storm"):
            rows = []
            for name in FISH_ZONE_WEIGHTS.get(zone, ()):
                rows.append([
                    name,
                    str(fish_min_power(zone, name)),
                    *[
                        self._probability_value(self._fish_probability_map(candidate, zone, 25).get(name, 0))
                        for candidate in POLES
                    ],
                ])
            tables.append({
                "title": f"普通鱼 · {fishing_zone_label(zone)}",
                "headers": ["鱼", "FP", *POLES],
                "rows": rows,
                "weights": [1.8, 0.8, *([1] * len(POLES))],
                "aligns": ["l", "r", *(["r"] * len(POLES))],
                "icons": True,
                "merge_equal_from": 2,
                "note": "概率为每竿出现率；相邻鱼竿数值相同时合并显示。熔岩深渊及之后的鱼点见“钓鱼概率 鱼点”。",
            })

        king_rows = []
        for king_name, king in FISH_KINGS.items():
            values = []
            for candidate in POLES:
                bite, hook = fish_king_probabilities(king_name, candidate, prerequisite_caught=True)
                values.append(f"{bite * 100:.1f}/{hook * 100:.1f}")
            role = "后置" if king.get("kind") == "final" else "前置"
            cap = "×10/×30/×100" if king.get("kind") == "final" else "×3/×5/×8"
            king_rows.append([
                king_name, role, str(int(king.get("base_price", 0))), cap, *values,
            ])
        tables.append({
            "title": "限时鱼王（咬钩率/基础上钩率）",
            "headers": ["鱼王", "类型", "基础价", "磁石", *POLES],
            "rows": king_rows,
            "weights": [1.5, 0.8, 0.9, 1.1, *([0.95] * len(POLES))],
            "aligns": ["l", "l", "r", "l", *(["r"] * len(POLES))],
            "icons": True,
            "merge_equal_from": 4,
            "note": "后置行显示前置已咬钩后的概率；未满足前置条件时为 0。相邻鱼竿数值相同时合并显示。",
        })

        resident_rows = []
        for resident in RESIDENT_KING_NAMES:
            entry = RESIDENT_KINGS[resident]
            resident_rows.append([
                resident,
                fishing_zone_label(entry["zone"]),
                str(int(entry["fp"])),
                f"{float(entry['bite']) * 100:.2f}%",
                f"{float(entry['hook']) * 100:.2f}%",
                str(int(entry["base_price"])),
            ])
        tables.append({
            "title": "常驻鱼王",
            "headers": ["鱼王", "鱼点", "钓鱼力", "咬钩", "上钩", "参考售价"],
            "rows": resident_rows,
            "weights": [1.8, 1, 1, 1, 1, 1.2],
            "aligns": ["l", "l", "r", "r", "r", "r"],
            "icons": True,
            "note": "常驻鱼王不占用每周窗口，每日上限 1 条，不产出鱼王鳞片。",
        })

        hidden_rows = [
            [name, fishing_zone_label(item["zone"]), "4.50%", "对应鱼点 + 隐秘诱饵"]
            for name, item in HIDDEN_FISH.items()
        ]
        tables.append({
            "title": "隐藏鱼",
            "headers": ["隐藏鱼", "触发鱼点", "概率", "条件"],
            "rows": hidden_rows,
            "weights": [1.8, 1.2, 1, 2],
            "aligns": ["l", "l", "r", "l"],
            "icons": True,
        })
        return tables

    @staticmethod
    def _merge_equal_cells(row: list, start: int | None) -> list[tuple[int, int, object]]:
        """把从 start 列开始、相邻且相同的单元格合并成 (起始列, 跨列数, 值)。"""
        cells: list[tuple[int, int, object]] = []
        index = 0
        while index < len(row):
            span = 1
            if start is not None and index >= start:
                while index + span < len(row) and row[index + span] == row[index]:
                    span += 1
            cells.append((index, span, row[index]))
            index += span
        return cells

    def _probability_draw_table(self, image, draw, x: int, y: int, width: int, section: dict) -> int:
        title = str(section.get("title", ""))
        headers = list(section.get("headers", []))
        rows = list(section.get("rows", []))
        weights = list(section.get("weights") or [1] * len(headers))
        aligns = list(section.get("aligns") or ["l"] * len(headers))
        note = str(section.get("note", ""))
        icons = bool(section.get("icons"))
        measure = ImageDraw.Draw(PILImage.new("RGB", (1, 1)))
        inner = width - 28
        total_weight = sum(weights) or 1
        col_widths = [inner * weight / total_weight for weight in weights]

        def fit(text: str, font, max_width: int) -> str:
            value = str(text)
            if measure.textbbox((0, 0), value, font=font)[2] <= max_width:
                return value
            while value and measure.textbbox((0, 0), value + "…", font=font)[2] > max_width:
                value = value[:-1]
            return value + "…"

        note_lines = self._wrap_text(note, self._font(14), inner)[:3] if note else []
        title_height = 52
        header_height = 30
        row_height = 30
        note_height = len(note_lines) * 18 + 10 if note_lines else 0
        height = title_height + header_height + len(rows) * row_height + note_height + 16
        self._card(draw, (x, y, x + width, y + height), fill=(251, 252, 252, 255), radius=14)
        draw.text((x + 14, y + 10), title, fill=(40, 61, 68, 255), font=self._font(21, bold=True))

        header_y = y + title_height
        draw.rounded_rectangle((x + 8, header_y, x + width - 8, header_y + header_height), radius=8, fill=(229, 238, 240, 255))
        header_font = self._font(15, bold=True)
        header_mid = header_y + header_height / 2
        cell_x = x + 14
        for index, label in enumerate(headers):
            cell_width = col_widths[index]
            if aligns[index] == "r":
                draw.text(
                    (cell_x + cell_width - 8, header_mid),
                    fit(label, header_font, cell_width - 14),
                    fill=(40, 61, 68, 255),
                    font=header_font,
                    anchor="rm",
                )
            else:
                draw.text(
                    (cell_x + 6, header_mid),
                    fit(label, header_font, cell_width - 14),
                    fill=(40, 61, 68, 255),
                    font=header_font,
                    anchor="lm",
                )
            cell_x += cell_width

        row_y = header_y + header_height
        row_font = self._font(15)
        merge_from = section.get("merge_equal_from")
        for row_index, row in enumerate(rows):
            if row_index % 2 == 0:
                draw.rounded_rectangle((x + 8, row_y, x + width - 8, row_y + row_height), radius=6, fill=(246, 250, 250, 255))
            row_mid = row_y + row_height / 2
            cell_x = x + 14
            for index, span, value in self._merge_equal_cells(row, merge_from):
                cell_width = sum(col_widths[index:index + span])
                if span > 1:
                    draw.rounded_rectangle(
                        (cell_x + 2, row_y + 3, cell_x + cell_width - 2, row_y + row_height - 3),
                        radius=6,
                        fill=(236, 245, 244, 255),
                        outline=(214, 231, 229, 255),
                        width=1,
                    )
                    draw.text(
                        (cell_x + cell_width / 2, row_mid),
                        fit(value, row_font, cell_width - 16),
                        fill=(70, 83, 88, 255),
                        font=row_font,
                        anchor="mm",
                    )
                else:
                    text_x = cell_x + 6
                    available = cell_width - 14
                    if index == 0 and icons:
                        icon = self._resource_image(str(value), (22, 22))
                        if icon:
                            image.alpha_composite(icon, (cell_x + 2, int(row_y + (row_height - 22) / 2)))
                            text_x = cell_x + 28
                            available = cell_width - 34
                    if aligns[index] == "r":
                        draw.text(
                            (cell_x + cell_width - 8, row_mid),
                            fit(value, row_font, available),
                            fill=(70, 83, 88, 255),
                            font=row_font,
                            anchor="rm",
                        )
                    else:
                        draw.text(
                            (text_x, row_mid),
                            fit(value, row_font, available),
                            fill=(70, 83, 88, 255),
                            font=row_font,
                            anchor="lm",
                        )
                cell_x += cell_width
            row_y += row_height

        if note_lines:
            note_y = row_y + 4
            for line in note_lines:
                draw.text((x + 14, note_y), line, fill=(92, 112, 117, 255), font=self._font(14))
                note_y += 18
        return height

    def _render_probability_image(self, uid: str, state: dict, page: str = "") -> Path:
        key = page.strip()
        if key in ("鱼点", "鱼种"):
            title = "钓鱼概率 · 鱼点鱼种"
            subtitle = "每个鱼点会出的鱼与所需钓鱼力"
            summary = []
            tables = self._probability_zone_tables(state)
            single_column = False
        elif key in ("全部", "所有", "all"):
            title = "钓鱼概率 · 全部鱼竿"
            subtitle = "九档鱼竿对照 · 表格视图"
            summary = []
            tables = self._probability_all_tables()
            single_column = True
        else:
            title = "钓鱼概率"
            subtitle = "当前装备明细 · 表格视图"
            summary, tables = self._probability_current_tables(uid, state)
            single_column = False

        width = 1600
        margin = 42
        gap = 24
        column_width = (width - margin * 2 - gap) // 2
        header_height = 172
        summary_height = 0
        if summary:
            summary_height = 58 + len(summary) * 32 + 18

        def note_height(section: dict, table_width: int) -> int:
            note = str(section.get("note", ""))
            if not note:
                return 0
            return len(self._wrap_text(note, self._font(14), table_width - 28)) * 18 + 10

        def table_height(section: dict, table_width: int) -> int:
            return 52 + 30 + len(section.get("rows", [])) * 30 + note_height(section, table_width) + 16

        if single_column:
            sections_height = sum(table_height(section, width - margin * 2) + 16 for section in tables)
            height = header_height + 24 + sections_height + 42
        else:
            left: list[dict] = []
            right: list[dict] = []
            left_height = right_height = 0
            for section in tables:
                section_height = table_height(section, column_width) + 16
                if left_height <= right_height:
                    left.append(section)
                    left_height += section_height
                else:
                    right.append(section)
                    right_height += section_height
            sections_height = max(left_height, right_height)
            height = header_height + 24 + summary_height + (18 if summary else 0) + sections_height + 42

        image = PILImage.new("RGBA", (width, height), (244, 248, 249, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, width, header_height), radius=26, fill=(33, 68, 82, 255))
        draw.rectangle((0, header_height - 20, width, header_height), fill=(33, 68, 82, 255))
        draw.text((margin, 34), title, fill=(250, 252, 252, 255), font=self._font(62, bold=True))
        draw.text((margin, 116), subtitle, fill=(190, 222, 225, 255), font=self._font(24))
        draw.text((width - margin, 118), "分页：钓鱼概率 鱼点 / 全部", fill=(220, 234, 235, 255), font=self._font(19), anchor="ra")

        measure = ImageDraw.Draw(PILImage.new("RGB", (1, 1)))

        def fit(text: str, font, max_width: int) -> str:
            value = str(text)
            if measure.textbbox((0, 0), value, font=font)[2] <= max_width:
                return value
            while value and measure.textbbox((0, 0), value + "…", font=font)[2] > max_width:
                value = value[:-1]
            return value + "…"

        content_y = header_height + 24
        if summary:
            self._card(draw, (margin, content_y, width - margin, content_y + summary_height), fill=(251, 252, 252, 255), radius=14)
            draw.text((margin + 18, content_y + 12), "账号与鱼点", fill=(40, 61, 68, 255), font=self._font(22, bold=True))
            label_font = self._font(16)
            value_font = self._font(18, bold=True)
            row_y = content_y + 50
            for left_label, left_value, right_label, right_value in summary:
                draw.text((margin + 18, row_y + 4), left_label, fill=(92, 112, 117, 255), font=label_font)
                if right_label:
                    left_width = column_width - 110 - 24
                    draw.text((margin + 110, row_y), fit(left_value, value_font, left_width), fill=(40, 61, 68, 255), font=value_font)
                    right_x = margin + column_width + 20
                    draw.text((right_x, row_y + 4), right_label, fill=(92, 112, 117, 255), font=label_font)
                    draw.text((right_x + 92, row_y), fit(right_value, value_font, column_width - 92 - 24), fill=(40, 61, 68, 255), font=value_font)
                else:
                    draw.text((margin + 110, row_y), fit(left_value, value_font, width - margin * 2 - 130), fill=(40, 61, 68, 255), font=value_font)
                row_y += 32
            content_y += summary_height + 18

        if single_column:
            table_y = content_y
            for section in tables:
                table_y += self._probability_draw_table(image, draw, margin, table_y, width - margin * 2, section) + 16
        else:
            left_y = content_y
            for section in left:
                left_y += self._probability_draw_table(image, draw, margin, left_y, column_width, section) + 16
            right_y = content_y
            for section in right:
                right_y += self._probability_draw_table(image, draw, margin + column_width + gap, right_y, column_width, section) + 16

        output = Path(self.workspace) / "images" / f"probability_{uid}.png"
        if key in ("鱼点", "鱼种"):
            output = Path(self.workspace) / "images" / f"probability_{uid}_zones.png"
        elif key in ("全部", "所有", "all"):
            output = Path(self.workspace) / "images" / f"probability_{uid}_all.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, "PNG")
        return output

    def _probability_detail_text(self, state: dict) -> str:
        # 大类概率直接取自 _zone_boundaries，保证展示与实际掉落完全一致。
        boundaries = self._zone_boundaries(state)
        category = [
            boundaries[1] - boundaries[0],
            boundaries[2] - boundaries[1],
            boundaries[3] - boundaries[2],
            boundaries[4] - boundaries[3],
        ]
        lines = [
            "\n大类概率:",
            f"宝藏 : {category[0]}%",
            f"鱼竿 : {category[1]}%",
            f"鱼类 : {category[2]}%",
            f"垃圾 : {category[3]}%",
            f"合计 : {sum(category)}%",
            "-----------",
            "宝藏概率:",
        ]
        raw_items = getattr(self, "_catalog", {}).get("物品", [])
        item_ranges: dict[str, int] = {}
        type_totals: dict[str, int] = {}
        for item in raw_items:
            item_type = str(item.get("类型", ""))
            item_ranges[str(item.get("名称", ""))] = int(item.get("概率[0-100)", 0))
            type_totals[item_type] = type_totals.get(item_type, 0) + int(item.get("概率[0-100)", 0))

        def item_probability(name: str, item_type: str, category_probability: int) -> str:
            raw = item_ranges.get(name, 0)
            total = type_totals.get(item_type, 0)
            value = raw * category_probability / total if total else 0
            return f"{value:.2f}%"

        treasure_names = [
            str(item.get("名称")) for item in raw_items
            if str(item.get("类型")) == "treasure"
        ]
        for name in treasure_names:
            lines.append(f"{name} : {item_probability(name, 'treasure', category[0])}")
        lines.extend(["-----------", "鱼竿概率:"])
        pole_names = [
            str(item.get("名称")) for item in raw_items
            if str(item.get("类型")) == "pole" or str(item.get("名称")) == AXOLOTL
        ]
        current_rod = str((state.get("equip") or {}).get("name", "木竿"))
        for name in pole_names:
            probability_category = category[0] if name == AXOLOTL else category[1]
            probability_type = "treasure" if name == AXOLOTL else "pole"
            if name != AXOLOTL and not rod_drop_allowed(current_rod, name):
                lines.append(f"{name} : 0.00%（当前{current_rod}不可越级钓到）")
            else:
                lines.append(f"{name} : {item_probability(name, probability_type, probability_category)}")
        lines.extend(["-----------", "鱼类概率:"])
        zone = str(state.get("fishing_zone", "bay"))
        weights = fish_zone_weights(zone, current_rod, power=fishing_power(state))
        total_weight = sum(value for value in weights.values() if value > 0)
        for name in FISH_ZONE_WEIGHTS.get(zone, ()):
            value = weights.get(name, 0)
            share = value / total_weight if total_weight else 0
            lines.append(f"{name} : {share * category[2]:.2f}%")
        if str((state.get("equip") or {}).get("name", "")) == AXOLOTL:
            lines.append(f"海豚 : {item_probability('海豚', 'treasure', category[0])}")
        lines.append("-----------")
        return "\n".join(lines)

    async def _buy_emergency_pole(self, event, state: dict) -> bool:
        if self._has_usable_pole(state):
            await event.reply(text="你已经有鱼竿了，请先装备或使用现有鱼竿")
            return False
        if int(state.get("emergency_pole_count", 0)) >= 3:
            await event.reply(text="请装备鱼竿后钓鱼")
            return False
        price = int(self.get_config("starting_pole_price", 100))
        uid = str(event.sender.user_id)
        balance = int(self._economy.store.account(uid)["balance"])
        if balance < price:
            await event.reply(text=f"你钱包当前只有{balance}金币,无法完成支付")
            return False
        if not self._economy_change(uid, -price, "mcfish_starting_pole"):
            await event.reply(text="钱包余额不足")
            return False
        self._advance_tasks(state, spend=price)
        state["equip"] = {"name": "木竿", "durable": 30, "maintenance": 0, "induce": 0, "favor": 0}
        state["emergency_pole_count"] = int(state.get("emergency_pole_count", 0)) + 1
        self._save(uid, state)
        await event.reply(text=f"已花费{price}金币购买并装备应急木竿")
        return True

    def _grant_starter_pole(self, uid: str, state: dict) -> None:
        state["equip"] = {
            "name": "木竿",
            "durable": 30,
            "maintenance": 0,
            "induce": 0,
            "favor": 0,
        }
        state["starter_pole_day"] = date.today().isoformat()
        self._save(uid, state)

    async def _choose(self, prompt: str, source_event, *, choices: dict[str, str],
                      timeout: float, timeout_reply: str, cancel_reply: str,
                      parse_response=None):
        """兼容未提供 session_choose 的旧版 NcatBot。"""
        if parse_response is not None:
            await source_event.reply(text=prompt, at_sender=False)
            last_text = ""
            for attempt in range(2):
                try:
                    incoming = await self.wait_event(
                        predicate=lambda event: _same_conversation(event, source_event),
                        timeout=timeout,
                    )
                except (asyncio.TimeoutError, TimeoutError):
                    await source_event.reply(text=timeout_reply, at_sender=False)
                    return SimpleNamespace(ok=False, key=None, text=last_text, timed_out=True)
                event = getattr(incoming, "data", incoming)
                last_text = (getattr(event, "raw_message", "") or self._text(event)).strip()
                if last_text in choices:
                    return SimpleNamespace(ok=True, key=choices[last_text], text=last_text, timed_out=False)
                key = parse_response(last_text)
                if key is not None:
                    return SimpleNamespace(ok=True, key=key, text=last_text, timed_out=False)
                if attempt == 0:
                    await source_event.reply(text=f'请输入：{"/".join(choices)}或三个序号', at_sender=False)
            await source_event.reply(text=cancel_reply, at_sender=False)
            return SimpleNamespace(ok=False, key=None, text=last_text, timed_out=False)
        session_choose = getattr(self, "session_choose", None)
        if session_choose is not None:
            result = await session_choose(
                prompt, source_event, choices=choices, timeout=timeout,
                timeout_reply=timeout_reply,
            )
            if getattr(result, "key", None) == "cancel":
                await source_event.reply(text=cancel_reply, at_sender=False)
            return result
        await source_event.reply(text=prompt, at_sender=False)
        last_text = ""
        for attempt in range(2):
            try:
                incoming = await self.wait_event(
                    predicate=lambda event: _same_conversation(event, source_event),
                    timeout=timeout,
                )
            except (asyncio.TimeoutError, TimeoutError):
                await source_event.reply(text=timeout_reply, at_sender=False)
                return SimpleNamespace(ok=False, key=None, text=last_text, timed_out=True)
            event = getattr(incoming, "data", incoming)
            last_text = (getattr(event, "raw_message", "") or self._text(event)).strip()
            if last_text in choices:
                return SimpleNamespace(ok=True, key=choices[last_text], text=last_text, timed_out=False)
            if attempt == 0:
                await source_event.reply(text=f'请输入：{"/".join(choices)}', at_sender=False)
        await source_event.reply(text=cancel_reply, at_sender=False)
        return SimpleNamespace(ok=False, key=None, text=last_text, timed_out=False)

    @registrar.qq.on_command("钓鱼", priority=80)
    async def handle_short_fishing(self, event) -> None:
        """兼容原群内常用的短指令，并优先于普通消息处理器执行。"""
        if _is_blocked_group(event):
            return
        self._record_group_member(event)
        text = (event.raw_message or self._text(event)).strip()
        if text != "钓鱼":
            return
        uid = str(event.sender.user_id)
        async with self._user_lock(uid):
            state = self._state(uid)
            if "_fish" in getattr(self, "__dict__", {}):
                await self._fish(event, state, "进行钓鱼")
            else:
                await self._start_fishing(event, state, "进行钓鱼")

    @registrar.qq.on_group_increase(priority=29)
    async def handle_group_increase(self, event) -> None:
        if not _is_blocked_group(event):
            self._record_group_member(event)

    @registrar.qq.on_group_decrease(priority=29)
    async def handle_group_decrease(self, event) -> None:
        if _is_blocked_group(event) or not hasattr(self, "_db"):
            return
        group_id = getattr(event, "group_id", None)
        user_id = getattr(event, "user_id", None)
        if group_id is None or user_id is None:
            return
        self._db.execute(
            "DELETE FROM fishing_group_members WHERE group_id = ? AND user_id = ?",
            (str(group_id), str(user_id)),
        )
        self._db.commit()

    @staticmethod
    def _task_command_index(text: str, prefix: str) -> int | None | object:
        if text == prefix:
            return None
        if not text.startswith(prefix + " "):
            return _MISSING
        suffix = text[len(prefix):].strip()
        return int(suffix) if suffix.isdigit() and int(suffix) > 0 else 0

    async def _handle_message(self, event) -> None:
        text = (event.raw_message or self._text(event)).strip()
        task_prefixes = ("领取钓鱼任务",)
        is_task_command = any(text == prefix or text.startswith(prefix + " ") for prefix in task_prefixes)
        item_grant_prefixes = ("钓鱼发放", "钓鱼充值")
        is_item_grant_command = any(text == prefix or text.startswith(prefix + " ") for prefix in item_grant_prefixes)
        if not text or not (text.startswith(("进行", "购买", "出售", "装备", "卸下", "升级", "附魔", "合成", "消除", "修复", "维修", "分解", "前往", "开箱", "吃海藻", "强化渔具", "卸下渔具", "交付", "学习")) or text in ("花费100购买鱼竿", "购买饰品槽位", "解锁饰品槽位", "钓鱼帮助", "钓鱼概率", "钓鱼背包", "钓鱼商店", "钓鱼看板", "钓鱼时钟", "钓鱼图鉴", "钓鱼任务", "钓鱼力", "钓鱼鱼点", "鱼点", "钓鱼卡牌", "卡牌", "钓鱼装备", "装备图鉴", "饰品图鉴", "钓鱼工坊", "领取 三叉戟", "领取 神谕卡组", "钓鱼天赋", "重置钓鱼天赋", "查看上次钓鱼结果", "出售所有垃圾", "当前装备概率明细", "查看钓鱼规则") or text.startswith("钓鱼图鉴 ") or text.startswith("装备图鉴 ") or text.startswith("钓鱼帮助 ") or text.startswith("钓鱼概率 ") or is_task_command or is_item_grant_command):
            return
        uid = str(event.sender.user_id)
        state = self._state(uid)
        if is_item_grant_command:
            await self._grant_item(event, text)
            return
        if text == "钓鱼力":
            await event.reply(text=self._power_text(state))
            return
        if text in ("钓鱼鱼点", "鱼点"):
            await event.reply(text=self._spot_text(state))
            return
        if text.startswith("前往"):
            requested = text[2:].strip()
            zone = resolve_fishing_zone(requested)
            if zone is None:
                await event.reply(text=f"未知鱼点：{requested or '（空）'}")
                return
            power = fishing_power(state)
            required = fishing_zone_required_power(zone)
            if not fishing_zone_unlocked(zone, power):
                await event.reply(text=f"{fishing_zone_label(zone)}：需要钓鱼力 {required}，当前 {power}")
                return
            state["fishing_zone"] = zone
            self._save(uid, state)
            await event.reply(text=f"当前鱼点：{fishing_zone_label(zone)}")
            return
        if text.startswith("开箱"):
            await self._open_box(event, state, text[2:].strip())
            return
        if text.startswith("吃海藻"):
            await self._eat_food(event, state, text[3:].strip())
            return
        if text == "钓鱼工坊" or text.startswith("钓鱼工坊 "):
            await event.reply(text=self._workshop_text(state, text[4:].strip()))
            return
        if text.startswith("交付"):
            await self._deliver(event, state, text[2:].strip())
            return
        if text in ("领取 三叉戟", "领取 神谕卡组"):
            await self._claim_workshop(event, state, text[3:].strip())
            return
        if text == "钓鱼天赋":
            refunded = int(state.pop("talent_refunded", 0) or 0)
            if refunded:
                self._save(uid, state)
            await event.reply(text=self._talent_text(state, refunded))
            return
        if text.startswith("学习 "):
            await self._learn_talent_cmd(event, state, text[3:].strip())
            return
        if text == "重置钓鱼天赋":
            await self._reset_talents_cmd(event, state)
            return
        if text in ("钓鱼卡牌", "卡牌"):
            await event.reply(text=self._card_text(state))
            return
        if text == "钓鱼装备":
            output = await asyncio.to_thread(self._render_gear_image, uid, state)
            await self._send_original_message(event, MessageArray([Image(file=str(output))]))
            return
        if text.startswith("合成渔具"):
            requested = text[4:].strip()
            if not requested:
                output = await asyncio.to_thread(self._render_craft_image, uid, state)
                await self._send_original_message(event, MessageArray([Image(file=str(output))]))
                return
            await self._craft_gear(event, state, requested)
            return
        if text.startswith("装备渔具"):
            await self._equip_gear(event, state, text[4:].strip())
            return
        if text.startswith("强化渔具"):
            await self._upgrade_gear(event, state, text[4:].strip())
            return
        if text.startswith("卸下渔具"):
            await self._unequip_gear(event, state, text[4:].strip())
            return
        if text == "查看上次钓鱼结果":
            await self._show_last_fishing_result(event, state); return
        if text in ("钓鱼背包",):
            output = await asyncio.to_thread(self._render_pack_image, uid, state)
            await self._send_original_message(event, MessageArray([Image(file=str(output))])); return
        if text == "钓鱼时钟":
            output = await asyncio.to_thread(self._render_clock_image, uid)
            await self._send_original_message(event, MessageArray([Image(file=str(output))])); return
        if text in ("装备图鉴", "钓鱼图鉴 装备"):
            output = await asyncio.to_thread(self._render_gear_slot_catalog_image, uid, state)
            await self._send_original_message(event, MessageArray([Image(file=str(output))])); return
        if text == "装备图鉴 套装":
            output = await asyncio.to_thread(self._render_gear_catalog_image, uid, state)
            await self._send_original_message(event, MessageArray([Image(file=str(output))])); return
        if text in ("饰品图鉴", "钓鱼图鉴 饰品"):
            output = await asyncio.to_thread(self._render_gear_catalog_image, uid, state, True)
            await self._send_original_message(event, MessageArray([Image(file=str(output))])); return
        if text == "钓鱼图鉴" or text.startswith("钓鱼图鉴 "):
            requested = text[4:].strip()
            if requested and requested not in FISH and requested not in KING_INFO:
                await event.reply(text=f"钓鱼图鉴没有 {requested}；可用鱼名、鱼王名或直接发送“钓鱼图鉴”")
                return
            output = await asyncio.to_thread(self._render_catalog_image, uid, state, requested)
            await self._send_original_message(event, MessageArray([Image(file=str(output))])); return
        if text in ("钓鱼帮助", "查看钓鱼规则"):
            output = await asyncio.to_thread(self._render_help_image, uid)
            await self._send_original_message(event, MessageArray([Image(file=str(output))])); return
        if text.startswith("钓鱼帮助 "):
            arguments = text[5:].strip()
            if arguments.startswith(("改版", "版本")):
                await event.reply(text=revision_page(self._help_revision_page(arguments)))
                return
            if arguments not in ("快速", "鱼点", "装备", "鱼竿", "掉落", "任务", "机制", "全部"):
                await event.reply(text="钓鱼帮助分页：鱼点 / 装备 / 鱼竿 / 掉落 / 任务 / 机制 / 全部；改版说明：钓鱼帮助 改版 1-8")
                return
            output = await asyncio.to_thread(self._render_help_image, uid, arguments)
            await self._send_original_message(event, MessageArray([Image(file=str(output))]))
            return
        if text == "钓鱼概率" or text.startswith("钓鱼概率 "):
            page = text[4:].strip()
            if page not in ("", "鱼点", "鱼种", "全部", "所有", "all"):
                await event.reply(text="钓鱼概率分页：鱼点 / 全部；不填为当前装备概率")
                return
            output = await asyncio.to_thread(self._render_probability_image, uid, state, page)
            await self._send_original_message(event, MessageArray([Image(file=str(output))])); return
        if text == "钓鱼任务":
            await self._show_tasks(event, state); return
        task_index = self._task_command_index(text, "领取钓鱼任务")
        if task_index is not _MISSING:
            if task_index == 0:
                await event.reply(text="任务编号格式错误，请使用“领取钓鱼任务 2”")
            else:
                await self._claim_tasks(event, state, task_index)
            return
        if text == "花费100购买鱼竿":
            await self._buy_emergency_pole(event, state); return
        if text == "当前装备概率明细":
            output = await asyncio.to_thread(self._render_probability_image, uid, state)
            await self._send_original_message(event, MessageArray([Image(file=str(output))]))
            return
        if text in ("钓鱼商店", "钓鱼看板"):
            output = await asyncio.to_thread(self._render_store_image, uid, state)
            await self._send_original_message(event, MessageArray([Image(file=str(output))])); return
        if text == "出售所有垃圾":
            await self._sell(event, state, "垃圾"); return
        if text.startswith("进行") and text.endswith("钓鱼"):
            await self._start_fishing(event, state, text); return
        if text in ("购买饰品槽位", "解锁饰品槽位"):
            await self._buy_accessory_slot(event, state); return
        if text.startswith("购买"):
            await self._buy(event, state, text[2:].strip()); return
        if text.startswith("出售"):
            await self._sell(event, state, text[2:].strip()); return
        if text.startswith("装备"):
            await self._equip(event, state, text[2:].strip()); return
        if text.startswith("卸下"):
            await self._unequip(event, state, text[2:].strip()); return
        if text.startswith("升级"):
            await self._upgrade_accessory(event, state, text[2:].strip()); return
        if text in ("修复鱼竿", "维修鱼竿"):
            await self._repair(event, state); return
        if text.startswith("附魔"):
            await self._enchant(event, state, text[2:].strip()); return
        if text.startswith("合成"):
            await self._craft(event, state, text[2:].strip()); return
        if text.startswith("分解"):
            await self._dismantle(event, state, text[2:].strip()); return
        if text.startswith("消除"):
            await self._purify(event, state, text); return

    @registrar.qq.on_message(priority=29)
    async def handle_message(self, event) -> None:
        if _is_blocked_group(event):
            return
        self._record_group_member(event)
        event = _McFishEvent(event, self)
        uid = str(event.sender.user_id)
        async with self._user_lock(uid):
            await self._handle_message(event)

    def _power_text(self, state: dict) -> str:
        rows = [f"{label} {value}" for label, value in power_breakdown(state) if value]
        power = fishing_power(state)
        rows.append(f"等待倍率 {1.0 / (1.0 + power / 100.0):.2f}（×1/(1+FP/100)）")
        return "\n".join(["钓鱼力 " + str(power), *rows])

    def _spot_text(self, state: dict) -> str:
        current = str(state.get("fishing_zone", daily_fishing_zone()))
        hot = daily_fishing_zone()
        level = int(state.get("fish_level", 1))
        power = fishing_power(state)
        caught_today = state.get("resident_king_today", {})
        lines = [
            f"当前鱼点：{fishing_zone_label(current)}（等级 {level} · 钓鱼力 {power}）",
            f"活跃鱼点：{fishing_zone_label(hot)}",
            "鱼点与常驻鱼王：",
        ]
        for zone in FISHING_ZONES:
            resident = resident_king_for_zone(zone)
            gate = resident_king_fp(resident) if resident else 0
            today = int(caught_today.get(resident, 0)) if resident else 0
            suffix = f"，今日已捕获 {today}/{RESIDENT_KING_DAILY_LIMIT}" if resident and today else ""
            fish_names = "/".join(FISH_ZONE_WEIGHTS.get(zone, ()))
            used = int((state.get("zone_fish_today") or {}).get(zone, 0))
            cap = FISHING_ZONE_DAILY_CAPS.get(zone, 0)
            lines.append(
                f"{fishing_zone_label(zone)}：钓鱼力 {fishing_zone_required_power(zone)}，"
                f"今日 {used}/{cap} 次，鱼 {fish_names}，常驻鱼王 {resident or '无'}（也属于本鱼点鱼类，钓鱼力 {gate}）{suffix}"
            )
        lines.append("切换：前往 深海")
        return "\n".join(lines)

    def _help_revision_page(self, arguments: str) -> int:
        parts = str(arguments or "").split()
        if parts and parts[0] in ("改版", "版本"):
            if len(parts) > 1 and parts[1].isdigit():
                return int(parts[1])
            return 1
        return 1

    def _pack_text(self, state: dict) -> str:
        lines = ["钓鱼背包："]
        if state.get("equip"): lines.append(f"装备：{state['equip']['name']} 耐久 {state['equip']['durable']} 修复 {state['equip']['maintenance']} 诱钓 {state['equip']['induce']} 眷顾 {state['equip']['favor']}")
        for name, value in state["pack"].items():
            if (isinstance(value, list) and value) or (isinstance(value, int) and value > 0): lines.append(f"{name}: {len(value) if isinstance(value, list) else value}")
        return "\n".join(lines)

    def _render_catalog_image(self, uid: str, state: dict, requested: str = "") -> Path:
        """Render the personal catalogue only after an explicit query."""
        stats = state.get("fish_stats", {})
        king_stats = state.get("king_stats", {})
        total = sum(int(item.get("count", 0)) for item in stats.values())
        names = [requested] if requested and requested in stats else list(FISH)

        requested_king = requested in KING_INFO
        if requested_king:
            fish_names: list[str] = []
            king_names = [requested]
        else:
            fish_names = names
            king_names = [name for name in KING_INFO if not requested or requested == name]

        discovered_fish = sum(1 for name in FISH if int(stats.get(name, {}).get("count", 0)) > 0)
        discovered_kings = sum(1 for name in KING_INFO if int(king_stats.get(name, {}).get("caught", 0)) > 0)
        width = 1440
        margin = 46
        gap = 24
        card_width = (width - margin * 2 - gap) // 2
        header_height = 186
        summary_height = 142
        fish_card_height = 126
        king_card_height = 118
        fish_rows = max(1, (len(fish_names) + 1) // 2) if fish_names else 0
        king_rows = max(1, (len(king_names) + 1) // 2) if king_names else 0
        section_height = 68 + fish_rows * fish_card_height + (king_rows * king_card_height + 68 if king_names else 0) + 34
        height = header_height + 26 + summary_height + 22 + section_height + 42
        image = PILImage.new("RGBA", (width, height), (244, 248, 249, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, width, header_height), radius=26, fill=(33, 68, 82, 255))
        draw.rectangle((0, header_height - 20, width, header_height), fill=(33, 68, 82, 255))
        title = "钓鱼图鉴" if not requested else f"钓鱼图鉴 · {requested}"
        draw.text((margin, 36), title, fill=(250, 252, 252, 255), font=self._font(62, bold=True))
        draw.text((margin, 120), "个人捕获记录与鱼王战绩", fill=(190, 222, 225, 255), font=self._font(25))

        summary_y = header_height + 26
        summary_values = (
            ("总捕获", str(total), (38, 144, 158)),
            ("鱼类发现", f"{discovered_fish}/{len(FISH)}", (78, 116, 176)),
            ("鱼王成功", f"{discovered_kings}/{len(KING_INFO)}", (61, 145, 112)),
        )
        summary_gap = 18
        summary_width = (width - margin * 2 - summary_gap * 2) // 3
        for index, (label, value, accent) in enumerate(summary_values):
            x = margin + index * (summary_width + summary_gap)
            self._card(draw, (x, summary_y, x + summary_width, summary_y + summary_height))
            draw.rounded_rectangle((x, summary_y, x + 9, summary_y + summary_height), radius=4, fill=(*accent, 255))
            draw.text((x + 28, summary_y + 23), label, fill=(92, 112, 117, 255), font=self._font(23))
            draw.text((x + 28, summary_y + 62), value, fill=(40, 61, 68, 255), font=self._font(36, bold=True))

        section_y = summary_y + summary_height + 22
        fish_section_height = 68 + fish_rows * fish_card_height + 18 if fish_names else 0
        if fish_names:
            self._card(draw, (margin, section_y, width - margin, section_y + fish_section_height))
            draw.text((margin + 28, section_y + 20), "普通鱼", fill=(40, 61, 68, 255), font=self._font(31, bold=True))
            draw.text((width - margin - 28, section_y + 28), "数量 · 占比 · 首次捕获", fill=(92, 112, 117, 255), font=self._font(19), anchor="ra")
            for index, name in enumerate(fish_names):
                col, row = index % 2, index // 2
                x = margin + 20 + col * (card_width + gap)
                y = section_y + 62 + row * fish_card_height
                self._card(draw, (x, y, x + card_width - 20, y + fish_card_height - 14), fill=(248, 251, 251, 255), radius=12)
                icon = self._resource_image(name, (66, 66))
                if icon:
                    image.alpha_composite(icon, (x + 18, y + 26))
                text_x = x + 98
                item = stats.get(name, {})
                count = int(item.get("count", 0))
                ratio = (count / total * 100) if total else 0
                first = str(item.get("first_caught", "未捕获"))
                draw.text((text_x, y + 14), name, fill=(40, 61, 68, 255), font=self._font(26, bold=True))
                draw.text((text_x, y + 53), f"{count} 条  ·  {ratio:.1f}%", fill=(38, 144, 158, 255), font=self._font(22))
                draw.text((text_x, y + 82), f"首次：{first[:18]}", fill=(92, 112, 117, 255), font=self._font(18))

        king_section_y = section_y + fish_section_height + 18 if fish_names else section_y
        if king_names:
            king_section_height = 68 + king_rows * king_card_height + 18
            self._card(draw, (margin, king_section_y, width - margin, king_section_y + king_section_height))
            draw.text((margin + 28, king_section_y + 20), "鱼王战绩", fill=(40, 61, 68, 255), font=self._font(31, bold=True))
            for index, name in enumerate(king_names):
                col, row = index % 2, index // 2
                x = margin + 20 + col * (card_width + gap)
                y = king_section_y + 62 + row * king_card_height
                self._card(draw, (x, y, x + card_width - 20, y + king_card_height - 14), fill=(249, 249, 246, 255), radius=12)
                icon = self._item_image(name)
                if icon:
                    king_icon = self._resource_image(name, (58, 58))
                    if king_icon:
                        image.alpha_composite(king_icon, (x + 18, y + 23))
                item = king_stats.get(name, {})
                draw.text((x + 92, y + 14), name, fill=(40, 61, 68, 255), font=self._font(25, bold=True))
                draw.text(
                    (x + 92, y + 54),
                    f"咬钩 {int(item.get('bitten', 0))}  ·  成功 {int(item.get('caught', 0))}  ·  脱钩 {int(item.get('escaped', 0))}",
                    fill=(112, 96, 54, 255),
                    font=self._font(20),
                )

        output = Path(self.workspace) / "images" / f"catalog_{uid}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, "PNG")
        return output

    def _shop_text(self) -> str:
        rod_prices = {"木竿": SHOP_WOOD_PRICE, **SHOP_ROD_PRICES}
        item_prices = dict(ITEMS)
        item_prices.update(SHOP_EQUIPMENT_PRICES)
        rod_lines = [
            f"{name}: {price} 金币（Lv{max(1, rod_unlock_level(name))} · FP{ROD_POWER.get(name, 0)}）"
            for name, price in rod_prices.items()
        ]
        item_lines = [f"{name}: {price} 金币" for name, price in item_prices.items() if price]
        return "钓鱼商店：\n" + "\n".join(rod_lines) + "\n" + "\n".join(item_lines)

    @staticmethod
    def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        candidates = ("C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc") if bold else ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf")
        for path in candidates:
            if Path(path).exists():
                # BASIC 布局避免 HarfBuzz 对中文字符做字距压缩，汉字之间才不会叠在一起。
                return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.BASIC)
        return ImageFont.load_default()

    @classmethod
    def _wrap_text(cls, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
        measure = ImageDraw.Draw(PILImage.new("RGB", (1, 1)))
        lines: list[str] = []
        for paragraph in str(text).splitlines() or [""]:
            current = ""
            for char in paragraph:
                candidate = current + char
                if current and measure.textbbox((0, 0), candidate, font=font)[2] > max_width:
                    lines.append(current)
                    current = char
                else:
                    current = candidate
            lines.append(current)
        return lines or [""]

    @classmethod
    def _card(
        cls,
        draw: ImageDraw.ImageDraw,
        box: tuple[int, int, int, int],
        *,
        fill: tuple[int, int, int, int] = (255, 255, 255, 255),
        outline: tuple[int, int, int, int] = (220, 231, 233, 255),
        radius: int = 18,
    ) -> None:
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=2)

    @classmethod
    def _panel(cls, title: str, lines: list[str], height: int = 300) -> PILImage.Image:
        width = 1000
        height = max(250, height)
        image = PILImage.new("RGBA", (width, height), (244, 248, 249, 255))
        draw = ImageDraw.Draw(image)
        header_height = 132
        draw.rounded_rectangle((0, 0, width, header_height), radius=24, fill=(33, 68, 82, 255))
        draw.rectangle((0, header_height - 20, width, header_height), fill=(33, 68, 82, 255))
        title_font = cls._font(58, bold=True)
        body_font = cls._font(34)
        draw.text((34, 28), title, fill=(250, 252, 252, 255), font=title_font)
        cls._card(draw, (24, header_height + 18, width - 24, height - 24))
        y = header_height + 48
        for line in lines:
            draw.text((48, y), line, fill=(70, 83, 88, 255), font=body_font)
            y += 52
        return image

    def _resource_image(self, name: str, size: tuple[int, int]) -> PILImage.Image | None:
        path = self._item_image(name)
        if path is None:
            return None
        try:
            image = PILImage.open(path).convert("RGBA")
        except OSError:
            return None
        image.thumbnail(size, PILImage.Resampling.LANCZOS)
        return image

    @classmethod
    def _draw_bar(cls, draw: ImageDraw.ImageDraw, xy: tuple[int, int, int, int], value: float) -> None:
        left, top, right, bottom = xy
        draw.rounded_rectangle(xy, radius=max(2, (bottom - top) // 2), fill=(224, 234, 236, 255))
        filled_right = left + int(max(0.0, min(1.0, value)) * (right - left))
        if filled_right > left:
            draw.rounded_rectangle(
                (left, top, filled_right, bottom),
                radius=max(2, (bottom - top) // 2),
                fill=(58, 163, 150, 255),
            )

    @staticmethod
    def _pack_item_label(name: str, item: dict) -> str:
        if name in POLES and item.get("craftable", True) is False:
            return f"{name}（不可合成）"
        return name

    def _render_pack_image(self, uid: str, state: dict) -> Path:
        self._ensure_accessory_state(state)
        equip = state.get("equip")
        entries: list[tuple[str, str, int, str, bool, tuple[int, int] | None]] = []
        fish_count = 0
        pole_count = 0
        type_order = {"article": 0, "fish": 1, "pole": 2, "treasure": 3, "waste": 4, "material": 5, "food": 6, "valuable": 7, "jewelry": 8, "key": 9, "box": 10, "card": 11}
        item_types = getattr(self, "_types", {})
        pack_items = list(state.get("pack", {}).items())
        pack_items.sort(key=lambda item: (type_order.get(item_types.get(item[0], ""), 99), item[0]))
        for name, value in pack_items:
            if isinstance(value, list):
                pole_count += len(value)
                sorted_poles = sorted(
                    value,
                    key=lambda item: f"{item.get('durable', 0)}/{item.get('maintenance', 0)}"
                    f"/{item.get('induce', 0)}/{item.get('favor', 0)}",
                )
                entries.extend(
                    (
                        self._pack_item_label(name, item),
                        name,
                        1,
                        f"耐久 {item.get('durable', 0)}  ·  修复 {item.get('maintenance', 0)}\n"
                        f"诱钓 {self._enchant_label(item.get('induce', 0))}  · 眷顾 {self._enchant_label(item.get('favor', 0))}",
                        True,
                        None,
                    )
                    for item in sorted_poles
                )
            elif int(value) > 0:
                active_uses = int(state.get("active_consumables", {}).get(name, 0))
                progress = None
                if name in EQUIPMENT and EQUIPMENT[name]["kind"] == "consumable" and active_uses > 0:
                    uses = int(EQUIPMENT[name]["uses"])
                    capacity = max(uses, ((active_uses + uses - 1) // uses) * uses)
                    progress = (active_uses, capacity)
                display_name = name
                detail = ""
                if name in PERMANENT_ACCESSORY_EFFECTS:
                    accessory_level = self._accessory_level(state, name)
                    display_name = f"{name}（{self._enchant_label(accessory_level)}级）"
                    detail = "已装备" if name in state["accessories"] else "未装备"
                entries.append((display_name, name, int(value), detail, False, progress))
                if name in FISH:
                    fish_count += int(value)

        for name, info in EQUIPMENT.items():
            if info["kind"] != "consumable":
                continue
            active_uses = int(state.get("active_consumables", {}).get(name, 0))
            if active_uses <= 0 or any(entry[1] == name for entry in entries):
                continue
            uses = int(info["uses"])
            capacity = max(uses, ((active_uses + uses - 1) // uses) * uses)
            entries.append((name, name, 0, "", False, (active_uses, capacity)))

        valuable_names = {name for table in ZONE_VALUABLES.values() for name in table}
        wastes = set(getattr(self, "_wastes", ["垃圾"]))

        def pack_group(name: str) -> str:
            if name in POLES:
                return "鱼竿"
            if name in FISH or name in KING_INFO or name in HIDDEN_FISH:
                return "鱼与鱼王"
            if name in EQUIPMENT and EQUIPMENT[name]["kind"] == "consumable":
                return "消耗品"
            if name in EQUIPMENT or name in PERMANENT_ACCESSORY_EFFECTS or name in PIECE_INFO:
                return "装备与饰品"
            if name in valuable_names:
                return "贵重品"
            if name in MATERIAL_NAMES:
                return "材料"
            if name in FOOD_RESTORE:
                return "食物"
            if name in wastes:
                return "垃圾"
            if name in KEY_NAMES.values() or name in BOX_NAMES.values():
                return "钥匙与箱子"
            if name in CARD_NAMES:
                return "神谕卡片"
            if name in ITEMS:
                return "宝藏"
            return "其他"

        group_order = (
            "鱼竿", "鱼与鱼王", "装备与饰品", "消耗品", "贵重品", "材料",
            "食物", "垃圾", "宝藏", "钥匙与箱子", "神谕卡片", "其他",
        )
        grouped = {group: [] for group in group_order}
        for entry in entries:
            grouped[pack_group(entry[1])].append(entry)

        economy = self._plugin_loader.get_plugin("GroupEconomy") if hasattr(self, "_plugin_loader") else None
        if economy is not None:
            self._economy = economy
        account = self._economy.store.account(uid)
        level = int(state.get("fish_level", max(1, level_for_xp(int(state.get("fish_xp", 0))))))
        total_xp = int(state.get("fish_xp", 0))
        current_level_xp = xp_required_for_level(level)
        next_level_xp = xp_required_for_level(level + 1)
        level_span = max(1, next_level_xp - current_level_xp)
        level_progress = max(0.0, min(1.0, (total_xp - current_level_xp) / level_span))
        daily_limit = self._daily_limit(state)
        width = 1240
        margin = 42
        gap = 22
        header_height = 174
        profile_height = 168
        equip_height = 224
        # Rod cards contain two attribute lines; leave enough room below the icon
        # so the second line stays inside its card and cannot overlap the next row.
        inventory_row_height = 106
        inventory_height = 78 + 50 + 28
        if any(grouped.values()):
            inventory_height = 78 + sum(
                34 + max(1, (len(rows) + 1) // 2) * inventory_row_height
                for rows in grouped.values()
                if rows
            ) + 28
        height = header_height + 26 + profile_height + 20 + equip_height + 20 + inventory_height + 42
        image = PILImage.new("RGBA", (width, height), (244, 248, 249, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, width, header_height), radius=26, fill=(33, 68, 82, 255))
        draw.rectangle((0, header_height - 20, width, header_height), fill=(33, 68, 82, 255))
        draw.text((margin, 34), "钓鱼背包", fill=(250, 252, 252, 255), font=self._font(62, bold=True))
        draw.text((margin, 116), f"用户 {uid}  ·  物品、装备与成长状态", fill=(190, 222, 225, 255), font=self._font(25))

        profile_y = header_height + 26
        self._card(draw, (margin, profile_y, width - margin, profile_y + profile_height))
        avatar_path = CommonUtil.get_avatar(uid)
        avatar_box = (margin + 24, profile_y + 27, margin + 124, profile_y + 127)
        try:
            avatar = PILImage.open(avatar_path).convert("RGBA")
            avatar = avatar.resize((100, 100), PILImage.Resampling.LANCZOS)
            mask = PILImage.new("L", (100, 100), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, 99, 99), fill=255)
            image.paste(avatar, (avatar_box[0], avatar_box[1]), mask)
        except (OSError, ValueError):
            draw.ellipse(avatar_box, fill=(222, 234, 235, 255), outline=(184, 202, 205, 255), width=2)
        draw.text((margin + 148, profile_y + 25), "钓鱼档案", fill=(40, 61, 68, 255), font=self._font(28, bold=True))
        draw.text((margin + 148, profile_y + 66), f"Lv {level}", fill=(40, 61, 68, 255), font=self._font(24, bold=True))
        draw.text((margin + 148, profile_y + 96), f"经验 {max(0, total_xp - current_level_xp)}/{level_span}", fill=(92, 112, 117, 255), font=self._font(18))
        self._draw_bar(draw, (margin + 148, profile_y + 122, margin + 360, profile_y + 136), level_progress)
        metrics = (
            ("今日钓鱼", f"{min(daily_limit, int(state.get('fish_today', 0)))}/{daily_limit}"),
            ("钱包", f"{int(account['balance'])} 金币"),
            ("背包统计", f"{fish_count} 条鱼 · {pole_count} 支竿"),
        )
        metric_width = 210
        metric_start = margin + 478
        for index, (label, value) in enumerate(metrics):
            x = metric_start + index * (metric_width + 12)
            draw.text((x, profile_y + 35), label, fill=(92, 112, 117, 255), font=self._font(20))
            draw.text((x, profile_y + 75), value, fill=(40, 61, 68, 255), font=self._font(26, bold=True))
        badges = []
        if fish_count > 100:
            badges.append("钓鱼佬")
        if pole_count > 10:
            badges.append("维修大师")
        if badges:
            draw.text((width - margin - 28, profile_y + 127), " · ".join(badges) + " 已激活", fill=(61, 145, 112, 255), font=self._font(18), anchor="ra")
        accessory_status = "、".join(
            f"{name}{self._enchant_label(self._accessory_level(state, name))}"
            for name in state["accessories"]
        ) or "无"
        draw.text(
            (margin + 148, profile_y + 142),
            f"饰品槽位 {len(state['accessories'])}/{state['accessory_slots']}：{accessory_status}",
            fill=(38, 144, 158, 255),
            font=self._font(17),
        )

        equip_y = profile_y + profile_height + 20
        self._card(draw, (margin, equip_y, width - margin, equip_y + equip_height))
        draw.text((margin + 28, equip_y + 20), "当前装备", fill=(40, 61, 68, 255), font=self._font(31, bold=True))
        draw.text((width - margin - 28, equip_y + 30), f"钓鱼力 {fishing_power(state)}", fill=(38, 144, 158, 255), font=self._font(24, bold=True), anchor="ra")
        if equip:
            icon = self._resource_image(equip["name"], (126, 126))
            if icon:
                image.alpha_composite(icon, (margin + 28, equip_y + 70))
            text_x = margin + 190
            max_durable = 999 if equip["name"] == AXOLOTL else POLES[equip["name"]][0]
            draw.text((text_x, equip_y + 76), equip["name"], fill=(40, 61, 68, 255), font=self._font(30, bold=True))
            draw.text((text_x, equip_y + 126), f"耐久 {equip.get('durable', 0)}/{max_durable}", fill=(92, 112, 117, 255), font=self._font(20))
            self._draw_bar(draw, (text_x + 155, equip_y + 130, width - margin - 38, equip_y + 147), int(equip.get("durable", 0)) / max_durable)
            draw.text((text_x, equip_y + 164), f"维修 {equip.get('maintenance', 0)}/10  ·  诱钓 {self._enchant_label(equip.get('induce', 0))}  ·  海之眷顾 {self._enchant_label(equip.get('favor', 0))}", fill=(38, 144, 158, 255), font=self._font(20))
        else:
            draw.text((margin + 30, equip_y + 102), "没有装备任何鱼竿", fill=(92, 112, 117, 255), font=self._font(25))

        inventory_y = equip_y + equip_height + 20
        self._card(draw, (margin, inventory_y, width - margin, inventory_y + inventory_height))
        draw.text((margin + 28, inventory_y + 20), "背包物品", fill=(40, 61, 68, 255), font=self._font(31, bold=True))
        draw.text((width - margin - 28, inventory_y + 28), "鱼竿属性：耐久 / 修复 / 诱钓 / 眷顾", fill=(92, 112, 117, 255), font=self._font(18), anchor="ra")
        if any(grouped.values()):
            tile_width = (width - margin * 2 - gap - 40) // 2
            cursor = inventory_y + 62
            for group in group_order:
                rows = grouped.get(group)
                if not rows:
                    continue
                draw.text((margin + 28, cursor), group, fill=(38, 144, 158, 255), font=self._font(22, bold=True))
                cursor += 34
                for index, (display_name, asset_name, count, detail, is_pole, progress) in enumerate(rows):
                    col, row = index % 2, index // 2
                    x = margin + 20 + col * (tile_width + gap)
                    y = cursor + row * inventory_row_height
                    self._card(draw, (x, y, x + tile_width, y + inventory_row_height - 10), fill=(248, 251, 251, 255), radius=12)
                    icon = self._resource_image(asset_name, (48, 48))
                    if icon:
                        image.alpha_composite(icon, (x + 16, y + 20))
                    text_x = x + 80
                    draw.text((text_x, y + 14), display_name, fill=(40, 61, 68, 255), font=self._font(23, bold=True))
                    if is_pole:
                        detail_font = self._font(16)
                        for detail_index, detail_line in enumerate(detail.splitlines()):
                            draw.text((text_x, y + 45 + detail_index * 20), detail_line, fill=(38, 144, 158, 255), font=detail_font)
                    elif progress:
                        active_uses, capacity = progress
                        inventory_text = (
                            f"库存 {count}  ·  激活剩余 {active_uses}/{capacity} 次"
                            if count
                            else f"激活剩余 {active_uses}/{capacity} 次"
                        )
                        draw.text((text_x, y + 45), inventory_text, fill=(92, 112, 117, 255), font=self._font(18))
                        self._draw_bar(
                            draw,
                            (text_x, y + 70, x + tile_width - 18, y + 78),
                            active_uses / capacity,
                        )
                    elif detail:
                        draw.text((text_x, y + 49), detail, fill=(38, 144, 158, 255), font=self._font(20))
                    else:
                        draw.text((text_x, y + 49), f"数量 {count}", fill=(92, 112, 117, 255), font=self._font(20))
                cursor += max(1, (len(rows) + 1) // 2) * inventory_row_height
        else:
            draw.text((margin + 30, inventory_y + 98), "背包没有存放任何东西", fill=(92, 112, 117, 255), font=self._font(25))

        output = Path(self.workspace) / "images" / f"pack_{uid}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, "PNG")
        return output

    def _render_gear_slot_catalog_image(self, uid: str, state: dict) -> Path:
        """按部位展示所有装备：初始等级/FP 与满级 FP，以及套装效果起始→最终值。"""
        slot_order = (
            ("head", "头盔"),
            ("body", "胸甲"),
            ("legs", "腿甲"),
            ("necklace", "项链"),
            ("ring", "戒指"),
        )
        sections = []
        for slot, label in slot_order:
            cards = []
            for set_name, data in SETS.items():
                piece = data.get("slots", {}).get(slot)
                if not piece:
                    continue
                start = int(data.get("start", 1))
                lines = []
                for needed, effects in sorted(data.get("effects", {}).items()):
                    parts = []
                    for key, value in effects.items():
                        initial = float(value) * start / 20.0
                        if key == "daily":
                            initial = float(int(initial))
                        elif key == "fragment":
                            initial = round(initial, 1)
                        parts.append(f"{format_effect(key, initial)} → {format_effect(key, value)}")
                    lines.extend(
                        self._wrap_text(f"{needed}件：" + "，".join(parts), self._font(14), 430)
                    )
                cards.append((set_name, piece, data, start, lines))
            if cards:
                cards.sort(key=lambda item: (item[3], set_label(item[0])))
                sections.append((label, cards))

        width = 1600
        margin = 42
        gap = 18
        header_height = 172
        column_width = (width - margin * 2 - 40 - gap * 2) // 3
        sections_height = 0
        prepared_sections = []
        for label, cards in sections:
            max_lines = max(len(card[4]) for card in cards)
            card_height = 96 + max_lines * 20 + 12
            rows = (len(cards) + 2) // 3
            section_height = 56 + rows * (card_height + 12) + 20
            prepared_sections.append((label, cards, card_height, section_height))
            sections_height += section_height + 16

        accessories = {
            "财富浮标": "Ⅰ/Ⅱ/Ⅲ：宝藏×1.35/1.50/1.65；鱼王咬钩率×1.20/1.28/1.36",
            "坚韧钓组": "Ⅰ/Ⅱ/Ⅲ：鱼王上钩率 +12/16/20 个百分点（最低 5%）",
            "黄金磁石": "Ⅰ/Ⅱ/Ⅲ：宝藏品质 +1/2/3；宝藏售价 ×1.25/1.35/1.50；鱼价 ×1.08/1.12/1.16；后置鱼王售价 ×10/30/100（前置与常驻封顶 ×3/5/8）",
            "幸运海星": "Ⅰ/Ⅱ/Ⅲ：普通碎片概率 +16%/20%/24%",
            "魔法鱼饵": "10 竿：等待 ×0.85，稀有鱼权重 ×1.25",
            "隐秘诱饵": "6 竿：隐藏鱼触发 ×1.5",
            "万能鱼饵": "5 竿：每竿兜底一条普通鱼",
        }
        accessory_height = 60 + 4 * 150
        height = header_height + 24 + sections_height + accessory_height + 42
        image = PILImage.new("RGBA", (width, height), (244, 248, 249, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, width, header_height), radius=26, fill=(33, 68, 82, 255))
        draw.rectangle((0, header_height - 20, width, header_height), fill=(33, 68, 82, 255))
        draw.text((margin, 34), "装备图鉴", fill=(250, 252, 252, 255), font=self._font(62, bold=True))
        draw.text((margin, 116), "按部位展示 · 初始值 → 最终值（20级）", fill=(190, 222, 225, 255), font=self._font(24))
        draw.text((width - margin, 118), "装备图鉴 套装 查看套装总览", fill=(220, 234, 235, 255), font=self._font(19), anchor="ra")

        section_y = header_height + 24
        for label, cards, card_height, section_height in prepared_sections:
            self._card(draw, (margin, section_y, width - margin, section_y + section_height))
            draw.text((margin + 26, section_y + 16), f"{label}（{len(cards)}）", fill=(40, 61, 68, 255), font=self._font(27, bold=True))
            draw.text((width - margin - 26, section_y + 22), "起始 FP → 满级 FP · 套装效果 起始→最终", fill=(92, 112, 117, 255), font=self._font(16), anchor="ra")
            for index, (set_name, piece, data, start, lines) in enumerate(cards):
                col, row = index % 3, index // 3
                x = margin + 20 + col * (column_width + gap)
                y = section_y + 54 + row * (card_height + 12)
                self._card(draw, (x, y, x + column_width, y + card_height), fill=(249, 250, 247, 255), radius=12)
                icon = self._resource_image(piece, (40, 40))
                if icon:
                    image.alpha_composite(icon, (x + 12, y + 12))
                draw.text((x + 58, y + 10), piece, fill=(40, 61, 68, 255), font=self._font(17, bold=True))
                draw.text((x + 58, y + 36), f"{set_label(set_name)} · 起始 {start} 级", fill=(92, 112, 117, 255), font=self._font(14))
                start_fp = fp_for_level(start)
                final_fp = fp_for_level(20)
                draw.text((x + 58, y + 58), f"FP {start_fp} → {final_fp}", fill=(38, 144, 158, 255), font=self._font(15, bold=True))
                source = "获取：合成" if data.get("craftable") else "获取：掉落 / 开箱"
                draw.text((x + column_width - 12, y + 12), source, fill=(112, 96, 54, 255), font=self._font(12), anchor="ra")
                for line_index, line in enumerate(lines):
                    draw.text((x + 12, y + 82 + line_index * 20), line, fill=(70, 83, 88, 255), font=self._font(14))
            section_y += section_height + 16

        self._card(draw, (margin, section_y, width - margin, section_y + accessory_height))
        draw.text((margin + 24, section_y + 18), "饰品与消耗品", fill=(40, 61, 68, 255), font=self._font(30, bold=True))
        draw.text((width - margin - 24, section_y + 26), "碎片合成 · 金币手续费", fill=(92, 112, 117, 255), font=self._font(19), anchor="ra")
        cell_gap = 14
        cell_width = (width - margin * 2 - 48 - cell_gap) // 2
        for index, (name, effect) in enumerate(accessories.items()):
            col, row = index % 2, index // 2
            x = margin + 24 + col * (cell_width + cell_gap)
            y = section_y + 58 + row * 150
            icon = self._resource_image(name, (40, 40))
            if icon:
                image.alpha_composite(icon, (x, y + 2))
            draw.text((x + 50, y), name, fill=(40, 61, 68, 255), font=self._font(21, bold=True))
            effect_lines = self._wrap_text(effect, self._font(16), cell_width - 60)[:2]
            for line_index, line in enumerate(effect_lines):
                draw.text((x + 50, y + 28 + line_index * 22), line, fill=(92, 112, 117, 255), font=self._font(16))
            source = f"获取：合成{name}（{EQUIPMENT[name]['fragments']} 碎片 + {EQUIPMENT_CRAFT_FEES[name]} 金币）"
            source_lines = self._wrap_text(source, self._font(15), cell_width - 60)[:2]
            for line_index, line in enumerate(source_lines):
                draw.text((x + 50, y + 84 + line_index * 20), line, fill=(112, 96, 54, 255), font=self._font(15))

        output = Path(self.workspace) / "images" / f"gearcatalog_slots_{uid}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, "PNG")
        return output

    def _render_gear_catalog_image(self, uid: str, state: dict, only_accessories: bool = False) -> Path:
        """装备与饰品图鉴：图标、套装效果、获取途径。"""
        accessories = {
            "财富浮标": "Ⅰ/Ⅱ/Ⅲ：宝藏×1.35/1.50/1.65；鱼王咬钩率×1.20/1.28/1.36",
            "坚韧钓组": "Ⅰ/Ⅱ/Ⅲ：鱼王上钩率 +12/16/20 个百分点（最低 5%）",
            "黄金磁石": "Ⅰ/Ⅱ/Ⅲ：宝藏品质 +1/2/3；宝藏售价 ×1.25/1.35/1.50；鱼价 ×1.08/1.12/1.16；后置鱼王售价 ×10/30/100（前置与常驻封顶 ×3/5/8）",
            "幸运海星": "Ⅰ/Ⅱ/Ⅲ：普通碎片概率 +16%/20%/24%",
            "魔法鱼饵": "10 竿：等待 ×0.85，稀有鱼权重 ×1.25",
            "隐秘诱饵": "6 竿：隐藏鱼触发 ×1.5",
            "万能鱼饵": "5 竿：每竿兜底一条普通鱼",
        }
        set_names = [] if only_accessories else list(SETS)
        width = 1600
        margin = 42
        gap = 26
        column_width = (width - margin * 2 - gap) // 2
        header_height = 176
        accessory_height = 60 + 4 * 150
        slot_text = {"head": "头", "body": "胸", "legs": "腿", "necklace": "链", "ring": "戒"}

        def source_text(name: str, data: dict) -> str:
            if name == "神谕":
                return "获取：集齐 9 张神谕卡片后，在钓鱼工坊领取"
            if not data.get("craftable"):
                return "获取：钓鱼掉落 / 开箱"
            head = data.get("slots", {}).get("head") or f"{name}头盔"
            return f"获取：合成渔具 单件（如 {head}，材料见合成界面）"

        layouts = []
        for name in set_names:
            data = SETS[name]
            effect_lines: list[str] = []
            for pieces, effects in sorted(data.get("effects", {}).items()):
                text = f"{pieces}件：" + "、".join(format_effect(key, value) for key, value in effects.items())
                effect_lines.extend(self._wrap_text(text, self._font(18), column_width - 36))
            source_lines = self._wrap_text(source_text(name, data), self._font(17), column_width - 36)
            card_height = 110 + len(effect_lines) * 24 + 6 + len(source_lines) * 22 + 14
            layouts.append((name, data, effect_lines, source_lines, max(210, card_height)))
        rows = [layouts[index:index + 2] for index in range(0, len(layouts), 2)]
        row_heights = [max(item[4] for item in row) for row in rows]
        sets_height = (60 + sum(height + 16 for height in row_heights)) if set_names else 0
        height = header_height + 24 + sets_height + 20 + accessory_height + 42
        image = PILImage.new("RGBA", (width, height), (244, 248, 249, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, width, header_height), radius=26, fill=(33, 68, 82, 255))
        draw.rectangle((0, header_height - 20, width, header_height), fill=(33, 68, 82, 255))
        title = "饰品图鉴" if only_accessories else "装备与饰品图鉴"
        subtitle = "4 件永久饰品与 3 种消耗品" if only_accessories else "13 套装备 · 6 个槽位 · 4 件永久饰品与 3 种消耗品"
        draw.text((margin, 34), title, fill=(250, 252, 252, 255), font=self._font(60, bold=True))
        draw.text((margin, 112), subtitle, fill=(190, 222, 225, 255), font=self._font(24))

        row_y = header_height + 24 + 60
        row_index = 0
        for row in rows:
            row_height = row_heights[row_index]
            row_index += 1
            for col, (name, data, effect_lines, source_lines, _card_height) in enumerate(row):
                x = margin + col * (column_width + gap)
                y = row_y
                self._card(draw, (x, y, x + column_width, y + row_height), fill=(251, 252, 252, 255))
                icon = self._resource_image(set_icon(name, data), (58, 58))
                if icon:
                    image.alpha_composite(icon, (x + 18, y + 18))
                draw.text((x + 90, y + 16), set_label(name), fill=(40, 61, 68, 255), font=self._font(27, bold=True))
                draw.text(
                    (x + 90, y + 52),
                    f"起始 {data['start']} 级 · " + "".join(slot_text.get(slot, slot) for slot in data.get("slots", {})),
                    fill=(92, 112, 117, 255),
                    font=self._font(19),
                )
                draw.text((x + 18, y + 84), "套装效果", fill=(38, 144, 158, 255), font=self._font(19, bold=True))
                for line_index, text in enumerate(effect_lines):
                    draw.text((x + 18, y + 110 + line_index * 24), text, fill=(70, 83, 88, 255), font=self._font(18))
                source_y = y + 110 + len(effect_lines) * 24 + 6
                for line_index, line in enumerate(source_lines):
                    draw.text((x + 18, source_y + line_index * 22), line, fill=(92, 112, 117, 255), font=self._font(17))
            row_y += row_height + 16

        section_y = header_height + 24 + sets_height + 20
        self._card(draw, (margin, section_y, width - margin, section_y + accessory_height))
        draw.text((margin + 24, section_y + 18), "饰品与消耗品", fill=(40, 61, 68, 255), font=self._font(30, bold=True))
        draw.text((width - margin - 24, section_y + 26), "碎片合成 · 金币手续费", fill=(92, 112, 117, 255), font=self._font(19), anchor="ra")
        cell_gap = 14
        cell_width = (width - margin * 2 - 48 - cell_gap) // 2
        for index, (name, effect) in enumerate(accessories.items()):
            col, row = index % 2, index // 2
            x = margin + 24 + col * (cell_width + cell_gap)
            y = section_y + 58 + row * 150
            icon = self._resource_image(name, (40, 40))
            if icon:
                image.alpha_composite(icon, (x, y + 2))
            draw.text((x + 50, y), name, fill=(40, 61, 68, 255), font=self._font(21, bold=True))
            effect_lines = self._wrap_text(effect, self._font(16), cell_width - 60)[:2]
            for line_index, line in enumerate(effect_lines):
                draw.text((x + 50, y + 28 + line_index * 22), line, fill=(92, 112, 117, 255), font=self._font(16))
            source = f"获取：合成{name}（{EQUIPMENT[name]['fragments']} 碎片 + {EQUIPMENT_CRAFT_FEES[name]} 金币）"
            source_lines = self._wrap_text(source, self._font(15), cell_width - 60)[:2]
            for line_index, line in enumerate(source_lines):
                draw.text((x + 50, y + 84 + line_index * 20), line, fill=(112, 96, 54, 255), font=self._font(15))

        output = Path(self.workspace) / "images" / f"gearcatalog_{uid}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, "PNG")
        return output

    def _render_gear_image(self, uid: str, state: dict) -> Path:
        equipment = state.get("equipment") or {}
        jewelry = state.get("jewelry") or {}
        width = 1440
        margin = 46
        gap = 24
        header_height = 172
        card_width = (width - margin * 2 - gap) // 2
        tile_height = 104
        slots = [(slot, "equipment") for slot in GEAR_SLOTS] + [(slot, "jewelry") for slot in JEWELRY_SLOTS]
        left_rows = (len(slots) + 1) // 2
        left_height = 74 + left_rows * (tile_height + 14)
        worn = worn_sets(equipment, jewelry)
        effects = scaled_effects(equipment, jewelry)
        right_lines: list[str] = []
        for set_name, info in worn.items():
            right_lines.append(f"{set_label(set_name)} {info['count']}件 · 均级 {info['avg_level']:.0f}")
        if not right_lines:
            right_lines.append("当前没有穿戴成套装备")
        if effects:
            right_lines.append("")
            right_lines.append("套装效果")
            for key, value in effects.items():
                right_lines.append(format_effect(key, value))
        bag = state.get("gear_bag") or []
        right_lines.append("")
        right_lines.append(f"未装备 {len(bag)} 件")
        right_lines.append(f"装备 FP {fishing_power(state)}")
        right_font = self._font(22)
        prepared_right: list[str] = []
        for line in right_lines:
            prepared_right.extend(self._wrap_text(line, right_font, card_width - 52) if line else [""])
        right_height = 74 + len(prepared_right) * 34 + 20
        height = header_height + 28 + max(left_height, right_height) + 46
        image = PILImage.new("RGBA", (width, height), (244, 248, 249, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, width, header_height), radius=26, fill=(33, 68, 82, 255))
        draw.rectangle((0, header_height - 20, width, header_height), fill=(33, 68, 82, 255))
        draw.text((margin, 34), "钓鱼装备", fill=(250, 252, 252, 255), font=self._font(60, bold=True))
        draw.text((margin, 112), "六个装备位 · 套装进度 · 戒指占两个独立格", fill=(190, 222, 225, 255), font=self._font(24))

        def draw_slot(slot: str, group: str, x: int, y: int) -> None:
            item = (equipment if group == "equipment" else jewelry).get(slot)
            self._card(draw, (x, y, x + card_width // 2 - 10, y + tile_height), fill=(250, 252, 252, 255), radius=14)
            label = SLOT_LABELS.get(slot, slot)
            draw.text((x + 16, y + 10), label, fill=(92, 112, 117, 255), font=self._font(19))
            if not item:
                draw.text((x + 16, y + 48), "空", fill=(150, 166, 170, 255), font=self._font(24))
                return
            icon = self._resource_image(str(item.get("name", "")), (48, 48))
            if icon:
                image.alpha_composite(icon, (x + 12, y + 44))
            text_x = x + 68 if icon else x + 16
            draw.text((text_x, y + 40), str(item.get("name", "")), fill=(40, 61, 68, 255), font=self._font(22, bold=True))
            draw.text(
                (text_x, y + 70),
                f"{int(item.get('level', 1))}级 · FP {fp_for_level(int(item.get('level', 1)))}",
                fill=(38, 144, 158, 255),
                font=self._font(19),
            )

        left_y = header_height + 26
        self._card(draw, (margin, left_y, margin + card_width, left_y + left_height))
        draw.text((margin + 26, left_y + 20), "装备位", fill=(40, 61, 68, 255), font=self._font(30, bold=True))
        tile_width = card_width // 2 - 10
        for index, (slot, group) in enumerate(slots):
            col, row = index % 2, index // 2
            draw_slot(
                slot,
                group,
                margin + 24 + col * (tile_width + 12),
                left_y + 60 + row * (tile_height + 14),
            )

        right_x = margin + card_width + gap
        right_y = header_height + 26
        self._card(draw, (right_x, right_y, right_x + card_width, right_y + right_height))
        draw.text((right_x + 26, right_y + 20), "套装与效果", fill=(40, 61, 68, 255), font=self._font(30, bold=True))
        line_y = right_y + 66
        for line in prepared_right:
            draw.text((right_x + 26, line_y), line, fill=(70, 83, 88, 255) if line else (0, 0, 0, 0), font=right_font)
            line_y += 34

        output = Path(self.workspace) / "images" / f"gear_{uid}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, "PNG")
        return output

    def _render_craft_image(self, uid: str, state: dict) -> Path:
        sets = [(name, data) for name, data in SETS.items() if data.get("craftable")]
        width = 1600
        margin = 42
        gap = 10
        set_col_width = 210
        slot_names = ("head", "body", "legs", "necklace", "ring")
        slot_labels = ("头盔", "胸甲", "腿甲", "项链", "戒指")
        cell_width = (width - margin * 2 - set_col_width - gap * (len(slot_names) + 1)) // len(slot_names)
        header_height = 172
        table_header_height = 46
        row_height = 170
        height = header_height + 24 + table_header_height + len(sets) * row_height + 42
        image = PILImage.new("RGBA", (width, height), (244, 248, 249, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, width, header_height), radius=26, fill=(33, 68, 82, 255))
        draw.rectangle((0, header_height - 20, width, header_height), fill=(33, 68, 82, 255))
        draw.text((margin, 34), "合成渔具", fill=(250, 252, 252, 255), font=self._font(62, bold=True))
        draw.text((margin, 116), "单件合成 · 头 / 胸 / 腿 / 项链 / 戒指可自由混搭", fill=(190, 222, 225, 255), font=self._font(24))
        draw.text((width - margin, 118), "合成渔具 铜制头盔", fill=(220, 234, 235, 255), font=self._font(21), anchor="ra")

        pack = state.get("pack") or {}
        balance = None
        economy = getattr(self, "_economy", None)
        if economy is not None:
            try:
                balance = int(economy.store.account(str(uid))["balance"])
            except Exception:
                balance = None

        header_y = header_height + 24
        self._card(draw, (margin, header_y, width - margin, header_y + table_header_height), fill=(229, 238, 240, 255), radius=12)
        draw.text((margin + 16, header_y + 12), "套装", fill=(40, 61, 68, 255), font=self._font(21, bold=True))
        for index, label in enumerate(slot_labels):
            x = margin + set_col_width + gap + index * (cell_width + gap)
            draw.text((x + 14, header_y + 12), label, fill=(40, 61, 68, 255), font=self._font(21, bold=True))

        for row_index, (set_name, data) in enumerate(sets):
            y = header_y + table_header_height + row_index * row_height
            self._card(draw, (margin, y + 4, margin + set_col_width, y + row_height - 10), fill=(251, 252, 252, 255), radius=12)
            set_icon_name = set_icon(set_name, data)
            icon = self._resource_image(set_icon_name, (44, 44))
            if icon:
                image.alpha_composite(icon, (margin + 14, y + 18))
            draw.text((margin + 68, y + 16), set_label(set_name), fill=(40, 61, 68, 255), font=self._font(20, bold=True))
            draw.text((margin + 68, y + 48), f"起始 {data['start']} 级", fill=(92, 112, 117, 255), font=self._font(16))
            draw.text((margin + 14, y + 92), "可单件合成", fill=(38, 144, 158, 255), font=self._font(15))

            for slot_index, slot in enumerate(slot_names):
                piece = data.get("slots", {}).get(slot)
                x = margin + set_col_width + gap + slot_index * (cell_width + gap)
                self._card(draw, (x, y + 4, x + cell_width, y + row_height - 10), fill=(249, 250, 247, 255), radius=12)
                if not piece:
                    draw.text((x + cell_width // 2, y + 54), "—", fill=(170, 185, 188, 255), font=self._font(28), anchor="mm")
                    continue
                recipe = craft_recipe(piece)
                if recipe is None:
                    draw.text((x + 12, y + 18), piece, fill=(40, 61, 68, 255), font=self._font(17, bold=True))
                    draw.text((x + 12, y + 48), "不可合成", fill=(194, 91, 86, 255), font=self._font(15))
                    continue
                enough = True
                material_parts = []
                for item, need in recipe["items"].items():
                    owned = int(pack.get(item, 0))
                    if owned < need:
                        enough = False
                    material_parts.append(f"{item} {owned}/{need}")
                gold = int(recipe["gold"])
                if balance is not None and balance < gold:
                    enough = False
                icon = self._resource_image(piece, (30, 30))
                if icon:
                    image.alpha_composite(icon, (x + 10, y + 12))
                draw.text((x + 44, y + 9), piece, fill=(40, 61, 68, 255), font=self._font(17, bold=True))
                for line_index, line in enumerate(material_parts[:4]):
                    draw.text((x + 10, y + 36 + line_index * 14), line, fill=(92, 112, 117, 255), font=self._font(12))
                draw.text((x + 10, y + 94), f"{gold} 金币", fill=(112, 96, 54, 255), font=self._font(15, bold=True))
                draw.text(
                    (x + cell_width - 10, y + 116),
                    "可合成" if enough else "材料不足",
                    fill=(61, 145, 112, 255) if enough else (194, 91, 86, 255),
                    font=self._font(15, bold=True),
                    anchor="ra",
                )

        output = Path(self.workspace) / "images" / f"craft_{uid}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, "PNG")
        return output

    def _render_store_image(self, uid: str, state: dict | None = None) -> Path:
        discounts, inventory = self._shop_data()
        state = state or {}
        pack = state.get("pack", {})
        player_level = int(state.get("fish_level", 1) or 1)
        valuable_names = {name for table in ZONE_VALUABLES.values() for name in table}
        wastes = set(getattr(self, "_wastes", ["垃圾"]))

        def type_label(name: str) -> str:
            if name in FISH or name in KING_INFO:
                return "鱼与鱼王"
            if name in valuable_names:
                return "贵重品"
            if name in FOOD_RESTORE:
                return "食物"
            if name in wastes:
                return "垃圾"
            if name in ITEMS:
                return "宝藏"
            if name in SHOP_EQUIPMENT_PRICES:
                return "消耗品"
            return "其他"

        def sellable(name: str) -> bool:
            if name in MATERIAL_NAMES or name in CARD_NAMES:
                return False
            if name in KEY_NAMES.values() or name in BOX_NAMES.values():
                return False
            if name in PIECE_INFO and name not in valuable_names:
                return False
            if name in EQUIPMENT:
                return False
            return (
                name in FISH or name in KING_INFO or name in valuable_names
                or name in FOOD_RESTORE or name in wastes or name in ITEMS
                or name in SHOP_EQUIPMENT_PRICES
            )

        group_order = ("鱼与鱼王", "贵重品", "食物", "垃圾", "宝藏", "消耗品", "其他")

        rod_names = ["木竿", *SHOP_ROD_PRICES]
        rod_rows = [
            (name, self._shop_price(name, discounts), max(1, rod_unlock_level(name)), ROD_POWER.get(name, 0))
            for name in rod_names
        ]

        shop_groups: dict[str, list[tuple[str, int, int]]] = {}
        for name, count in inventory.items():
            if name == "初始木竿" or name in POLES:
                continue
            try:
                number = int(count)
            except (TypeError, ValueError):
                continue
            if number <= 0:
                continue
            shop_groups.setdefault(type_label(name), []).append(
                (name, number, self._shop_price(name, discounts))
            )
        for rows in shop_groups.values():
            rows.sort(key=lambda row: row[0])

        player_groups: dict[str, list[tuple[str, int, int, int]]] = {}
        for name, value in pack.items():
            if name in POLES or isinstance(value, list):
                continue
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if number <= 0 or not sellable(name):
                continue
            player_groups.setdefault(type_label(name), []).append(
                (name, number, self._shop_price(name, discounts), int(discounts.get(name, 100)) - 100)
            )
        for rows in player_groups.values():
            rows.sort(key=lambda row: row[0])

        width = 1400
        margin = 46
        gap = 22
        header_height = 174
        tile_width = (width - margin * 2 - gap - 40) // 2
        rod_tile_width = (width - margin * 2 - gap * 2 - 40) // 3
        rod_height = 70 + 3 * 88 + 20
        shop_height = 70 + 50 + 20
        if shop_groups:
            shop_height = 70 + sum(
                34 + max(1, (len(rows) + 1) // 2) * 78
                for rows in shop_groups.values()
            ) + 20
        player_height = 70 + 50 + 20
        if player_groups:
            player_height = 70 + sum(
                34 + max(1, (len(rows) + 1) // 2) * 80
                for rows in player_groups.values()
            ) + 20
        height = header_height + 24 + rod_height + 16 + shop_height + 16 + player_height + 42
        image = PILImage.new("RGBA", (width, height), (244, 248, 249, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, width, header_height), radius=26, fill=(33, 68, 82, 255))
        draw.rectangle((0, header_height - 20, width, header_height), fill=(33, 68, 82, 255))
        draw.text((margin, 34), "钓鱼商店", fill=(250, 252, 252, 255), font=self._font(62, bold=True))
        draw.text((margin, 116), "今日上架 · 你的可售物品", fill=(190, 222, 225, 255), font=self._font(25))
        draw.text((width - margin, 116), "价格随每日折扣变化", fill=(220, 234, 235, 255), font=self._font(21), anchor="ra")

        section_y = header_height + 24
        self._card(draw, (margin, section_y, width - margin, section_y + rod_height))
        draw.text((margin + 28, section_y + 20), "鱼竿", fill=(40, 61, 68, 255), font=self._font(31, bold=True))
        draw.text((width - margin - 28, section_y + 28), "Lv · FP · 价格", fill=(92, 112, 117, 255), font=self._font(19), anchor="ra")
        for index, (name, price, unlock, fp) in enumerate(rod_rows):
            col, row = index % 3, index // 3
            x = margin + 20 + col * (rod_tile_width + gap)
            y = section_y + 64 + row * 88
            self._card(draw, (x, y, x + rod_tile_width, y + 78), fill=(248, 251, 251, 255), radius=12)
            icon = self._resource_image(name, (42, 42))
            if icon:
                image.alpha_composite(icon, (x + 14, y + 18))
            draw.text((x + 66, y + 10), name, fill=(40, 61, 68, 255), font=self._font(21, bold=True))
            level_color = (92, 112, 117, 255) if player_level >= unlock else (194, 91, 86, 255)
            draw.text((x + 66, y + 42), f"Lv{unlock} · FP{fp}", fill=level_color, font=self._font(17))
            draw.text((x + rod_tile_width - 16, y + 24), f"{price} 金币", fill=(112, 96, 54, 255), font=self._font(20, bold=True), anchor="ra")

        shop_y = section_y + rod_height + 16
        self._card(draw, (margin, shop_y, width - margin, shop_y + shop_height))
        draw.text((margin + 28, shop_y + 20), "商店上架", fill=(40, 61, 68, 255), font=self._font(31, bold=True))
        draw.text((width - margin - 28, shop_y + 28), "库存 · 单价", fill=(92, 112, 117, 255), font=self._font(19), anchor="ra")
        if shop_groups:
            cursor = shop_y + 64
            for group in group_order:
                rows = shop_groups.get(group)
                if not rows:
                    continue
                draw.text((margin + 28, cursor), group, fill=(38, 144, 158, 255), font=self._font(22, bold=True))
                cursor += 34
                for index, (name, count, price) in enumerate(rows):
                    col, row = index % 2, index // 2
                    x = margin + 20 + col * (tile_width + gap)
                    y = cursor + row * 78
                    self._card(draw, (x, y, x + tile_width, y + 68), fill=(249, 250, 247, 255), radius=12)
                    icon = self._resource_image(name, (40, 40))
                    if icon:
                        image.alpha_composite(icon, (x + 16, y + 14))
                    draw.text((x + 70, y + 11), name, fill=(40, 61, 68, 255), font=self._font(19))
                    draw.text((x + 70, y + 40), f"库存 {count}", fill=(92, 112, 117, 255), font=self._font(17))
                    draw.text((x + tile_width - 18, y + 25), f"{price} 金币", fill=(112, 96, 54, 255), font=self._font(20, bold=True), anchor="ra")
                cursor += max(1, (len(rows) + 1) // 2) * 78
        else:
            draw.text((margin + 30, shop_y + 98), "当前商店没有上架物品", fill=(92, 112, 117, 255), font=self._font(25))

        player_y = shop_y + shop_height + 16
        self._card(draw, (margin, player_y, width - margin, player_y + player_height))
        draw.text((margin + 28, player_y + 20), "你的背包可出售", fill=(40, 61, 68, 255), font=self._font(31, bold=True))
        total_value = sum(
            price * count
            for rows in player_groups.values()
            for _name, count, price, _change in rows
        )
        draw.text((width - margin - 28, player_y + 28), f"可售总价 {total_value} 金币", fill=(112, 96, 54, 255), font=self._font(21, bold=True), anchor="ra")
        if player_groups:
            cursor = player_y + 64
            for group in group_order:
                rows = player_groups.get(group)
                if not rows:
                    continue
                draw.text((margin + 28, cursor), group, fill=(38, 144, 158, 255), font=self._font(22, bold=True))
                cursor += 34
                for index, (name, count, price, change) in enumerate(rows):
                    col, row = index % 2, index // 2
                    x = margin + 20 + col * (tile_width + gap)
                    y = cursor + row * 76
                    self._card(draw, (x, y, x + tile_width, y + 70), fill=(248, 251, 251, 255), radius=12)
                    icon = self._resource_image(name, (38, 38))
                    if icon:
                        image.alpha_composite(icon, (x + 14, y + 14))
                    draw.text((x + 64, y + 9), name, fill=(40, 61, 68, 255), font=self._font(19))
                    draw.text((x + 64, y + 38), f"×{count}", fill=(92, 112, 117, 255), font=self._font(17))
                    draw.text((x + tile_width - 18, y + 20), f"{price} 金币", fill=(112, 96, 54, 255), font=self._font(19, bold=True), anchor="ra")
                    change_text = f"+{change}%" if change > 0 else f"{change}%"
                    change_color = (194, 91, 86, 255) if change > 0 else (61, 145, 112, 255) if change < 0 else (92, 112, 117, 255)
                    draw.text((x + tile_width - 18, y + 44), change_text, fill=change_color, font=self._font(16), anchor="ra")
                cursor += max(1, (len(rows) + 1) // 2) * 80
        else:
            draw.text((margin + 30, player_y + 98), "背包没有可出售物品", fill=(92, 112, 117, 255), font=self._font(25))
        draw.text((margin, height - 28), "材料、装备、卡片不可出售，因此不在列表中显示；当前收购价已含当日折扣，实际出售另计鱼点税、黄金磁石与装备加成。", fill=(92, 112, 117, 255), font=self._font(18))
        output = Path(self.workspace) / "images" / f"store_{uid}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, "PNG")
        return output

    async def _fish(self, event, state: dict, text: str) -> None:
        if not isinstance(event, _McFishEvent):
            event = _McFishEvent(event, self)
        uid = str(event.sender.user_id)
        match = re.fullmatch(r"进行(?:(\d+|[零〇一二两三四五六七八九十百千]+)次)?钓鱼", text)
        if not match:
            return
        requested = parse_count_token(match.group(1) or "1")
        if requested is None or requested <= 0:
            await event.reply(text="钓鱼次数格式不正确")
            return
        has_level_state = "fish_level" in state or "fish_xp" in state
        level = int(state.get("fish_level", level_for_xp(int(state.get("fish_xp", 0)))))
        daily_limit = self._daily_limit(state) if has_level_state else int(self.get_config("fish_limit", 50))
        if requested > self.MAX_FISH_BATCH:
            await event.reply(text=f"单次最多进行{self.MAX_FISH_BATCH}次钓鱼")
            return
        if requested > daily_limit:
            await event.reply(text=f"你当前等级每日最多钓鱼{daily_limit}次")
            return
        number = requested
        equip = state.get("equip")
        remaining_today = daily_limit - int(state["fish_today"])
        if remaining_today <= 0:
            await event.reply(text=f"今天你已经进行{daily_limit}次钓鱼了.\n游戏虽好,但请不要沉迷。")
            return
        zone_key = str(state.get("fishing_zone", daily_fishing_zone()))
        zone_cap = FISHING_ZONE_DAILY_CAPS.get(zone_key, daily_limit)
        zone_used = int((state.get("zone_fish_today") or {}).get(zone_key, 0))
        zone_remaining = zone_cap - zone_used
        if zone_remaining <= 0:
            await event.reply(text=f"{fishing_zone_label(zone_key)}：今日该鱼点已完成 {zone_cap}/{zone_cap} 次")
            return
        if requested > zone_remaining:
            await event.reply(
                text=f"{fishing_zone_label(zone_key)}：今日该鱼点剩余 {zone_remaining} 次（上限 {zone_cap}）"
            )
            return
        pole_count = sum(
            len(value) for name, value in state.get("pack", {}).items()
            if name in POLES and isinstance(value, list)
        )
        if pole_count > 30:
            await event.reply(text=f"你有{pole_count}支鱼竿,大于30支的玩家不允许钓鱼")
            return
        if not equip:
            if self._has_usable_pole(state):
                await event.reply(text="你背包里已有鱼竿，请先装备后再钓鱼")
                return
            price = int(self.get_config("starting_pole_price", 100))
            balance = int(self._economy.store.account(uid)["balance"])
            if balance < price and self._starter_pole_available(state):
                self._grant_starter_pole(uid, state)
                equip = state["equip"]
                await event.reply(text="本次补偿一把耐久30的木竿（每3天一次）")
            else:
                if int(state.get("emergency_pole_count", 0)) >= 3:
                    await event.reply(text="请装备鱼竿后钓鱼")
                    return
                result = await self._choose(
                    '你尚未装备鱼竿,是否花费100购买鱼竿?\n回答"是"或"否"',
                    event,
                    choices={"是": "buy", "否": "cancel"},
                    timeout=120,
                    timeout_reply="等待超时,取消购买",
                    cancel_reply="已取消购买",
                )
                if result.key != "buy":
                    return
                if not await self._buy_emergency_pole(event, state):
                    return
                equip = state["equip"]
        axolotl = equip["name"] == AXOLOTL
        if has_level_state and not axolotl and level < rod_unlock_level(equip["name"]):
            await event.reply(text=f"你的钓鱼等级为{level}，无法使用{equip['name']}，需要等级{rod_unlock_level(equip['name'])}")
            return
        if not axolotl and int(equip["durable"]) <= 0:
            await event.reply(text=f"当前鱼竿没有耐久了，请先修复或更换鱼竿；{self._fish_count_text(state)}")
            return
        number = min(number, remaining_today)
        if not axolotl:
            number = min(number, int(equip["durable"]))
        if number <= 0:
            await event.reply(text="当前没有可用的钓鱼次数或鱼竿耐久")
            return
        fish_attempts = number
        result_note = ""
        base = copy.deepcopy(state)
        if axolotl:
            fish_available = sum(
                int(state["pack"].get(name, 0)) for name in FISH
            )
            required_fish = number * 3
            if fish_available < required_fish:
                state["equip"] = None
                self._save(uid, state)
                await event.reply(text="美西螈因为没吃到鱼,钓鱼时一直没回来,你失去了美西螈")
                return
            result_note = "(美西螈每竿吃3条鱼，掉落翻5倍)"
        initial_magic_bait = int(state.get("active_consumables", {}).get("魔法鱼饵", 0)) > 0
        initial_wait_rate = EQUIPMENT["魔法鱼饵"]["effects"]["wait_rate"] if initial_magic_bait else 1.0
        # 钓鱼力越高等待越短（设计值：等待 × 1/(1 + FP/100)），与鱼竿诱钓、鱼饵叠加。
        initial_wait_rate *= 1.0 / (1.0 + fishing_power(state) / 100.0)
        initial_wait_seconds = fishing_wait_for_result(
            "鳕鱼", induce=int(equip.get("induce", 0)), wait_rate=initial_wait_rate
        )
        await event.reply(text="你开始去钓鱼了,每条鱼会分别等待上钩,请耐心等待")
        results = {}
        attempt_results: list[dict] = []
        earned_xp = 0
        starting_level = int(state.get("fish_level", level))
        single_image: Path | None = None
        fishing_started_at = datetime.now()
        current_time = fishing_started_at
        current_wait_seconds = initial_wait_seconds
        windows = self._fish_windows()
        completed_attempts = 0
        for attempt_index in range(fish_attempts):
            current_time += timedelta(seconds=current_wait_seconds)
            scheduled_at = current_time
            wait_seconds = (scheduled_at - datetime.now()).total_seconds()
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            # 每竿开始前读回最新存档：等待期间玩家的购买/合成/装备改动不会被本竿覆盖。
            state = self._state(uid)
            equip = state.get("equip") or equip
            base = copy.deepcopy(state)
            zone_now = str(state.get("fishing_zone", daily_fishing_zone()))
            if int(state.get("fish_today", 0)) >= self._daily_limit(state):
                result_note += "\n今日次数已用尽，提前结束。"
                break
            zone_used = int((state.get("zone_fish_today") or {}).get(zone_now, 0))
            if zone_used >= FISHING_ZONE_DAILY_CAPS.get(zone_now, daily_limit):
                result_note += f"\n{fishing_zone_label(zone_now)}今日次数已用尽，提前结束。"
                break
            if not axolotl and (not equip or int(equip.get("durable", 0)) <= 0):
                state["equip"] = None
                result_note += "\n鱼竿耐久已耗尽，提前结束。"
                base = self._save_fishing_state(uid, state, base)
                break
            # 本竿结算后立即占用次数与耐久，避免批次取消时整批预扣。
            state["fish_today"] = int(state.get("fish_today", 0)) + 1
            zone_today = state.setdefault("zone_fish_today", {})
            zone_today[zone_now] = zone_used + 1
            state["fish_bless"] = int(state.get("fish_bless", 0)) + 1
            completed_attempts += 1
            if axolotl:
                need = 3
                for fish_name in FISH:
                    if need <= 0:
                        break
                    take = min(need, int(state["pack"].get(fish_name, 0)))
                    if take:
                        self._remove(state, fish_name, take)
                        need -= take
                if need > 0:
                    state["equip"] = None
                    self._save(uid, state)
                    result_note += "\n美西螈没有足够的鱼，提前结束。"
                    break
            else:
                equip["durable"] = int(equip.get("durable", 0)) - 1
                state["equip"] = equip
            # 海豚等动态背包效果必须按本竿最新存档计算，不能沿用批次开始时的边界。
            drop_boundaries = self._zone_boundaries(state)
            self._advance_tasks(state, casts=1)
            # 卡牌独立于普通掉落：每竿判定一次，按鱼点单独累计保底。
            card_pity = state.setdefault("card_pity", {})
            card_name = roll_card(zone_now, int(card_pity.get(zone_now, 0)))
            if card_name:
                card_pity[zone_now] = 0
                cards = state.setdefault("cards", {})
                cards[card_name] = 1
                earned_xp += 20
                self._add_fishing_xp(state, 20)
                result_note += f"\n钓到了神谕卡片·{card_name}（{len(cards)}/9）"
            else:
                card_pity[zone_now] = int(card_pity.get(zone_now, 0)) + 1
            magic_bait_active = int(state.get("active_consumables", {}).get("魔法鱼饵", 0)) > 0
            universal_bait = int(state.get("active_consumables", {}).get("万能鱼饵", 0)) > 0
            hidden_lure_active = int(state.get("active_consumables", {}).get("隐秘诱饵", 0)) > 0
            current_wait_rate = EQUIPMENT["魔法鱼饵"]["effects"]["wait_rate"] if magic_bait_active else 1.0
            gear = self._gear_effects(state)
            current_wait_rate *= 1.0 / (1.0 + gear.get("wait", 0.0))
            # 钓鱼力直接缩短等待：等待 × 1/(1 + FP/100)；最低等待仍由 fishing_wait_for_result 兜底。
            current_wait_rate *= 1.0 / (1.0 + fishing_power(state) / 100.0)
            bait_save = min(0.9, float(gear.get("bait_save", 0.0)))
            for bait_name, bait_active in (
                ("魔法鱼饵", magic_bait_active),
                ("万能鱼饵", universal_bait),
                ("隐秘诱饵", hidden_lure_active),
            ):
                if bait_active and not (bait_save > 0 and random.random() < bait_save):
                    state["active_consumables"][bait_name] = int(state["active_consumables"].get(bait_name, 0)) - 1
            current_zone = str(state.get("fishing_zone", daily_fishing_zone()))
            active = [
                window for window in windows
                if window.contains(scheduled_at)
                and FISH_KINGS.get(window.king_name, {}).get("zone") == current_zone
            ]
            eligible_active = []
            for window in active:
                window_king = FISH_KINGS.get(window.king_name)
                prerequisite_name = window_king.get("requires") if window_king else None
                prerequisite_ready = not prerequisite_name or int(
                    state.get("king_stats", {}).get(prerequisite_name, {}).get("bitten", 0)
                ) > 0
                if prerequisite_ready:
                    eligible_active.append(window)
            king_window = random.choice(eligible_active) if eligible_active else None
            hidden_name = hidden_fish_trigger(
                zone=str(state.get("fishing_zone", "deep")),
                hidden_lure=hidden_lure_active,
                rate=0.045 * (1.0 + gear.get("hidden", 0.0) + gear.get("legendary", 0.0)),
            )
            if hidden_name:
                attempt_xp = fishing_xp(hidden_name)
                earned_xp += attempt_xp
                self._add_fishing_xp(state, attempt_xp)
                self._add(state, hidden_name)
                self._record_item_zone(state, hidden_name, zone_now)
                self._record_capture(state, hidden_name, caught_at=scheduled_at)
                self._advance_tasks(state, catches={hidden_name: 1})
                results[hidden_name] = results.get(hidden_name, 0) + 1
                attempt_results.append({"time": scheduled_at, "result": hidden_name, "quantity": 1, "xp": attempt_xp})
                current_wait_seconds = fishing_wait_for_result(
                    hidden_name, induce=int(equip.get("induce", 0)), wait_rate=current_wait_rate
                )
                result_note += f"\n水中出现隐秘的光影……成功钓起【{hidden_name}】！"
                self._grant_fishing_fragments(state, "rare")
                self._record_attempt(uid, attempt_index, scheduled_at, equip["name"], "隐秘诱饵", king_window, "hidden_fish")
                base = self._save_fishing_state(uid, state, base)
                continue
            king = FISH_KINGS.get(king_window.king_name) if king_window else None
            prerequisite = king.get("requires") if king else None
            prerequisite_ready = not prerequisite or int(
                state.get("king_stats", {}).get(prerequisite, {}).get("bitten", 0)
            ) > 0
            bite_rate, hook_rate = (
                fish_king_probabilities(king_window.king_name, equip["name"], prerequisite_caught=prerequisite_ready)
                if king else (0.0, 0.0)
            )
            wealth_multiplier = self._accessory_effect(state, "财富浮标", "king_clue_rate", 1.0)
            king_bite = king_bite_rate(bite_rate, wealth_bobber=wealth_multiplier)
            king_bite = min(1.0, king_bite * (1.0 + gear.get("king_bite", 0.0)))
            king_bites = bool(king and random.random() < king_bite)
            if king_bites:
                king_stats = state.setdefault("king_stats", {}).setdefault(king_window.king_name, {})
                king_stats["bitten"] = int(king_stats.get("bitten", 0)) + 1
                post_window = next(
                    (
                        window for window in active
                        if FISH_KINGS.get(window.king_name, {}).get("requires") == king_window.king_name
                    ),
                    None,
                )
                if king["kind"] == "prerequisite" and post_window:
                    bite_choice = await self._choose(
                        f"{FISH_KING_PROMPTS[king_window.king_name]['trigger']}\n咬钩阶段技能：\n1. 保留目标（进入上钩判定）\n2. 以小钓大（牺牲当前鱼，尝试后置鱼王）\n3. 见好就收（放弃追逐）\n不回复将自动保留目标",
                        event,
                        choices={"1": "hold_target", "2": "mooch_post", "3": "give_up"},
                        timeout=30,
                        timeout_reply="等待超时，自动保留目标",
                        cancel_reply="选择无效，自动保留目标",
                    )
                else:
                    bite_choice = await self._choose(
                        f"{FISH_KING_PROMPTS[king_window.king_name]['trigger']}\n咬钩阶段技能：\n1. 保留目标（进入上钩判定）\n2. 见好就收（放弃追逐）\n不回复将自动保留目标",
                        event,
                        choices={"1": "hold_target", "2": "give_up"},
                        timeout=30,
                        timeout_reply="等待超时，自动保留目标",
                        cancel_reply="选择无效，自动保留目标",
                    )
                bite_skill = bite_choice.key if bite_choice.key in ("hold_target", "mooch_post", "give_up") else bite_skill_from_reply(bite_choice.text or "")
                if bite_skill is None:
                    # 批量钓鱼时玩家常常来不及回复：默认保留目标，只有明确选择才放弃。
                    bite_skill = "hold_target"
                if bite_skill == "mooch_post" and post_window is None:
                    # 没有后置鱼王窗口时“以小钓大”退化为见好就收，避免空引用。
                    bite_skill = "give_up"
                if bite_skill == "give_up":
                    king_stats["escaped"] = int(king_stats.get("escaped", 0)) + 1
                    attempt_results.append({"time": scheduled_at, "result": "鱼王机会（放弃）", "quantity": 0, "xp": 0})
                    current_wait_seconds = fishing_wait_for_result(
                        "鱼王机会（放弃）", induce=int(equip.get("induce", 0)), wait_rate=current_wait_rate
                    )
                    result_note += f"\n你放弃了这次鱼王机会。\n{FISH_KING_PROMPTS[king_window.king_name]['disappear']}"
                    self._record_attempt(uid, attempt_index, scheduled_at, equip["name"], "咬钩阶段放弃", king_window, "king_give_up")
                    base = self._save_fishing_state(uid, state, base)
                    continue
                if bite_skill == "mooch_post":
                    king_window = post_window
                    king = FISH_KINGS[king_window.king_name]
                    king_stats = state.setdefault("king_stats", {}).setdefault(king_window.king_name, {})
                    king_stats["bitten"] = int(king_stats.get("bitten", 0)) + 1
                    _, hook_rate = fish_king_probabilities(king_window.king_name, equip["name"], prerequisite_caught=True)
                    result_note += f"\n你牺牲了当前前置鱼王，尝试引出后置鱼王！\n{FISH_KING_PROMPTS[king_window.king_name]['trigger']}"

                hook_choice = await self._choose(
                    "已进入上钩判定。\n上钩阶段技能：\n1. 精准提竿（上钩率基准，鱼体最大）\n2. 强力提竿（上钩率×1.10，鱼体较大）\n3. 谦逊之饵（上钩率×1.24，鱼体随机，计入路径）\n4. 雄心之饵（上钩率×1.45，可出极限尺寸，计入路径）\n不回复按1稳妥收线",
                    event,
                    choices={number: strategy for number, strategy in (("1", "direct_reel"), ("2", "small_to_large"), ("3", "humble_bait"), ("4", "ambitious_bait"))},
                    timeout=30,
                    timeout_reply="等待超时，按1稳妥收线",
                    cancel_reply="选择无效，按1稳妥收线",
                )
                hook_skill = hook_skill_from_reply(hook_choice.text or "")
                if hook_skill is None:
                    hook_skill = {
                        "direct_reel": "precision_hookset",
                        "small_to_large": "powerful_hookset",
                        "humble_bait": "patient_control",
                        "ambitious_bait": "burst_reel",
                    }.get(hook_choice.key, "precision_hookset")
                strategy = {
                    "precision_hookset": "direct_reel",
                    "powerful_hookset": "small_to_large",
                    "patient_control": "humble_bait",
                    "burst_reel": "ambitious_bait",
                }[hook_skill]
                guaranteed = path_is_satisfied(state, king_window.king_name)
                tough_points = self._accessory_effect(state, "坚韧钓组", "king_hook_success_points", 0)
                success = random.random() < king_success_rate(
                    hook_rate,
                    guaranteed=guaranteed,
                    tough_tackle=tough_points,
                    rate_multiplier=hook_skill_rate_multiplier(hook_skill),
                )
                if success:
                    if guaranteed:
                        # 保证上钩会消耗本周路径，避免一次刷满后永久保证。
                        consume_path(state, king_window.king_name)
                    else:
                        advance_path(state, king_window.king_name, strategy)
                    attempt_xp = fishing_xp(king_window.king_name)
                    earned_xp += attempt_xp
                    self._add_fishing_xp(state, attempt_xp)
                    length = king_length_for_hook(king_window.king_name, hook_skill)
                    self._add(state, king_window.king_name)
                    self._add(state, "鱼王鳞片", 1)
                    self._record_capture(state, king_window.king_name, caught_at=scheduled_at)
                    king_stats["caught"] = int(king_stats.get("caught", 0)) + 1
                    self._advance_tasks(state, king=1)
                    base_price = int(king["base_price"] * length / ((king["length"][0] + king["length"][1]) / 2))
                    discounts_now, _ = self._shop_data()
                    discounted = int(base_price * discounts_now.get(king_window.king_name, 100) // 100)
                    king_price_fn = king_sale_price if king.get("kind") == "final" else capped_king_sale_price
                    price = int(
                        king_price_fn(
                            discounted,
                            gold_magnet=self._accessory_effect(state, "黄金磁石", "king_price", 1.0),
                        )
                        * self._sale_tax_factor(king_window.king_name, state)
                    )
                    state.setdefault("king_sale_prices", {}).setdefault(king_window.king_name, []).append(
                        {"base": base_price, "price": price}
                    )
                    results[king_window.king_name] = results.get(king_window.king_name, 0) + 1
                    attempt_results.append({"time": scheduled_at, "result": king_window.king_name, "quantity": 1, "xp": attempt_xp})
                    current_wait_seconds = fishing_wait_for_result(
                        king_window.king_name, induce=int(equip.get("induce", 0)), wait_rate=current_wait_rate
                    )
                    self._grant_fishing_fragments(state, "king")
                    state.setdefault("king_results", {})[king_window.king_name] = {"length": length, "price": price}
                    result_note += f"\n{FISH_KING_PROMPTS[king_window.king_name]['success'].format(length=length, price=price)}\n鱼王鳞片 ×1（工坊交付材料）"
                    self._record_attempt(uid, attempt_index, scheduled_at, equip["name"], "鱼王策略", king_window, "king_success")
                else:
                    king_stats["escaped"] = int(king_stats.get("escaped", 0)) + 1
                    attempt_results.append({"time": scheduled_at, "result": "鱼王脱钩", "quantity": 0, "xp": 0})
                    current_wait_seconds = fishing_wait_for_result(
                        "鱼王脱钩", induce=int(equip.get("induce", 0)), wait_rate=current_wait_rate
                    )
                    result_note += f"\n{FISH_KING_PROMPTS[king_window.king_name]['disappear']}"
                    self._record_attempt(uid, attempt_index, scheduled_at, equip["name"], "鱼王策略", king_window, "king_escape")
                base = self._save_fishing_state(uid, state, base)
                continue
            resident_name = resident_king_for_zone(current_zone)
            resident_today = state.setdefault("resident_king_today", {})
            resident_ready = bool(
                resident_name
                and int(resident_today.get(resident_name, 0)) < RESIDENT_KING_DAILY_LIMIT
                and fishing_power(state) >= resident_king_fp(resident_name)
            )
            if resident_ready and random.random() < resident_king_bite_chance(
                resident_name,
                gear_bonus=float(gear.get("king_bite", 0.0)) + float(gear.get("legendary", 0.0)),
                clue_multiplier=self._accessory_effect(state, "财富浮标", "king_clue_rate", 1.0),
                bait_bonus=resident_king_bait_multiplier(state.get("active_consumables", {})),
            ):
                resident_stats = state.setdefault("king_stats", {}).setdefault(resident_name, {})
                resident_stats["bitten"] = int(resident_stats.get("bitten", 0)) + 1
                tough_points = self._accessory_effect(state, "坚韧钓组", "king_hook_success_points", 0)
                if random.random() < resident_king_hook_chance(resident_name, tough_points=tough_points):
                    length = resident_king_length(resident_name)
                    base_price = resident_king_price(resident_name, length)
                    resident_stats["caught"] = int(resident_stats.get("caught", 0)) + 1
                    resident_today[resident_name] = int(resident_today.get(resident_name, 0)) + 1
                    attempt_xp = fishing_xp(resident_name)
                    earned_xp += attempt_xp
                    self._add_fishing_xp(state, attempt_xp)
                    self._add(state, resident_name)
                    self._record_capture(state, resident_name, caught_at=scheduled_at)
                    self._advance_tasks(state, king=1)
                    discounts_now, _ = self._shop_data()
                    discounted = int(base_price * discounts_now.get(resident_name, 100) // 100)
                    price = int(
                        capped_king_sale_price(
                            discounted,
                            gold_magnet=self._accessory_effect(state, "黄金磁石", "king_price", 1.0),
                        )
                        * self._sale_tax_factor(resident_name, state)
                    )
                    state.setdefault("king_sale_prices", {}).setdefault(resident_name, []).append(
                        {"base": base_price, "price": price}
                    )
                    state.setdefault("king_results", {})[resident_name] = {"length": length, "price": price}
                    results[resident_name] = results.get(resident_name, 0) + 1
                    attempt_results.append({"time": scheduled_at, "result": resident_name, "quantity": 1, "xp": attempt_xp})
                    current_wait_seconds = fishing_wait_for_result(
                        resident_name, induce=int(equip.get("induce", 0)), wait_rate=current_wait_rate
                    )
                    self._grant_fishing_fragments(state, "king")
                    result_note += "\n" + RESIDENT_KINGS[resident_name]["trigger"] + "\n" + RESIDENT_KINGS[resident_name]["success"].format(length=length, price=price)
                    self._record_attempt(uid, attempt_index, scheduled_at, equip["name"], "常驻鱼王", None, "resident_king_success")
                else:
                    resident_stats["escaped"] = int(resident_stats.get("escaped", 0)) + 1
                    attempt_results.append({"time": scheduled_at, "result": "常驻鱼王脱钩", "quantity": 0, "xp": 0})
                    current_wait_seconds = fishing_wait_for_result(
                        resident_name, induce=int(equip.get("induce", 0)), wait_rate=current_wait_rate
                    )
                    result_note += f"\n{RESIDENT_KINGS[resident_name]['trigger']}\n常驻鱼王【{resident_name}】脱钩了。"
                    self._record_attempt(uid, attempt_index, scheduled_at, equip["name"], "常驻鱼王", None, "resident_king_escape")
                base = self._save_fishing_state(uid, state, base)
                continue
            name, quantity = self._original_drop(
                state, drop_boundaries, magic_bait=magic_bait_active
            )
            if name:
                self._record_item_zone(state, name, zone_now)
            if universal_bait and name is None:
                name = random.choice(list(FISH))
                quantity = 1
            if name in FISH and gear.get("double_fish", 0.0) > 0 and random.random() < gear["double_fish"]:
                quantity += 1
            if name is None:
                catalog = getattr(self, "_catalog", {})
                if catalog.get("分类") and catalog.get("物品"):
                    results["赛博空气"] = results.get("赛博空气", 0) + 1
                    single_image = None
                    attempt_results.append({"time": scheduled_at, "result": "赛博空气", "quantity": 1, "xp": 0})
                    current_wait_seconds = fishing_wait_for_result(
                        "赛博空气", induce=int(equip.get("induce", 0)), wait_rate=current_wait_rate
                    )
                    self._record_attempt(uid, attempt_index, scheduled_at, equip["name"], "默认鱼饵", king_window, "air")
                    base = self._save_fishing_state(uid, state, base)
                    continue
                else:
                    roll = random.random()
                    if roll < 0.30:
                        name = random.choice(self._wastes if hasattr(self, "_wastes") else ["垃圾"])
                        quantity = 1
                    elif roll < 0.90:
                        name = random.choice(list(FISH))
                        quantity = 1
                    else:
                        name = "钓鱼碎片"
                        quantity = random.randint(1, 2)
            if name in POLES:
                # Fishing drops must use a fresh roll; shop, exchange, and crafting
                # rods intentionally keep their own fixed or inherited attributes.
                self._add(state, name, quantity, pole=True, random_pole=True)
            else:
                self._add(state, name, quantity)
                if getattr(self, "_types", {}).get(name) == "treasure" and self._accessory_level(state, "黄金磁石"):
                    quality = state.setdefault("treasure_quality", {})
                    quality[name] = max(1, int(quality.get(name, 0))) + int(
                        self._accessory_effect(state, "黄金磁石", "treasure_quality", 0)
                    )
            if name in FISH:
                self._record_capture(state, name, caught_at=scheduled_at)
                self._advance_tasks(state, catches={name: max(1, int(quantity))})
                fish_xp = fishing_xp(name) * max(1, int(quantity))
                earned_xp += fish_xp
                self._add_fishing_xp(state, fish_xp)
            else:
                # P4：材料/食物 2 点，贵重品 5 点；其余非鱼类不给经验。
                item_type = getattr(self, "_types", {}).get(name, "")
                per_item = 2 if item_type in ("material", "food") else 5 if item_type == "valuable" else 0
                fish_xp = per_item * max(1, int(quantity))
                if fish_xp:
                    earned_xp += fish_xp
                    self._add_fishing_xp(state, fish_xp)
            attempt_results.append({"time": scheduled_at, "result": name, "quantity": quantity, "xp": fish_xp})
            current_wait_seconds = fishing_wait_for_result(
                name, induce=int(equip.get("induce", 0)), wait_rate=current_wait_rate
            )
            self._grant_fishing_fragments(state, "ordinary")
            if state.pop("fragment_jackpot", False):
                result_note += "\n触发碎片宝藏！额外获得50个钓鱼碎片！"
            results[name] = results.get(name, 0) + quantity
            if len(results) == 1:
                single_image = self._item_image(name)
            else:
                single_image = None
            self._record_attempt(
                uid,
                attempt_index,
                scheduled_at,
                equip["name"],
                "魔法鱼饵" if magic_bait_active else "默认鱼饵",
                king_window,
                "ordinary",
            )
            base = self._save_fishing_state(uid, state, base)
        if not axolotl and equip["durable"] <= 0 and not equip.get("unbreakable", False):
            state["equip"] = None
        
        while state["fish_bless"] >= 75:
            state["fish_bless"] -= 75
            self._add(state, "净化书")
        base = self._save_fishing_state(uid, state, base)
        if int(state.get("fish_level", 0)) > starting_level:
            result_note += f"\n恭喜你，钓鱼等级提升至{state['fish_level']}级！每日钓鱼次数上限为{daily_attempt_limit(state['fish_level'])}次。"
        detail_lines = ["\n逐竿上钩记录："]
        for index, attempt in enumerate(attempt_results, start=1):
            caught_at = attempt["time"].strftime("%m月%d日 %H:%M:%S")
            quantity = f" ×{attempt['quantity']}" if attempt["quantity"] else ""
            detail_lines.append(
                f"第{index}竿：上钩时间：{caught_at}，【{attempt['result']}】{quantity}，获得经验 {attempt['xp']} XP"
            )
        result_note = f"\n本次获得经验：{earned_xp} XP" + "\n" + "\n".join(detail_lines) + result_note
        if len(results) == 1:
            name, count = next(iter(results.items()))
            result_text = f"恭喜你钓到了{count}{name}\n{result_note}"
        else:
            result_text = f"你进行了{completed_attempts}次钓鱼,结果如下:\n"
            result_text += "".join(f"{name} : {count}\n" for name, count in results.items())
            result_text += result_note
        state["last_fishing_result"] = {
            "text": result_text,
            "image": str(single_image) if len(results) == 1 and single_image else None,
        }
        base = self._save_fishing_state(uid, state, base)
        await self._send_fishing_result(event, result_text, single_image if len(results) == 1 else None)

    async def _buy(self, event, state: dict, item_text: str) -> None:
        uid = str(event.sender.user_id); match = re.fullmatch(r"(.+?)(?:\s+(\d+))?", item_text); name = match.group(1) if match else item_text; number = int(match.group(2) or 1) if match else 1
        requested_name = name
        name = POLE_ALIASES.get(name, name)
        player_level = int(state.get("fish_level", level_for_xp(int(state.get("fish_xp", 0)))))
        if name in POLES and player_level < rod_unlock_level(name):
            await event.reply(text=f"你的钓鱼等级为{player_level}，{name}需要等级{rod_unlock_level(name)}才能购买")
            return
        if name in POLES and name != "木竿":
            number = 1
        discounts, inventory = self._shop_data()
        known_item = name in POLES or name in FISH or name in ITEMS or name in SHOP_EQUIPMENT_PRICES
        listed_item = name in inventory or (name == "木竿" and "初始木竿" in inventory)
        if (not known_item and not listed_item and name not in SHOP_ROD_PRICES) or name in ("垃圾", "宝藏诅咒"):
            await event.reply(text="商店没有这个物品")
            return
        if name == "木竿":
            initial_wood = self._shop_poles(inventory.get("初始木竿", []), "木竿")
            shop_name = "初始木竿" if initial_wood else "木竿"
        else:
            shop_name = name
        if shop_name not in inventory and name not in SHOP_ROD_PRICES:
            await event.reply(text="当前商店并没有上架该物品")
            return
        pole_count = sum(len(value) for pole, value in state["pack"].items() if pole in POLES and isinstance(value, list))
        if pole_count > 50:
            await event.reply(text=f"你有{pole_count}支鱼竿,大于50支的玩家不允许购买东西")
            return
        is_pole = name in POLES
        if is_pole and int(state.get("pole_trade_count", 0)) >= 10:
            await event.reply(text="今天鱼竿交易次数已达上限")
            return
        if name == "木竿" and shop_name == "初始木竿":
            # 常驻木竿可按数量购买，但仍受每日交易次数和持有上限约束
            number = min(number, 10 - int(state.get("pole_trade_count", 0)), 50 - pole_count)
            if number <= 0:
                await event.reply(text="当前无法购买更多鱼竿")
                return
        if not is_pole and int(state.get("item_buy_count", 0)) + number > 30:
            number = max(0, 30 - int(state.get("item_buy_count", 0)))
            if number == 0:
                await event.reply(text="一天只能购买30次物品，明天再来吧~")
                return
        if name == "净化书":
            curse_count = int(state["pack"].get("宝藏诅咒", 0))
            if curse_count <= 0:
                await event.reply(text="你身上没有诅咒,不能购买该商品")
                return
            if int(state["pack"].get("净化书", 0)) >= curse_count:
                await event.reply(text="你身上净化书已足够,不能购买该商品")
                return
            if number > curse_count:
                number = curse_count
        if is_pole:
            if name in SHOP_ROD_PRICES:
                available = [{
                    "durable": POLES[name][0],
                    "maintenance": 0,
                    "induce": 0,
                    "favor": 0,
                }]
            else:
                available = self._shop_poles(inventory[shop_name], name if shop_name != "初始木竿" else "木竿")
            if shop_name == "初始木竿":
                # 常驻商品：库存不足时按购买数量补足同规格木竿
                while len(available) < number:
                    available.append({"durable": POLES["木竿"][0], "maintenance": 0, "induce": 0, "favor": 0})
            if len(available) < number:
                await event.reply(text="当前商店并没有上架该物品")
                return
            if len(available) > 1 and number == 1 and shop_name != "初始木竿":
                choices = {str(i): str(i) for i in range(len(available))}
                listing = "\n".join(f"[{i}]{name}({p['durable']}/{p['maintenance']}/{p['induce']}/{p['favor']}) 价格:{self._pole_price(p, discounts, name if shop_name != '初始木竿' else '木竿')}" for i, p in enumerate(available))
                result = await self._choose(
                    f"找到以下物品:\n{listing}\n————————\n输入对应序号进行购买,或回复“取消”取消",
                    event, choices=choices, timeout=60, timeout_reply="等待超时,取消购买", cancel_reply="已取消购买",
                )
                if result.key is None: return
                selected = available.pop(int(result.key))
                selected_poles = [selected]
            else:
                selected_poles = available[:number]
                del available[:number]
            custom_price = int(getattr(self, "_shop_custom_prices", {}).get(name, 0) or 0)
            if custom_price:
                price = custom_price * len(selected_poles)
            elif name in SHOP_ROD_PRICES:
                price = self._shop_price(name, discounts) * len(selected_poles)
            elif shop_name == "初始木竿":
                price = self._shop_price(shop_name, discounts) * len(selected_poles)
            else:
                price = sum(self._pole_price(p, discounts, name) for p in selected_poles)
        else:
            if int(inventory.get(shop_name, 0)) < number:
                await event.reply(text="当前商店并没有上架该物品")
                return
            price = self._shop_price(shop_name, discounts) * number
        coupon_count = min(3, number, int(state.get("half_price_count", 0))) if not is_pole else 0
        if coupon_count:
            unit_price = price // number
            price -= (unit_price // 2) * coupon_count
        coupon_text = f"\n(半价福利还有{int(state.get('half_price_count', 0)) - coupon_count}次)" if coupon_count else ""
        curse = int(state["pack"].get("宝藏诅咒", 0))
        curse_text = f"\n(你身上绑定了{curse}层诅咒)" if curse else ""
        price = price * (100 + curse * 10) // 100
        confirmation = await self._choose(
            f'你确定花费{price}购买{number}个{requested_name}?{coupon_text}{curse_text}\n回答"是"或"否"',
            event, choices={"是": "confirm", "否": "cancel"}, timeout=60,
            timeout_reply="等待超时,取消购买", cancel_reply="已取消购买",
        )
        if confirmation.key != "confirm":
            return
        if not self._economy_change(uid, -price, "mcfish_purchase"):
            await event.reply(text=f"你身上的钱不够支付{curse_text}")
            return
        self._advance_tasks(state, spend=price)
        if coupon_count:
            state["half_price_count"] -= coupon_count
        if is_pole:
            for selected in selected_poles:
                self._add(
                    state,
                    "木竿" if shop_name == "初始木竿" else name,
                    pole=True,
                    pole_data=selected,
                    craftable=name not in SHOP_ROD_PRICES,
                )
            state["pole_trade_count"] = int(state.get("pole_trade_count", 0)) + len(selected_poles)
            state["trade_count"] = int(state.get("trade_count", 0)) + len(selected_poles)
            if state["trade_count"] % 10 == 0:
                self._add(state, "宝藏诅咒")
        else:
            self._add(state, name, number)
            inventory[shop_name] -= number
            if inventory[shop_name] <= 0:
                if shop_name in SHOP_EQUIPMENT_STOCK:
                    inventory[shop_name] = 0
                else:
                    inventory.pop(shop_name, None)
            state["item_buy_count"] = int(state.get("item_buy_count", 0)) + number
            if not coupon_count:
                state["buy_times"] = int(state.get("buy_times", 0)) + 1
                if state["buy_times"] > 20:
                    state["buy_times"] -= 20
                    state["half_price_count"] = 3
        if is_pole and name not in SHOP_ROD_PRICES:
            if available:
                inventory[shop_name] = available
            else:
                inventory.pop(shop_name, None)
        if hasattr(self, "_db"):
            self._db.execute("UPDATE shop SET inventory = ? WHERE day = ?", (json.dumps(inventory, ensure_ascii=False), date.today().isoformat()))
            self._db.commit()
        self._save(uid, state); await event.reply(text=f"你用{price}购买了{number}{requested_name}")

    def _parse_sell_items(self, item_text: str, prices: dict[str, int], extra_names=()) -> list[tuple[str, int]] | None:
        aliases = dict(POLE_ALIASES)
        names = sorted(set(prices) | set(POLES) | set(aliases) | set(extra_names), key=len, reverse=True)
        name_pattern = "|".join(re.escape(name) for name in names)
        pattern = re.compile(rf"(?P<name>{name_pattern})(?:\s*(?P<number>\d+))?")
        items = []
        position = 0
        for match in pattern.finditer(item_text):
            if item_text[position:match.start()].strip():
                return None
            name = aliases.get(match.group("name"), match.group("name"))
            number = int(match.group("number") or 1)
            if number <= 0:
                return None
            items.append((name, number))
            position = match.end()
        if not items or item_text[position:].strip():
            return None
        merged: dict[str, int] = {}
        for name, number in items:
            merged[name] = merged.get(name, 0) + number
        return list(merged.items())

    async def _sell(self, event, state: dict, item_text: str) -> None:
        uid = str(event.sender.user_id)
        prices = getattr(self, "_prices", dict(FISH) | {pole: price for pole, (_, price) in POLES.items()} | ITEMS)
        valuable_names = {name for table in ZONE_VALUABLES.values() for name in table}
        blocked_names = (
            set(MATERIAL_NAMES)
            | set(CARD_NAMES)
            | set(KEY_NAMES.values())
            | set(BOX_NAMES.values())
            | set(EQUIPMENT)
            | (set(PIECE_INFO) - valuable_names)
            | {"鱼王鳞片", "钓鱼碎片"}
        )
        if item_text.strip() == "垃圾":
            waste_names = set(getattr(self, "_wastes", ["垃圾"]))
            wastes = [
                name
                for name, value in state["pack"].items()
                if name in waste_names and int(value) > 0
            ]
            if not wastes:
                await event.reply(text="你的背包不存在该物品", at_sender=False)
                return
            discounts, _ = self._shop_data()
            gross = sum(prices.get(name, 10) * discounts.get(name, 100) // 100 * int(state["pack"].get(name, 0)) for name in wastes)
            price = gross * 8 // 10
            found = "\n".join(
                f"[{index}]{name}  数量: {int(state['pack'][name])}"
                for index, name in enumerate(wastes)
            )
            if len(wastes) > 1:
                await event.reply(text=f"找到以下物品:\n{found}\n————————", at_sender=False)
            result = await self._choose(
                f'是否接受回收站将以{price}收购全部垃圾?\n回答"是"或"否"',
                event,
                choices={"是": "confirm", "否": "cancel"},
                timeout=60,
                timeout_reply="等待超时,取消出售垃圾",
                cancel_reply="已取消出售",
            )
            if result.key != "confirm":
                return
            curse = int(state["pack"].get("宝藏诅咒", 0))
            curse_text = f"\n(你身上绑定了{curse}层诅咒)" if curse else ""
            price = price * (100 - 10 * curse) // 100
            for waste in wastes:
                self._remove(state, waste, int(state["pack"].get(waste, 0)))
            self._economy_change(uid, price, "mcfish_waste_sale")
            self._save(uid, state)
            await event.reply(text=f"出售成功,你赚到了{price}{curse_text}", at_sender=False)
            return
        items = self._parse_sell_items(item_text.strip(), prices, blocked_names)
        if not items:
            await event.reply(text="出售格式错误，请使用：出售物品 数量 物品 数量")
            return
        items = [(name, 1 if name in POLES else number) for name, number in items]
        if any(name in POLES for name, _number in items) and int(state.get("pole_trade_count", 0)) >= 10:
            await event.reply(text="一天只能交易10把鱼竿,明天再来售卖吧")
            return
        for name, number in items:
            if name in blocked_names:
                await event.reply(text=f"{name} 不可交易")
                return
            if name == "宝藏诅咒":
                await event.reply(text="宝藏诅咒无法交易")
                return
            if name not in prices and name not in POLES:
                await event.reply(text="背包没有这个物品")
                return
            owned = state["pack"].get(name, []) if name in POLES else state["pack"].get(name, 0)
            owned_count = len(owned) if isinstance(owned, list) else int(owned)
            if owned_count < number:
                await event.reply(text="背包数量不足")
                return
        discounts, _ = self._shop_data()
        selected_indices: dict[str, int] = {}
        sold_poles: list[tuple[str, dict, int]] = []
        for name, number in items:
            if name in POLES and number == 1:
                poles = state["pack"].get(name, [])
                if len(poles) > 1:
                    choices = {str(i): str(i) for i in range(len(poles))}
                    listing = "\n".join(f"[{i}]{name}({p.get('durable', 0)}/{p.get('maintenance', 0)}/{p.get('induce', 0)}/{p.get('favor', 0)})" for i, p in enumerate(poles))
                    result = await self._choose(
                        f"找到以下物品:\n{listing}\n————————\n输入对应序号进行出售,或回复“取消”取消",
                        event, choices=choices, timeout=60, timeout_reply="等待超时,取消出售", cancel_reply="已取消出售",
                    )
                    if result.key is None:
                        return
                    selected_indices[name] = int(result.key)
        gross = 0
        for name, number in items:
            if name in POLES:
                poles = state["pack"][name]
                if name in selected_indices:
                    pole = poles[selected_indices[name]]
                    unit_price = self._pole_price(pole, discounts, name)
                    gross += int(unit_price * self._sale_tax_factor(name, state))
                    sold_poles.append((name, pole, unit_price))
                else:
                    for pole in poles[:number]:
                        unit_price = self._pole_price(pole, discounts, name)
                        gross += int(unit_price * self._sale_tax_factor(name, state))
                        sold_poles.append((name, pole, unit_price))
            else:
                unit_price = self._shop_price(name, discounts)
                if name in FISH:
                    unit_price = fish_sale_price(
                        unit_price,
                        gold_magnet=self._accessory_effect(state, "黄金磁石", "fish_price", 1.0),
                    )
                    price_bonus = self._gear_effects(state).get("fish_price", 0.0)
                    if price_bonus:
                        unit_price = int(unit_price * (1.0 + price_bonus))
                elif name in KING_INFO:
                    magnet = self._accessory_effect(state, "黄金磁石", "king_price", 1.0)
                    king_price_fn = (
                        king_sale_price
                        if KING_INFO.get(name, {}).get("kind") == "final"
                        else capped_king_sale_price
                    )
                    captured_prices = state.get("king_sale_prices", {}).get(name, [])
                    if isinstance(captured_prices, list) and captured_prices:
                        captured_count = min(number, len(captured_prices))
                        king_gross = 0
                        for raw_price in captured_prices[:captured_count]:
                            if isinstance(raw_price, dict):
                                # 捕获快照：已含磁石、当天折扣与鱼点税，不再按当前状态重算。
                                king_gross += int(raw_price.get("price", raw_price.get("base", 0)))
                            else:
                                king_gross += int(
                                    king_price_fn(
                                        int(raw_price) * discounts.get(name, 100) // 100,
                                        gold_magnet=magnet,
                                    )
                                    * self._sale_tax_factor(name, state)
                                )
                        if captured_count < number:
                            king_gross += int(
                                king_price_fn(
                                    self._shop_price(name, discounts),
                                    gold_magnet=magnet,
                                )
                                * self._sale_tax_factor(name, state)
                            ) * (number - captured_count)
                        gross += king_gross
                        continue
                    unit_price = king_price_fn(unit_price, gold_magnet=magnet)
                elif getattr(self, "_types", {}).get(name) == "treasure":
                    unit_price = treasure_sale_price(
                        unit_price,
                        gold_magnet=self._accessory_effect(state, "黄金磁石", "treasure_price", 1.0),
                    )
                unit_price = int(unit_price * self._sale_tax_factor(name, state))
                gross += unit_price * number
        quote = gross
        sale_prompt = (
            f'是否接受商店将以{quote}收购{items[0][1]}个{items[0][0]}?\n回答"是"或"否"'
            if len(items) == 1 else
            f'是否接受商店将以{quote}收购以上物品?\n回答"是"或"否"'
        )
        confirmation = await self._choose(
            sale_prompt,
            event, choices={"是": "confirm", "否": "cancel"}, timeout=60,
            timeout_reply="等待超时,取消出售", cancel_reply="已取消出售",
        )
        if confirmation.key != "confirm":
            return
        record_available = int(state["pack"].get("唱片", 0)) - sum(
            number for name, number in items if name == "唱片"
        )
        if record_available > 0:
            record = await self._choose(
                '是否使用唱片让价格翻倍?\n回答"是"或"否"', event,
                choices={"是": "yes", "否": "no"}, timeout=60,
                timeout_reply="等待超时,按原价出售", cancel_reply="已取消出售",
            )
            if record.key == "yes":
                self._remove(state, "唱片")
                gross *= 2
        curse = int(state["pack"].get("宝藏诅咒", 0))
        curse_text = f"\n(你身上绑定了{curse}层诅咒)" if curse else ""
        gross = gross * (100 - 10 * curse) // 100
        for name, number in items:
            self._remove(state, name, number, selected_indices.get(name))
            if name in KING_INFO:
                prices_by_name = state.get("king_sale_prices", {}).get(name, [])
                if isinstance(prices_by_name, list):
                    del prices_by_name[:number]
        discounts, inventory = self._shop_data()
        record_multiplier = 2 if record_available > 0 and record.key == "yes" else 1
        for name, number in items:
            if name not in POLES and name != "宝藏诅咒" and name not in getattr(self, "_wastes", ()):
                inventory[name] = min(255, int(inventory.get(name, 0)) + number)
        for name, pole, unit_price in sold_poles:
            final_unit_price = unit_price * record_multiplier * (100 - 10 * curse) // 100
            if final_unit_price >= getattr(self, "_prices", {}).get(name, POLES[name][1]) * 2:
                poles = self._shop_poles(inventory.get(name, []), name)
                if len(poles) <= 5:
                    poles.append(dict(pole))
                    inventory[name] = poles
        if hasattr(self, "_db"):
            self._db.execute(
                "UPDATE shop SET inventory = ? WHERE day = ?",
                (json.dumps(inventory, ensure_ascii=False), date.today().isoformat()),
            )
            self._db.commit()
        price = int(gross)
        pole_sales = sum(number for name, number in items if name in POLES)
        state["pole_trade_count"] = int(state.get("pole_trade_count", 0)) + pole_sales
        state["trade_count"] = int(state.get("trade_count", 0)) + pole_sales
        if pole_sales and state["trade_count"] % 10 == 0:
            self._add(state, "宝藏诅咒")
        self._economy_change(uid, price, "mcfish_sale")
        self._save(uid, state)
        if len(items) == 1:
            name, number = items[0]
            await event.reply(text=f"成功出售{name}：{number}个,你赚到了{price}{curse_text}")
        else:
            await event.reply(text=f"成功出售本次物品,你赚到了{price}{curse_text}")

    async def _equip(self, event, state: dict, name: str) -> None:
        self._ensure_accessory_state(state)
        has_level_state = "fish_level" in state or "fish_xp" in state
        player_level = int(state.get("fish_level", level_for_xp(int(state.get("fish_xp", 0)))))
        if name in POLE_ALIASES:
            name = POLE_ALIASES[name]
        if name == "三叉戟":
            await event.reply(text="三叉戟只能在工坊交付：等级60、捕获全部8条限时鱼王、鱼王鳞片8个、时砂5个、7万金币")
            return
        if has_level_state and name in POLES and player_level < rod_unlock_level(name):
            await event.reply(text=f"你的钓鱼等级为{player_level}，{name}需要等级{rod_unlock_level(name)}才能装备")
            return
        if name in EQUIPMENT:
            if int(state["pack"].get(name, 0)) <= 0:
                await event.reply(text="背包没有这个装备")
                return
            if EQUIPMENT[name]["kind"] == "permanent":
                if name in state["accessories"]:
                    await event.reply(text="这个永久饰品已经装备")
                    return
                if len(state["accessories"]) >= state["accessory_slots"]:
                    await event.reply(text=f"当前最多同时装备{state['accessory_slots']}件永久饰品，请先购买更多饰品槽位")
                else:
                    state["accessories"].append(name)
                    self._save(str(event.sender.user_id), state)
                    level = self._accessory_level(state, name)
                    await event.reply(text=f"已装备{name}（{self._enchant_label(level)}级）")
                return
            uses = int(EQUIPMENT[name].get("uses") or 0)
            self._remove(state, name)
            state.setdefault("active_consumables", {})[name] = (
                int(state["active_consumables"].get(name, 0)) + uses
            )
            self._save(str(event.sender.user_id), state)
            await event.reply(text=f"已启用{name}，剩余{state['active_consumables'][name]}次")
            return
        if name == AXOLOTL:
            if not self._remove(state, name):
                await event.reply(text="背包没有这个装备")
                return
            if state.get("equip"):
                old_name = state["equip"]["name"]
                self._add(
                    state,
                    old_name,
                    pole=old_name in POLES,
                    pole_data=state["equip"] if old_name in POLES else None,
                    craftable=state["equip"].get("craftable", True),
                )
            state["equip"] = {"name": AXOLOTL, "durable": 999, "maintenance": 0, "induce": 0, "favor": 0}
            self._save(str(event.sender.user_id), state)
            await event.reply(text="装备成功")
            return
        equipped = list(state["pack"].get(name, [])) if name in POLES else []
        if name not in POLES or not equipped:
            await event.reply(text="背包没有这个装备")
            return
        selected_index = 0
        if len(equipped) > 1:
            listing = "\n".join(
                f"[{index}] {name} : 耐{pole.get('durable', 0)}/修{pole.get('maintenance', 0)}"
                f"/诱{self._enchant_label(pole.get('induce', 0))}/眷顾{self._enchant_label(pole.get('favor', 0))}"
                for index, pole in enumerate(equipped)
            )
            result = await self._choose(
                f"找到以下鱼竿:\n{listing}\n————————\n- 输入对应序号进行装备\n"
                "- 输入“取消”终止本次操作\n- 鱼竿数量请使用钓鱼背包查看",
                event, choices={str(index): str(index) for index in range(len(equipped))},
                timeout=120, timeout_reply="等待超时,取消装备", cancel_reply="已取消装备",
            )
            if result.key is None:
                return
            selected_index = int(result.key)
        selected = equipped[selected_index] if equipped else {"durable": POLES[name][0], "maintenance": 0, "induce": 0, "favor": 0}
        self._remove(state, name, 1, selected_index)
        if state.get("equip"):
            old_name = state["equip"]["name"]
            self._add(
                state,
                old_name,
                pole=old_name in POLES,
                pole_data=state["equip"] if old_name in POLES else None,
                craftable=state["equip"].get("craftable", True),
            )
        state["equip"] = {"name": name, **selected}; self._save(str(event.sender.user_id), state); await event.reply(text="装备成功")

    @staticmethod
    def _normalize_accessory_command_name(name: str) -> str:
        name = name.strip()
        return name[2:].strip() if name.startswith("饰品") else name

    @staticmethod
    def _accessory_upgrade_summary(name: str, level: int) -> str:
        effects = PERMANENT_ACCESSORY_EFFECTS[name][level]
        if name == "财富浮标":
            return f"宝藏概率×{effects['treasure_rate']:.2f}，鱼王咬钩率×{effects['king_clue_rate']:.2f}"
        if name == "坚韧钓组":
            return f"鱼王上钩率+{effects['king_hook_success_points']}个百分点"
        if name == "黄金磁石":
            cap = KING_MAGNET_CAP.get(float(effects["king_price"]), effects["king_price"])
            return (
                f"宝藏品质+{effects['treasure_quality']}档，"
                f"宝藏售价×{effects['treasure_price']:.2f}，"
                f"普通鱼售价×{effects['fish_price']:.2f}，"
                f"后置鱼王售价×{effects['king_price']:.0f}（前置/常驻封顶×{cap:.0f}）"
            )
        return f"普通碎片概率{8 + effects['fragment_points']}%"

    async def _buy_accessory_slot(self, event, state: dict) -> None:
        self._ensure_accessory_state(state)
        current_slots = int(state["accessory_slots"])
        next_slot = current_slots + 1
        unlock = ACCESSORY_SLOT_UNLOCKS.get(next_slot)
        if unlock is None:
            await event.reply(text="饰品槽位已达到当前上限")
            return
        level = int(state.get("fish_level", level_for_xp(int(state.get("fish_xp", 0)))))
        required_level = int(unlock["level"])
        required_fragments = int(unlock["fragments"])
        if level < required_level:
            await event.reply(text=f"钓鱼等级达到{required_level}级后才能解锁第{next_slot}个饰品槽位")
            return
        owned = int(state.get("pack", {}).get("钓鱼碎片", 0))
        if owned < required_fragments:
            await event.reply(
                text=(
                    f"解锁第{next_slot}个饰品槽位需要{required_fragments}个钓鱼碎片，"
                    f"你当前有{owned}个，还差{required_fragments - owned}个"
                )
            )
            return
        result = await self._choose(
            f"解锁第{next_slot}个饰品槽位需要{required_fragments}个钓鱼碎片，确认支付吗？",
            event,
            choices={"确认": "confirm", "是": "confirm", "取消": "cancel"},
            timeout=120,
            timeout_reply="等待超时，取消解锁",
            cancel_reply="已取消解锁",
        )
        if result.key != "confirm":
            return
        if not self._remove(state, "钓鱼碎片", required_fragments):
            await event.reply(text="钓鱼碎片不足，无法解锁饰品槽位")
            return
        state["accessory_slots"] = next_slot
        self._save(str(event.sender.user_id), state)
        await event.reply(text=f"已解锁第{next_slot}个饰品槽位，当前共{next_slot}格")

    async def _upgrade_accessory(self, event, state: dict, name: str) -> None:
        self._ensure_accessory_state(state)
        name = self._normalize_accessory_command_name(name)
        if name not in ACCESSORY_UPGRADE_COSTS:
            await event.reply(text="可升级的永久饰品：财富浮标、坚韧钓组、黄金磁石、幸运海星")
            return
        level = self._accessory_level(state, name)
        if level <= 0:
            await event.reply(text=f"你还没有{name}，请先合成{name}")
            return
        if level >= ACCESSORY_MAX_LEVEL:
            await event.reply(text=f"{name}已经达到最高{self._enchant_label(ACCESSORY_MAX_LEVEL)}级")
            return
        required = int(ACCESSORY_UPGRADE_COSTS[name][level - 1])
        owned = int(state.get("pack", {}).get("钓鱼碎片", 0))
        next_level = level + 1
        if owned < required:
            await event.reply(
                text=(
                    f"升级{name}至{self._enchant_label(next_level)}级需要{required}个钓鱼碎片，"
                    f"你当前有{owned}个，还差{required - owned}个"
                )
            )
            return
        result = await self._choose(
            f"升级{name}至{self._enchant_label(next_level)}级需要{required}个钓鱼碎片。\n"
            f"升级后效果：{self._accessory_upgrade_summary(name, next_level)}\n确认升级吗？",
            event,
            choices={"确认": "confirm", "是": "confirm", "取消": "cancel"},
            timeout=120,
            timeout_reply="等待超时，取消升级",
            cancel_reply="已取消升级",
        )
        if result.key != "confirm":
            return
        if not self._remove(state, "钓鱼碎片", required):
            await event.reply(text="钓鱼碎片不足，无法升级饰品")
            return
        state["accessory_levels"][name] = next_level
        self._save(str(event.sender.user_id), state)
        await event.reply(text=f"{name}已升级至{self._enchant_label(next_level)}级：{self._accessory_upgrade_summary(name, next_level)}")

    async def _unequip(self, event, state: dict, name: str) -> None:
        self._ensure_accessory_state(state)
        name = self._normalize_accessory_command_name(name)
        if name in ("全部", "全部饰品"):
            if not state["accessories"]:
                await event.reply(text="当前没有装备永久饰品")
                return
            result = await self._choose(
                "确认卸下全部永久饰品吗？",
                event,
                choices={"确认": "confirm", "是": "confirm", "取消": "cancel"},
                timeout=120,
                timeout_reply="等待超时，取消卸下",
                cancel_reply="已取消卸下",
            )
            if result.key != "confirm":
                return
            removed = "、".join(state["accessories"])
            state["accessories"] = []
            self._save(str(event.sender.user_id), state)
            await event.reply(text=f"已卸下：{removed}")
            return
        if name not in PERMANENT_ACCESSORY_EFFECTS:
            await event.reply(text="请指定已装备的永久饰品，或使用“卸下全部饰品”")
            return
        if name not in state["accessories"]:
            await event.reply(text=f"当前没有装备{name}")
            return
        state["accessories"].remove(name)
        self._save(str(event.sender.user_id), state)
        await event.reply(text=f"已卸下{name}，饰品仍保留在背包中")

    async def _repair(self, event, state: dict) -> None:
        equip = state.get("equip")
        if not equip or equip["name"] == "美西螈":
            await event.reply(text="仅能修复装备中的鱼竿")
            return
        paid_trident = equip["name"] == "三叉戟" and bool(equip.get("unbreakable", False))
        if int(equip.get("durable", 0)) >= POLES[equip["name"]][0]:
            await event.reply(text="装备的鱼竿耐久已经满了")
            return
        if int(equip.get("maintenance", 0)) >= 10 and not paid_trident:
            await event.reply(text="装备的鱼竿已经达到修复上限")
            return
        materials = state.get("pack", {}).get(equip["name"], [])
        if not materials and not paid_trident:
            await event.reply(text="你的背包不存在相同鱼竿进行修复")
            return
        if paid_trident:
            repair_fee = self._discounted_fee(state, self._repair_fee(equip["name"], int(equip.get("durable", 0))))
            if getattr(self, "_economy", None) and int(self._economy.store.account(str(event.sender.user_id))["balance"]) < repair_fee:
                await event.reply(text=f"本次修复需要{repair_fee}金币，你当前余额不足")
                return
            if not self._charge_fee(str(event.sender.user_id), repair_fee, "mcfish_trident_repair"):
                await event.reply(text=f"本次修复需要{repair_fee}金币，你当前余额不足")
                return
            self._advance_tasks(state, spend=repair_fee)
            equip["durable"] = POLES[equip["name"]][0]
            equip["maintenance"] = int(equip.get("maintenance", 0)) + 1
            self._save(str(event.sender.user_id), state)
            await event.reply(text=f"永恒三叉戟修复成功，耐久恢复至{equip['durable']}，支付{repair_fee}金币")
            return
        selected_index = 0
        if len(materials) > 1:
            listing = "\n".join(
                f"[{index}] {equip['name']} : 耐{pole.get('durable', 0)}/修{pole.get('maintenance', 0)}"
                f"/诱{self._enchant_label(pole.get('induce', 0))}/眷顾{self._enchant_label(pole.get('favor', 0))}"
                for index, pole in enumerate(materials)
            )
            result = await self._choose(
                f"找到以下鱼竿:\n{listing}\n————————\n输入对应序号进行修复,或回复“取消”取消",
                event, choices={str(index): str(index) for index in range(len(materials))},
                timeout=120, timeout_reply="等待超时,取消修复", cancel_reply="已取消修复",
            )
            if result.key is None:
                return
            selected_index = int(result.key)
        material = materials[selected_index]
        pole_count = sum(
            len(value) for name, value in state.get("pack", {}).items()
            if name in POLES and isinstance(value, list)
        )
        repair_fee = self._discounted_fee(state, self._repair_fee(equip["name"], int(equip.get("durable", 0))))
        if getattr(self, "_economy", None) and int(self._economy.store.account(str(event.sender.user_id))["balance"]) < repair_fee:
            await event.reply(text=f"本次修复需要{repair_fee}金币，你当前余额不足")
            return
        if not self._charge_fee(str(event.sender.user_id), repair_fee, "mcfish_repair"):
            await event.reply(text=f"本次修复需要{repair_fee}金币，你当前余额不足")
            return
        self._advance_tasks(state, spend=repair_fee)
        factor = 8 if pole_count <= 10 else 10
        equip["durable"] = min(
            POLES[equip["name"]][0],
            int(equip.get("durable", 0)) + int(material.get("durable", 0)) * factor // 10,
        )
        message = ""
        for key, label in (("induce", "诱钓"), ("favor", "海之眷顾")):
            if int(material.get(key, 0)) and random.randrange(100) < 50:
                equip[key] = min(3, int(equip.get(key, 0)) + int(material[key]))
                message += f",{label}等级提升至{self._enchant_label(equip[key])}"
        self._remove(state, equip["name"], 1, selected_index)
        equip["maintenance"] = int(equip.get("maintenance", 0)) + 1
        self._save(str(event.sender.user_id), state)
        await event.reply(text=f"鱼竿修复成功,耐久提高至{equip['durable']}，支付{repair_fee}金币{message}")

    async def _enchant(self, event, state: dict, book: str) -> None:
        equip = state.get("equip")
        if not equip or equip["name"] == "美西螈":
            await event.reply(text="仅可对装备中的进行附魔")
            return
        if book not in ("诱钓", "海之眷顾"):
            await event.reply(text="附魔失败了")
            return
        key = "induce" if book == "诱钓" else "favor"
        if int(state["pack"].get(book, 0)) < 1:
            await event.reply(text=f"你的背包不存在{book}进行附魔")
            return
        if int(equip.get(key, 0)) >= 3:
            await event.reply(text=f"{book}等级已达到上限，不会消耗附魔书")
            return
        self._remove(state, book)
        if random.randrange(100) >= 50:
            self._save(str(event.sender.user_id), state)
            await event.reply(text="附魔失败了")
            return
        equip[key] = int(equip.get(key, 0)) + 1
        self._save(str(event.sender.user_id), state)
        await event.reply(text=f"附魔成功,{book}等级提高至{self._enchant_label(equip[key])}")

    async def _dismantle(self, event, state: dict, requested_name: str = "") -> None:
        name = POLE_ALIASES.get(requested_name.strip(), requested_name.strip())
        if name in ("鱼竿", "鱼杆", ""):
            name = ""
        if name and name not in POLES:
            await event.reply(text="请指定鱼竿名称，例如“分解铁竿”，或发送“分解鱼竿”查看全部鱼竿")
            return

        candidates: list[tuple[str, int, dict]] = []
        for pole_name in (name,) if name else POLES:
            for index, pole in enumerate(state.get("pack", {}).get(pole_name, [])):
                candidates.append((pole_name, index, pole))
        if not candidates:
            await event.reply(text="背包中没有可分解的鱼竿")
            return

        selected: list[tuple[str, int, dict]]
        if len(candidates) == 1:
            selected = candidates
        else:
            listing = "\n".join(
                f"[{index}] {pole_name}：耐{pole.get('durable', 0)}/修{pole.get('maintenance', 0)}"
                f"/诱{self._enchant_label(pole.get('induce', 0))}/眷顾{self._enchant_label(pole.get('favor', 0))}"
                for index, (pole_name, _pole_index, pole) in enumerate(candidates)
            )
            choices = {"全部": "all", "取消": "cancel"}
            choices.update({str(index): f"one:{index}" for index in range(len(candidates))})
            result = await self._choose(
                f"找到以下可分解鱼竿：\n{listing}\n————————\n输入序号分解一根，输入“全部”分解以上全部鱼竿，或输入“取消”终止",
                event,
                choices=choices,
                timeout=120,
                timeout_reply="等待超时，取消分解",
                cancel_reply="已取消分解",
            )
            if result.key == "all":
                selected = candidates
            elif isinstance(result.key, str) and result.key.startswith("one:"):
                selected = [candidates[int(result.key[4:])]]
            else:
                return

        fragments = sum(POLE_DISMANTLE_FRAGMENTS[pole_name] for pole_name, _, _ in selected)
        summary = "、".join(
            f"{pole_name}×{sum(1 for selected_name, _, _ in selected if selected_name == pole_name)}"
            for pole_name in POLES
            if any(selected_name == pole_name for selected_name, _, _ in selected)
        )
        confirmation = await self._choose(
            f"确认分解{summary}吗？将获得钓鱼碎片×{fragments}，分解后无法恢复。\n回答“是”或“否”",
            event,
            choices={"是": "confirm", "否": "cancel"},
            timeout=60,
            timeout_reply="等待超时，取消分解",
            cancel_reply="已取消分解",
        )
        if confirmation.key != "confirm":
            return
        for pole_name, pole_index, _pole in sorted(selected, key=lambda item: item[1], reverse=True):
            self._remove(state, pole_name, 1, pole_index)
        self._add(state, "钓鱼碎片", fragments)
        self._save(str(event.sender.user_id), state)
        await event.reply(text=f"已分解{summary}，获得钓鱼碎片×{fragments}")

    @staticmethod
    def _craft_indices(response: str, pole_count: int) -> str | None:
        values = response.split()
        if len(values) != 3 or not all(value.isdigit() for value in values):
            return None
        indices = tuple(int(value) for value in values)
        if len(set(indices)) != 3 or any(index < 0 or index >= pole_count for index in indices):
            return None
        return "group:" + ":".join(map(str, indices))

    @staticmethod
    def _craft_durable(target_name: str, source_name: str, selected: list[dict]) -> int:
        """Scale target durability from three source rods, then add bounded variance."""
        source_max = POLES[source_name][0]
        target_max = POLES[target_name][0]
        source_total = sum(
            min(source_max, max(0, int(pole.get("durable", 0))))
            for pole in selected
        )
        proportional = target_max * source_total / (source_max * 3)
        lower = max(1, math.floor(proportional * 0.9))
        upper = min(target_max, max(lower, math.ceil(proportional * 1.1)))
        return lower + random.randrange(upper - lower + 1)

    @staticmethod
    def _craft_material_status(name: str, owned: int, required: int, unit: str) -> str:
        owned = max(0, int(owned))
        required = max(0, int(required))
        missing = max(0, required - owned)
        return f"{name}：已有{owned}/{required}{unit}，还差{missing}{unit}"

    async def _craft(self, event, state: dict, name: str) -> None:
        self._ensure_accessory_state(state)
        has_level_state = "fish_level" in state or "fish_xp" in state
        player_level = int(state.get("fish_level", level_for_xp(int(state.get("fish_xp", 0)))))
        if name in POLE_ALIASES:
            name = POLE_ALIASES[name]
        if has_level_state and name in POLES and player_level < rod_unlock_level(name):
            await event.reply(text=f"你的钓鱼等级为{player_level}，{name}需要等级{rod_unlock_level(name)}才能合成")
            return
        if name in EQUIPMENT:
            required = EQUIPMENT[name]["fragments"]
            owned = int(state["pack"].get("钓鱼碎片", 0))
            if owned < required:
                await event.reply(
                    text=(
                        f"合成{name}材料不足：\n"
                        f"{self._craft_material_status('钓鱼碎片', owned, required, '个')}"
                    )
                )
                return
            if EQUIPMENT[name]["kind"] == "permanent" and (name in state.get("accessories", []) or int(state["pack"].get(name, 0)) > 0):
                await event.reply(text="同名永久饰品不能重复合成")
                return
            craft_fee = self._discounted_fee(state, self._craft_fee(name, "equipment"))
            if getattr(self, "_economy", None) and int(self._economy.store.account(str(event.sender.user_id))["balance"]) < craft_fee:
                await event.reply(text=f"合成{name}还需要支付{craft_fee}金币手续费")
                return
            if not self._charge_fee(str(event.sender.user_id), craft_fee, "mcfish_craft"):
                await event.reply(text="金币不足，无法支付合成手续费")
                return
            self._advance_tasks(state, spend=craft_fee)
            self._remove(state, "钓鱼碎片", required)
            self._add(state, name)
            if EQUIPMENT[name]["kind"] == "permanent":
                state["accessory_levels"][name] = 1
            self._save(str(event.sender.user_id), state)
            uses = EQUIPMENT[name]["uses"]
            suffix = "永久饰品" if uses is None else f"消耗品，{uses}竿"
            fee_note = f"，支付{craft_fee}金币手续费" if getattr(self, "_economy", None) else ""
            await event.reply(text=f"成功合成{name}（{suffix}）{fee_note}")
            return
        source = UPGRADES.get(name)
        source_inventory = state.get("pack", {}).get(source, []) if source else []
        pole_indices = [
            index for index, pole in enumerate(source_inventory)
            if pole.get("craftable", True)
        ]
        poles = [source_inventory[index] for index in pole_indices]
        if name in POLES:
            required = POLE_FRAGMENT_COSTS[name]
            owned = int(state["pack"].get("钓鱼碎片", 0))
            can_use_fragments = owned >= required
            can_use_poles = bool(source and len(poles) >= 3)
            craft_mode = None
            if can_use_fragments and can_use_poles:
                result = await self._choose(
                    f"合成{name}请选择合成方式：\n"
                    f"1. 碎片合成（需要{required}个钓鱼碎片）\n"
                    f"2. 鱼竿合成（需要3根{source}，成功率90%）\n"
                    "回复“取消”终止本次合成",
                    event,
                    choices={
                        "1": "fragments", "碎片": "fragments", "碎片合成": "fragments",
                        "2": "poles", "鱼竿": "poles", "鱼竿合成": "poles", "取消": "cancel",
                    },
                    timeout=120,
                    timeout_reply="等待超时,取消合成",
                    cancel_reply="已取消合成",
                )
                if result.key not in ("fragments", "poles"):
                    return
                craft_mode = result.key
            elif can_use_fragments:
                result = await self._choose(
                    f"合成{name}需要使用{required}个钓鱼碎片兑换，确认使用碎片吗？\n"
                    "回复“确认”继续，回复“取消”终止本次合成",
                    event,
                    choices={
                        "确认": "fragments", "是": "fragments", "1": "fragments",
                        "碎片": "fragments", "碎片合成": "fragments", "取消": "cancel",
                    },
                    timeout=120,
                    timeout_reply="等待超时,取消合成",
                    cancel_reply="已取消合成",
                )
                if result.key != "fragments":
                    return
                craft_mode = "fragments"
            elif can_use_poles:
                craft_mode = "poles"
            if craft_mode == "fragments":
                craft_fee = self._discounted_fee(state, self._craft_fee(name, "fragments"))
                if getattr(self, "_economy", None) and int(self._economy.store.account(str(event.sender.user_id))["balance"]) < craft_fee:
                    await event.reply(text=f"兑换{name}还需要支付{craft_fee}金币手续费")
                    return
                if not self._charge_fee(str(event.sender.user_id), craft_fee, "mcfish_craft"):
                    await event.reply(text="金币不足，无法支付兑换手续费")
                    return
                self._advance_tasks(state, spend=craft_fee)
                self._remove(state, "钓鱼碎片", required)
                self._add(state, name, pole=True)
                self._save(str(event.sender.user_id), state)
                fee_note = f"，支付{craft_fee}金币手续费" if getattr(self, "_economy", None) else ""
                await event.reply(text=f"成功使用{required}个钓鱼碎片兑换{name}{fee_note}")
                return
            if craft_mode != "poles":
                requirements = [self._craft_material_status("钓鱼碎片", owned, required, "个")]
                if source:
                    requirements.append(
                        self._craft_material_status(source, len(poles), 3, "根")
                    )
                await event.reply(text=f"合成{name}材料不足：\n" + "\n".join(requirements))
                return
        if name not in UPGRADES or len(poles) < 3:
            requirements = []
            if name in POLES:
                required = POLE_FRAGMENT_COSTS[name]
                requirements.append(
                    self._craft_material_status(
                        "钓鱼碎片", state["pack"].get("钓鱼碎片", 0), required, "个"
                    )
                )
                if source:
                    requirements.append(
                        self._craft_material_status(source, len(poles), 3, "根")
                    )
            if requirements:
                await event.reply(text=f"合成{name}材料不足：\n" + "\n".join(requirements))
            else:
                await event.reply(text="合成材料不足")
            return
        selected_groups: list[tuple[int, int, int]]
        if len(poles) == 3:
            selected_groups = [(0, 1, 2)]
        else:
            choices = {"梭哈": "all", "取消": "cancel"}
            listing = "\n".join(
                f"[{index}] {source} : 耐{pole.get('durable', 0)}/修{pole.get('maintenance', 0)}"
                f"/诱{self._enchant_label(pole.get('induce', 0))}/眷顾{self._enchant_label(pole.get('favor', 0))}"
                for index, pole in enumerate(poles)
            )
            result = await self._choose(
                f"找到以下鱼竿:\n{listing}\n————————\n- 输入3个序号进行合成(用空格分割)\n"
                "- 输入“取消”，终止本次合成\n- 输入“梭哈”，合成所有鱼竿",
                event, choices=choices, timeout=120, timeout_reply="等待超时,取消合成", cancel_reply="已取消合成",
                parse_response=lambda response: self._craft_indices(response, len(poles)),
            )
            if result.key is None:
                return
            if result.key == "all":
                batch_count = len(poles) // 3
                selected_groups = [
                    (index, index + 1, index + 2)
                    for index in range(0, batch_count * 3, 3)
                ][::-1]
            elif result.key.startswith("group:"):
                selected_groups = [tuple(map(int, result.key[6:].split(":")))]
            else:
                return
        craft_fee = self._discounted_fee(state, self._craft_fee(name, "poles", len(selected_groups)))
        if getattr(self, "_economy", None) and int(self._economy.store.account(str(event.sender.user_id))["balance"]) < craft_fee:
            await event.reply(text=f"本次合成需要支付{craft_fee}金币手续费")
            return
        if not self._charge_fee(str(event.sender.user_id), craft_fee, "mcfish_craft"):
            await event.reply(text="金币不足，无法支付合成手续费")
            return
        self._advance_tasks(state, spend=craft_fee)
        fee_note = f"，支付{craft_fee}金币手续费" if getattr(self, "_economy", None) else ""
        success = 0
        failure = 0
        last_attribute = ""
        for group in selected_groups:
            selected = [poles[index] for index in group]
            for index in sorted(group, reverse=True):
                self._remove(state, source, 1, pole_indices[index])
            attribute = {
                "durable": self._craft_durable(name, source, selected),
                "maintenance": 0,
                "induce": sum(int(p.get("induce", 0)) for p in selected) // 3,
                "favor": sum(int(p.get("favor", 0)) for p in selected) // 3,
            }
            last_attribute = f"{attribute['durable']}/0/{attribute['induce']}/{attribute['favor']}"
            if random.randrange(100) < 90:
                self._add(state, name, pole=True, pole_data=attribute)
                success += 1
            else:
                failure += 1
        self._save(str(event.sender.user_id), state)
        if len(selected_groups) > 1:
            remaining = len(poles) % 3
            message = f"合成完成，成功：{success} 个，失败：{failure} 个{fee_note}"
            if remaining:
                message += f"，剩余 {remaining} 根材料未参与合成"
        elif success:
            message = f"成功合成：1 个{name}\n属性: {last_attribute}{fee_note}"
        else:
            message = f"合成失败,材料已销毁{fee_note}"
        await event.reply(text=message)

    async def _purify(self, event, state: dict, text: str) -> None:
        number = int(re.search(r"(\d+)$", text).group(1)) if re.search(r"(\d+)$", text) else 1
        curse_count = int(state["pack"].get("宝藏诅咒", 0))
        if curse_count == 0:
            await event.reply(text="你没有绑定任何诅咒"); return
        number = min(number, curse_count)
        if int(state["pack"].get("净化书", 0)) < number:
            await event.reply(text="你没有足够的解除诅咒的道具"); return
        self._remove(state, "宝藏诅咒", number)
        self._remove(state, "净化书", number)
        self._save(str(event.sender.user_id), state); await event.reply(text="消除成功")

    async def _open_box(self, event, state: dict, box_text: str) -> None:
        box_name = box_text.strip()
        key_name = box_key_name(box_name)
        if key_name is None:
            await event.reply(text=f"未知箱子：{box_name or '（空）'}")
            return
        box_count = int(state.get("pack", {}).get(box_name, 0))
        key_count = int(state.get("pack", {}).get(key_name, 0))
        if box_count <= 0 or key_count <= 0:
            await event.reply(text=f"{box_name}：箱子 {box_count} / {key_name} {key_count}")
            return
        self._remove(state, box_name, 1)
        self._remove(state, key_name, 1)
        item, quantity = roll_box_loot(box_name)
        if item in CARD_NAMES:
            state.setdefault("cards", {})[item] = 1
        else:
            self._add(state, item, quantity)
        self._save(str(event.sender.user_id), state)
        await event.reply(text=f"{box_name}：{item} ×{quantity}")

    async def _eat_food(self, event, state: dict, text: str) -> None:
        parts = text.split()
        if not parts:
            await event.reply(text="吃海藻：需要食物名，例如 吃海藻 绿色海藻 3")
            return
        name = parts[0]
        number = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
        restore = FOOD_RESTORE.get(name)
        if restore:
            restore = max(1, int(restore * (1.0 + self._gear_effects(state).get("food", 0.0))))
        if not restore:
            await event.reply(text=f"未知食物：{name}")
            return
        owned = int(state.get("pack", {}).get(name, 0))
        if owned <= 0:
            await event.reply(text=f"{name}：数量 0")
            return
        number = min(number, owned)
        remaining = max(0, 15 - int(state.get("food_today", 0)))
        used_today = int(state.get("fish_today", 0))
        gain = min(restore * number, remaining, used_today)
        if gain <= 0:
            await event.reply(text=f"今日已用 {used_today}/{self._daily_limit(state)}")
            return
        used = max(1, (gain + restore - 1) // restore)
        self._remove(state, name, used)
        state["fish_today"] = max(0, used_today - gain)
        zone_key = str(state.get("fishing_zone", ""))
        zone_today = state.get("zone_fish_today")
        if zone_key and isinstance(zone_today, dict):
            zone_used = int(zone_today.get(zone_key, 0))
            zone_today[zone_key] = max(0, zone_used - min(gain, zone_used))
        state["food_today"] = int(state.get("food_today", 0)) + gain
        self._save(str(event.sender.user_id), state)
        await event.reply(
            text=f"{name} ×{used}：恢复 {gain} 次，今日已用 {state['fish_today']}/{self._daily_limit(state)}"
        )

    def _talent_text(self, state: dict, refunded: int = 0) -> str:
        lines = talent_tree_lines(state)
        if refunded:
            lines.insert(1, f"已退还 {refunded} 点：前置条件不满足或超过上限的加点已清除。")
        return "\n".join(lines)

    async def _learn_talent_cmd(self, event, state: dict, text: str) -> None:
        key = talent_by_label(text)
        if key is None:
            return
        refunded = int(state.pop("talent_refunded", 0) or 0)
        ok, reason = can_learn_talent(state, key)
        if not ok:
            if refunded:
                self._save(str(event.sender.user_id), state)
            note = f"\n已退还 {refunded} 点（前置或上限异常）" if refunded else ""
            await event.reply(text=f"{TALENTS[key]['label']}：{reason}{note}")
            return
        learn_talent(state, key)
        self._save(str(event.sender.user_id), state)
        rank = int(state.get("talents", {}).get("levels", {}).get(key, 0))
        note = f"\n已退还 {refunded} 点（前置或上限异常）" if refunded else ""
        await event.reply(
            text=f"{TALENTS[key]['label']}：{rank}/{TALENTS[key]['max']}（可用 {talent_free_points(state)}）{note}"
        )

    async def _reset_talents_cmd(self, event, state: dict) -> None:
        if not (state.get("talents") or {}).get("levels"):
            await event.reply(text="已分配天赋：0")
            return
        cost = talent_reset_cost(state)
        if not self._charge_fee(str(event.sender.user_id), cost, "mcfish_talent_reset"):
            await event.reply(text=f"重置钓鱼天赋：金币不足（需要 {cost}）")
            return
        reset_talents(state)
        self._save(str(event.sender.user_id), state)
        await event.reply(text=f"已重置钓鱼天赋（花费 {cost} 金币）")

    def _workshop_text(self, state: dict, name: str = "") -> str:
        recipe = recipe_of(name) if name else None
        if name and recipe is None:
            return f"未知配方：{name}"
        lines = ["钓鱼工坊"]
        for item in RECIPES.values():
            lines.append(f"{item['name']}：{workshop_status(state, item)}")
        if recipe:
            lines.append("")
            lines.append(f"{recipe['name']}")
            if recipe.get("cards"):
                owned = sum(1 for card in CARD_NAMES if card in (state.get("cards") or {}))
                lines.append(f"交付：神谕卡片 {owned}/9")
            else:
                delivered = workshop_progress(state, recipe)["delivered"]
                for item, need in recipe["items"].items():
                    lines.append(f"交付：{item} {int(delivered.get(item, 0))}/{need}")
                lines.append(f"金币：{recipe['gold']}")
            state_text = "可领取" if can_claim(state, recipe) else workshop_status(state, recipe)
            lines.append(f"状态：{state_text}")
        else:
            lines.append("交付：交付 三叉戟 鱼王鳞片 1")
        return "\n".join(lines)

    async def _deliver(self, event, state: dict, text: str) -> None:
        parts = text.split()
        if len(parts) < 2:
            await event.reply(text="交付：需要配方名与材料名，例如 交付 三叉戟 鱼王鳞片 1")
            return
        recipe = recipe_of(parts[0])
        if recipe is None:
            await event.reply(text=f"未知配方：{parts[0]}")
            return
        if recipe.get("cards"):
            await event.reply(text=f"{recipe['name']}：卡牌集齐后直接领取")
            return
        if not recipe_unlocked(recipe, state):
            await event.reply(text=f"{recipe['name']}：{workshop_status(state, recipe)}")
            return
        item = parts[1]
        if item not in recipe["items"]:
            await event.reply(text=f"{recipe['name']}：不接受 {item}")
            return
        count = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        owned = int(state.get("pack", {}).get(item, 0))
        count = min(count, owned)
        if count <= 0:
            await event.reply(text=f"{item}：数量 0")
            return
        self._remove(state, item, count)
        mark_delivered(state, recipe, item, count)
        self._save(str(event.sender.user_id), state)
        delivered = workshop_progress(state, recipe)["delivered"]
        need = recipe["items"][item]
        await event.reply(text=f"{recipe['name']}：{item} {int(delivered.get(item, 0))}/{need}")

    async def _claim_workshop(self, event, state: dict, name: str) -> None:
        recipe = recipe_of(name.strip())
        if recipe is None:
            await event.reply(text=f"未知配方：{name.strip() or '（空）'}")
            return
        info = workshop_progress(state, recipe)
        if info["claimed"]:
            await event.reply(text=f"{recipe['name']}：已领取")
            return
        if not recipe_unlocked(recipe, state):
            await event.reply(text=f"{recipe['name']}：{workshop_status(state, recipe)}")
            return
        if not can_claim(state, recipe):
            await event.reply(text=f"{recipe['name']}：{workshop_status(state, recipe)}")
            return
        gold = int(recipe.get("gold", 0))
        if gold and not self._charge_fee(str(event.sender.user_id), gold, "mcfish_workshop"):
            await event.reply(text=f"{recipe['name']}：金币不足（需要 {gold}）")
            return
        if recipe.get("cards"):
            state["cards"] = {}
            state.setdefault("gear_bag", []).append(
                {"name": "神谕卡组", "set": "神谕", "slot": "necklace", "level": 20, "fp": 20}
            )
        else:
            self._add(
                state,
                "三叉戟",
                pole=True,
                pole_data={
                    "durable": POLES["三叉戟"][0],
                    "maintenance": 0,
                    "induce": 0,
                    "favor": 0,
                    "unbreakable": True,
                },
            )
        mark_claimed(state, recipe)
        self._save(str(event.sender.user_id), state)
        await event.reply(text=f"已领取 {recipe['name']}")

    def _gear_effects(self, state: dict) -> dict[str, float]:
        effects = dict(scaled_effects(state.get("equipment") or {}, state.get("jewelry") or {}))
        for key, value in talent_effects(state).items():
            effects[key] = effects.get(key, 0.0) + float(value)
        return effects

    def _daily_limit(self, state: dict) -> int:
        return daily_attempt_limit(int(state.get("fish_level", 1))) + int(
            self._gear_effects(state).get("daily", 0)
        )

    def _gear_slot_line(self, slot: str, item: dict | None) -> str:
        label = SLOT_LABELS.get(slot, slot)
        if not isinstance(item, dict) or not item.get("name"):
            return f"{label}：空"
        name = str(item["name"])
        info = PIECE_INFO.get(name, {})
        level = int(item.get("level", info.get("start", 1)))
        return f"{label}：{name}（{set_label(str(info.get('set', '?')))}，{level}级，FP {fp_for_level(level)}）"

    def _gear_text(self, state: dict) -> str:
        equipment = state.get("equipment") or {}
        jewelry = state.get("jewelry") or {}
        lines = ["钓鱼装备"]
        for slot in GEAR_SLOTS:
            lines.append(self._gear_slot_line(slot, equipment.get(slot)))
        for slot in JEWELRY_SLOTS:
            lines.append(self._gear_slot_line(slot, jewelry.get(slot)))
        worn = worn_sets(equipment, jewelry)
        if worn:
            lines.append("套装：" + "、".join(f"{set_label(name)} {value['count']}件" for name, value in worn.items()))
        effects = scaled_effects(equipment, jewelry)
        if effects:
            parts = [format_effect(key, value) for key, value in effects.items()]
            lines.append("套装效果：" + "、".join(parts))
        bag = state.get("gear_bag") or []
        lines.append("未装备：" + ("、".join(f"{item.get('name')}({item.get('level')}级)" for item in bag) or "无"))
        lines.append(f"装备 FP：{fishing_power(state)}")
        return "\n".join(lines)

    def _find_gear(self, state: dict, name: str):
        for group, slots in (("equipment", GEAR_SLOTS), ("jewelry", JEWELRY_SLOTS)):
            for slot in slots:
                item = (state.get(group) or {}).get(slot)
                if isinstance(item, dict) and item.get("name") == name:
                    return group, slot, item
        for index, item in enumerate(state.get("gear_bag") or []):
            if item.get("name") == name:
                return "bag", index, item
        return None

    async def _craft_gear(self, event, state: dict, name: str) -> None:
        name = name.strip()
        recipe = craft_recipe(name)
        if recipe is None:
            if piece_info(name):
                await event.reply(text=f"{name}：只能通过钓鱼或开箱获得")
            else:
                await event.reply(text=f"未知装备：{name or '（空）'}")
            return
        missing = {
            item: need - int(state.get("pack", {}).get(item, 0))
            for item, need in recipe["items"].items()
            if int(state.get("pack", {}).get(item, 0)) < need
        }
        if missing:
            detail = "、".join(f"{item} 还差 {count}" for item, count in missing.items())
            await event.reply(text=f"{name}：材料不足（{detail}）")
            return
        if not self._charge_fee(str(event.sender.user_id), int(recipe["gold"]), "mcfish_gear"):
            await event.reply(text=f"{name}：金币不足（需要 {recipe['gold']}）")
            return
        for item, need in recipe["items"].items():
            self._remove(state, item, int(need))
        state.setdefault("gear_bag", [])
        state.setdefault("gear_bag", []).append(make_piece(name))
        self._save(str(event.sender.user_id), state)
        await event.reply(text=f"已合成 {name}（{recipe['level']}级）")

    async def _equip_gear(self, event, state: dict, name: str) -> None:
        name = name.strip()
        bag = state.setdefault("gear_bag", [])
        index = next((i for i, item in enumerate(bag) if item.get("name") == name), None)
        if index is None:
            # 钓鱼与开箱掉落会先把装备记在背包；穿戴时再转成带等级的装备实例。
            if piece_info(name) and int(state.get("pack", {}).get(name, 0)) > 0:
                self._remove(state, name, 1)
                item = make_piece(name)
            else:
                await event.reply(text=f"背包中没有 {name or '（空）'}")
                return
        else:
            item = bag.pop(index)
        info = PIECE_INFO.get(name, {})
        slot_key = info.get("slot")
        if slot_key == "ring":
            jewelry = state.setdefault("jewelry", {})
            target = "ring1" if not jewelry.get("ring1") else "ring2"
            old = jewelry.get(target)
            jewelry[target] = item
        elif slot_key == "necklace":
            jewelry = state.setdefault("jewelry", {})
            target = "necklace"
            old = jewelry.get(target)
            jewelry[target] = item
        else:
            equipment = state.setdefault("equipment", {})
            target = slot_key
            old = equipment.get(target)
            equipment[target] = item
        if old:
            state["gear_bag"].append(old)
        self._save(str(event.sender.user_id), state)
        await event.reply(text=f"已装备 {name}（{SLOT_LABELS.get(target, target)}）")

    async def _upgrade_gear(self, event, state: dict, name: str) -> None:
        name = name.strip()
        found = self._find_gear(state, name)
        if found is None:
            await event.reply(text=f"背包与装备中都没有 {name or '（空）'}")
            return
        _group, _slot, item = found
        level = int(item.get("level", 1))
        if level >= 20:
            await event.reply(text=f"{name}：已满级（20）")
            return
        target = level + 1
        cost = upgrade_cost(name, target)
        if cost is None:
            await event.reply(text=f"{name}：无法强化")
            return
        gold, items = cost
        missing = {
            material: need - int(state.get("pack", {}).get(material, 0))
            for material, need in items.items()
            if int(state.get("pack", {}).get(material, 0)) < need
        }
        if missing:
            detail = "、".join(f"{material} 还差 {count}" for material, count in missing.items())
            await event.reply(text=f"{name}：材料不足（{detail}）")
            return
        if not self._charge_fee(str(event.sender.user_id), int(gold), "mcfish_gear_upgrade"):
            await event.reply(text=f"{name}：金币不足（需要 {gold}）")
            return
        for material, need in items.items():
            self._remove(state, material, int(need))
        item["level"] = target
        self._save(str(event.sender.user_id), state)
        await event.reply(text=f"{name}：{target}级（FP {fp_for_level(target)}）")

    async def _unequip_gear(self, event, state: dict, text: str) -> None:
        text = text.strip()
        mapping = {
            "头盔": "head", "胸甲": "body", "腿甲": "legs", "项链": "necklace",
            "戒指1": "ring1", "戒指2": "ring2",
        }
        if text == "戒指":
            jewelry = state.get("jewelry") or {}
            slot = "ring1" if jewelry.get("ring1") else "ring2"
        else:
            slot = mapping.get(text)
        if slot is None:
            await event.reply(text=f"未知部位：{text or '（空）'}")
            return
        group = "equipment" if slot in GEAR_SLOTS else "jewelry"
        item = (state.get(group) or {}).get(slot)
        if not isinstance(item, dict):
            await event.reply(text=f"{SLOT_LABELS.get(slot, slot)}：空")
            return
        del state[group][slot]
        state.setdefault("gear_bag", []).append(item)
        self._save(str(event.sender.user_id), state)
        await event.reply(text=f"已卸下 {item.get('name')}")

    def _card_text(self, state: dict) -> str:
        cards = state.get("cards") or {}
        pity = state.get("card_pity") or {}
        lines = [f"神谕卡片 {len(cards)}/9"]
        for zone, (name, _rate, limit) in CARDS.items():
            if name in cards:
                lines.append(f"{name}（{FISHING_ZONE_LABELS[zone]}）：已获得")
            else:
                lines.append(f"{name}（{FISHING_ZONE_LABELS[zone]}）：保底 {int(pity.get(zone, 0))}/{limit}")
        return "\n".join(lines)
