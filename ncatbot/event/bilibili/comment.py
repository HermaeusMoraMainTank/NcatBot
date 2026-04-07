"""评论事件实体"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..common.base import BaseEvent
from ..common.mixins import Replyable, HasSender, Deletable

if TYPE_CHECKING:
    from ncatbot.api.bilibili import IBiliAPIClient
    from ncatbot.types.bilibili.events import BiliCommentEventData

__all__ = [
    "BiliCommentEvent",
]


class BiliCommentEvent(BaseEvent, Replyable, HasSender, Deletable):
    """B 站评论事件"""

    _data: "BiliCommentEventData"
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
        return await self._api.reply_comment(
            resource_id=self._data.resource_id,
            resource_type=self._data.resource_type,
            root_id=int(self._data.root_id)
            if self._data.root_id != "0"
            else int(self._data.comment_id),
            parent_id=int(self._data.comment_id),
            text=text,
        )

    async def delete(self) -> Any:
        return await self._api.delete_comment(
            resource_id=self._data.resource_id,
            resource_type=self._data.resource_type,
            comment_id=int(self._data.comment_id),
        )
