"""同模型短 prompt 分析员。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

from common.utils.AiUtil import AiUtil

from .config import DEFAULT_CONFIG, InteractionConfig
from .context_view import format_recent_for_analyst
from .state_machine import InteractionState

_log = logging.getLogger(__name__)

ANALYST_SYSTEM = """你是群聊秘书，只判断蓝晴要不要开口。
只输出一个 JSON 对象，不要 markdown，不要解释。
字段：should_reply(bool), urgency(low|normal|high), reply_strategy(string), topic(string), silence_reason(string|null)。
召唤态(SUMMONED)：除非无意义（纯表情/只@无字），倾向 should_reply=true。
混脸熟(FAMILIAR)：除非明显好玩或能接梗，倾向 should_reply=false。
不要扮演蓝晴说话；reply_strategy 是给正文模型的策略，不是对用户的回复。
reply_strategy≤80字，topic≤40字。"""


@dataclass
class AnalystDecision:
    should_reply: bool
    urgency: str = "normal"
    reply_strategy: str = ""
    topic: str = ""
    silence_reason: Optional[str] = None
    source: str = "analyst"  # analyst | force | fallback | default

    def inject_block(self) -> str:
        if not self.should_reply:
            return ""
        strategy = self.reply_strategy or "正常友好地回应用户"
        topic = self.topic or "当前对话"
        return (
            f"\n\n【本轮秘书决策】\n话题：{topic}\n策略：{strategy}\n"
            "请按策略回复，不要复述本段。"
        )


def _extract_json_obj(text: str) -> Optional[dict]:
    if not text:
        return None
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            return None
    return None


def _from_obj(obj: dict, source: str = "analyst") -> AnalystDecision:
    should = bool(obj.get("should_reply"))
    urgency = str(obj.get("urgency") or "normal").lower()
    if urgency not in ("low", "normal", "high"):
        urgency = "normal"
    strategy = str(obj.get("reply_strategy") or "")[:80]
    topic = str(obj.get("topic") or "")[:40]
    silence = obj.get("silence_reason")
    silence_s = None if silence is None else str(silence)[:80]
    if should and not strategy:
        strategy = "正常友好地回应用户"
    return AnalystDecision(
        should_reply=should,
        urgency=urgency,
        reply_strategy=strategy,
        topic=topic,
        silence_reason=silence_s,
        source=source,
    )


def parse_error_decision() -> AnalystDecision:
    return AnalystDecision(
        should_reply=False,
        urgency="low",
        silence_reason="parse_error",
        source="analyst",
    )


async def decide(
    state: InteractionState,
    replies: list,
    *,
    config: InteractionConfig | None = None,
    is_empty_summon: bool = False,
) -> AnalystDecision:
    cfg = config or DEFAULT_CONFIG

    if is_empty_summon and state == InteractionState.SUMMONED:
        return AnalystDecision(
            should_reply=False,
            urgency="low",
            silence_reason="empty_summon",
            source="force",
        )

    if not cfg.analyst_enabled:
        if state == InteractionState.SUMMONED and cfg.force_reply_when_summoned:
            return AnalystDecision(
                should_reply=True,
                urgency="normal",
                reply_strategy="正常友好地回应用户",
                topic="被呼唤",
                source="default",
            )
        return AnalystDecision(
            should_reply=False,
            silence_reason="analyst_disabled",
            source="default",
        )

    recent = format_recent_for_analyst(replies, limit=cfg.analyst_context_limit)
    user_prompt = f"状态: {state.value}\n最近对话（新在下，已标发言人）:\n{recent}"

    try:
        raw = await AiUtil.search_deepseek(
            user_prompt,
            ANALYST_SYSTEM,
            max_tokens=cfg.analyst_max_tokens,
            temperature=cfg.analyst_temperature,
        )
    except Exception as e:
        _log.error("[FakeAi] analyst 调用异常: %s", e)
        raw = None

    if not raw:
        if state == InteractionState.SUMMONED and cfg.force_reply_when_summoned:
            return AnalystDecision(
                should_reply=True,
                urgency="normal",
                reply_strategy="正常友好地回应用户",
                topic="被呼唤",
                source="default",
            )
        return AnalystDecision(
            should_reply=False,
            silence_reason="analyst_error",
            source="analyst",
        )

    content = raw.get("content", "") if isinstance(raw, dict) else str(raw)
    obj = _extract_json_obj(content)
    if not obj or "should_reply" not in obj:
        _log.warning("[FakeAi] analyst parse_error: %s", content[:200])
        if state == InteractionState.SUMMONED and cfg.force_reply_when_summoned:
            return AnalystDecision(
                should_reply=True,
                urgency="normal",
                reply_strategy="正常友好地回应用户",
                topic="被呼唤",
                source="default",
            )
        return parse_error_decision()

    decision = _from_obj(obj)

    # 召唤态：被叫到尽量回应（空消息已在上方拦截）
    if (
        state == InteractionState.SUMMONED
        and cfg.force_reply_when_summoned
        and not decision.should_reply
        and not is_empty_summon
    ):
        decision.should_reply = True
        decision.reply_strategy = decision.reply_strategy or "正常友好地回应用户"
        decision.topic = decision.topic or "被呼唤"
        decision.silence_reason = None
        decision.source = "force"
        _log.info("[FakeAi] force_reply_when_summoned 覆盖否决")

    return decision
