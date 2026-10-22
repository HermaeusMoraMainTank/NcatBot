"""Agent 运行记录：内存 + 追加式 JSONL，重启后可回看。"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ncatbot.utils import get_log

LOG = get_log("AgentStore")


@dataclass
class AgentStep:
    index: int = 0
    kind: str = "tool"  # tool | llm | note
    name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    content: str = ""
    ok: bool = True
    ms: int = 0
    ts: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "kind": self.kind,
            "name": self.name,
            "args": self.args,
            "content": self.content[:1200],
            "ok": self.ok,
            "ms": self.ms,
            "ts": self.ts,
        }


@dataclass
class AgentRun:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    task: str = ""
    session_key: str = ""
    parent_id: str = ""
    depth: int = 0
    status: str = "pending"  # pending | running | done | failed | cancelled
    model: str = ""
    system_prompt: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    steps: list[AgentStep] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    result: str = ""
    error: str = ""
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    workspace: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    def add_usage(self, usage: dict[str, Any]) -> None:
        for key, value in (usage or {}).items():
            try:
                self.usage[key] = self.usage.get(key, 0) + int(value or 0)
            except (TypeError, ValueError):
                continue

    def finish(self, status: str, *, result: str = "", error: str = "") -> None:
        self.status = status
        self.result = result
        self.error = error
        self.finished_at = time.time()

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task": self.task[:300],
            "session_key": self.session_key,
            "parent_id": self.parent_id,
            "depth": self.depth,
            "status": self.status,
            "model": self.model,
            "steps": len(self.steps),
            "tools": sum(1 for s in self.steps if s.kind == "tool"),
            "usage": dict(self.usage),
            "tokens": self.usage.get("total_tokens", 0),
            "result": self.result[:500],
            "error": self.error[:300],
            "workspace": self.workspace,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": int(((self.finished_at or time.time()) - self.started_at) * 1000),
        }

    def as_dict(self, *, include_messages: bool = True) -> dict[str, Any]:
        data = self.summary()
        data["steps"] = [s.as_dict() for s in self.steps]
        data["artifacts"] = self.artifacts
        if include_messages:
            data["messages"] = _slim_messages(self.messages)
            data["system_prompt"] = self.system_prompt[:4000]
        return data


def _slim_messages(messages: list[dict[str, Any]], limit: int = 60) -> list[dict[str, Any]]:
    out = []
    for message in messages[-limit:]:
        item = {"role": message.get("role"), "content": message.get("content")}
        if item["content"] is not None and not isinstance(item["content"], (str, list)):
            item["content"] = str(item["content"])
        if isinstance(item["content"], str) and len(item["content"]) > 4000:
            item["content"] = item["content"][:4000] + "\n…"
        if message.get("tool_calls"):
            item["tool_calls"] = message["tool_calls"]
        if message.get("tool_call_id"):
            item["tool_call_id"] = message["tool_call_id"]
        out.append(item)
    return out


class RunStore:
    """保留最近 N 条 run；完整记录落 JSONL 便于事后排查。"""

    def __init__(self, path: Path | str = "data/agent/runs.jsonl", *, keep: int = 200) -> None:
        self.path = Path(path)
        self.keep = max(10, int(keep))
        self._runs: dict[str, AgentRun] = {}
        self._order: list[str] = []
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def add(self, run: AgentRun) -> None:
        self._runs[run.id] = run
        if run.id not in self._order:
            self._order.append(run.id)
        while len(self._order) > self.keep:
            old = self._order.pop(0)
            self._runs.pop(old, None)

    def save(self, run: AgentRun, *, final: bool = False) -> None:
        self.add(run)
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(run.as_dict(include_messages=final), ensure_ascii=False) + "\n")
        except OSError as exc:
            LOG.debug("写入 run 记录失败：%s", exc)

    def get(self, run_id: str) -> Optional[AgentRun]:
        return self._runs.get(run_id)

    def list(self, *, limit: int = 50) -> list[dict[str, Any]]:
        ids = list(reversed(self._order))[:limit]
        return [self._runs[i].summary() for i in ids if i in self._runs]

    def active(self) -> list[dict[str, Any]]:
        return [
            run.summary()
            for run in self._runs.values()
            if run.status in ("pending", "running")
        ]

    def load_tail(self, limit: int = 100) -> int:
        """启动时回填最近记录（只保留摘要级信息）。"""
        if not self.path.is_file():
            return 0
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()[-limit:]
        except OSError:
            return 0
        loaded = 0
        for line in lines:
            try:
                data = json.loads(line)
            except ValueError:
                continue
            run = AgentRun(
                id=str(data.get("id") or ""),
                task=str(data.get("task") or ""),
                session_key=str(data.get("session_key") or ""),
                parent_id=str(data.get("parent_id") or ""),
                depth=int(data.get("depth") or 0),
                status=str(data.get("status") or "done"),
                model=str(data.get("model") or ""),
                usage=dict(data.get("usage") or {}),
                result=str(data.get("result") or ""),
                error=str(data.get("error") or ""),
                artifacts=list(data.get("artifacts") or []),
                workspace=str(data.get("workspace") or ""),
                started_at=float(data.get("started_at") or time.time()),
                finished_at=data.get("finished_at"),
            )
            run.steps = [
                AgentStep(
                    index=int(s.get("index") or 0),
                    kind=str(s.get("kind") or "tool"),
                    name=str(s.get("name") or ""),
                    args=dict(s.get("args") or {}),
                    content=str(s.get("content") or ""),
                    ok=bool(s.get("ok", True)),
                    ms=int(s.get("ms") or 0),
                    ts=float(s.get("ts") or time.time()),
                )
                for s in (data.get("steps") or [])
                if isinstance(s, dict)
            ]
            if run.id:
                self.add(run)
                loaded += 1
        return loaded
