"""QQ 专用文本类消息段"""

from __future__ import annotations

from typing import ClassVar

from pydantic import field_validator

from ...common.segment.base import MessageSegment

__all__ = [
    "Face",
]


class Face(MessageSegment):
    """QQ 表情"""

    _type: ClassVar[str] = "face"
    id: str

    @field_validator("id", mode="before")
    @classmethod
    def _coerce_id(cls, v: object) -> str:
        return str(v)
