"""直播间事件实体"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..common.base import BaseEvent
from ..common.mixins import Replyable, HasSender, GroupScoped, Bannable

if TYPE_CHECKING:
    from ncatbot.api.bilibili import IBiliAPIClient
    from ncatbot.types.bilibili.events import (
        BiliLiveEventData,
        DanmuMsgEventData,
        SuperChatEventData,
        GiftEventData,
        GuardBuyEventData,
        InteractEventData,
        LikeEventData,
    )

__all__ = [
    "BiliLiveEvent",
    "DanmuMsgEvent",
    "SuperChatEvent",
    "GiftEvent",
    "GuardBuyEvent",
    "InteractEvent",
    "LikeEvent",
    "LiveNoticeEvent",
]


class BiliLiveEvent(BaseEvent, GroupScoped):
    """直播间事件基类"""

    _data: "BiliLiveEventData"
    _api: "IBiliAPIClient"

    @property
    def api(self) -> "IBiliAPIClient":
        return self._api

    @property
    def group_id(self) -> str:
        return self._data.room_id


class DanmuMsgEvent(BiliLiveEvent, Replyable, HasSender, Bannable):
    """弹幕消息事件"""

    _data: "DanmuMsgEventData"

    @property
    def user_id(self) -> str:
        return self._data.user_id

    @property
    def sender(self) -> Any:
        return self._data.sender

    async def reply(self, text: str, **kwargs: Any) -> Any:
        return await self._api.send_danmu(room_id=int(self._data.room_id), text=text)

    async def ban(self, duration: int = 1, **kwargs: Any) -> Any:
        return await self._api.ban_user(
            room_id=int(self._data.room_id),
            user_id=int(self._data.user_id),
            hour=duration,
        )


class SuperChatEvent(BiliLiveEvent, HasSender):
    """醒目留言事件"""

    _data: "SuperChatEventData"

    @property
    def user_id(self) -> str:
        return self._data.user_id

    @property
    def sender(self) -> Any:
        return self._data.sender


class GiftEvent(BiliLiveEvent, HasSender):
    """礼物事件"""

    _data: "GiftEventData"

    @property
    def user_id(self) -> str:
        return self._data.user_id

    @property
    def sender(self) -> Any:
        return self._data.sender


class GuardBuyEvent(BiliLiveEvent, HasSender):
    """大航海事件"""

    _data: "GuardBuyEventData"

    @property
    def user_id(self) -> str:
        return self._data.user_id

    @property
    def sender(self) -> Any:
        return self._data.sender


class InteractEvent(BiliLiveEvent, HasSender):
    """互动事件 (进入/关注/分享)"""

    _data: "InteractEventData"

    @property
    def user_id(self) -> str:
        return self._data.user_id

    @property
    def sender(self) -> Any:
        return self._data.sender


class LikeEvent(BiliLiveEvent, HasSender):
    """点赞事件"""

    _data: "LikeEventData"

    @property
    def user_id(self) -> str:
        return self._data.user_id

    @property
    def sender(self) -> Any:
        return self._data.sender


class LiveNoticeEvent(BiliLiveEvent):
    """直播间通知事件 (人气/开播/下播/房间变更等)"""
