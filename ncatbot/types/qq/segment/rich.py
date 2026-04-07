"""QQ 专用富媒体消息段"""

from __future__ import annotations

from typing import ClassVar, Literal, Optional

from ...common.segment.base import MessageSegment

__all__ = [
    "Share",
    "Location",
    "Music",
    "Json",
    "Markdown",
]


class Share(MessageSegment):
    _type: ClassVar[str] = "share"
    url: str
    title: str
    content: Optional[str] = None
    image: Optional[str] = None


class Location(MessageSegment):
    _type: ClassVar[str] = "location"
    lat: float
    lon: float
    title: Optional[str] = None
    content: Optional[str] = None


class Music(MessageSegment):
    """音乐消息段

    _type = "music" 为内部判别（OB11 外层 type）。
    type 字段保留 OB11 data.type 原始语义（"qq" / "163" / "custom"）。
    """

    _type: ClassVar[str] = "music"
    type: Literal["qq", "163", "custom"]
    id: Optional[str] = None
    url: Optional[str] = None
    audio: Optional[str] = None
    title: Optional[str] = None


class Json(MessageSegment):
    _type: ClassVar[str] = "json"
    data: str


class Markdown(MessageSegment):
    _type: ClassVar[str] = "markdown"
    content: str
