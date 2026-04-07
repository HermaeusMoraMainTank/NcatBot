"""QQ 平台请求事件数据模型"""

from __future__ import annotations

from typing import Optional

from pydantic import Field

from ..common.base import BaseEventData
from .enums import PostType, RequestType

__all__ = [
    "RequestEventData",
    "FriendRequestEventData",
    "GroupRequestEventData",
]


class RequestEventData(BaseEventData):
    platform: str = "qq"
    post_type: PostType = Field(default=PostType.REQUEST)
    request_type: RequestType
    user_id: str
    comment: Optional[str] = None
    flag: str


class FriendRequestEventData(RequestEventData):
    request_type: RequestType = Field(default=RequestType.FRIEND)


class GroupRequestEventData(RequestEventData):
    request_type: RequestType = Field(default=RequestType.GROUP)
    sub_type: str = Field(default="add")
    group_id: str
