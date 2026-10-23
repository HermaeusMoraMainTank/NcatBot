"""插件配置的展示元数据：中文标签、控件类型、分组。

后台不想让用户直接面对英文 key 和 JSON 文本框，这里把扁平的配置字典
翻译成「标签 + 控件 + 值」的描述，前端照着渲染即可。

优先级：
1. 插件自己声明的 ``config_labels`` / ``config_choices``
2. 本模块的词典
3. 按英文单词自动拼一个中文名（兜底，不认识的词保留原文）
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# 词典
# ---------------------------------------------------------------------------

CONFIG_LABELS: dict[str, str] = {
    # 后台 / 通用
    "admin_enabled": "启用管理面板",
    "admin_host": "管理页监听地址",
    "admin_port": "管理页端口",
    "admin_token": "独立面板令牌",
    "standalone_admin_enabled": "启用独立端口面板",
    "admin_help_triggers": "管理帮助触发词",
    "admin_prefix": "管理命令前缀",
    "delete_prefix": "删除命令前缀",
    "upload_prefix": "上传命令前缀",
    "admin_ids": "管理员 QQ",
    "admin_users": "管理员账号",
    "enabled": "启用",
    "name": "名称",
    "aliases": "触发别名",
    "debug_mode": "调试模式",
    "debug_config": "调试配置",
    "timezone": "时区",
    "font_path": "字体路径",
    # 群组范围
    "group_mode": "群组模式",
    "allowed_groups": "启用的群",
    "disabled_groups": "禁用的群",
    "target_groups": "目标群",
    "group_list": "群列表",
    "group_whitelist": "群白名单",
    "whitelist_reject_message": "白名单拒绝提示",
    "blacklist": "黑名单",
    "super_users": "超级用户",
    "frozen_users": "冻结用户",
    "favor_skip_ids": "跳过好感检查的用户",
    # FakeAi
    "bot_qq": "机器人 QQ",
    "agent_enabled": "启用 Agent 模式",
    "agent_max_rounds": "单轮最多工具轮数",
    "agent_max_tokens": "单轮 Token 上限",
    "skills_enabled": "允许调用插件技能",
    "enable_group_cd": "启用群冷却",
    "enable_user_cd": "启用用户冷却",
    "enable_callback": "启用回调",
    "callback_timeout": "回调超时（秒）",
    "enable_typing_delay": "启用打字延迟",
    "typing_delay_per_char": "每字延迟（秒）",
    "proactive_enabled": "启用主动发言",
    "proactive_quiet_minutes": "静默多久后主动（分钟）",
    "proactive_interval_minutes": "主动发言间隔（分钟）",
    "proactive_probability": "主动发言概率",
    "vision_cache_enabled": "启用识图缓存",
    "vision_cache_days": "识图缓存天数",
    "interaction": "互动策略",
    "cognition": "认知参数",
    "expression": "表情包策略",
    # 互动策略子项
    "observation_sec": "观察时长（秒）",
    "group_cd_sec": "群冷却（秒）",
    "familiar_cooldown_sec": "熟络冷却（秒）",
    "echo_window_sec": "复读判定窗口（秒）",
    "echo_threshold": "复读判定次数",
    "dense_window_sec": "密集聊天窗口（秒）",
    "dense_threshold": "密集聊天消息数",
    "min_participants": "最少参与人数",
    "verbose_log": "详细日志",
    "fallback_random_prob": "兜底随机触发概率",
    "analyst_context_limit": "分析员上下文条数",
    "analyst_temperature": "分析员温度",
    "analyst_max_tokens": "分析员最大 Token",
    "memory_record_enabled": "启用记忆记录",
    "sleep_enabled": "启用睡眠状态",
    "auto_recall_enabled": "自动回忆",
    "auto_remember_on_phrase": "触发词自动记忆",
    "knowledge_inject_limit": "知识注入条数",
    "knowledge_inject_max_chars": "知识注入最大字数",
    "knowledge_item_max_chars": "知识条目最大字数",
    "auto_recall_limit": "自动回忆条数上限",
    "analyst_enabled": "启用分析员",
    "force_reply_when_summoned": "被点名必须回",
    # 表情包策略子项
    "max_per_message": "单条最多表情",
    "catalog_path": "表情目录",
    # 玩法
    "daily_limit": "每日上限",
    "daily_play_limit": "每日游玩上限",
    "game_cooldown_seconds": "游戏冷却（秒）",
    "max_guess_attempts": "最多猜测次数",
    "default_pool": "默认卡池",
    "enabled_pools": "启用的卡池",
    "pool_effects": "卡池效果",
    "effects": "效果配置",
    "reward_valid_time": "奖励有效期",
    "ranking_display_count": "排行榜显示人数",
    "answer_timeout": "作答超时（秒）",
    "success_rate": "成功率",
    "victim_min_balance": "被抢者最低余额",
    "guaranteed_failure_users": "必定失败的用户",
    "fish_limit": "钓鱼次数上限",
    "starting_pole_price": "初始鱼竿价格",
    "sell_tax": "出售税",
    # 查询类
    "default_country": "默认国家",
    "default_history_country": "默认历史价格国家",
    "default_language": "默认语言",
    "history_days": "历史天数",
    "history_event_limit": "历史条目数",
    "global_price_limit": "全局价格上限",
    "show_api_links": "显示 API 链接",
    "llm_name_retry_count": "名称重试次数",
    "timeout_seconds": "超时（秒）",
    "music_cooldown": "点歌冷却（秒）",
    "exposed_commands": "对外暴露的命令",
    # FF14 / 代理
    "fflogs_client_id": "FFLogs 客户端 ID",
    "fflogs_client_secret": "FFLogs 客户端密钥",
    "ffxiv_icon_font_path": "FF14 图标字体路径",
    "use_global_calendar": "使用全局日历",
    "use_global_fflogs": "使用全局 FFLogs",
    "proxy_enabled": "启用代理",
    "proxy_host": "代理地址",
    "proxy_port": "代理端口",
    "proxy_username": "代理用户名",
    "proxy_password": "代理密码",
    "weibo_cookie": "微博 Cookie",
    # 健康
    "deficit_kcal": "热量缺口（千卡）",
}

# 单词级兜底翻译
WORD_LABELS: dict[str, str] = {
    "enable": "启用",
    "enabled": "启用",
    "disable": "禁用",
    "disabled": "禁用",
    "max": "最大",
    "min": "最小",
    "count": "数量",
    "limit": "上限",
    "attempts": "次数",
    "day": "天",
    "days": "天数",
    "hour": "小时",
    "hours": "小时",
    "minute": "分钟",
    "minutes": "分钟",
    "second": "秒",
    "seconds": "秒",
    "sec": "秒",
    "group": "群",
    "groups": "群",
    "user": "用户",
    "users": "用户",
    "token": "Token",
    "key": "密钥",
    "secret": "密钥",
    "password": "密码",
    "port": "端口",
    "host": "地址",
    "path": "路径",
    "url": "地址",
    "name": "名称",
    "timeout": "超时",
    "probability": "概率",
    "rate": "比率",
    "interval": "间隔",
    "threshold": "阈值",
    "window": "窗口",
    "size": "大小",
    "mode": "模式",
    "level": "等级",
    "type": "类型",
    "list": "列表",
    "ids": "ID",
    "id": "ID",
    "qq": "QQ",
    "api": "API",
    "time": "时间",
    "date": "日期",
    "price": "价格",
    "tax": "税",
    "stock": "库存",
    "reward": "奖励",
    "pool": "卡池",
    "card": "卡片",
    "game": "游戏",
    "daily": "每日",
    "global": "全局",
    "local": "本地",
    "default": "默认",
    "custom": "自定义",
    "auto": "自动",
    "allow": "允许",
    "allowed": "允许",
    "deny": "拒绝",
    "black": "黑",
    "white": "白",
    "catalog": "目录",
    "cache": "缓存",
    "vision": "识图",
    "proactive": "主动",
    "callback": "回调",
    "typing": "打字",
    "delay": "延迟",
    "per": "每",
    "message": "消息",
    "messages": "消息",
    "format": "格式",
    "font": "字体",
    "cognition": "认知",
    "interaction": "互动",
    "expression": "表情",
    "analyst": "分析员",
    "observation": "观察",
    "echo": "复读",
    "dense": "密集",
    "participants": "参与人数",
    "force": "强制",
    "reply": "回复",
    "summoned": "被点名",
    "favor": "好感",
    "skip": "跳过",
    "frozen": "冻结",
    "agent": "Agent",
    "skills": "技能",
    "rounds": "轮数",
    "tokens": "Token",
    "fish": "钓鱼",
    "pole": "鱼竿",
    "sell": "出售",
    "starting": "初始",
    "victim": "被抢者",
    "balance": "余额",
    "guaranteed": "必定",
    "failure": "失败",
    "ranking": "排行榜",
    "display": "显示",
    "answer": "作答",
    "success": "成功",
    "history": "历史",
    "country": "国家",
    "language": "语言",
    "music": "点歌",
    "cooldown": "冷却",
    "exposed": "对外暴露",
    "commands": "命令",
    "proxy": "代理",
    "calendar": "日历",
    "weibo": "微博",
    "cookie": "Cookie",
    "deficit": "缺口",
    "kcal": "千卡",
    "admin": "管理",
    "help": "帮助",
    "trigger": "触发",
    "triggers": "触发词",
    "prefix": "前缀",
    "delete": "删除",
    "upload": "上传",
    "standalone": "独立",
    "panel": "面板",
    "send": "发送",
    "image": "图片",
    "images": "图片",
    "text": "文本",
    "file": "文件",
    "files": "文件",
    "voice": "语音",
    "player": "玩家",
    "players": "玩家",
    "item": "物品",
    "items": "物品",
    "shop": "商店",
    "balance": "余额",
    "grant": "发放",
    "adjust": "调整",
    "note": "备注",
    "reason": "原因",
    "operator": "操作者",
    "audit": "审计",
    "log": "日志",
    "logs": "日志",
    "target": "目标",
    "source": "来源",
    "cache": "缓存",
    "retry": "重试",
    "ttl": "有效期",
    "verbose": "详细",
    "fallback": "兜底",
    "random": "随机",
    "prob": "概率",
    "context": "上下文",
    "temperature": "温度",
    "memory": "记忆",
    "record": "记录",
    "sleep": "睡眠",
    "recall": "回忆",
    "remember": "记忆",
    "phrase": "短语",
    "knowledge": "知识",
    "inject": "注入",
    "chars": "字数",
    "whitelist": "白名单",
    "blacklist": "黑名单",
}

CONFIG_DESCRIPTIONS: dict[str, str] = {
    "admin_enabled": "是否在统一后台显示这个面板",
    "standalone_admin_enabled": "开启后仍会单独监听 admin_host:admin_port，一般不需要",
    "admin_token": "独立端口面板的令牌；并入统一后台后一般不用",
    "admin_host": "仅独立端口模式使用",
    "admin_port": "仅独立端口模式使用",
    "agent_max_rounds": "一次对话最多调用多少次工具，越大越费钱",
    "agent_max_tokens": "一次对话的 Token 预算上限",
    "skills_enabled": "关掉后 AI 只能聊天，不能调用插件能力",
    "success_rate": "每次钓鱼的成功概率，0~1",
    "fish_limit": "每人每天最多钓几次",
    "allowed_groups": "留空表示不限制；填了就只有这些群生效",
    "disabled_groups": "这些群里不启用",
    "proactive_probability": "0~1，越大越爱主动说话",
}

SECRET_HINTS = ("secret", "token", "password", "cookie", "api_key", "apikey", "private")


def _is_secret(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in SECRET_HINTS)


def humanize(key: str) -> str:
    """把 snake_case 配置键变成可读中文，尽量短。"""
    if key in CONFIG_LABELS:
        return CONFIG_LABELS[key]
    parts = [p for p in re.split(r"[_\-\s]+", str(key)) if p]
    if not parts:
        return str(key)
    words = [WORD_LABELS.get(p.lower(), p) for p in parts]
    return "".join(words)


def _widget_for(key: str, value: Any, choices: dict[str, Any] | None) -> str:
    if choices and key in choices:
        return "select"
    if isinstance(value, bool):
        return "switch"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, dict):
        return "group"
    if isinstance(value, (list, tuple)):
        if all(isinstance(item, (str, int, float)) for item in value):
            return "tags"
        return "json"
    if isinstance(value, str):
        if _is_secret(key):
            return "password"
        if "\n" in value or len(value) > 120:
            return "textarea"
        return "text"
    return "json"


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (list, tuple)):
        return "list"
    if value is None:
        return "null"
    return "string"


def build_fields(
    config: dict[str, Any],
    labels: dict[str, str] | None = None,
    choices: dict[str, Any] | None = None,
    descriptions: dict[str, str] | None = None,
    prefix: str = "",
) -> list[dict[str, Any]]:
    """把配置字典转成前端可渲染的字段树。"""
    labels = labels or {}
    descriptions = descriptions or {}
    fields: list[dict[str, Any]] = []
    for key, value in config.items():
        full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        label = labels.get(full_key) or labels.get(key) or humanize(key)
        widget = _widget_for(key, value, choices)
        field: dict[str, Any] = {
            "key": key,
            "path": full_key,
            "label": label,
            "type": _type_name(value),
            "widget": widget,
            "value": value,
            "secret": _is_secret(key),
            "description": descriptions.get(full_key)
            or descriptions.get(key)
            or CONFIG_DESCRIPTIONS.get(full_key)
            or CONFIG_DESCRIPTIONS.get(key)
            or "",
        }
        if widget == "select" and choices:
            field["choices"] = list(choices.get(key) or [])
        if widget == "group" and isinstance(value, dict):
            field["children"] = build_fields(
                value,
                labels=labels,
                choices=choices,
                descriptions=descriptions,
                prefix=full_key,
            )
        fields.append(field)
    # 布尔值和短配置排前面，长文本/对象排后面
    order = {"switch": 0, "select": 0, "number": 1, "tags": 2, "text": 3, "password": 4, "textarea": 5, "group": 6, "json": 7}
    fields.sort(key=lambda f: (order.get(f["widget"], 9), f["label"]))
    return fields
