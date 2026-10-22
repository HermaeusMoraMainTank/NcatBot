"""通用管理后台：插件配置、技能目录、Agent 运行、审计与 MCP 入口。

只绑定本机回环；Bearer / Cookie 令牌认证；写操作只收 JSON。
"""

from __future__ import annotations

import asyncio
import hmac
import json
import secrets
import time
from collections import deque
from pathlib import Path
from typing import Any, Optional

from aiohttp import web

from ncatbot.sandbox import mask_mapping, merge_keep_secrets, unmask_guard
from ncatbot.utils import get_log

LOG = get_log("AdminServer")

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class AdminServer:
    def __init__(self, bot: Any) -> None:
        self.bot = bot
        config = getattr(bot, "config", None)
        admin = getattr(config, "admin", None)
        self.enabled = bool(getattr(admin, "enabled", True))
        self.host = str(getattr(admin, "host", "127.0.0.1") or "127.0.0.1")
        self.port = int(getattr(admin, "port", 8770) or 8770)
        self.allow_remote = bool(getattr(admin, "allow_remote", False))
        self._token_override = str(getattr(admin, "token", "") or "")
        self.token = ""
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self.started_at = time.time()
        self.actions: deque[dict[str, Any]] = deque(maxlen=1000)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    @property
    def url(self) -> str:
        port = self.port
        if self._site is not None and getattr(self._site, "_server", None):
            sockets = self._site._server.sockets
            if sockets:
                port = int(sockets[0].getsockname()[1])
        host = "127.0.0.1" if self.host in ("0.0.0.0", "::") else self.host
        return f"http://{host}:{port}"

    async def start(self) -> None:
        if self._runner is not None:
            return
        if not self.enabled:
            LOG.info("管理后台未启用（admin.enabled=false）")
            return
        if self.host not in _LOOPBACK and not self.allow_remote:
            LOG.warning(
                "admin.host=%s 不是回环地址，已强制改为 127.0.0.1；"
                "如确需远程访问请设置 admin.allow_remote=true",
                self.host,
            )
            self.host = "127.0.0.1"

        self.token = self._load_token()
        app = self._create_app()
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            site = web.TCPSite(runner, self.host, self.port)
            await site.start()
        except Exception:
            await runner.cleanup()
            raise
        self._runner = runner
        self._site = site
        LOG.warning("管理后台已启动：%s/?token=%s", self.url, self.token)

    async def stop(self) -> None:
        if self._runner is None:
            return
        await self._runner.cleanup()
        self._runner = None
        self._site = None

    def _load_token(self) -> str:
        if self._token_override.strip():
            return self._token_override.strip()
        token_file = Path("data/admin/token.txt")
        try:
            if token_file.is_file():
                value = token_file.read_text(encoding="utf-8").strip()
                if len(value) >= 16:
                    return value
            token = secrets.token_hex(20)
            token_file.parent.mkdir(parents=True, exist_ok=True)
            token_file.write_text(token, encoding="utf-8")
            try:
                token_file.chmod(0o600)
            except OSError:
                pass
            return token
        except OSError as exc:
            LOG.error("读取/写入管理令牌失败，使用进程内临时令牌：%s", exc)
            return secrets.token_hex(20)

    # ------------------------------------------------------------------
    # App
    # ------------------------------------------------------------------

    def _create_app(self) -> web.Application:
        app = web.Application(client_max_size=4 * 1024 * 1024)
        app.router.add_get("/api/overview", self._overview)
        app.router.add_get("/api/plugins", self._plugins)
        app.router.add_get("/api/plugins/{name}", self._plugin)
        app.router.add_put("/api/plugins/{name}/config", self._plugin_config)
        app.router.add_post("/api/plugins/{name}/reload", self._plugin_reload)
        app.router.add_get("/api/skills", self._skills)
        app.router.add_get("/api/skills/{name}", self._skill)
        app.router.add_put("/api/skills/{name}", self._skill_update)
        app.router.add_post("/api/skills/{name}/invoke", self._skill_invoke)
        app.router.add_get("/api/runs", self._runs)
        app.router.add_get("/api/runs/{run_id}", self._run_detail)
        app.router.add_post("/api/runs/{run_id}/cancel", self._run_cancel)
        app.router.add_post("/api/agent/run", self._agent_run)
        app.router.add_get("/api/audit", self._audit)
        app.router.add_get("/api/sandbox/workspaces", self._sandboxes)
        app.router.add_post("/api/sandbox/cleanup", self._sandbox_cleanup)
        app.router.add_get("/api/config", self._config)
        app.router.add_post("/mcp", self._mcp)
        app.router.add_get("/", self._index)
        app.router.add_get("/{path:.*}", self._index)
        return app

    # ------------------------------------------------------------------
    # Auth / helpers
    # ------------------------------------------------------------------

    def _authorized(self, request: web.Request) -> bool:
        header = request.headers.get("Authorization", "")
        scheme, _, candidate = header.partition(" ")
        if scheme.lower() == "bearer" and candidate:
            if hmac.compare_digest(candidate, self.token):
                return True
        cookie = request.cookies.get("ncatbot_admin", "")
        return bool(cookie) and hmac.compare_digest(cookie, self.token)

    async def _guard(self, request: web.Request) -> Optional[web.Response]:
        if self._authorized(request):
            return None
        return web.json_response({"ok": False, "error": "未授权"}, status=401)

    @staticmethod
    def _dumps(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)

    @classmethod
    def _ok(cls, data: Any) -> web.Response:
        return web.json_response({"ok": True, "data": data}, dumps=cls._dumps)

    @classmethod
    def _err(cls, message: str, status: int = 400) -> web.Response:
        return web.json_response(
            {"ok": False, "error": message}, status=status, dumps=cls._dumps
        )

    @staticmethod
    async def _body(request: web.Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError("请求体必须是 JSON") from exc
        if not isinstance(data, dict):
            raise TypeError("请求体必须是 JSON 对象")
        return data

    def _record(self, request: web.Request, action: str, detail: Any = None) -> None:
        self._record_plain(action, detail, operator=request.remote or "local")

    def _record_plain(self, action: str, detail: Any = None, *, operator: str = "system") -> None:
        self.actions.append(
            {
                "ts": time.time(),
                "action": action,
                "operator": operator,
                "detail": detail,
            }
        )

    async def _run(
        self, request: web.Request, callback: Any, *, method: str = ""
    ) -> web.Response:
        denied = await self._guard(request)
        if denied is not None:
            return denied
        try:
            result = callback()
            if asyncio.iscoroutine(result):
                result = await result
            if method:
                self._record(request, method)
            return self._ok(result)
        except KeyError as exc:
            return self._err(f"找不到对象：{exc}", 404)
        except (TypeError, ValueError) as exc:
            return self._err(str(exc), 400)
        except PermissionError as exc:
            return self._err(str(exc), 403)
        except Exception:
            LOG.exception("管理请求失败：%s %s", request.method, request.path)
            return self._err("服务器内部错误", 500)

    # ------------------------------------------------------------------
    # 页面
    # ------------------------------------------------------------------

    async def _index(self, request: web.Request) -> web.StreamResponse:
        if request.path.startswith("/api/") or request.path == "/mcp":
            return self._err("接口不存在", 404)
        query_token = request.query.get("token", "")
        if query_token and hmac.compare_digest(query_token, self.token):
            response = web.FileResponse(self._index_path())
            response.set_cookie(
                "ncatbot_admin",
                self.token,
                httponly=True,
                samesite="Strict",
                max_age=30 * 86400,
            )
            return response
        if not self._authorized(request):
            # 未登录也返回页面，由前端弹出令牌输入框
            return web.FileResponse(self._index_path())
        return web.FileResponse(self._index_path())

    @staticmethod
    def _index_path() -> Path:
        return Path(__file__).resolve().parent / "static" / "index.html"

    # ------------------------------------------------------------------
    # Overview / plugins
    # ------------------------------------------------------------------

    async def _overview(self, request: web.Request) -> web.Response:
        def build() -> dict[str, Any]:
            loader = getattr(self.bot, "plugin_loader", None)
            skills = self.bot.skills
            agents = self.bot.agents
            loaded = list(getattr(loader, "plugins", {}) or {})
            indexed = {}
            if loader is not None:
                try:
                    indexed = loader.list_indexed()
                except Exception:
                    indexed = {}
            runs = agents.list_runs(limit=200)
            sandbox = agents.runner.describe()
            sandbox["root"] = str(agents.workspaces.root)
            sandbox["backend"] = sandbox.get("effective_backend", "disabled")
            return {
                "bot_uin": str(getattr(self.bot.config, "bot_uin", "")),
                "uptime_seconds": int(time.time() - self.started_at),
                "platforms": list(getattr(self.bot.api, "platforms", {}) or {}),
                "plugins": {"loaded": len(loaded), "indexed": len(indexed)},
                "skills": {
                    "total": len(skills.all()),
                    "enabled": sum(1 for s in skills.all() if s.enabled),
                    "calls": sum(v.get("calls", 0) for v in skills.stats().values()),
                    "errors": sum(v.get("errors", 0) for v in skills.stats().values()),
                },
                "runs": {
                    "total": len(runs),
                    "active": sum(
                        1 for r in runs if r["status"] in ("pending", "running")
                    ),
                },
                "sandbox": sandbox,
                "admin": {"host": self.host, "port": self.port},
                "plugins_loaded": loaded,
            }

        return await self._run(request, build)

    async def _plugins(self, request: web.Request) -> web.Response:
        def build() -> list[dict[str, Any]]:
            loader = self.bot.plugin_loader
            indexed = loader.list_indexed()
            loaded = {name: True for name in loader.plugins}
            rows = []
            for name, manifest in indexed.items():
                plugin = loader.plugins.get(name)
                rows.append(
                    {
                        "name": name,
                        "version": manifest.version,
                        "author": manifest.author,
                        "description": manifest.description,
                        "folder": manifest.folder_name,
                        "dependencies": manifest.dependencies,
                        "loaded": bool(loaded.get(name)),
                        "config_keys": sorted(getattr(plugin, "config", {}) or {})
                        if plugin
                        else [],
                        "skill_count": sum(
                            1 for s in self.bot.skills.all() if s.plugin == name
                        ),
                    }
                )
            for name in loader.plugins:
                if name not in indexed:
                    plugin = loader.plugins[name]
                    rows.append(
                        {
                            "name": name,
                            "version": getattr(plugin, "version", ""),
                            "author": getattr(plugin, "author", ""),
                            "description": getattr(plugin, "description", ""),
                            "folder": "",
                            "dependencies": getattr(plugin, "dependencies", {}),
                            "loaded": True,
                            "config_keys": sorted(getattr(plugin, "config", {}) or {}),
                            "skill_count": sum(
                                1 for s in self.bot.skills.all() if s.plugin == name
                            ),
                        }
                    )
            return sorted(rows, key=lambda r: r["name"].lower())

        return await self._run(request, build)

    async def _plugin(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]

        def build() -> dict[str, Any]:
            loader = self.bot.plugin_loader
            plugin = loader.plugins.get(name)
            manifest = loader.list_indexed().get(name)
            if plugin is None and manifest is None:
                raise KeyError(name)
            config = {}
            if plugin is not None:
                config = mask_mapping(dict(getattr(plugin, "config", {}) or {}))
            elif manifest is not None:
                path = manifest.plugin_path / "config.yaml"
                if path.is_file():
                    import yaml

                    config = mask_mapping(yaml.safe_load(path.read_text("utf-8")) or {})
            return {
                "name": name,
                "loaded": plugin is not None,
                "version": getattr(plugin, "version", "")
                or (manifest.version if manifest else ""),
                "description": getattr(plugin, "description", "")
                or (manifest.description if manifest else ""),
                "config": config,
                "skills": [
                    s.describe_entry()
                    for s in self.bot.skills.all()
                    if s.plugin == name
                ],
            }

        return await self._run(request, build)

    async def _plugin_config(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]

        async def action() -> dict[str, Any]:
            plugin = self.bot.plugin_loader.plugins.get(name)
            if plugin is None:
                raise KeyError(name)
            body = await self._body(request)
            updates = body.get("config")
            if updates is None:
                key = body.get("key")
                if not key:
                    raise ValueError("缺少 config 或 key")
                updates = {str(key): body.get("value")}
            if not isinstance(updates, dict):
                raise TypeError("config 必须是对象")
            cleaned = unmask_guard(updates)
            merged = merge_keep_secrets(dict(getattr(plugin, "config", {}) or {}), updates)
            for key in cleaned:
                plugin.set_config(str(key), merged.get(key))
            self._record(request, "plugin.config", {"plugin": name, "keys": list(cleaned)})
            return {
                "plugin": name,
                "updated": sorted(cleaned),
                "config": mask_mapping(dict(getattr(plugin, "config", {}) or {})),
            }

        return await self._run(request, action)

    async def _plugin_reload(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]

        async def action() -> dict[str, Any]:
            loader = self.bot.plugin_loader
            if name not in loader.plugins:
                raise KeyError(name)
            self.bot.skills.unregister_plugin(name)
            ok = await loader.reload_plugin(name)
            plugin = loader.plugins.get(name)
            if plugin is not None:
                self.bot.skills.discover_plugin(plugin)
            self._record(request, "plugin.reload", {"plugin": name})
            return {"plugin": name, "ok": bool(ok)}

        return await self._run(request, action)

    # ------------------------------------------------------------------
    # Skills
    # ------------------------------------------------------------------

    async def _skills(self, request: web.Request) -> web.Response:
        def build() -> list[dict[str, Any]]:
            entries = self.bot.skills.catalog(
                query=request.query.get("q", ""),
                tag=request.query.get("tag", ""),
                plugin=request.query.get("plugin", ""),
                limit=200,
            )
            stats = self.bot.skills.stats()
            for entry in entries:
                stat = dict(stats.get(entry["name"], {}))
                calls = int(stat.get("calls", 0) or 0)
                stat["avg_ms"] = round(int(stat.get("total_ms", 0) or 0) / calls) if calls else 0
                entry.update(stat)
                spec = self.bot.skills.get(entry["name"])
                entry["enabled"] = bool(spec and spec.enabled)
            return entries

        return await self._run(request, build)

    async def _skill(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]

        def build() -> dict[str, Any]:
            spec = self.bot.skills.get(name)
            if spec is None:
                raise KeyError(name)
            data = spec.describe_entry()
            data["enabled"] = spec.enabled
            stat = dict(self.bot.skills.stats(name).get(spec.name, {}))
            calls = int(stat.get("calls", 0) or 0)
            stat["avg_ms"] = round(int(stat.get("total_ms", 0) or 0) / calls) if calls else 0
            data["stats"] = stat
            return data

        return await self._run(request, build)

    async def _skill_update(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]

        async def action() -> dict[str, Any]:
            body = await self._body(request)
            spec = self.bot.skills.set_enabled(name, bool(body.get("enabled", True)))
            self._record(request, "skill.toggle", {"skill": spec.name, "enabled": spec.enabled})
            return {"name": spec.name, "enabled": spec.enabled}

        return await self._run(request, action)

    async def _skill_invoke(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]

        async def action() -> dict[str, Any]:
            body = await self._body(request)
            arguments = body.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise TypeError("arguments 必须是对象")
            from ncatbot.skill import SkillContext

            capabilities = {"network", "fs.read", "fs.write", "llm", "media", "event"}
            if getattr(self.bot.agents.runner, "allow_subprocess", False):
                capabilities.add("subprocess")
            ctx = SkillContext(
                session_key=str(body.get("session_key") or "admin"),
                run_id="admin",
                roles={"owner"},
                user_id=str(body.get("user_id") or ""),
                group_id=str(body.get("group_id") or ""),
                capabilities=capabilities,
            )
            result = await self.bot.skills.invoke(name, arguments, ctx=ctx)
            self._record(request, "skill.invoke", {"skill": name, "ok": result.ok})
            return {
                "ok": result.ok,
                "content": result.content,
                "error": result.error,
                "data": result.data,
                "artifacts": result.artifacts,
            }

        return await self._run(request, action)

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    async def _runs(self, request: web.Request) -> web.Response:
        limit = max(1, min(200, int(request.query.get("limit", 50) or 50)))
        return await self._run(request, lambda: self.bot.agents.list_runs(limit=limit))

    async def _run_detail(self, request: web.Request) -> web.Response:
        denied = await self._guard(request)
        if denied is not None:
            return denied
        run_id = request.match_info["run_id"]
        run = self.bot.agents.get_run(run_id)
        if run is None:
            return self._err("找不到该运行记录", 404)
        return self._ok(run.as_dict())

    async def _run_cancel(self, request: web.Request) -> web.Response:
        run_id = request.match_info["run_id"]

        def action() -> dict[str, Any]:
            ok = self.bot.agents.cancel(run_id)
            self._record(request, "run.cancel", {"run": run_id, "ok": ok})
            return {"run": run_id, "cancelled": ok}

        return await self._run(request, action)

    async def _agent_run(self, request: web.Request) -> web.Response:
        async def action() -> dict[str, Any]:
            body = await self._body(request)
            task = str(body.get("task") or "").strip()
            if not task:
                raise ValueError("缺少 task")
            from ncatbot.agent import AgentContext

            session_key = str(body.get("session_key") or "admin")
            capabilities = {"network", "fs.read", "fs.write", "llm", "media", "event"}
            if getattr(self.bot.agents.runner, "allow_subprocess", False):
                capabilities.add("subprocess")
            ctx = AgentContext(
                session_key=session_key,
                user_id=str(body.get("user_id") or ""),
                group_id=str(body.get("group_id") or ""),
                roles={"owner"},
                capabilities=capabilities,
            )
            run = await self.bot.agents.run(
                task,
                session_key=session_key,
                ctx=ctx,
                max_rounds=int(body.get("max_rounds") or self.bot.agents.max_rounds),
            )
            self._record(request, "agent.run", {"task": task[:120], "run": run.id})
            return run.as_dict()

        return await self._run(request, action)

    # ------------------------------------------------------------------
    # Audit / sandbox / config
    # ------------------------------------------------------------------

    async def _audit(self, request: web.Request) -> web.Response:
        limit = max(1, min(500, int(request.query.get("limit", 100) or 100)))

        def build() -> dict[str, Any]:
            skills = self.bot.skills.audit(limit=limit)
            actions = list(self.actions)[-limit:]
            return {"skills": skills, "actions": list(reversed(actions))}

        return await self._run(request, build)

    async def _sandboxes(self, request: web.Request) -> web.Response:
        return await self._run(request, lambda: self.bot.agents.workspaces.list_workspaces())

    async def _sandbox_cleanup(self, request: web.Request) -> web.Response:
        async def action() -> dict[str, Any]:
            body = await self._body(request)
            days = float(body.get("max_age_days", 7) or 7)
            removed = self.bot.agents.workspaces.cleanup(max_age_seconds=days * 86400)
            self._record(request, "sandbox.cleanup", {"removed": removed})
            return {"removed": removed}

        return await self._run(request, action)

    async def _config(self, request: web.Request) -> web.Response:
        def build() -> dict[str, Any]:
            config = getattr(self.bot, "config", None)
            if config is None:
                return {}
            try:
                data = config.to_dict()
            except Exception:
                data = {}
            return mask_mapping(data)

        return await self._run(request, build)

    # ------------------------------------------------------------------
    # MCP over HTTP（JSON-RPC 2.0）
    # ------------------------------------------------------------------

    async def _mcp(self, request: web.Request) -> web.Response:
        denied = await self._guard(request)
        if denied is not None:
            return denied
        try:
            payload = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
                status=200,
            )
        if isinstance(payload, list):
            results = [await self._mcp_one(item) for item in payload]
            return web.json_response(results)
        return web.json_response(await self._mcp_one(payload))

    async def _mcp_one(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = payload.get("id")
        method = str(payload.get("method") or "")
        params = payload.get("params") or {}

        def error(code: int, message: str) -> dict[str, Any]:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "ncatbot-skill-hub", "version": "1.0.0"},
                },
            }
        if method in ("notifications/initialized", "ping"):
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}
        if method == "tools/list":
            tools = []
            for spec in self.bot.skills.all():
                if not spec.enabled or not spec.mcp_exposed:
                    continue
                tools.append(
                    {
                        "name": spec.tool_name,
                        "description": spec.description or spec.summary,
                        "inputSchema": spec.parameters,
                        "annotations": {"title": spec.summary},
                        "x-skill-name": spec.name,
                    }
                )
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}}
        if method == "tools/call":
            name = str(params.get("name") or "")
            spec = self.bot.skills.find_by_tool_name(name)
            if spec is None:
                return error(-32602, f"Unknown tool: {name}")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                return error(-32602, "arguments 必须是对象")
            from ncatbot.skill import SkillContext

            ctx = SkillContext(
                session_key="mcp",
                run_id="mcp",
                roles={"owner"},
                capabilities={"network", "fs.read", "fs.write", "llm", "media"},
            )
            result = await self.bot.skills.invoke(spec.name, arguments, ctx=ctx)
            self._record_plain("mcp.call", {"skill": spec.name, "ok": result.ok}, operator="mcp")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": result.to_content()}],
                    "isError": not result.ok,
                },
            }
        return error(-32601, f"Method not found: {method}")
