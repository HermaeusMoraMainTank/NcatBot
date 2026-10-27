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
    "persona": "人设",
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
    "command_blacklist": "全局屏蔽指令",
    "super_users": "超级用户",
    "frozen_users": "冻结用户",
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
    "proactive_probability": "冷场主动概率",
    "interaction": "互动策略",
    "cognition": "认知参数",
    "expression": "表情包策略",
    "sticker_library": "表情包图库",
    "activity": "表情活跃度",
    "sticker_auto_rate": "自动补表情概率",
    "tone_strength": "人味强度（off/light/normal/max）",
    "editor_pass": "草稿删减（实验）",
    # 互动策略子项
    "dialog_window_sec": "对话延续窗口（秒）",
    "dialog_max_rounds": "对话窗口最多回几轮",
    "silence_sec": "被要求闭嘴后静默（秒）",
    "reply_per_10min": "每 10 分钟最多发言",
    "reply_per_hour": "每小时最多发言",
    "group_cd_sec": "群冷却（秒）",
    "verbose_log": "详细日志",
    "trigger_tier": "触发档位",
    "keywords": "触发关键词",
    "random_percent": "随机触发概率（%）",
    "history_count": "携带历史条数",
    "memory_record_enabled": "启用记忆记录",
    "sleep_enabled": "启用睡眠整理",
    "auto_remember_on_phrase": "触发词自动记忆",
    # 表情包策略子项
    "max_per_message": "每轮最多表情",
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
    # GroupEconomy
    "checkin_min_reward": "签到最小奖励",
    "checkin_max_reward": "签到最大奖励",
    "gift_min_amount": "礼物最小数量",
    "gift_max_amount": "礼物最大数量",
    "gift_wallet_reserve": "礼物后钱包保留额",
    "gift_unlimited_users": "礼物不受限用户",
    "gift_favor_min": "礼物好感下限",
    "gift_favor_max": "礼物好感上限",
    "background_url": "背景图地址",
    "background_timeout": "背景图超时（秒）",
    "background_cache_hours": "背景图缓存（小时）",
    "ranking_limit": "排行榜人数上限",
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
    "checkin": "签到",
    "reward": "奖励",
    "wallet": "钱包",
    "reserve": "保留",
    "background": "背景图",
    "gift": "礼物",
    "amount": "数量",
    "unlimited": "不受限",
    "address": "地址",
    "rating": "评分",
    "score": "分数",
    "status": "状态",
    "level": "等级",
    "exp": "经验",
    "gold": "金币",
    "coin": "金币",
    "coins": "金币",
    "item": "物品",
    "shop": "商店",
    "trade": "交易",
    "market": "市场",
    "bank": "银行",
    "cost": "花费",
    "fee": "费用",
    "quality": "品质",
    "rarity": "稀有度",
    "refresh": "刷新",
    "sync": "同步",
    "channel": "频道",
    "webhook": "Webhook",
    "notify": "通知",
    "mention": "提及",
    "prefix": "前缀",
    "suffix": "后缀",
    "template": "模板",
    "message": "消息",
    "messages": "消息",
    "reply": "回复",
    "forward": "转发",
    "recall": "撤回",
    "mute": "禁言",
    "ban": "封禁",
    "kick": "踢出",
    "welcome": "欢迎",
    "leave": "离开",
    "join": "加入",
    "member": "成员",
    "members": "成员",
    "owner": "拥有者",
    "master": "主人",
    "admin": "管理",
    "operator": "操作者",
    "permission": "权限",
    "role": "角色",
    "roles": "角色",
    "whitelist": "白名单",
    "blacklist": "黑名单",
    "random": "随机",
    "weight": "权重",
    "ratio": "比例",
    "scale": "缩放",
    "width": "宽度",
    "height": "高度",
    "color": "颜色",
    "style": "样式",
    "theme": "主题",
    "icon": "图标",
    "image": "图片",
    "video": "视频",
    "audio": "音频",
    "file": "文件",
    "dir": "目录",
    "folder": "目录",
    "root": "根目录",
    "temp": "临时",
    "cache": "缓存",
    "expire": "过期",
    "expires": "过期",
    "duration": "时长",
    "period": "周期",
    "schedule": "计划",
    "cron": "定时",
    "task": "任务",
    "tasks": "任务",
    "queue": "队列",
    "worker": "工作线程",
    "retry": "重试",
    "times": "次数",
    "attempt": "尝试",
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
    "proactive_probability": "冷场专用：群安静 N 分钟后，每次检查主动起话题的概率",
    "sticker_auto_rate": "0~1；这轮模型没发图时，按这个概率自动补一张",
    "activity": "0 安静 / 1 克制 / 2 普通 / 3 活跃，直接影响模型配表情的频率",
    "trigger_tier": "1 只回@/叫名字；2 再加关键词；3 再加随机；4 全部回",
    "random_percent": "3 档时：这条消息有多大概率让蓝晴参与（0~100）",
    "history_count": "每次回复带多少条历史消息，越多越贵",
    "dialog_window_sec": "被叫到或接上话之后，这么久内继续参与对话，不会立刻转冷",
}

SECRET_HINTS = ("secret", "token", "password", "cookie", "api_key", "apikey", "private")


def _is_secret(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in SECRET_HINTS)


def _split_key(key: str) -> list[str]:
    """拆 snake_case 与 camelCase，例如 giftWalletReserve -> gift wallet reserve。"""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key))
    return [p for p in re.split(r"[_\-\s]+", spaced) if p]


def _is_ascii(ch: str) -> bool:
    return bool(ch) and ord(ch) < 128


def humanize(key: str) -> str:
    """把配置键变成可读中文；认不出的英文词之间留空格，避免糊成一团。"""
    if key in CONFIG_LABELS:
        return CONFIG_LABELS[key]
    parts = _split_key(key)
    if not parts:
        return str(key)
    words = [WORD_LABELS.get(p.lower(), p) for p in parts]
    out = ""
    for word in words:
        if out and _is_ascii(out[-1]) and _is_ascii(word[0]):
            out += " "
        out += word
    return out


def _widget_for(
    key: str,
    value: Any,
    choices: dict[str, Any] | None,
    multi: set[str] | None = None,
) -> str:
    if choices and key in choices:
        spec = choices[key]
        if isinstance(spec, dict) and spec.get("multi"):
            return "multiselect"
        if multi and key in multi:
            return "multiselect"
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


_MISSING = object()


def _pop_path(config: dict[str, Any], path: str) -> Any:
    """按 a.b.c 取出并移除值，用于把常用项从嵌套组里提到顶层。"""
    parts = path.split(".")
    node: Any = config
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    if not isinstance(node, dict):
        return _MISSING
    return node.pop(parts[-1], _MISSING)


def build_fields(
    config: dict[str, Any],
    labels: dict[str, str] | None = None,
    choices: dict[str, Any] | None = None,
    descriptions: dict[str, str] | None = None,
    prefix: str = "",
    pinned: list[str] | None = None,
    multi: set[str] | None = None,
) -> list[dict[str, Any]]:
    """把配置字典转成前端可渲染的字段树。

    ``pinned`` 里的 ``a.b.c`` 会被提到最前面，方便常用项一眼看到。
    """
    labels = labels or {}
    descriptions = descriptions or {}
    pinned_fields: list[dict[str, Any]] = []
    for path in pinned or []:
        value = _pop_path(config, path)
        if value is _MISSING:
            continue
        key = path.split(".")[-1]
        widget = _widget_for(key, value, choices, multi)
        field: dict[str, Any] = {
            "key": key,
            "path": path,
            "label": labels.get(path) or labels.get(key) or humanize(key),
            "type": _type_name(value),
            "widget": widget,
            "value": value,
            "secret": _is_secret(key),
            "pinned": True,
            "description": descriptions.get(path)
            or descriptions.get(key)
            or CONFIG_DESCRIPTIONS.get(path)
            or CONFIG_DESCRIPTIONS.get(key)
            or "",
        }
        if widget in ("select", "multiselect") and choices:
            spec = choices.get(key) or []
            if isinstance(spec, dict):
                field["choices"] = list(spec.get("options") or [])
            else:
                field["choices"] = list(spec)
        pinned_fields.append(field)

    fields: list[dict[str, Any]] = []
    for key, value in config.items():
        full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        label = labels.get(full_key) or labels.get(key) or humanize(key)
        widget = _widget_for(key, value, choices, multi)
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
        if widget in ("select", "multiselect") and choices:
            spec = choices.get(key) or []
            if isinstance(spec, dict):
                field["choices"] = list(spec.get("options") or [])
            else:
                field["choices"] = list(spec)
        if widget == "group" and isinstance(value, dict):
            field["children"] = build_fields(
                value,
                labels=labels,
                choices=choices,
                descriptions=descriptions,
                prefix=full_key,
                multi=multi,
            )
        fields.append(field)
    # 布尔值和短配置排前面，长文本/对象排后面
    order = {"switch": 0, "select": 0, "multiselect": 0, "number": 1, "tags": 2, "text": 3, "password": 4, "textarea": 5, "group": 6, "json": 7}
    fields.sort(key=lambda f: (order.get(f["widget"], 9), f["label"]))
    return pinned_fields + fields
