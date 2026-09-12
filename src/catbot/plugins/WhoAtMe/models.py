"""谁艾特我 — 数据模型与序列化。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, List


@dataclass
class MessagePart:
    kind: str  # text | at | image | reply | video | record | other
    text: str = ""
    url: str = ""
    file: str = ""
    user_id: str = ""
    reply_id: str = ""


@dataclass
class StoredMessage:
    message_id: str
    user_id: str
    nickname: str
    card: str = ""
    role: str = "member"
    level: str = ""
    title: str = ""
    parts: List[MessagePart] = field(default_factory=list)
    at_user_ids: List[str] = field(default_factory=list)
    timestamp: float = 0.0

    @property
    def display_name(self) -> str:
        return self.card or self.nickname or self.user_id

    @property
    def plain_preview(self) -> str:
        chunks: List[str] = []
        for p in self.parts:
            if p.kind == "text":
                chunks.append(p.text)
            elif p.kind == "at":
                chunks.append(p.text or f"@{p.user_id}")
            elif p.kind == "image":
                chunks.append("[图片]")
            elif p.kind == "reply":
                chunks.append("")
            elif p.kind == "video":
                chunks.append("[视频]")
            elif p.kind == "record":
                chunks.append("[语音]")
            else:
                chunks.append(p.text or f"[{p.kind}]")
        return "".join(chunks).strip() or "[消息]"


@dataclass
class PendingAt:
    id: int | None  # DB 主键；内存新建时为 None
    message_id: str
    target_user_id: str
    atter_user_id: str
    atter_nickname: str
    timestamp: float
    created_at: float
    group_id: int
    context: List[StoredMessage] = field(default_factory=list)


def message_part_from_dict(data: dict[str, Any]) -> MessagePart:
    return MessagePart(
        kind=str(data.get("kind") or "other"),
        text=str(data.get("text") or ""),
        url=str(data.get("url") or ""),
        file=str(data.get("file") or ""),
        user_id=str(data.get("user_id") or ""),
        reply_id=str(data.get("reply_id") or ""),
    )


def stored_message_from_dict(data: dict[str, Any]) -> StoredMessage:
    parts = [
        message_part_from_dict(p)
        for p in (data.get("parts") or [])
        if isinstance(p, dict)
    ]
    at_ids = [str(x) for x in (data.get("at_user_ids") or [])]
    return StoredMessage(
        message_id=str(data.get("message_id") or ""),
        user_id=str(data.get("user_id") or ""),
        nickname=str(data.get("nickname") or ""),
        card=str(data.get("card") or ""),
        role=str(data.get("role") or "member"),
        level=str(data.get("level") or ""),
        title=str(data.get("title") or ""),
        parts=parts,
        at_user_ids=at_ids,
        timestamp=float(data.get("timestamp") or 0),
    )


def stored_message_to_dict(msg: StoredMessage) -> dict[str, Any]:
    return asdict(msg)


def context_to_jsonable(context: List[StoredMessage]) -> list[dict[str, Any]]:
    return [stored_message_to_dict(m) for m in context]


def context_from_jsonable(raw: Any) -> List[StoredMessage]:
    if not isinstance(raw, list):
        return []
    out: List[StoredMessage] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(stored_message_from_dict(item))
    return out
