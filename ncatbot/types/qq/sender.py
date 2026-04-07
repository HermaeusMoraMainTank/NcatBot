"""QQ 平台发送者类型"""

from __future__ import annotations

from typing import Optional

from ..common.sender import BaseSender

__all__ = [
    "QQSender",
    "GroupSender",
]


class QQSender(BaseSender):
    sex: Optional[str] = "unknown"
    age: Optional[int] = 0


class GroupSender(QQSender):
    card: Optional[str] = None
    area: Optional[str] = None
    level: Optional[str] = None
    role: Optional[str] = None
    title: Optional[str] = None
