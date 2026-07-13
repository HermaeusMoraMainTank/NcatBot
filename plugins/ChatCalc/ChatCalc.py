import re

from ncatbot.core import registrar
from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.plugin import NcatBotPlugin
from ncatbot.types import At, MessageArray as MessageChain, PlainText, Reply
from ncatbot.utils import get_log

from .calc_engine import calc_failure_message, find_best_expression
from .triggers import has_calc_trigger

_log = get_log()

_TEXT_ONLY_SEGMENTS = (PlainText, Reply, At)


def _extract_pure_text(message: MessageChain) -> str | None:
    """仅当消息只含文本/引用/@ 段时返回纯文本，否则忽略。"""
    if not message:
        return None
    for seg in message:
        if not isinstance(seg, _TEXT_ONLY_SEGMENTS):
            return None
    text = message.text.strip()
    return text or None


def _get_reply_id(message: MessageChain, raw_message: str) -> str | None:
    replies = message.filter(Reply)
    if replies:
        return replies[0].id
    match = re.search(r"\[CQ:reply,id=(\d+)\]", raw_message)
    return match.group(1) if match else None


def _extract_plain_text_from_msg_data(msg_data) -> str | None:
    """从 get_msg 结果提取纯文本（同样忽略图片等非文本段）。"""
    segments = getattr(msg_data, "message", None) or []
    parts: list[str] = []
    for seg in segments:
        if isinstance(seg, dict):
            typ = seg.get("type")
            data = seg.get("data") or {}
            if typ == "text":
                parts.append(str(data.get("text", "")))
            elif typ in ("reply", "at"):
                continue
            else:
                return None
        elif isinstance(seg, PlainText):
            parts.append(seg.text)
        elif isinstance(seg, (Reply, At)):
            continue
        else:
            return None
    if parts:
        text = "".join(parts).strip()
        return text or None

    raw = getattr(msg_data, "raw_message", None)
    if not raw:
        return None
    if re.search(r"\[CQ:(?!reply|at)[^\]]+\]", raw):
        return None
    text = re.sub(r"\[CQ:(?:reply|at)[^\]]+\]", "", raw).strip()
    return text or None


class ChatCalc(NcatBotPlugin):
    name = "ChatCalc"
    version = "1.0.0"

    async def on_load(self):
        _log.info(f"开始加载 {self.name} 插件 v{self.version}")

    async def _find_from_replied_message(
        self, event: GroupMessage
    ) -> tuple[str, str] | str | None:
        reply_id = _get_reply_id(event.message, event.raw_message)
        if reply_id is None:
            return None
        try:
            reply_msg = await self.api.qq.query.get_msg(reply_id)
        except Exception as exc:
            _log.warning("ChatCalc 获取引用消息失败: %s", exc)
            return None
        reply_text = _extract_plain_text_from_msg_data(reply_msg)
        if not reply_text:
            return None
        found = find_best_expression(reply_text)
        if found:
            return found
        return calc_failure_message(reply_text)

    async def _resolve_calc(self, event: GroupMessage) -> tuple[str, str] | str | None:
        text = _extract_pure_text(event.message)
        if not text or not has_calc_trigger(text):
            return None

        found = find_best_expression(text)
        if found:
            return found

        replied = await self._find_from_replied_message(event)
        if replied:
            return replied

        return calc_failure_message(text)

    @registrar.qq.on_group_message()
    async def handle_group_message(self, event: GroupMessage):
        resolved = await self._resolve_calc(event)
        if not resolved:
            return

        if isinstance(resolved, str):
            await self.api.qq.post_group_msg(
                group_id=event.group_id,
                rtf=MessageChain([Reply(id=event.message_id), resolved]),
            )
            return

        expr, result = resolved
        reply_text = f"{expr}={result}"
        await self.api.qq.post_group_msg(
            group_id=event.group_id,
            rtf=MessageChain([Reply(id=event.message_id), reply_text]),
        )
