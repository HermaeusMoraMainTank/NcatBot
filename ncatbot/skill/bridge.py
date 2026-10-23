"""把 SkillRegistry 接进已有 agent 循环的轻量桥。

FakeAi 这类自带工具循环的插件不用换成 AgentRuntime，
只要把 ``bridge.tools`` 拼进 tools、把 ``bridge.execute`` 接进工具分发即可。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable, Optional

from .registry import SkillRegistry
from .spec import SkillContext, SkillResult

BRIDGE_TOOL_NAMES = {"list_skills", "describe_skills", "invoke_skill"}

BRIDGE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": "列出机器人会做的具体动作（只返回名字和一行说明）。要做实事前先看这里。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索词，可空"},
                    "tag": {"type": "string", "description": "标签过滤，可空"},
                    "plugin": {"type": "string", "description": "插件过滤，可空"},
                    "limit": {"type": "integer", "description": "最多返回条数，默认 120"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_skills",
            "description": "展开能力的完整参数说明。调用 invoke_skill 前必须先用它确认参数。",
            "parameters": {
                "type": "object",
                "properties": {
                    "names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "能力名列表",
                    }
                },
                "required": ["names"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "invoke_skill",
            "description": "执行一个能力。arguments 必须严格按 describe_skills 的 schema。会直接产生副作用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "能力名"},
                    "arguments": {"type": "object", "description": "参数对象"},
                },
                "required": ["name"],
            },
        },
    },
]


class SkillBridge:
    def __init__(
        self,
        registry: SkillRegistry,
        ctx: SkillContext,
        *,
        allowed: Optional[Iterable[str]] = None,
        deny: Optional[Iterable[str]] = None,
        max_calls: int = 12,
        artifact_sink: Optional[Callable[[list[dict[str, Any]]], Any]] = None,
    ) -> None:
        self.registry = registry
        self.ctx = ctx
        self.allowed = set(allowed) if allowed else None
        self.deny = set(deny or ())
        self.max_calls = max(1, int(max_calls))
        self.calls = 0
        self.artifact_sink = artifact_sink

    @property
    def tools(self) -> list[dict[str, Any]]:
        return list(BRIDGE_TOOLS)

    def _available(self) -> set[str]:
        names = {
            spec.name
            for spec in self.registry.all()
            if spec.enabled and spec.name not in self.deny
        }
        if self.allowed is not None:
            names &= self.allowed
        return names

    async def execute(self, name: str, args: dict[str, Any]) -> str:
        if name == "list_skills":
            return self._list(args)
        if name == "describe_skills":
            return self._describe(args)
        if name == "invoke_skill":
            return await self._invoke(args)
        return f"未知 skill 工具：{name}"

    def _list(self, args: dict[str, Any]) -> str:
        limit = max(1, min(200, int(args.get("limit") or 120)))
        entries = self.registry.catalog(
            query=str(args.get("query") or ""),
            tag=str(args.get("tag") or ""),
            plugin=str(args.get("plugin") or ""),
            limit=200,
        )
        available = self._available()
        entries = [e for e in entries if e["name"] in available][:limit]
        compact = [
            {"name": e["name"], "summary": e["summary"], "plugin": e["plugin"]}
            for e in entries
        ]
        return json.dumps(compact, ensure_ascii=False)

    def _describe(self, args: dict[str, Any]) -> str:
        available = self._available()
        names = [str(n) for n in (args.get("names") or []) if str(n) in available]
        return json.dumps(self.registry.describe(names), ensure_ascii=False)

    async def _invoke(self, args: dict[str, Any]) -> str:
        name = str(args.get("name") or "").strip()
        if not name:
            return json.dumps({"ok": False, "error": "缺少 name"}, ensure_ascii=False)
        if name not in self._available():
            return json.dumps(
                {"ok": False, "error": f"能力不可用：{name}"}, ensure_ascii=False
            )
        if self.calls >= self.max_calls:
            return json.dumps(
                {"ok": False, "error": "本次工具调用次数已达上限，请直接收尾。"},
                ensure_ascii=False,
            )
        self.calls += 1
        arguments = args.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        result: SkillResult = await self.registry.invoke(name, arguments, ctx=self.ctx)
        delivered = await self._deliver_artifacts(result)
        if delivered:
            result.content = (
                f"{result.content}\n（{delivered} 个产物已自动发出，不要再重复发送）"
            )
        return result.to_content()

    async def _deliver_artifacts(self, result: SkillResult) -> int:
        """把 skill 产出的图片/语音交给发送层，避免依赖模型的第二次工具调用。"""
        if not result.ok or not result.artifacts or self.artifact_sink is None:
            return 0
        try:
            sent = await self.artifact_sink(list(result.artifacts))
        except Exception:
            return 0
        return int(sent or 0)
