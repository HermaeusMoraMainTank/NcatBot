"""每群交互状态机。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List

from .config import DEFAULT_CONFIG, InteractionConfig
from .context_view import (
    extract_plain_text,
    iter_user_messages_since_last_bot,
    parse_reply,
)

_log = logging.getLogger(__name__)


class InteractionState(str, Enum):
    NOT_PRESENT = "NOT_PRESENT"
    SUMMONED = "SUMMONED"
    FAMILIAR = "FAMILIAR"
    OBSERVATION = "OBSERVATION"


@dataclass
class ChatState:
    state: InteractionState = InteractionState.NOT_PRESENT
    observation_until: float = 0.0
    familiar_cooldown_until: float = 0.0


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

    def expire_observation_if_needed(self, group_id: int) -> ChatState:
        st = self.get(group_id)
        now = time.time()
        if (
            st.state == InteractionState.OBSERVATION
            and st.observation_until > 0
            and now >= st.observation_until
        ):
            st.state = InteractionState.NOT_PRESENT
            st.observation_until = 0.0
            if self.config.verbose_log:
                _log.info("[FakeAi] group=%s OBSERVATION 超时 → NOT_PRESENT", group_id)
            else:
                _log.debug("[FakeAi] group=%s OBSERVATION 超时 → NOT_PRESENT", group_id)
        return st

    def enter_observation(self, group_id: int, from_familiar: bool = False) -> None:
        st = self.get(group_id)
        now = time.time()
        st.state = InteractionState.OBSERVATION
        st.observation_until = now + self.config.observation_sec
        if from_familiar:
            st.familiar_cooldown_until = now + self.config.familiar_cooldown_sec
        _log.info(
            "[FakeAi] group=%s → OBSERVATION until=%.0f familiar_cd=%s",
            group_id,
            st.observation_until,
            from_familiar,
        )

    def enter_not_present(self, group_id: int) -> None:
        st = self.get(group_id)
        st.state = InteractionState.NOT_PRESENT
        st.observation_until = 0.0

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
        # @：上次 bot 回复之后任意 user 带 at 标记较难从前 JSON 还原，
        # 故以「本轮 is_at_bot」+ 近期别名辅助。
        if is_at_bot:
            return True
        if self._alias_hit(latest_clean_text):
            return True
        for _data, text in iter_user_messages_since_last_bot(replies):
            if self._alias_hit(text):
                return True
        return False

    def _detect_echo(self, replies: List[str]) -> bool:
        cfg = self.config
        cutoff = time.time() - cfg.echo_window_sec
        counts: Dict[str, int] = {}
        for reply_json in replies:
            data = parse_reply(reply_json)
            if not data or str(data.get("id", "")) == "0":
                continue
            ts = data.get("ts") or 0
            if isinstance(ts, (int, float)) and ts < cutoff:
                continue
            text = extract_plain_text(data.get("content", ""))
            if not text or "[图片" in text:
                continue
            counts[text] = counts.get(text, 0) + 1
            if counts[text] >= cfg.echo_threshold:
                return True
        return False

    def _detect_dense(self, replies: List[str]) -> bool:
        cfg = self.config
        cutoff = time.time() - cfg.dense_window_sec
        n = 0
        participants = set()
        for reply_json in replies:
            data = parse_reply(reply_json)
            if not data:
                continue
            ts = data.get("ts") or 0
            if isinstance(ts, (int, float)) and ts < cutoff:
                continue
            n += 1
            uid = str(data.get("id", ""))
            if uid and uid != "0":
                participants.add(uid)
        return n >= cfg.dense_threshold and len(participants) >= cfg.min_participants

    def determine(
        self,
        group_id: int,
        replies: List[str],
        latest_clean_text: str,
        is_at_bot: bool,
    ) -> DetermineResult:
        st = self.expire_observation_if_needed(group_id)
        now = time.time()

        summoned = self._is_summoned(replies, latest_clean_text, is_at_bot)

        if summoned:
            # 去掉别名后是否还有实质文字（汉字/字母数字）
            stripped = latest_clean_text or ""
            for a in self.config.aliases:
                if a:
                    stripped = stripped.replace(a, "")
            stripped = stripped.strip()
            has_substance = any(
                ch.isalnum() or ("\u4e00" <= ch <= "\u9fff") for ch in stripped
            )
            is_empty_summon = not has_substance
            return DetermineResult(
                state=InteractionState.SUMMONED,
                is_summoned=True,
                is_empty_summon=is_empty_summon,
                reason="summon",
            )

        if st.state == InteractionState.OBSERVATION and now < st.observation_until:
            return DetermineResult(
                state=InteractionState.OBSERVATION,
                reason="in_observation",
            )

        # 从滞留 FAMILIAR 重置
        if st.state == InteractionState.FAMILIAR:
            st.state = InteractionState.NOT_PRESENT

        if now < st.familiar_cooldown_until:
            return DetermineResult(
                state=InteractionState.NOT_PRESENT,
                reason="familiar_cooldown",
            )
        if self._detect_echo(replies):
            return DetermineResult(
                state=InteractionState.FAMILIAR,
                reason="echo",
            )
        if self._detect_dense(replies):
            return DetermineResult(
                state=InteractionState.FAMILIAR,
                reason="dense",
            )

        return DetermineResult(
            state=InteractionState.NOT_PRESENT,
            reason="idle",
        )


state_store = ChatStateStore()
