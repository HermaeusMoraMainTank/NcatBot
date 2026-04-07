"""事件行为 Mixin 抽象类

提供跨平台事件行为能力的 Mixin 基类，
平台事件通过多继承组合所需行为。
插件代码可通过 isinstance 检查事件是否支持某行为。
"""

from __future__ import annotations

from typing import Any
from ncatbot.types.common import Attachment, AttachmentList


class Replyable:
    """支持回复的事件"""

    async def reply(self, **kwargs: Any) -> Any:
        raise NotImplementedError

    __slots__ = ()


class Deletable:
    """支持撤回/删除的事件"""

    async def delete(self) -> Any:
        raise NotImplementedError

    __slots__ = ()


class HasSender:
    """包含发送者信息的事件"""

    @property
    def user_id(self) -> str:
        raise NotImplementedError

    @property
    def sender(self) -> Any:
        raise NotImplementedError

    __slots__ = ()


class GroupScoped:
    """属于群/频道的事件"""

    @property
    def group_id(self) -> str:
        raise NotImplementedError

    __slots__ = ()


class Kickable:
    """支持踢出成员的事件"""

    async def kick(self, **kwargs: Any) -> Any:
        raise NotImplementedError

    __slots__ = ()


class Bannable:
    """支持禁言的事件"""

    async def ban(self, duration: int = 1800, **kwargs: Any) -> Any:
        raise NotImplementedError

    __slots__ = ()


class Approvable:
    """支持同意/拒绝的事件"""

    async def approve(self, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def reject(self, **kwargs: Any) -> Any:
        raise NotImplementedError

    __slots__ = ()


class HasAttachments:
    """携带可下载附件的事件"""

    async def get_attachments(self) -> "AttachmentList[Attachment]":
        raise NotImplementedError

    __slots__ = ()
