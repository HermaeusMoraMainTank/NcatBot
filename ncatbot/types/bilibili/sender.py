"""Bilibili 平台发送者类型"""

from __future__ import annotations

from typing import Optional

from ..common.sender import BaseSender

__all__ = [
    "BiliSender",
]


class BiliSender(BaseSender):
    """Bilibili 用户信息"""

    face_url: Optional[str] = None
    medal_name: Optional[str] = None
    medal_level: int = 0
    guard_level: int = 0
    admin: bool = False
