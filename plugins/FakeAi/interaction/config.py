"""交互层可调参数（由 settings.apply_from_plugin 从 config 覆盖）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class InteractionConfig:
    # 触发档位：1=只回 @/叫名字  2=再加关键词  3=再加随机概率  4=全部回
    trigger_tier: int = 3
    keywords: List[str] = field(default_factory=list)
    # 3 档的随机概率（百分比 0~100）：群越热闹越要低
    random_percent: float = 8.0
    # 响应时带多少条历史（各档统一）
    history_count: int = 20

    group_cd_sec: float = 10.0
    # 对话延续窗口：被叫到之后，这段时间内可以继续参与
    dialog_window_sec: float = 180.0
    # 一个对话窗口里最多连着回几轮，到顶就必须等人再叫
    dialog_max_rounds: int = 2
    # 被明确要求闭嘴后的静默时长（秒），期间只有 @ 能触发
    silence_sec: float = 600.0
    # 发言频率上限：10 分钟内最多几条、1 小时内最多几条
    reply_per_10min: int = 3
    reply_per_hour: int = 15

    aliases: List[str] = field(default_factory=lambda: ["蓝晴"])
    bot_qq: str = "1512130053"
    # True：每条入账都打 state=…；False：仅被叫时 INFO
    verbose_log: bool = False


DEFAULT_CONFIG = InteractionConfig()
