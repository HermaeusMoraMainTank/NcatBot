"""每轮对话留档：prompt 摘要、工具轨迹、成本，供后台逐轮复盘。

写 JSONL，按天分文件，只保留最近 7 天；单条记录里的长文本一律截断，
避免日志把磁盘吃满。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_log = logging.getLogger(__name__)

TRACE_DIR = Path("data/FakeAi/traces")
KEEP_DAYS = 7
PREVIEW_CHARS = 600
MAX_TOOLS = 20


def _day_path(ts: float) -> Path:
    stamp = time.strftime("%Y%m%d", time.localtime(ts))
    return TRACE_DIR / f"{stamp}.jsonl"


def _clip(value: Any, limit: int = PREVIEW_CHARS) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text[:limit]


def record_turn(
    *,
    group_id: int,
    user_id: str = "",
    sender: str = "",
    trigger: str = "",
    wake: str = "",
    model: str = "",
    usage: Optional[Dict[str, int]] = None,
    rounds: int = 0,
    tools: Optional[List[Dict[str, str]]] = None,
    actions: Optional[List[Dict[str, str]]] = None,
    system_prompt: str = "",
    user_prompt: str = "",
    reply: str = "",
    error: str = "",
) -> None:
    """追加一条留档。任何异常都吞掉：留档不能影响正常回复。"""
    try:
        ts = time.time()
        payload = {
            "ts": ts,
            "group_id": int(group_id),
            "user_id": str(user_id or ""),
            "sender": _clip(sender, 40),
            "trigger": trigger,
            "wake": _clip(wake, 80),
            "model": model,
            "rounds": int(rounds),
            "usage": dict(usage or {}),
            "tools": (tools or [])[:MAX_TOOLS],
            "actions": actions or [],
            "system_chars": len(system_prompt or ""),
            "user_chars": len(user_prompt or ""),
            "system_preview": _clip(system_prompt),
            "user_preview": _clip(user_prompt, 1200),
            "reply": _clip(reply, 400),
            "error": _clip(error, 200),
        }
        path = _day_path(ts)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as e:
        _log.debug("[FakeAi Trace] 写入失败: %s", e)


def recent(limit: int = 50) -> List[Dict[str, Any]]:
    """读取最近的留档（新的在前）。"""
    limit = max(1, min(200, int(limit or 50)))
    items: List[Dict[str, Any]] = []
    if not TRACE_DIR.is_dir():
        return items
    for path in sorted(TRACE_DIR.glob("*.jsonl"), reverse=True):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(items) >= limit:
                return items
    return items


def prune(days: int = KEEP_DAYS) -> int:
    """删掉超过保留天数的留档文件。"""
    if not TRACE_DIR.is_dir():
        return 0
    cutoff = time.time() - max(1, int(days)) * 86400
    removed = 0
    for path in TRACE_DIR.glob("*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed
