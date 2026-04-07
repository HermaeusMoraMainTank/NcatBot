"""通用文本/引用类消息段"""

from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import AliasChoices, Field, field_validator

from .base import MessageSegment

__all__ = [
    "PlainText",
    "At",
    "Reply",
]


class PlainText(MessageSegment):
    _type: ClassVar[str] = "text"
    text: str


class At(MessageSegment):
    _type: ClassVar[str] = "at"
    user_id: str = Field(validation_alias=AliasChoices("user_id", "qq"))

    @field_validator("user_id", mode="before")
    @classmethod
    def _coerce_user_id(cls, v: object) -> str:
        return str(v).strip()

    def to_dict(self) -> Dict[str, Any]:
        # OB11 协议使用 "qq" 作为 key
        return {"type": self._type, "data": {"qq": self.user_id}}


class Reply(MessageSegment):
    _type: ClassVar[str] = "reply"
    id: str

    @field_validator("id", mode="before")
    @classmethod
    def _coerce_id(cls, v: object) -> str:
        return str(v)
