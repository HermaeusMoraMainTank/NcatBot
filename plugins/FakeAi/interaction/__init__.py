# FakeAi interaction — 状态机 / 分析员 / 触发（I1）

from .config import InteractionConfig, DEFAULT_CONFIG
from .state_machine import ChatState, ChatStateStore, InteractionState, state_store
from .analyst import AnalystDecision, decide as analyst_decide
from .context_view import format_recent_for_analyst, extract_plain_text

__all__ = [
    "InteractionConfig",
    "DEFAULT_CONFIG",
    "ChatState",
    "ChatStateStore",
    "InteractionState",
    "state_store",
    "AnalystDecision",
    "analyst_decide",
    "format_recent_for_analyst",
    "extract_plain_text",
]
