"""用户 273421673 回复「撤回」时，撤回被引用的消息。"""

from __future__ import annotations

import re

from ncatbot.core import registrar
from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.plugin import NcatBotPlugin
from ncatbot.types import At, MessageArray as MessageChain, PlainText, Reply
from ncatbot.utils import get_log

_log = get_log()

_ALLOWED_USER_ID = "273421673"
_RECALL_TEXT = "撤回"
_AT_NAME_PATTERN = re.compile(r"^@?蓝晴\s*撤回$")

_TEXT_ONLY_SEGMENTS = (PlainText, Reply, At)


def _get_reply_id(message: MessageChain, raw_message: str) -> str | None:
    replies = message.filter(Reply)
    if replies:
        return replies[0].id
    match = re.search(r"\[CQ:reply,id=(\d+)\]", raw_message)
    return match.group(1) if match else None


def _parse_recall_target(
    message: MessageChain, raw_message: str, bot_id: str
) -> str | None:
    """仅当消息为「撤回」或「@蓝晴 撤回」时返回被引用消息 ID。"""
    reply_id = _get_reply_id(message, raw_message)
    if reply_id is None:
        return None

    if not message:
        return None

    for seg in message:
        if not isinstance(seg, _TEXT_ONLY_SEGMENTS):
            return None

    text_parts: list[str] = []
    at_bot = False
    for seg in message:
        if isinstance(seg, PlainText):
            text_parts.append(seg.text)
        elif isinstance(seg, At):
            if str(seg.user_id) == str(bot_id):
                at_bot = True
            else:
                return None

    text = "".join(text_parts).strip()

    if not at_bot and text == _RECALL_TEXT:
        return reply_id
    if at_bot and text == _RECALL_TEXT:
        return reply_id
    if _AT_NAME_PATTERN.fullmatch(text):
        return reply_id

    return None


class WithdrawReply(NcatBotPlugin):
    name = "WithdrawReply"
    version = "1.0.0"

    async def on_load(self):
        _log.info("开始加载 %s 插件 v%s", self.name, self.version)

    async def _delete_msg(self, message_id: str) -> None:
        if hasattr(self.api.qq, "messaging") and hasattr(
            self.api.qq.messaging, "delete_msg"
        ):
            await self.api.qq.messaging.delete_msg(message_id)
        else:
            await self.api.qq.delete_msg(message_id)

    @registrar.qq.on_group_message()
    async def handle_group_message(self, event: GroupMessage) -> None:
        if str(event.sender.user_id) != _ALLOWED_USER_ID:
            return

        target_id = _parse_recall_target(
            event.message, event.raw_message, str(event.self_id)
        )
        if target_id is None:
            return

        try:
            await self._delete_msg(target_id)
            _log.info(
                "[WithdrawReply] 用户 %s 撤回消息 %s（群 %s）",
                _ALLOWED_USER_ID,
                target_id,
                event.group_id,
            )
        except Exception as exc:
            _log.warning("[WithdrawReply] 撤回失败 message_id=%s: %s", target_id, exc)
