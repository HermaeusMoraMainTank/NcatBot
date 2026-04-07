"""
事件包装类

将 resolved 事件类型字符串与原始数据模型打包在一起，
供流式消费 API 使用。
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ncatbot.types import BaseEventData


@dataclass(frozen=True, slots=True)
class Event:
    """流式消费中 yield 的事件对象。

    Attributes:
        type: resolved 事件类型，如 ``"message.group"``、``"notice.group_increase"``。
        data: 原始 :class:`BaseEventData` 实例。
        platform: 来源平台，如 ``"qq"``。
    """

    type: str
    data: "BaseEventData"
    platform: str = "unknown"

    def __repr__(self) -> str:
        return f"Event(type={self.type!r}, platform={self.platform!r}, data={self.data.__class__.__name__})"
