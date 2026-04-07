"""私信事件实体"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..common.base import BaseEvent
from ..common.mixins import Replyable, HasSender

if TYPE_CHECKING:
    from ncatbot.api.bilibili import IBiliAPIClient
    from ncatbot.types.bilibili.events import (
        BiliPrivateMessageEventData,
        BiliPrivateMessageWithdrawEventData,
    )

__all__ = [
    "BiliPrivateMessageEvent",
    "BiliPrivateMessageWithdrawEvent",
]


class BiliPrivateMessageEvent(BaseEvent, Replyable, HasSender):
    """B 站私信消息事件"""

    _data: "BiliPrivateMessageEventData"
    _api: "IBiliAPIClient"

    @property
    def api(self) -> "IBiliAPIClient":
        return self._api

    @property
    def user_id(self) -> str:
        return self._data.user_id

    @property
    def sender(self) -> Any:
        return self._data.sender

    async def reply(self, text: str, **kwargs: Any) -> Any:
        return await self._api.send_private_msg(
            user_id=int(self._data.user_id), content=text
        )


class BiliPrivateMessageWithdrawEvent(BaseEvent):
    """B 站私信撤回事件"""

    _data: "BiliPrivateMessageWithdrawEventData"
    _api: "IBiliAPIClient"

    @property
    def api(self) -> "IBiliAPIClient":
        return self._api
