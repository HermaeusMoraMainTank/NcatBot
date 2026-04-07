"""消息段基类 — 纯内存模型

序列化 (to_dict / from_dict) 由各平台在 types/<platform>/segment/serializer.py 实现。
此处仅定义字段 + __init_subclass__ 自动注册。
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, Type

from pydantic import BaseModel, ConfigDict

__all__ = [
    "MessageSegment",
    "parse_segment",
    "SEGMENT_MAP",
]

SEGMENT_MAP: Dict[str, Type[MessageSegment]] = {}


class MessageSegment(BaseModel):
    """消息段基类

    _type (ClassVar): 内部判别标识，对应各平台协议外层 type。
    子类的 type 实例字段（如 Music.type, Image.type）保留协议 data.type 原始语义。
    """

    model_config = ConfigDict(extra="allow")
    _type: ClassVar[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if isinstance(cls.__dict__.get("_type"), str):
            SEGMENT_MAP[cls._type] = cls

    def to_dict(self) -> Dict[str, Any]:
        """默认序列化实现（OB11 兼容），可被平台 serializer 覆盖"""
        return {"type": self._type, "data": self.model_dump(exclude_none=True)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MessageSegment:
        """从协议原始数据解析为具体子类"""
        return parse_segment(data)


def parse_segment(raw: Dict[str, Any]) -> MessageSegment:
    """从 {"type": "...", "data": {...}} 解析为具体 MessageSegment"""
    seg_type = raw.get("type", "")
    target_cls = SEGMENT_MAP.get(seg_type)
    if not target_cls:
        raise ValueError(f"Unknown segment type: {seg_type}")
    if "from_dict" in target_cls.__dict__:
        return target_cls.from_dict(raw)
    return target_cls.model_validate(raw.get("data", {}))
