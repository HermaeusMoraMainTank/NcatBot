"""每群交互状态：被叫到 / 对话延续 / 否则沉默。

只保留三态，判定顺序固定，不再有观察期、熟络冷却、复读/密集检测。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List

from .config import DEFAULT_CONFIG, InteractionConfig
from .context_view import iter_user_messages_since_last_bot

_log = logging.getLogger(__name__)


class InteractionState(str, Enum):
    NOT_PRESENT = "NOT_PRESENT"
    SUMMONED = "SUMMONED"
    FAMILIAR = "FAMILIAR"


@dataclass
class ChatState:
    state: InteractionState = InteractionState.NOT_PRESENT
    # 对话延续窗口：被叫到或接上话之后，这段时间内继续参与
    dialog_until: float = 0.0
    # 本次窗口里已经连着回了几轮（防止自问自答式无限续期）
    dialog_rounds: int = 0
    # 被要求闭嘴后的静默截止时间
    silence_until: float = 0.0


@dataclass
class DetermineResult:
    state: InteractionState
    is_summoned: bool = False
    is_empty_summon: bool = False
    reason: str = ""


class ChatStateStore:
    def __init__(self, config: InteractionConfig | None = None):
        self.config = config or DEFAULT_CONFIG
        self._by_group: Dict[int, ChatState] = {}

    def get(self, group_id: int) -> ChatState:
        if group_id not in self._by_group:
            self._by_group[group_id] = ChatState()
        return self._by_group[group_id]

    def enter_dialog(self, group_id: int, seconds: float = 0.0) -> None:
        st = self.get(group_id)
        st.state = InteractionState.FAMILIAR
        st.dialog_rounds = 1
        if seconds > 0:
            st.dialog_until = time.time() + seconds

    def note_dialog_reply(self, group_id: int) -> None:
        """对话窗口内又回了一轮：不续期，到上限就关窗。

        以前每次发言都把 dialog_until 往后推，导致「回一句 → 窗口续 3 分钟 →
        再回一句」自锁，群里一有动静就永远在说话。
        """
        st = self.get(group_id)
        st.dialog_rounds += 1
        limit = max(1, int(self.config.dialog_max_rounds))
        if st.dialog_rounds >= limit:
            self.enter_not_present(group_id)

    def enter_not_present(self, group_id: int) -> None:
        st = self.get(group_id)
        st.state = InteractionState.NOT_PRESENT
        st.dialog_until = 0.0
        st.dialog_rounds = 0

    def enter_silence(self, group_id: int, seconds: float = 0.0) -> None:
        st = self.get(group_id)
        st.silence_until = time.time() + max(1.0, float(seconds))
        self.enter_not_present(group_id)

    def silence_left(self, group_id: int) -> float:
        st = self.get(group_id)
        return max(0.0, st.silence_until - time.time())

    def _alias_hit(self, text: str) -> bool:
        if not text:
            return False
        return any(a and a in text for a in self.config.aliases)

    def _is_summoned(
        self,
        replies: List[str],
        latest_clean_text: str,
        is_at_bot: bool,
    ) -> bool:
        if is_at_bot:
            return True
        if self._alias_hit(latest_clean_text):
            return True
        # 上一次机器人发言之后有人叫过名字（同一轮对话里没被回应的情况）
        for _data, text in iter_user_messages_since_last_bot(replies):
            if self._alias_hit(text):
                return True
        return False

    def determine(
        self,
        group_id: int,
        replies: List[str],
        latest_clean_text: str,
        is_at_bot: bool,
    ) -> DetermineResult:
        st = self.get(group_id)
        now = time.time()

        if self._is_summoned(replies, latest_clean_text, is_at_bot):
            stripped = latest_clean_text or ""
            for a in self.config.aliases:
                if a:
                    stripped = stripped.replace(a, "")
            stripped = stripped.strip()
            has_substance = any(
                ch.isalnum() or ("\u4e00" <= ch <= "\u9fff") for ch in stripped
            )
            return DetermineResult(
                state=InteractionState.SUMMONED,
                is_summoned=True,
                is_empty_summon=not has_substance,
                reason="summon",
            )

        if now < st.silence_until:
            return DetermineResult(
                state=InteractionState.NOT_PRESENT,
                reason="silence",
            )

        if now < st.dialog_until and st.dialog_rounds < max(
            1, int(self.config.dialog_max_rounds)
        ):
            return DetermineResult(
                state=InteractionState.FAMILIAR,
                reason="dialog",
            )

        return DetermineResult(
            state=InteractionState.NOT_PRESENT,
            reason="idle",
        )


state_store = ChatStateStore()
