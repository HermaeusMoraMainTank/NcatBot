"""QQ 专用转发消息段"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, field_validator

from ...common.segment.base import MessageSegment, parse_segment

__all__ = [
    "ForwardNode",
    "Forward",
]


class ForwardNode(BaseModel):
    """转发消息的单个节点"""

    user_id: str
    nickname: str
    content: List[MessageSegment]

    @field_validator("user_id", mode="before")
    @classmethod
    def _coerce_uid(cls, v: object) -> str:
        return str(v) if v is not None else ""

    @field_validator("content", mode="before")
    @classmethod
    def _parse_content(cls, v: object) -> List[MessageSegment]:
        if isinstance(v, list):
            result: List[MessageSegment] = []
            for item in v:
                if isinstance(item, dict):
                    result.append(parse_segment(item))
                elif isinstance(item, MessageSegment):
                    result.append(item)
            return result
        return []

    def to_node_dict(self) -> Dict[str, Any]:
        content_list: List[Dict[str, Any]] = []
        for seg in self.content:
            if isinstance(seg, Forward) and seg.content:
                for inner_node in seg.content:
                    content_list.append(inner_node.to_node_dict())
            else:
                content_list.append(seg.to_dict())
        return {
            "type": "node",
            "data": {
                "name": self.nickname,
                "uin": self.user_id,
                "content": content_list,
            },
        }


class Forward(MessageSegment):
    _type: ClassVar[str] = "forward"
    id: Optional[str] = None
    content: Optional[List[ForwardNode]] = None

    def to_dict(self) -> Dict[str, Any]:
        if self.content:
            return {
                "type": "forward",
                "data": {"content": [node.to_node_dict() for node in self.content]},
            }
        if self.id:
            return {"type": "forward", "data": {"id": self.id}}
        return {"type": "forward", "data": {}}

    def to_forward_dict(self) -> Dict[str, Any]:
        if not self.content:
            return {"messages": []}
        return {"messages": [node.to_node_dict() for node in self.content]}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Forward:
        seg_data = dict(data.get("data", {}))
        content = seg_data.get("content")
        if isinstance(content, list):
            valid: List[Dict[str, Any]] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if "message" in item:
                    node_data = {
                        "user_id": item.get("user_id", ""),
                        "nickname": item.get("sender", {}).get("nickname", ""),
                        "content": item.get("message", []),
                    }
                    valid.append(node_data)
                else:
                    valid.append(item)
            seg_data["content"] = valid or None
        return cls.model_validate(seg_data)
