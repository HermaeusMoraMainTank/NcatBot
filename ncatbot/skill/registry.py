"""Skill 注册表：插件能力的统一目录、调用网关与审计点。

一个 skill = 名字 + 一行摘要 + 完整 JSON Schema + 异步 handler。
注册表只做四件事：发现、渐进展示、参数校验、受控调用。
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections import deque
from typing import Any, Callable, Iterable, Optional

from ncatbot.utils import get_log

from .spec import (
    SkillCallRecord,
    SkillContext,
    SkillDenied,
    SkillError,
    SkillNotFound,
    SkillResult,
    SkillSpec,
    build_schema,
)

LOG = get_log("SkillRegistry")

SKILL_ATTR = "__skill_spec__"


def skill(
    name: str,
    *,
    summary: str,
    description: str = "",
    params: Any = None,
    tags: Iterable[str] = (),
    scopes: Iterable[str] = ("user",),
    side_effects: str = "read",
    requires: Iterable[str] = (),
    timeout: float = 60.0,
    mcp_exposed: bool = True,
    aliases: Iterable[str] = (),
) -> Callable:
    """把插件方法标记成 skill。

    用法::

        class MyPlugin(NcatBotPlugin):
            @skill("image.send", summary="按标签发一张图", params=[...])
            async def send_image(self, ctx: SkillContext, tag: str):
                ...
    """

    parameters, required = build_schema(params)

    def decorator(func: Callable) -> Callable:
        setattr(
            func,
            SKILL_ATTR,
            {
                "name": name,
                "summary": summary,
                "description": description,
                "parameters": parameters,
                "required": required,
                "tags": list(tags),
                "scopes": tuple(scopes),
                "side_effects": side_effects,
                "requires": tuple(requires),
                "timeout": timeout,
                "mcp_exposed": mcp_exposed,
                "aliases": tuple(aliases),
            },
        )
        return func

    return decorator


class SkillRegistry:
    """进程内 skill 目录。一个 Bot 实例持有一个。"""

    def __init__(self, *, max_audit: int = 2000) -> None:
        self._skills: dict[str, SkillSpec] = {}
        self._aliases: dict[str, str] = {}
        self._stats: dict[str, dict[str, Any]] = {}
        self._audit: deque[SkillCallRecord] = deque(maxlen=max_audit)
        self._locks: dict[str, asyncio.Lock] = {}
        self._default_roles: set[str] = {"user"}

    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------

    def register(self, spec: SkillSpec) -> SkillSpec:
        name = spec.name.strip()
        if not name:
            raise ValueError("skill 名字不能为空")
        if spec.side_effects not in ("none", "read", "write", "external", "message"):
            raise ValueError(f"{name}: 非法 side_effects={spec.side_effects}")
        spec.name = name
        existing = self._skills.get(name)
        if existing is not None and existing.handler is not spec.handler:
            LOG.warning("skill 重复注册，后者覆盖前者：%s", name)
        self._skills[name] = spec
        for alias in spec.aliases:
            self._aliases[alias] = name
        self._stats.setdefault(
            name,
            {
                "calls": 0,
                "errors": 0,
                "total_ms": 0,
                "last_called_at": None,
                "last_error": "",
            },
        )
        LOG.debug("注册 skill: %s (plugin=%s)", name, spec.plugin)
        return spec

    def register_function(
        self,
        name: str,
        handler: Callable,
        *,
        summary: str,
        description: str = "",
        params: Any = None,
        plugin: str = "framework",
        **kwargs: Any,
    ) -> SkillSpec:
        parameters, required = build_schema(params)
        kwargs.pop("required", None)
        spec = SkillSpec(
            name=name,
            summary=summary,
            description=description,
            handler=handler,
            plugin=plugin,
            parameters=parameters,
            required=required,
            source="function",
            **kwargs,
        )
        return self.register(spec)

    def discover_plugin(self, plugin: Any) -> int:
        """扫描插件实例上的 @skill 方法并注册。"""
        plugin_name = getattr(plugin, "name", type(plugin).__name__)
        count = 0
        seen: set[str] = set()
        for cls in type(plugin).__mro__:
            for attr_name, raw in cls.__dict__.items():
                meta = getattr(raw, SKILL_ATTR, None)
                if not meta or attr_name in seen:
                    continue
                seen.add(attr_name)
                bound = getattr(plugin, attr_name)
                spec = SkillSpec(
                    name=meta["name"],
                    summary=meta["summary"],
                    description=meta["description"],
                    handler=bound,
                    plugin=plugin_name,
                    parameters=meta["parameters"],
                    required=meta["required"],
                    tags=meta["tags"],
                    scopes=meta["scopes"],
                    side_effects=meta["side_effects"],
                    requires=meta["requires"],
                    timeout=meta["timeout"],
                    mcp_exposed=meta["mcp_exposed"],
                    aliases=meta["aliases"],
                    source=f"{plugin_name}.{attr_name}",
                )
                self.register(spec)
                count += 1
        if count:
            LOG.info("插件 %s 注册了 %d 个 skill", plugin_name, count)
        return count

    def unregister_plugin(self, plugin_name: str) -> int:
        names = [n for n, s in self._skills.items() if s.plugin == plugin_name]
        for name in names:
            spec = self._skills.pop(name)
            for alias in spec.aliases:
                self._aliases.pop(alias, None)
            self._stats.pop(name, None)
            self._locks.pop(name, None)
        if names:
            LOG.info("插件 %s 卸载，移除 %d 个 skill", plugin_name, len(names))
        return len(names)

    def set_enabled(self, name: str, enabled: bool) -> SkillSpec:
        spec = self.resolve(name)
        spec.enabled = bool(enabled)
        return spec

    # ------------------------------------------------------------------
    # 查询 / 渐进展示
    # ------------------------------------------------------------------

    def resolve(self, name: str) -> SkillSpec:
        key = (name or "").strip()
        spec = self._skills.get(key)
        if spec is None:
            target = self._aliases.get(key)
            spec = self._skills.get(target) if target else None
        if spec is None:
            raise SkillNotFound(name)
        return spec

    def get(self, name: str) -> Optional[SkillSpec]:
        try:
            return self.resolve(name)
        except SkillNotFound:
            return None

    def find_by_tool_name(self, tool_name: str) -> Optional[SkillSpec]:
        for spec in self._skills.values():
            if spec.tool_name == tool_name:
                return spec
        return self.get(tool_name)

    def all(self) -> list[SkillSpec]:
        return sorted(self._skills.values(), key=lambda s: s.name)

    def catalog(
        self,
        *,
        query: str = "",
        tag: str = "",
        plugin: str = "",
        limit: int = 40,
    ) -> list[dict[str, Any]]:
        """第一层展示：只给名字、一行摘要、标签，不展开 schema。"""
        q = query.lower().strip()
        out = []
        for spec in self.all():
            if not spec.enabled:
                continue
            if tag and tag not in spec.tags:
                continue
            if plugin and plugin != spec.plugin:
                continue
            haystack = f"{spec.name} {spec.summary} {' '.join(spec.tags)} {spec.plugin}".lower()
            if q and q not in haystack:
                continue
            out.append(spec.catalog_entry())
            if len(out) >= limit:
                break
        return out

    def describe(self, names: Iterable[str]) -> list[dict[str, Any]]:
        """第二层展示：完整参数 schema。"""
        out = []
        for name in names:
            spec = self.get(name)
            if spec is not None:
                out.append(spec.describe_entry())
        return out

    def tool_schemas(
        self,
        *,
        names: Iterable[str] | None = None,
        scopes: Iterable[str] = ("user",),
        max_count: int = 40,
    ) -> list[dict[str, Any]]:
        """OpenAI tools 格式，可在需要时直接全部注册。"""
        allowed = set(scopes)
        schemas = []
        for spec in self.all():
            if not spec.enabled or not spec.mcp_exposed:
                continue
            if names is not None and spec.name not in names:
                continue
            if not (set(spec.scopes) & allowed or "owner" in allowed):
                continue
            schemas.append(spec.tool_schema())
            if len(schemas) >= max_count:
                break
        return schemas

    def stats(self, name: str | None = None) -> dict[str, Any]:
        if name:
            return {name: dict(self._stats.get(name, {}))}
        return {k: dict(v) for k, v in self._stats.items()}

    def audit(self, limit: int = 100) -> list[dict[str, Any]]:
        items = list(self._audit)[-limit:]
        return [item.as_dict() for item in reversed(items)]

    # ------------------------------------------------------------------
    # 调用
    # ------------------------------------------------------------------

    async def invoke(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        ctx: SkillContext | None = None,
        validate: bool = True,
    ) -> SkillResult:
        started = time.perf_counter()
        ctx = ctx or SkillContext()
        spec: SkillSpec | None = None
        record = SkillCallRecord(
            skill=name, run_id=ctx.run_id, session_key=ctx.session_key, user_id=ctx.user_id
        )
        try:
            spec = self.resolve(name)
            record.plugin = spec.plugin
            if not spec.enabled:
                raise SkillDenied(f"skill 已停用：{spec.name}")
            if not any(ctx.has_role(role) for role in spec.scopes):
                raise SkillDenied(
                    f"权限不足：{spec.name} 需要 {'/'.join(spec.scopes)}"
                )
            ok, reason = ctx.allows(spec.requires)
            if not ok:
                raise SkillDenied(f"{spec.name}: {reason}")

            args = dict(arguments or {})
            if validate:
                args = _validate_args(spec, args)

            lock = self._locks.setdefault(spec.name, asyncio.Lock())
            async with lock:
                result = await asyncio.wait_for(
                    _call_handler(spec.handler, ctx, args), timeout=spec.timeout
                )
            normalized = _normalize_result(result)
            record.ok = normalized.ok
            record.error = normalized.error
            record.ms = int((time.perf_counter() - started) * 1000)
            self._bump(spec.name, record)
            return normalized
        except asyncio.TimeoutError:
            msg = f"skill 超时（>{getattr(spec, 'timeout', 60)}s）：{name}"
            record.ok = False
            record.error = msg
            record.ms = int((time.perf_counter() - started) * 1000)
            self._bump(getattr(spec, "name", name), record)
            return SkillResult.fail(msg)
        except SkillError as exc:
            record.ok = False
            record.error = exc.message
            record.ms = int((time.perf_counter() - started) * 1000)
            self._bump(getattr(spec, "name", name), record)
            return SkillResult.fail(exc.message, code=exc.code)
        except Exception as exc:
            LOG.exception("skill 调用异常：%s", name)
            msg = f"{type(exc).__name__}: {exc}"
            record.ok = False
            record.error = msg
            record.ms = int((time.perf_counter() - started) * 1000)
            self._bump(getattr(spec, "name", name), record)
            return SkillResult.fail(msg)
        finally:
            self._audit.append(record)

    def _bump(self, name: str, record: SkillCallRecord) -> None:
        stat = self._stats.setdefault(
            name,
            {"calls": 0, "errors": 0, "total_ms": 0, "last_called_at": None, "last_error": ""},
        )
        stat["calls"] += 1
        if not record.ok:
            stat["errors"] += 1
            stat["last_error"] = record.error
        stat["total_ms"] += record.ms
        stat["last_called_at"] = record.ts


def _call_handler(handler: Callable, ctx: SkillContext, args: dict[str, Any]) -> Any:
    """同时支持 (ctx, **kwargs) 和 (**kwargs) 两种写法。"""
    try:
        sig = inspect.signature(handler)
    except (TypeError, ValueError):
        return handler(ctx=ctx, **args)
    params = sig.parameters
    if "ctx" in params:
        return handler(ctx=ctx, **args)
    return handler(**args)


def _normalize_result(value: Any) -> SkillResult:
    if isinstance(value, SkillResult):
        return value
    if value is None:
        return SkillResult(ok=True)
    if isinstance(value, str):
        return SkillResult(ok=True, content=value)
    if isinstance(value, dict):
        content = value.get("content")
        if isinstance(content, str):
            return SkillResult(
                ok=bool(value.get("ok", True)),
                content=content,
                data=value.get("data"),
                artifacts=list(value.get("artifacts") or []),
                error=str(value.get("error") or ""),
                meta=dict(value.get("meta") or {}),
            )
        return SkillResult(ok=True, content="", data=value)
    if isinstance(value, (list, tuple)):
        return SkillResult(ok=True, data=list(value))
    return SkillResult(ok=True, content=str(value))


def _validate_args(spec: SkillSpec, args: dict[str, Any]) -> dict[str, Any]:
    """轻量 JSON Schema 校验：必填、基础类型、枚举；未知参数直接报错。"""
    properties: dict[str, Any] = (spec.parameters or {}).get("properties") or {}
    required = spec.required or (spec.parameters or {}).get("required") or []
    missing = [key for key in required if key not in args or args[key] is None]
    if missing:
        raise SkillError(
            f"{spec.name} 缺少参数：{', '.join(missing)}", code="invalid_args"
        )
    cleaned = dict(args)
    for key, value in list(cleaned.items()):
        prop = properties.get(key)
        if prop is None:
            raise SkillError(
                f"{spec.name} 不接受参数 {key}；可用参数：{', '.join(properties) or '无'}",
                code="invalid_args",
            )
        expected = prop.get("type")
        if value is None:
            continue
        if expected == "integer" and isinstance(value, bool):
            raise SkillError(f"参数 {key} 应为整数", code="invalid_args")
        if expected == "integer" and not isinstance(value, int):
            try:
                cleaned[key] = int(value)
            except (TypeError, ValueError):
                raise SkillError(f"参数 {key} 应为整数", code="invalid_args") from None
        elif expected == "number" and not isinstance(value, (int, float)):
            try:
                cleaned[key] = float(value)
            except (TypeError, ValueError):
                raise SkillError(f"参数 {key} 应为数字", code="invalid_args") from None
        elif expected == "boolean" and not isinstance(value, bool):
            raise SkillError(f"参数 {key} 应为布尔值", code="invalid_args")
        elif expected == "array" and not isinstance(value, list):
            raise SkillError(f"参数 {key} 应为数组", code="invalid_args")
        elif expected == "object" and not isinstance(value, dict):
            raise SkillError(f"参数 {key} 应为对象", code="invalid_args")
        elif expected == "string" and not isinstance(value, str):
            cleaned[key] = str(value)
        if prop.get("enum") is not None and cleaned.get(key) not in prop["enum"]:
            raise SkillError(
                f"参数 {key} 只能是：{', '.join(map(str, prop['enum']))}",
                code="invalid_args",
            )
    return cleaned
