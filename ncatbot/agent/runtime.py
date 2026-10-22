"""工具型 Agent 运行时：目录 → 展开 → 调用 → 收敛。

关键点：
- 只把 4 个元工具暴露给模型（list_skills / describe_skills / invoke_skill / finish），
  插件再多，system prompt 也不会膨胀。
- 一个 session 串行，防止两轮 tool_calls 交错。
- 每次调用都经过 SkillRegistry 的权限、超时、审计。
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable, Optional

from ncatbot.sandbox import SandboxRunner, WorkspaceManager, scrub_secrets
from ncatbot.skill import SkillContext, SkillRegistry, SkillResult
from ncatbot.utils import get_log

from .llm import LLMClient, LLMResponse, OpenAICompatibleLLM
from .store import AgentRun, AgentStep, RunStore

LOG = get_log("AgentRuntime")

META_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": "列出可用能力（只返回名字和一行说明）。先看目录，再决定展开哪个。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索词，可空"},
                    "tag": {"type": "string", "description": "按标签过滤，可空"},
                    "plugin": {"type": "string", "description": "按插件过滤，可空"},
                    "limit": {"type": "integer", "description": "最多返回多少条，默认 30"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_skills",
            "description": "展开一个或多个能力的完整参数说明。调用前必须先用它确认参数。",
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
            "description": "执行一个能力。arguments 必须是对象，字段严格按 describe_skills 返回的 schema。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "能力名"},
                    "arguments": {
                        "type": "object",
                        "description": "参数对象",
                        "additionalProperties": True,
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "结束本次任务。summary 写给调用方看的最终结果（不要写内部推理）。",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
            },
        },
    },
]

DEFAULT_SYSTEM_PROMPT = """你是一个能调用工具的助手。你只能通过工具改变外部世界，不能假装已经完成。

工作方式：
1. 先判断任务是闲聊还是需要动手。闲聊直接回答。
2. 需要动手时，用 list_skills 看目录（只看名字和一行说明）。
3. 用 describe_skills 展开真正要用的能力，确认参数。
4. 用 invoke_skill 执行。一次只做一件事，拿到结果再决定下一步。
5. 全部完成后调用 finish，把结果写清楚。

规则：
- 不要猜测能力名；目录里没有就说明做不到。
- 不要把工具返回的原文大段复述给用户。
- 工具返回 ok=false 时，读 error，换参数或换能力，不要原样重试。
- 涉及发送消息、文件、图片的能力会直接产生副作用，确认参数后再调用。
- 不要编造文件路径；只能使用工具返回的路径。
"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class AgentContext:
    session_key: str = "manual"
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
    event: Any = None
    api: Any = None
    extras: dict[str, Any] = field(default_factory=dict)


class AgentRuntime:
    """一个 Bot 实例持有一个运行时。"""

    def __init__(
        self,
        registry: SkillRegistry,
        *,
        llm: LLMClient | None = None,
        store: RunStore | None = None,
        workspaces: WorkspaceManager | None = None,
        runner: SandboxRunner | None = None,
        max_rounds: int = 8,
        max_tool_calls: int = 20,
        run_timeout: float = 180.0,
        max_depth: int = 1,
        max_tokens_per_run: int = 60000,
    ) -> None:
        self.registry = registry
        self.llm: LLMClient | None = llm
        self.store = store or RunStore()
        self.workspaces = workspaces or WorkspaceManager()
        self.runner = runner or SandboxRunner()
        self.max_rounds = max(1, int(max_rounds))
        self.max_tool_calls = max(1, int(max_tool_calls))
        self.run_timeout = max(5.0, float(run_timeout))
        self.max_depth = max(0, int(max_depth))
        self.max_tokens_per_run = max(0, int(max_tokens_per_run))
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancelled: set[str] = set()
        self._listeners: list[Callable[[AgentRun, str], None]] = []

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------

    def set_llm(self, llm: LLMClient) -> None:
        self.llm = llm

    def use_config_llm(self, config: Any) -> None:
        if self.llm is None:
            self.llm = OpenAICompatibleLLM.from_config(config)

    def add_listener(self, callback: Callable[[AgentRun, str], None]) -> None:
        self._listeners.append(callback)

    def _notify(self, run: AgentRun, event: str) -> None:
        for callback in list(self._listeners):
            try:
                callback(run, event)
            except Exception:
                LOG.debug("agent listener 异常", exc_info=True)

    # ------------------------------------------------------------------
    # 查询 / 取消
    # ------------------------------------------------------------------

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list(limit=limit)

    def get_run(self, run_id: str) -> Optional[AgentRun]:
        return self.store.get(run_id)

    def cancel(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        self._cancelled.add(run_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def run(
        self,
        task: str,
        *,
        session_key: str = "manual",
        ctx: AgentContext | None = None,
        system_prompt: str | None = None,
        max_rounds: int | None = None,
        total_timeout: float | None = None,
        allowed_skills: Iterable[str] | None = None,
        deny_skills: Iterable[str] | None = None,
        direct_tools: Iterable[str] | None = None,
        parent_id: str = "",
        depth: int = 0,
        extra_context: str = "",
    ) -> AgentRun:
        context = ctx or AgentContext(session_key=session_key)
        context.session_key = session_key or context.session_key
        run = AgentRun(
            task=str(task or "").strip(),
            session_key=context.session_key,
            parent_id=parent_id,
            depth=depth,
            system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT,
            model=getattr(self.llm, "model", "") or "",
        )
        self.store.add(run)
        if not run.task:
            run.finish("failed", error="任务为空")
            self.store.save(run, final=True)
            return run

        lock = self._session_locks.setdefault(context.session_key or "manual", asyncio.Lock())
        self._tasks[run.id] = asyncio.current_task()
        try:
            async with lock:
                await asyncio.wait_for(
                    self._run_loop(
                        run,
                        context,
                        system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT,
                        max_rounds=max_rounds or self.max_rounds,
                        allowed_skills=set(allowed_skills) if allowed_skills else None,
                        deny_skills=set(deny_skills or ()),
                        direct_tools=set(direct_tools or ()),
                        extra_context=extra_context,
                    ),
                    timeout=total_timeout or self.run_timeout,
                )
        except asyncio.TimeoutError:
            run.finish("failed", error=f"任务超时（>{total_timeout or self.run_timeout:.0f}s）")
            self.store.save(run, final=True)
        except asyncio.CancelledError:
            run.finish("cancelled", error="已取消")
            self.store.save(run, final=True)
            raise
        except Exception as exc:
            LOG.exception("agent run 失败")
            run.finish("failed", error=f"{type(exc).__name__}: {exc}")
            self.store.save(run, final=True)
        finally:
            self._tasks.pop(run.id, None)
            self._cancelled.discard(run.id)
        return run

    async def _run_loop(
        self,
        run: AgentRun,
        context: AgentContext,
        *,
        system_prompt: str,
        max_rounds: int,
        allowed_skills: set[str] | None,
        deny_skills: set[str],
        direct_tools: set[str],
        extra_context: str,
    ) -> None:
        if self.llm is None:
            run.finish("failed", error="没有可用的模型客户端（未配置 ai.api_key）")
            self.store.save(run, final=True)
            return

        workspace = self.workspaces.get(f"{context.session_key}-{run.id}")
        run.workspace = str(workspace.root)
        run.status = "running"
        self._notify(run, "started")

        skill_ctx = SkillContext(
            session_key=context.session_key,
            run_id=run.id,
            user_id=context.user_id,
            group_id=context.group_id,
            roles=set(context.roles),
            capabilities=set(context.capabilities),
            event=context.event,
            api=context.api,
            workspace=workspace,
            sandbox=self.runner,
            runtime=self,
            extras=dict(context.extras),
        )

        available = self._available_skills(allowed_skills, deny_skills)
        catalog = self.registry.catalog(limit=40)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": self._user_prompt(
                    run.task, context, workspace, catalog, extra_context
                ),
            },
        ]
        run.messages = messages

        tools = self._tools_for(allowed_skills, deny_skills, direct_tools)
        tool_calls_used = 0
        final_status = "done"
        final_text = ""
        finish_reason = ""

        for round_index in range(max_rounds):
            if run.id in self._cancelled:
                run.finish("cancelled", error="已取消")
                return
            response = await self._complete(messages, tools)
            if response is None:
                final_status = "failed"
                final_text = "模型调用失败"
                break
            run.add_usage(response.usage)
            if response.model:
                run.model = response.model
            if (
                self.max_tokens_per_run
                and run.usage.get("total_tokens", 0) >= self.max_tokens_per_run
            ):
                final_status = "failed"
                final_text = f"达到本次 token 预算（{self.max_tokens_per_run}），已停止"
                run.steps.append(
                    AgentStep(
                        index=len(run.steps),
                        kind="note",
                        name="budget",
                        ok=False,
                        content=final_text,
                    )
                )
                break

            calls = response.tool_calls
            if not calls:
                final_text = (response.content or "").strip()
                run.steps.append(
                    AgentStep(
                        index=len(run.steps),
                        kind="llm",
                        name="final",
                        content=final_text[:1200],
                    )
                )
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": calls,
                    **(
                        {"reasoning_content": response.reasoning_content}
                        if response.reasoning_content
                        else {}
                    ),
                }
            )
            run.messages = messages

            finish_called = False
            for call in calls:
                name = (call.get("function") or {}).get("name", "")
                raw_args = (call.get("function") or {}).get("arguments") or "{}"
                args = _parse_args(raw_args)
                started = time.perf_counter()

                if name == "finish":
                    finish_reason = str(args.get("summary") or "").strip()
                    content = finish_reason or "任务已结束"
                    ok = True
                    finish_called = True
                elif name == "list_skills":
                    content = self._list_skills(args, available, deny_skills)
                    ok = True
                elif name == "describe_skills":
                    content = self._describe_skills(args, available, deny_skills)
                    ok = True
                elif name == "invoke_skill":
                    tool_calls_used += 1
                    if tool_calls_used > self.max_tool_calls:
                        content = json.dumps(
                            {"ok": False, "error": "本轮工具调用次数已达上限，请调用 finish 收尾。"},
                            ensure_ascii=False,
                        )
                        ok = False
                    else:
                        result, skill_name = await self._invoke(
                            args, skill_ctx, available, deny_skills
                        )
                        content = result.to_content()
                        ok = result.ok
                        run.artifacts.extend(result.artifacts)
                        run.steps.append(
                            AgentStep(
                                index=len(run.steps),
                                kind="tool",
                                name=skill_name,
                                args=_slim_args(args.get("arguments")),
                                content=content[:1200],
                                ok=ok,
                                ms=int((time.perf_counter() - started) * 1000),
                            )
                        )
                        self._notify(run, "step")
                else:
                    content = json.dumps(
                        {"ok": False, "error": f"未知工具：{name}"}, ensure_ascii=False
                    )
                    ok = False
                    run.steps.append(
                        AgentStep(
                            index=len(run.steps),
                            kind="tool",
                            name=name or "?",
                            ok=False,
                            content=content,
                        )
                    )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "content": scrub_secrets(content)[:8000],
                    }
                )
            run.messages = messages
            self.store.save(run)
            if finish_called:
                break
        else:
            final_status = "failed"
            final_text = "达到最大轮次仍未收敛"
            finish_reason = finish_reason or "轮次用尽"

        if final_status == "done":
            run.finish("done", result=scrub_secrets(finish_reason or final_text))
        else:
            run.finish(final_status, result=scrub_secrets(final_text), error=final_text)
        self.store.save(run, final=True)
        self._notify(run, "finished")

    # ------------------------------------------------------------------
    # 子任务
    # ------------------------------------------------------------------

    async def run_child(
        self,
        task: str,
        *,
        parent: AgentContext,
        parent_id: str,
        allowed_skills: Iterable[str] | None = None,
        system_prompt: str | None = None,
        depth: int = 1,
    ) -> AgentRun:
        if depth > self.max_depth:
            raise ValueError(f"子智能体层级超过上限 {self.max_depth}")
        child_ctx = AgentContext(
            session_key=f"{parent.session_key}:child",
            user_id=parent.user_id,
            group_id=parent.group_id,
            roles={"user"},
            capabilities={"fs.read", "fs.write", "llm", "network", "media"},
            event=None,
            api=None,
            extras={"parent": parent_id},
        )
        return await self.run(
            task,
            session_key=f"{parent.session_key}:child",
            ctx=child_ctx,
            system_prompt=system_prompt
            or (
                DEFAULT_SYSTEM_PROMPT
                + "\n你现在是子智能体：只在工作区内读写文件，不要尝试访问宿主路径，不要发送群消息。"
            ),
            allowed_skills=allowed_skills,
            parent_id=parent_id,
            depth=depth,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _complete(self, messages, tools) -> Optional[LLMResponse]:
        assert self.llm is not None
        try:
            return await self.llm.complete(
                messages,
                tools=tools,
                capability="chat",
                max_tokens=1200,
                temperature=0.3,
            )
        except Exception as exc:
            LOG.error("LLM 调用异常：%s: %s", type(exc).__name__, exc)
            return None

    def _available_skills(
        self, allowed: set[str] | None, deny: set[str]
    ) -> set[str]:
        names = {
            spec.name
            for spec in self.registry.all()
            if spec.enabled and spec.name not in deny
        }
        if allowed is not None:
            names &= allowed
        return names

    def _tools_for(
        self,
        allowed: set[str] | None,
        deny: set[str],
        direct: set[str],
    ) -> list[dict[str, Any]]:
        tools = list(META_TOOLS)
        if not direct:
            return tools
        available = self._available_skills(allowed, deny)
        for name in direct:
            if name in available:
                spec = self.registry.get(name)
                if spec is not None:
                    tools.append(spec.tool_schema())
        return tools

    def _list_skills(
        self, args: dict[str, Any], available: set[str], deny: set[str]
    ) -> str:
        limit = max(1, min(60, int(args.get("limit") or 30)))
        entries = [
            entry
            for entry in self.registry.catalog(
                query=str(args.get("query") or ""),
                tag=str(args.get("tag") or ""),
                plugin=str(args.get("plugin") or ""),
                limit=80,
            )
            if entry["name"] in available and entry["name"] not in deny
        ][:limit]
        return json.dumps(entries, ensure_ascii=False)

    def _describe_skills(
        self, args: dict[str, Any], available: set[str], deny: set[str]
    ) -> str:
        names = [str(n) for n in (args.get("names") or []) if str(n) in available]
        names = [n for n in names if n not in deny]
        described = self.registry.describe(names)
        return json.dumps(described, ensure_ascii=False)

    async def _invoke(
        self,
        args: dict[str, Any],
        ctx: SkillContext,
        available: set[str],
        deny: set[str],
    ) -> tuple[SkillResult, str]:
        name = str(args.get("name") or "").strip()
        if not name:
            return SkillResult.fail("缺少 name"), "invoke_skill"
        if name not in available or name in deny:
            return SkillResult.fail(f"能力不可用：{name}"), name
        arguments = args.get("arguments")
        if isinstance(arguments, str):
            arguments = _parse_args(arguments)
        if not isinstance(arguments, dict):
            arguments = {}
        result = await self.registry.invoke(name, arguments, ctx=ctx)
        return result, name

    @staticmethod
    def _user_prompt(
        task: str,
        ctx: AgentContext,
        workspace: Any,
        catalog: list[dict[str, Any]],
        extra: str,
    ) -> str:
        lines = [
            f"时间：{_now()}",
            f"会话：{ctx.session_key}",
        ]
        if ctx.user_id:
            lines.append(f"用户：{ctx.user_id}")
        if ctx.group_id:
            lines.append(f"群：{ctx.group_id}")
        lines.append(f"工作区：{workspace.root}（文件类能力只能在这里读写）")
        lines.append(f"可用能力：{len(catalog)} 个（用 list_skills 查看）")
        if extra:
            lines.extend(["", extra.strip()])
        lines.extend(["", "任务：", task])
        return "\n".join(lines)


def _parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(str(raw or "{}"))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _slim_args(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out = {}
    for key, item in list(value.items())[:12]:
        if isinstance(item, str) and len(item) > 300:
            out[key] = item[:300] + "…"
        else:
            out[key] = item
    return out
