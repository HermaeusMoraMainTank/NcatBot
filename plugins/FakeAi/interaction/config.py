"""交互层可调参数（由 settings.apply_from_plugin 从 config 覆盖）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class InteractionConfig:
    observation_sec: float = 45.0
    # 被叫到/接上话之后，把这轮对话延续这么久：窗口内的消息按「熟络」处理，
    # 不再滑回 NOT_PRESENT 静默
    dialog_window_sec: float = 180.0
    group_cd_sec: float = 10.0
    familiar_cooldown_sec: float = 120.0
    echo_window_sec: float = 30.0
    echo_threshold: int = 3
    dense_window_sec: float = 20.0
    dense_threshold: int = 8
    min_participants: int = 3
    aliases: List[str] = field(default_factory=lambda: ["蓝晴"])
    bot_qq: str = "3555202423"

    analyst_enabled: bool = True
    force_reply_when_summoned: bool = True
    # 普通闲聊（NOT_PRESENT）时插嘴的概率；0=只在被叫/熟络时回，1=每句都进决策
    passive_reply_rate: float = 0.0
    # 过渡期：仅 FAMILIAR + 分析员失败时抽卡；终态改 0
    fallback_random_prob: float = 0.03
    analyst_context_limit: int = 10
    analyst_max_tokens: int = 200
    analyst_temperature: float = 0.3
    # True：每条入账都打 state=…；False：仅 SUMMONED/FAMILIAR/空召唤 INFO
    verbose_log: bool = False


DEFAULT_CONFIG = InteractionConfig()
