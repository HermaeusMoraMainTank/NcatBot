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
    fishing_zone_label,
    fish_zone_weights,
    fish_king_probabilities,
    fishing_xp,
    fishing_wait_for_result,
    format_duration,
    bite_skill_from_reply,
    hook_skill_bonus,
    hook_skill_from_reply,
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
    FISHING_ZONE_LABELS,
    fish_sale_price,
    hidden_fish_trigger,
    king_sale_price,
    path_progress,
    parse_count_token,
    treasure_sale_price,
)
from .admin_server import DEFAULT_ADMIN_TOKEN, McFishAdminServer


LOG = get_log("McFish")
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
    "铁竿": (50, 300),
    "金竿": (70, 700),
    "钻石竿": (100, 1500),
    "下界合金竿": (150, 3100),
    "三叉戟": (301, 4000),
}
POLE_ALIASES = {
    "木杆": "木竿",
    "铁杆": "铁竿",
    "金杆": "金竿",
    "钻石杆": "钻石竿",
    "下界合金杆": "下界合金竿",
}
# Fishing drops use their own attribute roll. Shop, exchange, and crafting rods
# intentionally continue to use their existing fixed or inherited attributes.
POLE_DROP_ATTRIBUTE_LIMITS = {
    "maintenance": 10,
    "induce": 3,
    "favor": 2,
}
FISH = {
    "鳕鱼": 10, "鲑鱼": 50, "热带鱼": 100, "河豚": 300, "鹦鹉螺": 500, "墨鱼": 500,
    "鲤鱼": 80, "鳗鱼": 180, "水晶虾": 260, "深海鱼": 320, "银鱼": 400, "金鳞鱼": 600,
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
    "铁竿": 540,
    "金竿": 900,
    "钻石竿": 1260,
    "下界合金竿": 1620,
}
AXOLOTL = "美西螈"
UPGRADES = {"铁竿": "木竿", "金竿": "铁竿", "钻石竿": "金竿", "下界合金竿": "钻石竿", "三叉戟": "下界合金竿"}

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
        "rewards": {"daily": {"xp": 180, "items": {"隐秘诱饵": 1, "铁竿": 1}}, "weekly": {"xp": 480, "items": {"隐秘诱饵": 2}}},
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
        "rewards": {"daily": {"xp": 350, "items": {"净化书": 1, "金竿": 1}}, "weekly": {"xp": 1000, "items": {"净化书": 2}}},
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
        "rewards": {"daily": {"xp": 650, "items": {"净化书": 1, "隐秘诱饵": 1, "钻石竿": 1}}, "weekly": {"xp": 1900, "items": {"净化书": 3}}},
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
        "rewards": {"daily": {"xp": 1200, "items": {"净化书": 2, "下界合金竿": 1}}, "weekly": {"xp": 3600, "items": {"净化书": 5}}},
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
    "level_required": rod_unlock_level("三叉戟"),
    "requirements": {name: 1 for name in FISH_KINGS},
    "reward": {"items": {"三叉戟": 1}},
}
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


MCFISH_HELP = """一款钓鱼模拟器
----------指令----------
- 钓鱼 / 进行钓鱼 / 进行n次钓鱼（支持中文数量，单次最多10竿）
- 钓鱼看板 / 钓鱼商店 / 钓鱼背包 / 钓鱼时钟 / 钓鱼概率 / 钓鱼图鉴
- 钓鱼任务（查看每日、每周和常驻任务）
- 领取钓鱼任务 编号（每日/每周任务提交鱼获；常驻任务只检查历史记录）
- 购买xxx [数量] / 出售xxx [数量] / 出售所有垃圾（可一次出售多个物品）
- 装备鱼竿或装备，卸下饰品，购买饰品槽位，升级饰品，修复鱼竿，附魔诱钓或海之眷顾（细节见下方）
- 合成鱼竿 / 分解鱼竿 / 合成财富浮标 / 合成坚韧钓组 / 合成黄金磁石 / 合成幸运海星
- 合成魔法鱼饵 / 合成隐秘诱饵 / 合成万能鱼饵，消除宝藏诅咒，查看上次结果和当前装备概率明细
- 钓鱼发放 @用户 物品 数量 / 钓鱼充值 @用户 物品 数量（仅限管理员）

规则V6.2:
- 每竿独立结算并按隐藏上钩时间发送结果；同一玩家已有任务时不能重复开始，其他玩家不受影响。
- 普通鱼直接结算；鱼王有咬钩和上钩两个阶段，鱼王窗口只在对应鱼点和时间内生效。
- 经验用于升级：1→2级需要100经验，之后每级增加50；每日次数按等级为50/60/70/80/100次，鱼竿按等级解锁。
- 无鱼竿且余额不足时每3天最多补偿1根木竿；有余额可发送“花费100购买鱼竿”，最多可以购买3次。
- 三叉戟：达到35级后，完成常驻“鱼王全收集”任务（8条鱼王各成功获得1次）领取。耐久上限301，可使1次钓鱼视为3次钓鱼；任务奖励三叉戟不会损坏，耐久归零后可直接支付金币修复，不需要消耗同名鱼竿。
- 合成鱼竿需要3根上一等级鱼竿，合成成功率90%；使用碎片兑换时必须确认，未明确选择不会默认消耗碎片；合成和修复会收取金币手续费。
- 碎片可兑换鱼竿或合成装备；普通鱼基础8%获得1–2个，第13次未获得时保底，鱼王成功必得5–9个。
- 饰品合成：发送“合成财富浮标/合成坚韧钓组/合成黄金磁石/合成幸运海星”，分别需要120/160/220/300个钓鱼碎片，并支付30000/50000/80000/120000金币手续费；永久饰品默认2格。
- 饰品槽位：发送“购买饰品槽位”解锁下一格；钓鱼等级10级并支付80个碎片解锁第3格，20级并支付180个碎片解锁第4格，最多4格。
- 饰品升级：发送“升级财富浮标 / 升级坚韧钓组 / 升级黄金磁石 / 升级幸运海星”升级，升级只消耗钓鱼碎片、不消耗金币，最高Ⅲ级；同名永久饰品不能重复装备。
- 饰品管理：发送“卸下财富浮标 / 卸下坚韧钓组 / 卸下黄金磁石 / 卸下幸运海星”或“卸下全部饰品”取消装备，卸下不会丢失饰品。
- 消耗品合成：魔法鱼饵需要24个碎片（100金币手续费，10竿），隐秘诱饵需要42个碎片（160金币手续费，6竿），万能鱼饵需要50个碎片（220金币手续费，5竿）；合成后发送“装备魔法鱼饵”等命令启用，每完成1竿消耗1次。
- 永久饰品效果：财富浮标Ⅰ/Ⅱ/Ⅲ级为宝藏×1.35/1.50/1.65、鱼王咬钩×1.20/1.28/1.36；坚韧钓组为上钩率+12/16/20个百分点；黄金磁石为宝藏品质+1/2/3档、宝藏售价×1.25/1.35/1.50、普通鱼售价×1.08/1.12/1.16、鱼王售价×10/30/100（钓起显示和出售结算均生效）；幸运海星为普通碎片概率16%/20%/24%。
- 掉落鱼竿随机生成耐久、维修和附魔；合成与修复的手续费由钱包实际扣除。
- 达到对应等级后，商店常驻出售铁竿/金竿/钻石竿/下界合金竿，价格分别为540/900/1260/1620金币；商店购买鱼竿不能作为合成材料，掉落、任务奖励、碎片兑换和合成鱼竿仍可合成。
- 每日和每周任务从简单/中等/复杂任务池按等级档位组合生成；达到35级后才显示“鱼王全收集”常驻任务，要求成功获得全部8条鱼王各1次，完成后奖励不可损坏的三叉戟。常驻任务只检查历史成功捕获记录，不扣除或上交鱼王。发送“钓鱼任务”查看编号和奖励，发送“领取钓鱼任务 编号”领取奖励；任务跨日、跨周自动刷新，常驻任务不会刷新。
- 宝藏诅咒不能交易，可用净化书消除；鱼竿超过30支不能钓鱼，超过50支不能购买。"""


# 帮助图片里的特殊机制。每条只放进“特殊机制”卡片一次，避免和规则正文重复。
MCFISH_SPECIAL_MECHANICS = (
    "美西螈：宝藏掉落；装备后区间同样变为2/8/35/45；每竿要吃3条鱼，鱼不够时永久失去；其他物品一次获得5个，鱼竿和美西螈除外。",
    "海豚：背包里放着海豚时，空竿区间全部并入垃圾区间。",
    "鱼数超过100条：区间变为宝藏2%、鱼竿8%、鱼35%、垃圾45%，只改变概率、不改变数量。",
    "净化书：每完成1竿累计1点祝福，满75点自动获得1本净化书，超出部分计入下一轮。",
    "交易诅咒：买卖鱼竿每满10次获得1个宝藏诅咒；买入价+10%、卖出价-10%；可用净化书1:1消除。",
    "商店折扣每天重新生成（约50%–199%），库存跨天延续；木竿固定180金币；出售按收购价80%结算，可用唱片翻倍。",
    "高级鱼竿按等级常驻出售：铁竿540、金竿900、钻石竿1260、下界合金竿1620金币；商店购买杆不能参与合成。",
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
        self._ensure_fish_windows()
        self._repair_abnormal_pole_trades()
        self._repair_abnormal_pole_inventory()
        self._repair_abnormal_equipment()
        self._reset_fishing_progress_for_level_release()
        self._repair_excessive_drop_quantities()
        self._top_up_excessive_drop_quantities()
        self._repair_concurrent_overwrite_losses()
        self._top_up_concurrent_overwrite_losses()
        self._admin_server = None
        if bool(self.get_config("admin_enabled", True)):
            self._admin_server = McFishAdminServer(
                self,
                host=str(self.get_config("admin_host", "127.0.0.1")),
                port=int(self.get_config("admin_port", 8766)),
                token=str(self.get_config("admin_token", DEFAULT_ADMIN_TOKEN) or DEFAULT_ADMIN_TOKEN),
            )
            try:
                await self._admin_server.start()
            except OSError as exc:
                LOG.error("McFish 管理界面启动失败，请检查 admin_host/admin_port：%s", exc)
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
        state.setdefault("fishing_zone", daily_fishing_zone())
        state.setdefault("buy_times", 0)
        state.setdefault("emergency_pole_day", "")
        state.setdefault("emergency_pole_count", 1 if state.get("emergency_pole_day") else 0)
        state.setdefault("starter_pole_day", "")
        state.setdefault("last_fishing_result", None)
        if state["fish_day"] != today:
            state["fish_day"], state["fish_today"] = today, 0
            state["fishing_zone"] = daily_fishing_zone()
        if state["fishing_zone_day"] != today:
            state["fishing_zone_day"], state["fishing_zone"] = today, daily_fishing_zone()
        if state["trade_day"] != date.today().isoformat():
            state["trade_day"], state["trade_count"] = date.today().isoformat(), 0
            state["pole_trade_count"], state["item_buy_count"] = 0, 0
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
        table_height = 106 + max(1, len(upcoming)) * row_height
        height = table_top + table_height + 42
        image = PILImage.new("RGBA", (width, height), (244, 248, 249, 255))
        draw = ImageDraw.Draw(image)
        header_height = 170
        draw.rounded_rectangle((0, 0, width, header_height), radius=26, fill=(33, 68, 82, 255))
        draw.rectangle((0, header_height - 20, width, header_height), fill=(33, 68, 82, 255))
        draw.text((margin, 32), "钓鱼时钟", fill=(250, 252, 252, 255), font=self._font(64, bold=True))
        draw.text((margin, 112), "鱼点轮换与鱼王窗口", fill=(190, 222, 225, 255), font=self._font(25))
        draw.text((width - margin, 116), f"北京时间 {now:%m月%d日 %H:%M}", fill=(220, 234, 235, 255), font=self._font(23), anchor="ra")

        card_y = header_height + 28
        card_titles = ("当前鱼点", "下一鱼点", "当前状态")
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
        header_y = table_y + 78
        columns = ((margin + 30, "序号"), (margin + 125, "鱼王"), (margin + 410, "开始"), (margin + 665, "结束"), (width - margin - 30, "距离开始"))
        for x, label in columns:
            draw.text((x, header_y), label, fill=(92, 112, 117, 255), font=self._font(21), anchor="rs" if x == width - margin - 30 else "ls")
        draw.line((margin + 26, table_y + 101, width - margin - 26, table_y + 101), fill=(205, 220, 222, 255), width=2)
        if upcoming:
            for index, window in enumerate(upcoming):
                y = table_y + 119 + index * row_height
                if index % 2 == 0:
                    draw.rounded_rectangle((margin + 20, y - 14, width - margin - 20, y + 46), radius=10, fill=(246, 250, 250, 255))
                until = format_duration((window.starts_at - now).total_seconds())
                draw.text((margin + 34, y), str(index + 1), fill=(70, 83, 88, 255), font=self._font(25))
                draw.text((margin + 125, y), window.king_name, fill=(40, 61, 68, 255), font=self._font(25, bold=True))
                draw.text((margin + 410, y), f"{window.starts_at:%m月%d日 %H:%M}", fill=(70, 83, 88, 255), font=self._font(23))
                draw.text((margin + 665, y), f"{window.ends_at:%H:%M}", fill=(70, 83, 88, 255), font=self._font(23))
                draw.text((width - margin - 34, y), until, fill=(38, 144, 158, 255), font=self._font(23, bold=True), anchor="ra")
        else:
            draw.text((margin + 30, table_y + 128), "本周暂无后续鱼王窗口", fill=(92, 112, 117, 255), font=self._font(25))

        output = Path(self.workspace) / "images" / f"clock_{uid}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, "PNG")
        return output

    def _render_help_image(self, uid: str) -> Path:
        """Render concise fishing help as a two-column card layout."""
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
            "黄金磁石": "Ⅰ/Ⅱ/Ⅲ级：宝藏品质+1/2/3档；宝藏售价×1.25/1.35/1.50；普通鱼售价×1.08/1.12/1.16；鱼王售价×10/30/100",
            "魔法鱼饵": "等待时间×0.85，稀有鱼权重×1.25",
            "幸运海星": "Ⅰ/Ⅱ/Ⅲ级：普通碎片概率16%/20%/24%",
            "隐秘诱饵": "对应鱼点隐藏鱼触发率×1.50",
            "万能鱼饵": "每竿兜底一个普通鱼点或鱼饵限制",
        }
        rod_rows = []
        for name, (durability, price) in POLES.items():
            special = "  · 1次钓鱼按3竿计算  · 鱼王全收集任务奖励" if name == "三叉戟" else ""
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
                [
                    "开始：钓鱼 / 进行钓鱼 / 进行N次钓鱼（支持中文数量，单次最多10竿）",
                    "查询：钓鱼看板 / 钓鱼商店 / 钓鱼背包 / 钓鱼时钟 / 钓鱼概率 / 钓鱼图鉴 / 查看上次钓鱼结果",
                    "任务查看：钓鱼任务（查看编号、难度、等级档位、鱼种和奖励）",
                    "任务领取：领取钓鱼任务 编号（自动检查背包并提交，完成后领取奖励）",
                    "物品：购买xxx [数量]、出售xxx [数量]、出售所有垃圾（可一次出售多个）、分解鱼竿（回收碎片）",
                    "装备：装备鱼竿/饰品、卸下饰品、修复鱼竿、附魔诱钓/海之眷顾",
                    "饰品槽位：购买饰品槽位（按等级和碎片解锁下一格）",
                    "饰品升级：升级财富浮标 / 升级坚韧钓组 / 升级黄金磁石 / 升级幸运海星",
                    "成长：合成鱼竿/装备、分解鱼竿、消除宝藏诅咒、当前装备概率明细",
                ],
                (38, 144, 164),
            ),
            (
                "核心循环",
                [
                    "每竿独立结算并按隐藏上钩时间发送结果；完成后显示上钩时间、结果和经验。",
                    "普通鱼直接结算；鱼王分咬钩和上钩两个阶段，回复选项会影响后续机会和鱼体大小。",
                    "经验用于升级：1→2级需要100点，之后每级增加50点；每日次数按等级为50/60/70/80/100次。",
                    "鱼王成功获得300–900经验，隐藏鱼80–120经验，普通鱼5–50经验；脱钩不获得捕获经验。",
                    "同一玩家已有钓鱼任务时不能重复开始，其他玩家不受影响。",
                ],
                (86, 122, 174),
            ),
            (
                "任务与金币循环",
                [
                    "任务池分简单/中等/复杂，并按1-5、5-10、10-20、20-30、30+级分档；高等级任务目标更大，奖励会增加经验、金币、碎片、消耗品或鱼竿。",
                    "每日/每周任务从任务池排列组合生成；任务板可能保留1-5级低档任务，不想领取不会产生任何消耗。",
                    "达到35级后，常驻鱼王全收集要求8条鱼王各成功获得1次，只检查历史记录、不扣鱼王，完成后奖励不可损坏的三叉戟。",
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
                    "鱼竿也可分解回收碎片：木1、铁2、金4、钻石8、下界合金16、三叉戟32；分解前会列出属性并确认。",
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
                    "鱼竿掉落绝对概率：木70% / 铁20% / 金6% / 钻石3% / 下界合金1%。",
                    "当前鱼竿最多只能钓到下一等级；越级区间转为空竿。掉落鱼竿随机生成耐久、维修和附魔。",
                    "海之眷顾每级把6%空竿转为宝藏1%、鱼竿2%、鱼3%；诱钓按等级缩短等待。",
                    "隐藏鱼：幽光鳐（深海）、星砂鳗（遗迹水域）、云潮鲨（风暴海域）；需对应鱼点和隐秘诱饵，4.5%触发。",
                ],
                (49, 132, 190),
            ),
            (
                "鱼王与鱼点",
                [
                    "鱼点每日轮换：河湾、深海、遗迹水域、风暴海域；鱼王只在对应鱼点的窗口内出现。",
                    "鱼王窗口每周重新生成，位于09:00–21:00的非整点时段，前置和后置窗口可能重合。",
                    "后置鱼王需要前置鱼王咬钩过一次；咬钩阶段回复2可牺牲前置鱼王尝试后置目标。",
                    "鱼王路径按次数分别累计；满足条件后下一次上钩率锁定95%，进度长期保存。",
                    "发送钓鱼时钟查看当前环境和最近5次窗口，不会主动通知。",
                ],
                (61, 145, 112),
            ),
            ("特殊机制", list(MCFISH_SPECIAL_MECHANICS), (229, 112, 118)),
        ]

        def prepare_section(title: str, rows: list[str], accent: tuple[int, int, int]):
            wrapped_rows: list[list[str]] = []
            for row in rows:
                row_lines = wrap(f"• {row}", body_font, column_width - 48)
                wrapped_rows.append(row_lines)
            line_height = 34
            height = 82 + sum(len(lines) * line_height + 10 for lines in wrapped_rows) + 20
            return (title, wrapped_rows, accent, height)

        prepared = [prepare_section(*section) for section in sections]
        # Pack whole cards into two near-equal columns. There are only seven
        # cards, so exhaustive partitioning keeps the layout stable when one
        # section grows without adding a general-purpose layout dependency.
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
        header_height = 230
        footer_height = 92
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
        draw.text((margin, 137), "McFish  ·  指令、掉落、鱼王与装备规则", font=subtitle_font, fill=(190, 222, 225, 255))
        draw.text(
            (width - margin - 470, 148),
            f"{len(POLES)} 种鱼竿  ·  {len(EQUIPMENT)} 件装备  ·  规则 V6.2",
            font=small_font,
            fill=(220, 234, 235, 255),
        )
        for index, name in enumerate(("木竿", "金竿", "下界合金竿")):
            icon = self._resource_image(name, (86, 86))
            if icon:
                image.alpha_composite(icon, (width - margin - 310 + index * 105, 38))

        def draw_section(section, x: int, y: int) -> int:
            title, wrapped_rows, accent, card_height = section
            draw.rounded_rectangle(
                (x, y, x + column_width, y + card_height),
                radius=18,
                fill=(255, 255, 255, 255),
                outline=(222, 231, 233, 255),
                width=2,
            )
            draw.rounded_rectangle((x, y, x + 10, y + card_height), radius=4, fill=(*accent, 255))
            draw.text((x + 30, y + 24), title, font=section_font, fill=(40, 61, 68, 255))
            draw.line((x + 30, y + 72, x + column_width - 28, y + 72), fill=(*accent, 100), width=2)
            current_y = y + 92
            for lines in wrapped_rows:
                for line in lines:
                    draw.text((x + 30, current_y), line, font=body_font, fill=(70, 83, 88, 255))
                    current_y += 34
                current_y += 10
            return y + card_height + 22

        y_left = header_height + 28
        for section in left_sections:
            y_left = draw_section(section, margin, y_left)
        y_right = header_height + 28
        for section in right_sections:
            y_right = draw_section(section, margin + column_width + gap, y_right)

        footer_y = height - footer_height
        draw.line((margin, footer_y + 10, width - margin, footer_y + 10), fill=(184, 202, 205, 255), width=2)
        draw.text(
            (margin, footer_y + 30),
            "发送“钓鱼概率”查看实时概率  ·  发送“钓鱼图鉴”查看个人捕获记录  ·  发送“钓鱼时钟”查看鱼王窗口",
            font=small_font,
            fill=(92, 112, 117, 255),
        )

        output = Path(self.workspace) / "images" / f"help_{uid}.png"
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
            ) / 100
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
        for kind in ("daily", "weekly", "permanent"):
            if kind == "permanent" and int(state.get("fish_level", 1)) < int(PERMANENT_TASK["level_required"]):
                continue
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
        weekly_count = len(state["fishing_weekly_tasks"]["tasks"])
        level = int(state.get("fish_level", 1))
        permanent_visible = level >= int(PERMANENT_TASK["level_required"])
        lines = [
            f"钓鱼任务（当前等级 {level}）",
            "操作：领取钓鱼任务 编号（每日/每周任务会检查并提交鱼获；常驻任务只检查历史获得记录）",
            *self._task_lines(state, "daily", 1),
            "",
            *self._task_lines(state, "weekly", daily_count + 1),
        ]
        if permanent_visible:
            lines.extend(("", *self._task_lines(state, "permanent", daily_count + weekly_count + 1)))
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
        return min(600, max(20, POLES[pole_name][1] // 10 + missing // 10))

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
            | {name: int(item["base_price"]) for name, item in FISH_KINGS.items()}
            | ITEMS
            | SHOP_EQUIPMENT_PRICES
        )
        self._types = {}
        for name, item in HIDDEN_FISH.items():
            self._prices[name] = int(item["price"])
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
            discounts = json.loads(row[0])
            inventory = json.loads(row[1])
            try:
                custom_prices = json.loads(row[2] or "{}") if has_custom_prices else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                custom_prices = {}
            self._shop_custom_prices = self._shop_custom_price_items(custom_prices, inventory)
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
                "poles": [("下界合金竿", {"durable": 1, "maintenance": 0, "induce": 0, "favor": 0})],
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
                    held = int(pack.get(name, 0) or 0)
                    deducted = min(held, int(amount))
                    if deducted:
                        pack[name] = held - deducted
                        removed[name] = deducted
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
                name: int(amount) - int(removed.get(name, 0))
                for name, amount in repair["items"].items()
                if int(removed.get(name, 0)) < int(amount)
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
        candidates = [item for item in items if item.get("类型") == zone]
        if not candidates:
            return None, 1
        if zone == "fish" and "fishing_zone" in state:
            weights = fish_zone_weights(str(state.get("fishing_zone", daily_fishing_zone())), rod_name)
            if magic_bait:
                for name in ("水晶虾", "深海鱼", "银鱼", "金鳞鱼"):
                    if name in weights:
                        weights[name] = int(weights[name] * 1.25)
            names = [name for name, weight in weights.items() if weight > 0]
            selected_name = random.choices(names, weights=[weights[name] for name in names], k=1)[0]
            return selected_name, drop_quantity(selected_name)
        if zone == "fish" and magic_bait:
            rare_names = {"水晶虾", "深海鱼", "银鱼", "金鳞鱼"}
            weights = [
                int(item.get("概率[0-100)", 0)) * (1.25 if str(item.get("名称")) in rare_names else 1.0)
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
            **{name: item["asset_id"] for name, item in FISH_KINGS.items()},
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
        names = set(FISH) | set(FISH_KINGS) | set(HIDDEN_FISH)
        names.update(POLES)
        names.update(ITEMS)
        names.update(SHOP_EQUIPMENT_PRICES)
        names.update(EQUIPMENT)
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
        rendered_root = Path(self.workspace) / "images"
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

    def _fish_probability_map(self, rod_name: str, zone: str, category_probability: int, *, magic_bait: bool = False) -> dict[str, float]:
        weights = fish_zone_weights(zone, rod_name)
        if magic_bait:
            for name in ("水晶虾", "深海鱼", "银鱼", "金鳞鱼"):
                if name in weights:
                    weights[name] = int(weights[name] * 1.25)
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
        fish_values = self._fish_probability_map(rod_name, zone, categories[2], magic_bait=magic_bait)
        air = max(0, 100 - sum(categories)) + blocked_pole

        current: list[tuple[str, str]] = [
            ("heading", "第一部分 当前用户概率"),
            ("body", f"用户：{uid}    鱼竿：{equip_name or '未装备（按木竿限制计算）'}"),
            ("body", f"鱼点：{FISHING_ZONE_LABELS.get(zone, zone)}    鱼总数：{fish_count}    {'强化区间' if fish_count > 100 or equip_name == AXOLOTL else '基础区间'}"),
            ("body", f"饰品槽位：{len(state.get('accessories', []))}/{state.get('accessory_slots', 2)}    饰品：{'、'.join(f'{name}{self._enchant_label(self._accessory_level(state, name))}' for name in state.get('accessories', [])) or '无'}    鱼饵：{'、'.join(name for name, count in state.get('active_consumables', {}).items() if int(count) > 0) or '无'}"),
            ("body", f"大类：宝藏 {categories[0]}% / 鱼竿 {categories[1]}% / 普通鱼 {categories[2]}% / 垃圾 {categories[3]}% / 空竿 {air:.2f}%"),
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
        current.append(("heading", "垃圾"))
        current.append(("body", "、".join(f"{name} {self._probability_value(value)}" for name, value in waste_values.items())))
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
            bite, hook = fish_king_probabilities(king_name, rod_name, prerequisite_caught=ready)
            wealth_multiplier = self._accessory_effect(state, "财富浮标", "king_clue_rate", 1.0)
            if wealth_multiplier > 1.0:
                bite = king_bite_rate(bite, wealth_bobber=wealth_multiplier)
            tough_points = self._accessory_effect(state, "坚韧钓组", "king_hook_success_points", 0)
            if tough_points:
                hook = king_success_rate(hook, tough_tackle=tough_points)
            king_lines.append(f"{king_name}：咬钩 {bite * 100:.2f}% / 基础上钩 {hook * 100:.2f}%")
        current.extend(("body", line) for line in king_lines or ["鱼王：当前鱼点没有对应鱼王"])
        current.append(("note", "鱼王概率仅在对应窗口内生效；最终上钩率还受回复技能和路径影响。"))

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
        all_lines.append(("heading", "普通鱼（基础区间）"))
        for candidate_zone in ("bay", "deep", "ruin", "storm"):
            all_lines.append(("body", f"{FISHING_ZONE_LABELS[candidate_zone]}："))
            for candidate in POLES:
                values = self._fish_probability_map(candidate, candidate_zone, standard_categories[2])
                all_lines.append(("body", f"{candidate}：" + "、".join(f"{name}{self._probability_value(value)}" for name, value in values.items())))
        all_lines.append(("heading", "鱼王（咬钩率 / 基础上钩率）"))
        for king_name in FISH_KINGS:
            values = []
            for candidate in POLES:
                bite, hook = fish_king_probabilities(king_name, candidate, prerequisite_caught=True)
                values.append(f"{candidate}{bite * 100:.1f}/{hook * 100:.1f}")
            all_lines.append(("body", f"{king_name}：" + "、".join(values)))
        all_lines.extend([
            ("heading", "隐藏鱼与不可直接钓出的装备"),
            ("body", "深海：幽光鳐 4.50%；遗迹水域：星砂鳗 4.50%；风暴海域：云潮鲨 4.50%；河湾：无隐藏鱼。均需对应鱼点和隐秘诱饵。"),
            ("body", "财富浮标、坚韧钓组、黄金磁石、幸运海星、魔法鱼饵、隐秘诱饵、万能鱼饵：直接钓出概率均为0，需要合成或商店获得。"),
            ("note", "鱼王必须在对应鱼点和鱼王窗口内；后置鱼王未满足前置条件时为0。"),
        ])
        return current, all_lines

    def _render_probability_image(self, uid: str, state: dict) -> Path:
        current, all_probabilities = self._probability_sections(uid, state)
        width = 1600
        margin = 42
        gap = 28
        column_width = (width - margin * 2 - gap) // 2
        header_height = 176
        heading_font = self._font(29, bold=True)
        body_font = self._font(24)
        note_font = self._font(20)

        def prepare(entries: list[tuple[str, str]]) -> tuple[list[tuple[str, list[str]]], int]:
            prepared: list[tuple[str, list[str]]] = []
            height = 44
            for kind, text in entries:
                font = heading_font if kind == "heading" else note_font if kind == "note" else body_font
                lines = self._wrap_text(text, font, column_width - 72)
                prepared.append((kind, lines))
                line_height = 40 if kind == "heading" else 32 if kind == "body" else 28
                height += len(lines) * line_height + (18 if kind == "heading" else 12)
            return prepared, height + 28

        current_prepared, current_height = prepare(current)
        all_prepared, all_height = prepare(all_probabilities)
        card_height = max(current_height, all_height)
        height = header_height + card_height + 64
        image = PILImage.new("RGBA", (width, height), (244, 248, 249, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, width, header_height), radius=26, fill=(33, 68, 82, 255))
        draw.rectangle((0, header_height - 20, width, header_height), fill=(33, 68, 82, 255))
        draw.text((margin, 34), "钓鱼概率", fill=(250, 252, 252, 255), font=self._font(64, bold=True))
        draw.text((margin, 113), "当前装备明细 · 全部鱼竿对照", fill=(190, 222, 225, 255), font=self._font(25))
        draw.text((width - margin, 116), "实际掉落按当前鱼点与鱼竿限制结算", fill=(220, 234, 235, 255), font=self._font(21), anchor="ra")

        def draw_column(prepared: list[tuple[str, list[str]]], x: int, accent: tuple[int, int, int], own_height: int) -> None:
            y = header_height + 28
            self._card(draw, (x, y, x + column_width, y + own_height))
            y += 24
            for entry_index, (kind, lines) in enumerate(prepared):
                if kind == "heading":
                    block_height = max(42, len(lines) * 40 + 8)
                    draw.rounded_rectangle((x + 18, y, x + column_width - 18, y + block_height), radius=10, fill=(*accent, 28))
                    draw.rounded_rectangle((x + 18, y, x + 26, y + block_height), radius=4, fill=(*accent, 255))
                    for line_index, line in enumerate(lines):
                        draw.text((x + 38, y + 7 + line_index * 40), line, fill=(40, 61, 68, 255), font=heading_font)
                    y += block_height + 12
                    continue
                font = note_font if kind == "note" else body_font
                line_height = 28 if kind == "note" else 32
                for line in lines:
                    if entry_index % 2 == 0 and kind == "body":
                        draw.rounded_rectangle((x + 24, y - 3, x + column_width - 24, y + line_height - 2), radius=6, fill=(247, 250, 250, 255))
                    draw.text((x + 38, y), line, fill=(70, 83, 88, 255) if kind == "body" else (92, 112, 117, 255), font=font)
                    y += line_height
                y += 12

        draw_column(current_prepared, margin, (38, 144, 158), current_height)
        draw_column(all_prepared, margin + column_width + gap, (78, 116, 176), all_height)
        output = Path(self.workspace) / "images" / f"probability_{uid}.png"
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
        fish_names = [
            str(item.get("名称")) for item in raw_items
            if str(item.get("类型")) == "fish" or str(item.get("名称")) == "海豚"
        ]
        for name in fish_names:
            probability_category = category[0] if name == "海豚" else category[2]
            probability_type = "treasure" if name == "海豚" else "fish"
            lines.append(f"{name} : {item_probability(name, probability_type, probability_category)}")
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
        if not text or not (text.startswith(("进行", "购买", "出售", "装备", "卸下", "升级", "附魔", "合成", "消除", "修复", "维修", "分解")) or text in ("花费100购买鱼竿", "购买饰品槽位", "解锁饰品槽位", "钓鱼帮助", "钓鱼概率", "钓鱼背包", "钓鱼商店", "钓鱼看板", "钓鱼时钟", "钓鱼图鉴", "钓鱼任务", "查看上次钓鱼结果", "出售所有垃圾", "当前装备概率明细", "查看钓鱼规则") or text.startswith("钓鱼图鉴 ") or is_task_command or is_item_grant_command):
            return
        uid = str(event.sender.user_id)
        state = self._state(uid)
        if is_item_grant_command:
            await self._grant_item(event, text)
            return
        if text == "查看上次钓鱼结果":
            await self._show_last_fishing_result(event, state); return
        if text in ("钓鱼背包",):
            output = await asyncio.to_thread(self._render_pack_image, uid, state)
            await self._send_original_message(event, MessageArray([Image(file=str(output))])); return
        if text == "钓鱼时钟":
            output = await asyncio.to_thread(self._render_clock_image, uid)
            await self._send_original_message(event, MessageArray([Image(file=str(output))])); return
        if text == "钓鱼图鉴" or text.startswith("钓鱼图鉴 "):
            output = await asyncio.to_thread(self._render_catalog_image, uid, state, text[4:].strip())
            await self._send_original_message(event, MessageArray([Image(file=str(output))])); return
        if text in ("钓鱼帮助", "查看钓鱼规则"):
            output = await asyncio.to_thread(self._render_help_image, uid)
            await self._send_original_message(event, MessageArray([Image(file=str(output))])); return
        if text == "钓鱼概率":
            output = await asyncio.to_thread(self._render_probability_image, uid, state)
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
            await self._send_original_message(
                event,
                MessageArray([
                    At(user_id=uid),
                    PlainText(text=self._probability_detail_text(state)),
                ]),
            )
            return
        if text in ("钓鱼商店", "钓鱼看板"):
            output = await asyncio.to_thread(self._render_store_image, uid)
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

        requested_king = requested in FISH_KINGS
        if requested_king:
            fish_names: list[str] = []
            king_names = [requested]
        else:
            fish_names = names
            king_names = [name for name in FISH_KINGS if not requested or requested == name]

        discovered_fish = sum(1 for name in FISH if int(stats.get(name, {}).get("count", 0)) > 0)
        discovered_kings = sum(1 for name in FISH_KINGS if int(king_stats.get(name, {}).get("caught", 0)) > 0)
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
            ("鱼王成功", f"{discovered_kings}/{len(FISH_KINGS)}", (61, 145, 112)),
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
        return "钓鱼商店：\n" + "\n".join(f"{name}: {price} 金币" for name, price in rod_prices.items()) + "\n" + "\n".join(f"{name}: {price} 金币" for name, price in item_prices.items() if price)

    @staticmethod
    def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        candidates = ("C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc") if bold else ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf")
        for path in candidates:
            if Path(path).exists():
                return ImageFont.truetype(path, size)
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
        type_order = {"article": 0, "fish": 1, "pole": 2, "treasure": 3, "waste": 4}
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
        daily_limit = daily_attempt_limit(level)
        width = 1240
        margin = 42
        gap = 22
        header_height = 174
        profile_height = 158
        equip_height = 224
        # Rod cards contain two attribute lines; leave enough room below the icon
        # so the second line stays inside its card and cannot overlap the next row.
        inventory_row_height = 106
        inventory_rows = max(2, (len(entries) + 1) // 2)
        inventory_height = 78 + inventory_rows * inventory_row_height + 28
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
        draw.text((margin + 148, profile_y + 68), f"等级 {level}  ·  经验 {total_xp - current_level_xp}/{level_span}", fill=(92, 112, 117, 255), font=self._font(21))
        self._draw_bar(draw, (margin + 148, profile_y + 105, margin + 360, profile_y + 121), level_progress)
        metrics = (
            ("今日钓鱼", f"{min(daily_limit, int(state.get('fish_today', 0)))}/{daily_limit}"),
            ("钱包", f"{int(account['balance'])} 金币"),
            ("背包统计", f"{fish_count} 条鱼 · {pole_count} 支竿"),
        )
        metric_width = 220
        metric_start = margin + 410
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
            (margin + 148, profile_y + 132),
            f"饰品槽位 {len(state['accessories'])}/{state['accessory_slots']}：{accessory_status}",
            fill=(38, 144, 158, 255),
            font=self._font(18),
        )

        equip_y = profile_y + profile_height + 20
        self._card(draw, (margin, equip_y, width - margin, equip_y + equip_height))
        draw.text((margin + 28, equip_y + 20), "当前装备", fill=(40, 61, 68, 255), font=self._font(31, bold=True))
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
        if entries:
            tile_width = (width - margin * 2 - gap - 40) // 2
            for index, (display_name, asset_name, count, detail, is_pole, progress) in enumerate(entries):
                col, row = index % 2, index // 2
                x = margin + 20 + col * (tile_width + gap)
                y = inventory_y + 62 + row * inventory_row_height
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
        else:
            draw.text((margin + 30, inventory_y + 98), "背包没有存放任何东西", fill=(92, 112, 117, 255), font=self._font(25))

        output = Path(self.workspace) / "images" / f"pack_{uid}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, "PNG")
        return output

    def _render_store_image(self, uid: str) -> Path:
        discounts, inventory = self._shop_data()
        order = list(dict.fromkeys(
            list(getattr(self, "_thing_order", []))
            + list(POLES)
            + list(SHOP_EQUIPMENT_PRICES)
            + list(discounts)
        ))
        prices = {
            name: self._shop_price(name, discounts)
            for name in order
            if name in discounts and name not in ("垃圾", "宝藏诅咒")
        }
        output = Path(self.workspace) / "images" / f"store_{uid}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        inventory_rows: list[tuple[str, int, int, str]] = []
        for name, count in inventory.items():
            if name == "初始木竿" or name in POLES:
                pole_name = "木竿" if name == "初始木竿" else name
                for pole in self._shop_poles(count, pole_name):
                    label = "木竿（常驻）" if name == "初始木竿" else f"{name}({pole.get('durable', 0)}/{pole.get('maintenance', 0)}/{pole.get('induce', 0)}/{pole.get('favor', 0)})"
                    pole_price = self._shop_price(name, discounts) if name == "初始木竿" else self._pole_price(pole, discounts, name)
                    inventory_rows.append((label, 1, pole_price, pole_name))
            else:
                if int(count) <= 0:
                    continue
                inventory_rows.append((name, int(count), self._shop_price(name, discounts), name))
        listed_rods = {
            asset_name for _name, _count, _price, asset_name in inventory_rows
            if asset_name in SHOP_ROD_PRICES
        }
        for name in SHOP_ROD_PRICES:
            if name not in listed_rods:
                inventory_rows.append((f"{name}（常驻）", 1, self._shop_price(name, discounts), name))

        width = 1400
        margin = 46
        gap = 22
        header_height = 174
        price_rows = max(1, (len(prices) + 1) // 2)
        inventory_rows_count = max(1, (len(inventory_rows) + 1) // 2)
        price_section_height = 82 + price_rows * 94 + 30
        inventory_section_height = 82 + inventory_rows_count * 82 + 30
        height = header_height + 24 + price_section_height + 20 + inventory_section_height + 42
        image = PILImage.new("RGBA", (width, height), (244, 248, 249, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, width, header_height), radius=26, fill=(33, 68, 82, 255))
        draw.rectangle((0, header_height - 20, width, header_height), fill=(33, 68, 82, 255))
        draw.text((margin, 34), "钓鱼商店", fill=(250, 252, 252, 255), font=self._font(62, bold=True))
        draw.text((margin, 116), "今日价格波动 · 上架库存", fill=(190, 222, 225, 255), font=self._font(25))
        draw.text((width - margin, 116), "价格随每日折扣变化", fill=(220, 234, 235, 255), font=self._font(21), anchor="ra")

        tile_width = (width - margin * 2 - gap - 40) // 2
        price_y = header_height + 24
        self._card(draw, (margin, price_y, width - margin, price_y + price_section_height))
        draw.text((margin + 28, price_y + 20), "今日波动", fill=(40, 61, 68, 255), font=self._font(31, bold=True))
        draw.text((width - margin - 28, price_y + 28), "当前价 · 均价 · 涨跌", fill=(92, 112, 117, 255), font=self._font(19), anchor="ra")
        for index, (name, price) in enumerate(prices.items()):
            col, row = index % 2, index // 2
            x = margin + 20 + col * (tile_width + gap)
            y = price_y + 64 + row * 94
            self._card(draw, (x, y, x + tile_width, y + 80), fill=(248, 251, 251, 255), radius=12)
            icon = self._resource_image(name, (44, 44))
            if icon:
                image.alpha_composite(icon, (x + 16, y + 18))
            average = getattr(self, "_prices", {}).get(name, POLES.get(name, (0, 0))[1])
            change = discounts.get(name, 100) - 100
            change_text = f"+{change}%" if change > 0 else f"{change}%"
            change_color = (194, 91, 86, 255) if change > 0 else (61, 145, 112, 255) if change < 0 else (92, 112, 117, 255)
            draw.text((x + 72, y + 12), name, fill=(40, 61, 68, 255), font=self._font(21, bold=True))
            draw.text((x + 72, y + 45), f"{price} 金币  ·  均价 {average}", fill=(92, 112, 117, 255), font=self._font(17))
            draw.text((x + tile_width - 18, y + 23), change_text, fill=change_color, font=self._font(22, bold=True), anchor="ra")

        inventory_y = price_y + price_section_height + 20
        self._card(draw, (margin, inventory_y, width - margin, inventory_y + inventory_section_height))
        draw.text((margin + 28, inventory_y + 20), "上架内容", fill=(40, 61, 68, 255), font=self._font(31, bold=True))
        draw.text((width - margin - 28, inventory_y + 28), "库存 · 单价", fill=(92, 112, 117, 255), font=self._font(19), anchor="ra")
        if inventory_rows:
            for index, (display_name, row_count, row_price, asset_name) in enumerate(inventory_rows):
                col, row = index % 2, index // 2
                x = margin + 20 + col * (tile_width + gap)
                y = inventory_y + 64 + row * 82
                self._card(draw, (x, y, x + tile_width, y + 68), fill=(249, 250, 247, 255), radius=12)
                icon = self._resource_image(asset_name, (40, 40))
                if icon:
                    image.alpha_composite(icon, (x + 16, y + 14))
                draw.text((x + 70, y + 11), display_name, fill=(40, 61, 68, 255), font=self._font(19))
                draw.text((x + 70, y + 40), f"库存 {row_count}", fill=(92, 112, 117, 255), font=self._font(17))
                draw.text((x + tile_width - 18, y + 25), f"{row_price} 金币", fill=(112, 96, 54, 255), font=self._font(20, bold=True), anchor="ra")
        else:
            draw.text((margin + 30, inventory_y + 98), "当前商店并没有上架任何物品", fill=(92, 112, 117, 255), font=self._font(25))
        draw.text((margin, height - 28), "出售商品按收购价的 80% 结算；附魔鱼竿按实际属性计价。", fill=(92, 112, 117, 255), font=self._font(19))
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
        daily_limit = daily_attempt_limit(level) if has_level_state else int(self.get_config("fish_limit", 50))
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
        if not axolotl:
            number = min(number, int(equip["durable"]), remaining_today)
        if number <= 0:
            await event.reply(text="当前没有可用的钓鱼次数或鱼竿耐久")
            return
        fish_attempts = number
        result_note = ""
        if equip["name"] == "三叉戟":
            fish_attempts *= 3
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
            remaining = required_fish
            eaten: dict[str, int] = {}
            for fish_name in FISH:
                consumed = min(remaining, int(state["pack"].get(fish_name, 0)))
                if consumed:
                    self._remove(state, fish_name, consumed)
                    eaten[fish_name] = consumed
                    remaining -= consumed
                if remaining == 0:
                    break
            fish_attempts = sum(eaten.values()) // 3
            result_note = "(美西螈掉落翻5倍，吃3倍鱼：\n吃掉了：" + " ".join(
                f"{count}{fish_name}" for fish_name, count in eaten.items()
            ) + ")"
        else:
            equip["durable"] -= number
            if 0 < int(equip["durable"]) < 10:
                result_note = f"(你的鱼竿耐久仅剩{equip['durable']})"
            elif int(equip["durable"]) <= 0:
                result_note = "(你的鱼竿已销毁)"
        state["fish_today"] += number
        # 先持久化本次预约消耗；鱼获在每一竿实际等待结束后单独结算并保存。
        base = self._save_fishing_state(uid, state, base)
        initial_magic_bait = int(state.get("active_consumables", {}).get("魔法鱼饵", 0)) > 0
        initial_wait_rate = EQUIPMENT["魔法鱼饵"]["effects"]["wait_rate"] if initial_magic_bait else 1.0
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
        for attempt_index in range(fish_attempts):
            current_time += timedelta(seconds=current_wait_seconds)
            scheduled_at = current_time
            wait_seconds = (scheduled_at - datetime.now()).total_seconds()
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            # 每竿开始前读回最新存档：等待期间玩家的购买/合成/装备改动不会被本竿覆盖。
            state = self._state(uid)
            equip = state.get("equip") or equip
            # 海豚等动态背包效果必须按本竿最新存档计算，不能沿用批次开始时的边界。
            drop_boundaries = self._zone_boundaries(state)
            self._advance_tasks(state, casts=1)
            base = copy.deepcopy(state)
            magic_bait_active = int(state.get("active_consumables", {}).get("魔法鱼饵", 0)) > 0
            universal_bait = int(state.get("active_consumables", {}).get("万能鱼饵", 0)) > 0
            hidden_lure_active = int(state.get("active_consumables", {}).get("隐秘诱饵", 0)) > 0
            current_wait_rate = EQUIPMENT["魔法鱼饵"]["effects"]["wait_rate"] if magic_bait_active else 1.0
            if magic_bait_active:
                state["active_consumables"]["魔法鱼饵"] -= 1
            if universal_bait:
                state["active_consumables"]["万能鱼饵"] -= 1
            if hidden_lure_active:
                state["active_consumables"]["隐秘诱饵"] -= 1
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
            )
            if hidden_name:
                attempt_xp = fishing_xp(hidden_name)
                earned_xp += attempt_xp
                self._add_fishing_xp(state, attempt_xp)
                self._add(state, hidden_name)
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
                        f"{FISH_KING_PROMPTS[king_window.king_name]['trigger']}\n咬钩阶段技能：\n1. 保留目标（进入上钩判定）\n2. 以小钓大（牺牲当前鱼，尝试后置鱼王）\n3. 见好就收（放弃追逐）",
                        event,
                        choices={"1": "hold_target", "2": "mooch_post", "3": "give_up"},
                        timeout=30,
                        timeout_reply="等待超时，放弃这次机会",
                        cancel_reply="选择无效，放弃这次机会",
                    )
                else:
                    bite_choice = await self._choose(
                        f"{FISH_KING_PROMPTS[king_window.king_name]['trigger']}\n咬钩阶段技能：\n1. 保留目标（进入上钩判定）\n2. 见好就收（放弃追逐）",
                        event,
                        choices={"1": "hold_target", "2": "give_up"},
                        timeout=30,
                        timeout_reply="等待超时，放弃这次机会",
                        cancel_reply="选择无效，放弃这次机会",
                    )
                bite_skill = bite_skill_from_reply(bite_choice.text or "") or bite_choice.key
                if bite_choice.key in (None, "give_up") or bite_skill == "give_up":
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
                    "已进入上钩判定。\n上钩阶段技能：\n1. 精准提竿\n2. 强力提竿\n3. 谦逊之饵\n4. 雄心之饵",
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
                progress = path_progress(state, king_window.king_name, strategy)
                guaranteed = bool(progress.get("guaranteed"))
                tough_points = self._accessory_effect(state, "坚韧钓组", "king_hook_success_points", 0)
                success = random.random() < king_success_rate(
                    hook_rate + hook_skill_bonus(hook_skill),
                    guaranteed=guaranteed,
                    tough_tackle=tough_points,
                )
                if guaranteed:
                    progress["guaranteed"] = False
                if success:
                    attempt_xp = fishing_xp(king_window.king_name)
                    earned_xp += attempt_xp
                    self._add_fishing_xp(state, attempt_xp)
                    length = king_length_for_hook(king_window.king_name, hook_skill)
                    self._add(state, king_window.king_name)
                    self._record_capture(state, king_window.king_name, caught_at=scheduled_at)
                    king_stats["caught"] = int(king_stats.get("caught", 0)) + 1
                    self._advance_tasks(state, king=1)
                    base_price = int(king["base_price"] * length / ((king["length"][0] + king["length"][1]) / 2))
                    state.setdefault("king_sale_prices", {}).setdefault(king_window.king_name, []).append(base_price)
                    price = base_price
                    price = king_sale_price(
                        price,
                        gold_magnet=self._accessory_effect(state, "黄金磁石", "king_price", 1.0),
                    )
                    results[king_window.king_name] = results.get(king_window.king_name, 0) + 1
                    attempt_results.append({"time": scheduled_at, "result": king_window.king_name, "quantity": 1, "xp": attempt_xp})
                    current_wait_seconds = fishing_wait_for_result(
                        king_window.king_name, induce=int(equip.get("induce", 0)), wait_rate=current_wait_rate
                    )
                    self._grant_fishing_fragments(state, "king")
                    state.setdefault("king_results", {})[king_window.king_name] = {"length": length, "price": price}
                    result_note += f"\n{FISH_KING_PROMPTS[king_window.king_name]['success'].format(length=length, price=price)}"
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
            name, quantity = self._original_drop(
                state, drop_boundaries, magic_bait=magic_bait_active
            )
            if universal_bait and name is None:
                name = random.choice(list(FISH))
                quantity = 1
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
                fish_xp = 0
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
        state["fish_bless"] = int(state.get("fish_bless", 0)) + fish_attempts
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
            result_text = f"你进行了{fish_attempts}次钓鱼,结果如下:\n"
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

    def _parse_sell_items(self, item_text: str, prices: dict[str, int]) -> list[tuple[str, int]] | None:
        aliases = {
            "木杆": "木竿",
            "铁杆": "铁竿",
            "金杆": "金竿",
            "钻石杆": "钻石竿",
            "下界合金杆": "下界合金竿",
        }
        names = sorted(set(prices) | set(POLES) | set(aliases), key=len, reverse=True)
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
        items = self._parse_sell_items(item_text.strip(), prices)
        if not items:
            await event.reply(text="出售格式错误，请使用：出售物品 数量 物品 数量")
            return
        items = [(name, 1 if name in POLES else number) for name, number in items]
        if any(name in POLES for name, _number in items) and int(state.get("pole_trade_count", 0)) >= 10:
            await event.reply(text="一天只能交易10把鱼竿,明天再来售卖吧")
            return
        for name, number in items:
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
                    gross += unit_price
                    sold_poles.append((name, pole, unit_price))
                else:
                    for pole in poles[:number]:
                        unit_price = self._pole_price(pole, discounts, name)
                        gross += unit_price
                        sold_poles.append((name, pole, unit_price))
            else:
                unit_price = self._shop_price(name, discounts)
                if name in FISH:
                    unit_price = fish_sale_price(
                        unit_price,
                        gold_magnet=self._accessory_effect(state, "黄金磁石", "fish_price", 1.0),
                    )
                elif name in FISH_KINGS:
                    captured_prices = state.get("king_sale_prices", {}).get(name, [])
                    if isinstance(captured_prices, list) and captured_prices:
                        captured_count = min(number, len(captured_prices))
                        gross += sum(
                            king_sale_price(
                                int(raw_price) * discounts.get(name, 100) // 100,
                                gold_magnet=self._accessory_effect(state, "黄金磁石", "king_price", 1.0),
                            )
                            for raw_price in captured_prices[:captured_count]
                        )
                        if captured_count < number:
                            gross += king_sale_price(
                                self._shop_price(name, discounts),
                                gold_magnet=self._accessory_effect(state, "黄金磁石", "king_price", 1.0),
                            ) * (number - captured_count)
                        continue
                    unit_price = king_sale_price(
                        unit_price,
                        gold_magnet=self._accessory_effect(state, "黄金磁石", "king_price", 1.0),
                    )
                elif getattr(self, "_types", {}).get(name) == "treasure":
                    unit_price = treasure_sale_price(
                        unit_price,
                        gold_magnet=self._accessory_effect(state, "黄金磁石", "treasure_price", 1.0),
                    )
                gross += unit_price * number
        quote = gross * 8 // 10
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
            if name in FISH_KINGS:
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
        price = int(gross * float(self.get_config("sell_tax", 0.8)))
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
            await event.reply(text="三叉戟只能通过达到35级后完成“鱼王全收集”常驻任务获得")
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
            return (
                f"宝藏品质+{effects['treasure_quality']}档，"
                f"宝藏售价×{effects['treasure_price']:.2f}，"
                f"普通鱼售价×{effects['fish_price']:.2f}，"
                f"鱼王售价×{effects['king_price']:.2f}"
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
            repair_fee = self._repair_fee(equip["name"], int(equip.get("durable", 0)))
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
        repair_fee = self._repair_fee(equip["name"], int(equip.get("durable", 0)))
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
            craft_fee = self._craft_fee(name, "equipment")
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
                craft_fee = self._craft_fee(name, "fragments")
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
        craft_fee = self._craft_fee(name, "poles", len(selected_groups))
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
