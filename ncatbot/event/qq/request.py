"""QQ 请求事件实体"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from ncatbot.types.qq import (
    FriendRequestEventData,
    GroupRequestEventData,
    RequestEventData,
    RequestType,
)

from ..common.base import BaseEvent
from ..common.mixins import Approvable, GroupScoped, HasSender

if TYPE_CHECKING:
    from ncatbot.api.qq import QQAPIClient

__all__ = [
    "RequestEvent",
    "FriendRequestEvent",
    "GroupRequestEvent",
]


class RequestEvent(BaseEvent, HasSender, Approvable):
    """QQ 请求事件实体"""

    _data: RequestEventData
    _api: QQAPIClient

    @property
    def api(self) -> QQAPIClient:
        return self._api

    @property
    def request_type(self) -> RequestType:
        return self._data.request_type

    @property
    def user_id(self) -> str:
        return self._data.user_id

    @property
    def comment(self) -> Optional[str]:
        return self._data.comment

    @property
    def flag(self) -> str:
        return self._data.flag

    async def approve(self, remark: str = "", reason: str = "") -> Any:
        if self._data.request_type is RequestType.FRIEND:
            return await self._api.set_friend_add_request(
                flag=self._data.flag, approve=True, remark=remark
            )
        elif self._data.request_type is RequestType.GROUP:
            return await self._api.set_group_add_request(
                flag=self._data.flag,
                sub_type=self._data.sub_type,  # type: ignore[attr-defined]
                approve=True,
                reason="",
            )

    async def reject(self, reason: str = "") -> Any:
        if self._data.request_type is RequestType.FRIEND:
            return await self._api.set_friend_add_request(
                flag=self._data.flag, approve=False
            )
        elif self._data.request_type is RequestType.GROUP:
            return await self._api.set_group_add_request(
                flag=self._data.flag,
                sub_type=self._data.sub_type,  # type: ignore[attr-defined]
                approve=False,
                reason=reason,
            )


class FriendRequestEvent(RequestEvent):
    """QQ 好友请求事件"""

    _data: FriendRequestEventData


class GroupRequestEvent(RequestEvent, GroupScoped):
    """QQ 群请求事件"""

    _data: GroupRequestEventData

    @property
    def sub_type(self) -> str:
        return self._data.sub_type

    @property
    def group_id(self) -> str:
        return self._data.group_id
