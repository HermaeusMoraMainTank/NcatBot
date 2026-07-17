"""从 ReplyCache JSON 提取纯文本与分析员上下文。"""

from __future__ import annotations

import json
import time
from typing import Any, List, Optional, Tuple


def extract_plain_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for seg in content:
            if not isinstance(seg, dict):
                continue
            if seg.get("type") == "text":
                t = (seg.get("data") or {}).get("text") or ""
                if t:
                    parts.append(t)
            elif seg.get("type") == "image":
                summary = (seg.get("data") or {}).get("summary") or ""
                parts.append(f"[图片:{summary}]" if summary else "[图片]")
        return " ".join(parts).strip()
    return str(content).strip() if content else ""


def parse_reply(reply_json: str) -> Optional[dict]:
    try:
        data = json.loads(reply_json)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def format_recent_for_analyst(
    replies: List[str],
    limit: int = 10,
) -> str:
    """最近对话，新在下。"""
    lines: List[str] = []
    for reply_json in replies[-limit:]:
        data = parse_reply(reply_json)
        if not data:
            continue
        name = data.get("group_nickname") or data.get("name") or "?"
        uid = str(data.get("id", ""))
        text = extract_plain_text(data.get("content", ""))
        if not text and uid == "0":
            text = extract_plain_text(data.get("content", "")) or "（蓝晴发言）"
        ts = data.get("ts")
        if isinstance(ts, (int, float)) and ts > 0:
            stamp = time.strftime("%H:%M", time.localtime(ts))
        else:
            stamp = "--:--"
        who = "蓝晴" if uid == "0" else name
        lines.append(f"[{stamp}] {who}: {text}")
    return "\n".join(lines) if lines else "（暂无）"


def iter_user_messages_since_last_bot(
    replies: List[str],
) -> List[Tuple[dict, str]]:
    """上次 AI（id=0）之后的用户消息。"""
    last_bot_idx = -1
    parsed: List[Optional[dict]] = [parse_reply(r) for r in replies]
    for i, data in enumerate(parsed):
        if data and str(data.get("id", "")) == "0":
            last_bot_idx = i
    out: List[Tuple[dict, str]] = []
    for data in parsed[last_bot_idx + 1 :]:
        if not data:
            continue
        if str(data.get("id", "")) == "0":
            continue
        text = extract_plain_text(data.get("content", ""))
        out.append((data, text))
    return out
