"""QQ 平台通知事件数据模型"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from ..common.base import BaseEventData
from .enums import (
    PostType,
    NoticeType,
    NoticeNotifySubType,
    GroupAdminSubType,
    GroupDecreaseSubType,
    GroupIncreaseSubType,
    GroupBanSubType,
)
from .misc import FileInfo

__all__ = [
    "NoticeEventData",
    "GroupUploadNoticeEventData",
    "GroupAdminNoticeEventData",
    "GroupDecreaseNoticeEventData",
    "GroupIncreaseNoticeEventData",
    "GroupBanNoticeEventData",
    "FriendAddNoticeEventData",
    "GroupRecallNoticeEventData",
    "FriendRecallNoticeEventData",
    "EmojiLike",
    "GroupMsgEmojiLikeNoticeEventData",
    "NotifyEventData",
    "PokeNotifyEventData",
    "LuckyKingNotifyEventData",
    "HonorNotifyEventData",
]


class NoticeEventData(BaseEventData):
    platform: str = "qq"
    post_type: PostType = Field(default=PostType.NOTICE)  # type: ignore[assignment]
    notice_type: NoticeType
    sub_type: Optional[str] = None
    group_id: Optional[str] = None
    user_id: Optional[str] = None


class GroupUploadNoticeEventData(NoticeEventData):
    notice_type: NoticeType = Field(default=NoticeType.GROUP_UPLOAD)
    file: FileInfo


class GroupAdminNoticeEventData(NoticeEventData):
    notice_type: NoticeType = Field(default=NoticeType.GROUP_ADMIN)
    sub_type: GroupAdminSubType = Field(default=GroupAdminSubType.SET)  # type: ignore[assignment]


class GroupDecreaseNoticeEventData(NoticeEventData):
    notice_type: NoticeType = Field(default=NoticeType.GROUP_DECREASE)
    sub_type: GroupDecreaseSubType = Field(default=GroupDecreaseSubType.LEAVE)  # type: ignore[assignment]
    operator_id: str


class GroupIncreaseNoticeEventData(NoticeEventData):
    notice_type: NoticeType = Field(default=NoticeType.GROUP_INCREASE)
    sub_type: GroupIncreaseSubType = Field(default=GroupIncreaseSubType.APPROVE)  # type: ignore[assignment]
    operator_id: str


class GroupBanNoticeEventData(NoticeEventData):
    notice_type: NoticeType = Field(default=NoticeType.GROUP_BAN)
    sub_type: GroupBanSubType = Field(default=GroupBanSubType.BAN)  # type: ignore[assignment]
    operator_id: str
    duration: int


class FriendAddNoticeEventData(NoticeEventData):
    notice_type: NoticeType = Field(default=NoticeType.FRIEND_ADD)


class GroupRecallNoticeEventData(NoticeEventData):
    notice_type: NoticeType = Field(default=NoticeType.GROUP_RECALL)
    operator_id: str
    message_id: str


class FriendRecallNoticeEventData(NoticeEventData):
    notice_type: NoticeType = Field(default=NoticeType.FRIEND_RECALL)
    message_id: str


# --- 群消息表情回应 ---


class EmojiLike(BaseModel):
    emoji_id: str
    count: int


class GroupMsgEmojiLikeNoticeEventData(NoticeEventData):
    notice_type: NoticeType = Field(default=NoticeType.GROUP_MSG_EMOJI_LIKE)
    message_id: str
    likes: List[EmojiLike]
    message_seq: Optional[int] = None
    is_add: Optional[bool] = None


# --- Notify 子类 ---


class NotifyEventData(NoticeEventData):
    notice_type: NoticeType = Field(default=NoticeType.NOTIFY)
    sub_type: NoticeNotifySubType  # type: ignore[assignment]


class PokeNotifyEventData(NotifyEventData):
    sub_type: NoticeNotifySubType = Field(default=NoticeNotifySubType.POKE)
    target_id: str


class LuckyKingNotifyEventData(NotifyEventData):
    sub_type: NoticeNotifySubType = Field(default=NoticeNotifySubType.LUCKY_KING)
    target_id: str


class HonorNotifyEventData(NotifyEventData):
    sub_type: NoticeNotifySubType = Field(default=NoticeNotifySubType.HONOR)
    honor_type: Literal["talkative", "performer", "emotion"]
