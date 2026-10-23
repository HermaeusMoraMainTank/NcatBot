# FakeAi interaction — 状态机 / 触发（I1）

from .config import InteractionConfig, DEFAULT_CONFIG
from .state_machine import ChatState, ChatStateStore, InteractionState, state_store
from .context_view import format_recent_for_analyst, extract_plain_text

__all__ = [
    "InteractionConfig",
    "DEFAULT_CONFIG",
    "ChatState",
    "ChatStateStore",
    "InteractionState",
    "state_store",
    "format_recent_for_analyst",
    "extract_plain_text",
]
