"""交互层可调参数（由 settings.apply_from_plugin 从 config 覆盖）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class InteractionConfig:
    observation_sec: float = 45.0
    group_cd_sec: float = 10.0
    # 对话延续窗口：被叫到/接上话之后，这段时间内继续参与对话
    dialog_window_sec: float = 180.0
    # 普通闲聊（NOT_PRESENT）时插嘴的概率；0=只在被叫/熟络时回，1=每句都进决策
    passive_reply_rate: float = 0.0
    aliases: List[str] = field(default_factory=lambda: ["蓝晴"])
    bot_qq: str = "1512130053"
    # True：每条入账都打 state=…；False：仅被叫时 INFO
    verbose_log: bool = False


DEFAULT_CONFIG = InteractionConfig()
