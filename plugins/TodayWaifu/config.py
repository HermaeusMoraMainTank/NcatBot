"""TodayWaifu 默认配置。"""

from __future__ import annotations

from typing import Any

RAPE_COMMAND_USER_ID = "794383252"
RAPE_COMMAND_TARGET_ID = "1211330825"

DEFAULT_CONFIG: dict[str, Any] = {
    "daily_limit": 1,
    "force_marry_cd": 3,
    "propose_cooldown_minutes": 60,
    "propose_timeout_seconds": 30,
    "propose_auto_accept_base": 0.20,
    "propose_auto_accept_per_favor": 0.006,
    "propose_auto_accept_max": 0.90,
    "max_records": 500,
    "active_user_days": 30,
    # False=各自独立记录（同一人可被多人抽中，关系图更密）；True=一夫一妻互斥
    "exclusive_allocation": False,
    "excluded_users": [],
    "force_marry_excluded_users": [],
    "whitelist_groups": [],
    "blacklist_groups": [],
    "iterations": 140,
    # 抽取权重：好感每 1 点只增加很小倍率，并叠加轻微随机扰动。
    "draw_favor_weight": 0.001,
    "draw_favor_randomness": 0.05,
    "draw_favor_gain": 1,
    "propose_favor_gain": 3,
    "force_marry_favor_gain": 1,
    "keyword_trigger_enabled": True,
    "keyword_trigger_mode": "exact",  # exact | starts_with | contains
    "auto_set_other_half": False,
    "allow_marry_bot": False,
    "at_waifu": False,
    "debug_enabled": False,
    "favor_min": 0,
    "favor_max": 100,
}

# 关键词 -> action
KEYWORD_ROUTES: dict[str, str] = {
    "今日老婆": "draw",
    "jrlp": "draw",
    "抽老婆": "draw",
    "我的老婆": "history",
    "wdlp": "history",
    "抽取历史": "history",
    "强娶": "force_marry",
    "qiangqu": "force_marry",
    "强奸": "rape_marry",
    "关系图": "graph",
    "羁绊图谱": "graph",
    "gxt": "graph",
    "rbq排行": "rbq",
    "rbqph": "rbq",
    "抽老婆帮助": "help",
    "老婆插件帮助": "help",
    "clpbz": "help",
    "求婚": "propose",
    "qh": "propose",
    "重置记录": "reset_records",
    "czjl": "reset_records",
    "重置强娶时间": "reset_force_cd",
    "czqqsj": "reset_force_cd",
    "重置求婚时间": "reset_propose_cd",
    "czqhsj": "reset_propose_cd",
}
