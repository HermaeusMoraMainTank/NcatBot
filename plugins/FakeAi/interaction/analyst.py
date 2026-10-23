"""决策结果结构。

分析员（用 LLM 再判一次该不该回）已下线：状态机 + 一个概率已经足够，
多一层判断只会和状态机打架。这里保留数据结构，方便上层继续传 topic/策略。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .config import DEFAULT_CONFIG, InteractionConfig
from .state_machine import InteractionState


@dataclass
class AnalystDecision:
    should_reply: bool
    urgency: str = "normal"
    reply_strategy: str = ""
    topic: str = ""
    silence_reason: Optional[str] = None
    source: str = "rule"

    def inject_block(self) -> str:
        if not self.should_reply:
            return ""
        strategy = self.reply_strategy or "正常友好地回应用户"
        topic = self.topic or "当前对话"
        return (
            f"\n\n【本轮决策】\n话题：{topic}\n策略：{strategy}\n"
            "如果本轮是具体插件功能请求，先 list_skills 找插件，再 describe_skills/invoke_skill；"
            "策略只决定语气，不阻止工具调用。不要复述本段。"
        )


async def decide(
    state: InteractionState,
    replies: list,
    *,
    config: InteractionConfig | None = None,
    is_empty_summon: bool = False,
    reason: str = "",
) -> AnalystDecision:
    """纯规则决策：空召唤不回，其余交给上层。"""
    if is_empty_summon and state == InteractionState.SUMMONED:
        return AnalystDecision(
            should_reply=False,
            urgency="low",
            silence_reason="empty_summon",
            source="rule",
        )
    return AnalystDecision(
        should_reply=True,
        urgency="normal",
        reply_strategy="正常接一句，别自证、别客服腔",
        source="rule",
    )
