"""Skill 数据模型：把插件能力描述成可被模型发现、调用、审计的工具。

设计目标是「渐进展示」：
- ``SkillSpec.summary`` 进入目录（模型第一眼只看得到这一行）
- ``SkillSpec.description`` + ``parameters`` 只在模型明确要调用某个 skill 时才展开
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Optional

SCOPES = ("user", "group", "admin", "owner")
SIDE_EFFECTS = ("none", "read", "write", "external", "message")
REQUIRES = ("network", "fs.read", "fs.write", "subprocess", "llm", "media", "event")


@dataclass(slots=True)
class Param:
    """声明一个 skill 参数，最终会被转成 JSON Schema。"""

    name: str
    type: str = "string"
    description: str = ""
    required: bool = False
    default: Any = None
    enum: Optional[list[Any]] = None
    items: Optional[dict[str, Any]] = None

    def to_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": self.type}
        if self.description:
            schema["description"] = self.description
        if self.enum:
            schema["enum"] = list(self.enum)
        if self.items:
            schema["items"] = self.items
        if self.default is not None:
            schema["default"] = self.default
        return schema


def build_schema(params: Any) -> tuple[dict[str, Any], list[str]]:
    """把参数声明统一成 (JSON Schema, required 列表)。"""
    if params is None:
        return {"type": "object", "properties": {}}, []
    if isinstance(params, dict):
        schema = dict(params)
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        required = list(schema.get("required") or [])
        return schema, required
    properties: dict[str, Any] = {}
    required: list[str] = []
    for item in params:
        if isinstance(item, Param):
            properties[item.name] = item.to_schema()
            if item.required:
                required.append(item.name)
        elif isinstance(item, dict) and "name" in item:
            prop = {k: v for k, v in item.items() if k not in ("name", "required")}
            properties[str(item["name"])] = prop
            if item.get("required"):
                required.append(str(item["name"]))
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema, required


@dataclass(slots=True)
class SkillResult:
    """skill 的统一返回值。``content`` 会回灌给模型，``artifacts`` 留给发送层。"""

    ok: bool = True
    content: str = ""
    data: Any = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def fail(cls, error: str, **meta: Any) -> "SkillResult":
        return cls(ok=False, error=error, meta=meta)

    @classmethod
    def text(cls, content: str, **data: Any) -> "SkillResult":
        return cls(ok=True, content=content, data=data or None)

    def to_content(self, *, max_chars: int = 4000) -> str:
        """转成回灌模型的 tool message（保持紧凑、可解析）。"""
        import json

        payload: dict[str, Any] = {"ok": self.ok}
        if self.content:
            payload["content"] = _trim(str(self.content), max_chars)
        if self.error:
            payload["error"] = _trim(str(self.error), 800)
        if self.artifacts:
            payload["artifacts"] = [
                {
                    k: v
                    for k, v in item.items()
                    if k in ("kind", "name", "path", "url", "size", "mime")
                }
                for item in self.artifacts[:8]
            ]
        if self.data is not None:
            try:
                payload["data"] = json.loads(_trim(json.dumps(self.data, ensure_ascii=False), 2000))
            except (TypeError, ValueError):
                payload["data"] = _trim(str(self.data), 2000)
        return json.dumps(payload, ensure_ascii=False)


@dataclass(slots=True)
class SkillSpec:
    """一个可调用能力的完整描述。"""

    name: str
    summary: str
    handler: Callable[..., Awaitable[Any]] | Callable[..., Any]
    plugin: str = ""
    description: str = ""
    parameters: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    required: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    scopes: tuple[str, ...] = ("user",)
    side_effects: str = "read"
    requires: tuple[str, ...] = ()
    timeout: float = 60.0
    enabled: bool = True
    mcp_exposed: bool = True
    aliases: tuple[str, ...] = ()
    source: str = ""

    def tool_schema(self) -> dict[str, Any]:
        """OpenAI function-calling 格式。"""
        description = self.description or self.summary
        if self.side_effects == "message":
            description += "\n（会直接向当前会话发送内容）"
        return {
            "type": "function",
            "function": {
                "name": self.tool_name,
                "description": description,
                "parameters": self.parameters,
            },
        }

    @property
    def tool_name(self) -> str:
        """OpenAI / MCP 工具名：只保留字母数字与下划线。"""
        return _tool_name(self.name)

    def catalog_entry(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "summary": self.summary,
            "plugin": self.plugin,
            "tags": list(self.tags),
            "side_effects": self.side_effects,
            "scopes": list(self.scopes),
        }

    def describe_entry(self) -> dict[str, Any]:
        return {
            **self.catalog_entry(),
            "description": self.description or self.summary,
            "parameters": self.parameters,
            "required": list(self.required),
            "requires": list(self.requires),
            "timeout": self.timeout,
            "mcp_exposed": self.mcp_exposed,
            "source": self.source,
        }


def _tool_name(name: str) -> str:
    """OpenAI 工具名只允许 [a-zA-Z0-9_-]，把点号替换成下划线。"""
    return "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in name)


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n…（已截断）"


@dataclass
class SkillContext:
    """一次调用的运行上下文。"""

    session_key: str = ""
    run_id: str = ""
    plugin: str = ""
    event: Any = None
    api: Any = None
    services: Any = None
    user_id: str = ""
    group_id: str = ""
    roles: set[str] = field(default_factory=lambda: {"user"})
    capabilities: set[str] = field(
        default_factory=lambda: {
            "network",
            "fs.read",
            "fs.write",
            "llm",
            "media",
            "event",
        }
    )
    workspace: Optional[Path] = None
    sandbox: Any = None
    runtime: Any = None
    extras: dict[str, Any] = field(default_factory=dict)

    def has_role(self, role: str) -> bool:
        return role in self.roles or "owner" in self.roles

    def allows(self, requires: Iterable[str]) -> tuple[bool, str]:
        missing = [item for item in requires if item not in self.capabilities]
        if missing:
            return False, f"缺少能力：{', '.join(missing)}"
        return True, ""


@dataclass
class SkillCallRecord:
    ts: float = field(default_factory=time.time)
    skill: str = ""
    plugin: str = ""
    run_id: str = ""
    session_key: str = ""
    user_id: str = ""
    ok: bool = True
    ms: int = 0
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "skill": self.skill,
            "plugin": self.plugin,
            "run_id": self.run_id,
            "session_key": self.session_key,
            "user_id": self.user_id,
            "ok": self.ok,
            "ms": self.ms,
            "error": self.error,
        }


class SkillError(Exception):
    """skill 调用失败（参数、权限、超时、插件异常）。"""

    def __init__(self, message: str, *, code: str = "skill_error") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SkillNotFound(SkillError):
    def __init__(self, name: str) -> None:
        super().__init__(f"没有这个 skill：{name}", code="not_found")
        self.name = name


class SkillDenied(SkillError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="denied")
