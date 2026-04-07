"""QQ 专用媒体消息段"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, Optional

from ...common.segment.media import Image, Record

__all__ = [
    "QQImage",
    "QQRecord",
]


class QQImage(Image):
    _type: ClassVar[str] = "image"
    sub_type: int = 0
    type: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> QQImage:
        seg_data = dict(data.get("data", {}))
        return cls.model_validate(seg_data)


class QQRecord(Record):
    _type: ClassVar[str] = "record"
    magic: Optional[int] = None
