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

from .config_meta import build_fields

LOG = get_log("AdminServer")

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}

# FakeAi 每轮留档目录（JSONL，按天分文件）
_AI_TRACE_DIR = Path("data/FakeAi/traces")


def _read_ai_traces(limit: int = 40, group: str = "") -> list[dict[str, Any]]:
    """读取最近的 AI 留档，新的在前；可按群号过滤。"""
    if not _AI_TRACE_DIR.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(_AI_TRACE_DIR.glob("*.jsonl"), reverse=True):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if group and str(item.get("group_id")) != group:
                continue
            items.append(item)
            if len(items) >= limit:
                return items
    return items


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
        app.router.add_get("/api/ai/traces", self._ai_traces)
        app.router.add_get("/api/sandbox/workspaces", self._sandboxes)
        app.router.add_post("/api/sandbox/cleanup", self._sandbox_cleanup)
        app.router.add_get("/api/config", self._config)
        app.router.add_post("/api/auth", self._auth)
        # McFish 钓鱼面板（并入统一后台）
        app.router.add_get("/api/mcfish/session", self._mcfish_session)
        app.router.add_get("/api/mcfish/groups", self._mcfish_groups)
        app.router.add_get(
            "/api/mcfish/groups/{group_id}/players", self._mcfish_group_players
        )
        app.router.add_get("/api/mcfish/players/{user_id}", self._mcfish_player)
        app.router.add_get("/api/mcfish/items", self._mcfish_items)
        app.router.add_post(
            "/api/mcfish/players/{user_id}/grant", self._mcfish_grant
        )
        app.router.add_post(
            "/api/mcfish/players/{user_id}/balance", self._mcfish_balance
        )
        app.router.add_get("/api/mcfish/shop/today", self._mcfish_shop)
        app.router.add_put(
            "/api/mcfish/shop/today/listings", self._mcfish_listing
        )
        app.router.add_put(
            "/api/mcfish/shop/today/discounts", self._mcfish_discounts
        )
        app.router.add_get("/api/mcfish/audit", self._mcfish_audit)
        app.router.add_get("/mcfish-assets/{item_name}", self._mcfish_item_asset)
        app.router.add_get("/mcfish-avatar/{kind}/{avatar_id}", self._mcfish_avatar)
        app.router.add_get("/mcfish-panel", self._mcfish_panel)
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

    async def _auth(self, request: web.Request) -> web.Response:
        """校验令牌并写入 cookie：iframe、图片等无法带 header 的请求靠它。"""
        try:
            body = await self._body(request)
        except (TypeError, ValueError) as exc:
            return self._err(str(exc), 400)
        token = str(body.get("token") or "")
        if not token or not hmac.compare_digest(token, self.token):
            return self._err("令牌无效", 401)
        response = self._ok({"ok": True})
        response.set_cookie(
            "ncatbot_admin",
            self.token,
            httponly=True,
            samesite="Strict",
            max_age=30 * 86400,
        )
        return response

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
        started = time.perf_counter()
        LOG.debug("admin start %s %s", request.method, request.path)
        try:
            result = callback()
            if asyncio.iscoroutine(result):
                result = await result
            if method:
                self._record(request, method)
            elapsed = (time.perf_counter() - started) * 1000
            if elapsed >= 300:
                LOG.warning(
                    "管理接口较慢：%s %s %.0fms",
                    request.method,
                    request.path,
                    elapsed,
                )
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
            response.headers["Cache-Control"] = "no-store"
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
            response = web.FileResponse(self._index_path())
            response.headers["Cache-Control"] = "no-store"
            return response
        response = web.FileResponse(self._index_path())
        response.headers["Cache-Control"] = "no-store"
        return response

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

    @staticmethod
    def _config_dict(plugin: Any) -> dict[str, Any]:
        """安全读取插件配置，兼容只有 __getitem__ 的 ConfigWrapper。

        不能直接 dict()/sorted() 自定义 mapping：如果它没有 __iter__，
        Python 会从 0 开始不断调用 __getitem__，永远等不到 IndexError。
        """
        config = getattr(plugin, "config", {}) or {}
        if isinstance(config, dict):
            return dict(config)
        inner = getattr(config, "_config", None)
        if isinstance(inner, dict):
            return dict(inner)
        try:
            keys = config.keys()
        except Exception:
            return {}
        return {str(key): config[key] for key in keys}

    @classmethod
    def _config_keys(cls, plugin: Any) -> list[str]:
        return sorted(cls._config_dict(plugin))

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
                        "config_keys": self._config_keys(plugin)
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
                            "config_keys": self._config_keys(plugin),
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
                config = mask_mapping(self._config_dict(plugin))
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
                "fields": build_fields(
                    config,
                    labels=getattr(plugin, "config_labels", None) if plugin else None,
                    choices=getattr(plugin, "config_choices", None) if plugin else None,
                    descriptions=getattr(plugin, "config_descriptions", None)
                    if plugin
                    else None,
                    pinned=getattr(plugin, "config_pinned", None) if plugin else None,
                ),
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
            merged = merge_keep_secrets(self._config_dict(plugin), updates)
            for key in cleaned:
                plugin.set_config(str(key), merged.get(key))
            applier = getattr(plugin, "apply_config", None)
            if callable(applier):
                try:
                    applier()
                except Exception as exc:
                    LOG.warning("插件 %s 应用新配置失败：%s", name, exc)
            self._record(request, "plugin.config", {"plugin": name, "keys": list(cleaned)})
            return {
                "plugin": name,
                "updated": sorted(cleaned),
                "config": mask_mapping(self._config_dict(plugin)),
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
    # McFish 钓鱼面板（并入统一后台，不再单独开端口）
    # ------------------------------------------------------------------

    def _mcfish_service(self) -> Any:
        plugin = self.bot.plugin_loader.plugins.get("McFish")
        service = getattr(plugin, "admin_service", None) if plugin else None
        if service is None:
            raise KeyError("McFish 未加载或未启用管理面板")
        if not bool(plugin.get_config("admin_enabled", True)):
            raise KeyError("McFish 管理面板已关闭（admin_enabled=false）")
        return service

    @staticmethod
    def _mcfish_operator(request: web.Request) -> str:
        return f"admin@{request.remote or 'local'}"

    async def _mcfish_session(self, request: web.Request) -> web.Response:
        def build() -> dict[str, Any]:
            self._mcfish_service()
            return {"name": "McFish", "admin_enabled": True}

        return await self._run(request, build)

    async def _mcfish_groups(self, request: web.Request) -> web.Response:
        return await self._run(
            request,
            lambda: self._mcfish_service().groups(request.query.get("search", "")),
        )

    async def _mcfish_group_players(self, request: web.Request) -> web.Response:
        return await self._run(
            request,
            lambda: self._mcfish_service().group_players(
                request.match_info["group_id"], request.query.get("search", "")
            ),
        )

    async def _mcfish_player(self, request: web.Request) -> web.Response:
        return await self._run(
            request, lambda: self._mcfish_service().player(request.match_info["user_id"])
        )

    async def _mcfish_items(self, request: web.Request) -> web.Response:
        return await self._run(request, lambda: self._mcfish_service().grantable_items())

    async def _mcfish_shop(self, request: web.Request) -> web.Response:
        return await self._run(request, lambda: self._mcfish_service().shop_today())

    async def _mcfish_audit(self, request: web.Request) -> web.Response:
        return await self._run(
            request,
            lambda: self._mcfish_service().audit(request.query.get("limit", 100)),
        )

    async def _mcfish_grant(self, request: web.Request) -> web.Response:
        async def action() -> Any:
            body = await self._body(request)
            name = body.get("item") or body.get("name")
            if not name:
                raise ValueError("缺少 item 字段")
            result = await self._mcfish_service().grant_item(
                request.match_info["user_id"],
                str(name),
                int(body.get("quantity", 0)),
                self._mcfish_operator(request),
                rod_attributes=body.get("rod_attributes") or body.get("attributes"),
            )
            self._record(request, "mcfish.grant", {"item": str(name)})
            return result

        return await self._run(request, action)

    async def _mcfish_balance(self, request: web.Request) -> web.Response:
        async def action() -> Any:
            body = await self._body(request)
            mode = body.get("mode")
            amount = body.get("amount")
            if amount is None and body.get("delta") is not None:
                delta = int(body["delta"])
                mode = "grant" if delta > 0 else "deduct"
                amount = abs(delta)
            if not mode or amount is None:
                raise ValueError("缺少金币操作类型或数量")
            result = await self._mcfish_service().adjust_balance(
                request.match_info["user_id"],
                str(mode),
                int(amount),
                self._mcfish_operator(request),
                str(body.get("note", "")),
            )
            self._record(request, "mcfish.balance", {"mode": str(mode), "amount": amount})
            return result

        return await self._run(request, action)

    async def _mcfish_listing(self, request: web.Request) -> web.Response:
        async def action() -> Any:
            body = await self._body(request)
            name = body.get("item") or body.get("name")
            if not name:
                raise ValueError("缺少 item 字段")
            result = self._mcfish_service().set_listing(
                str(name),
                int(body.get("price", 0)),
                int(body.get("stock", 0)),
                self._mcfish_operator(request),
                rod_attributes=body.get("rod_attributes") or body.get("attributes"),
            )
            self._record(request, "mcfish.listing", {"item": str(name)})
            return result

        return await self._run(request, action)

    async def _mcfish_discounts(self, request: web.Request) -> web.Response:
        async def action() -> Any:
            body = await self._body(request)
            updates = body.get("discounts", body)
            if not isinstance(updates, dict):
                raise TypeError("discounts 必须是商品名到倍率的对象")
            result = self._mcfish_service().set_discounts(
                updates, self._mcfish_operator(request)
            )
            self._record(request, "mcfish.discounts", {"count": len(updates)})
            return result

        return await self._run(request, action)

    async def _mcfish_item_asset(self, request: web.Request) -> web.StreamResponse:
        guard = await self._guard(request)
        if guard is not None:
            return guard
        plugin = self.bot.plugin_loader.plugins.get("McFish")
        if plugin is None:
            return self._err("McFish 未加载", 404)
        from urllib.parse import unquote

        name = unquote(request.match_info["item_name"])
        if name not in plugin._grantable_item_names():
            return self._err("物品不存在", 404)
        path = plugin._item_image(name)
        if path is None or not Path(path).is_file():
            return self._err("该物品暂无图标", 404)
        return web.FileResponse(path)

    async def _mcfish_avatar(self, request: web.Request) -> web.StreamResponse:
        guard = await self._guard(request)
        if guard is not None:
            return guard
        from urllib.parse import unquote

        from common.utils.CommonUtil import CommonUtil

        kind = request.match_info["kind"]
        avatar_id = unquote(request.match_info["avatar_id"])
        if kind not in {"user", "group"} or not avatar_id.isdigit():
            return self._err("头像对象不存在", 404)
        getter = CommonUtil.get_avatar if kind == "user" else CommonUtil.get_group_avatar
        path = await asyncio.to_thread(getter, avatar_id)
        if not path or not Path(path).is_file():
            return self._err("头像暂不可用", 404)
        return web.FileResponse(path)

    async def _mcfish_panel(self, request: web.Request) -> web.StreamResponse:
        plugin = self.bot.plugin_loader.plugins.get("McFish")
        panel = None
        if plugin is not None:
            manifest = getattr(plugin, "_manifest", None)
            base = getattr(manifest, "plugin_path", None)
            if base is not None:
                candidate = Path(base) / "admin" / "index.html"
                if candidate.is_file():
                    panel = candidate
        if panel is None:
            candidate = (
                Path(__file__).resolve().parent.parent.parent
                / "plugins"
                / "McFish"
                / "admin"
                / "index.html"
            )
            if candidate.is_file():
                panel = candidate
        if panel is None:
            return self._err("钓鱼面板资源不存在", 503)
        response = web.FileResponse(panel)
        response.headers["Cache-Control"] = "no-store"
        return response

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

    # ------------------------------------------------------------------
    # AI 轨迹（FakeAi 每轮留档）
    # ------------------------------------------------------------------

    async def _ai_traces(self, request: web.Request) -> web.Response:
        limit = max(1, min(200, int(request.query.get("limit", 40) or 40)))
        group = str(request.query.get("group") or "").strip()

        def collect() -> dict[str, Any]:
            return {"items": _read_ai_traces(limit, group)}

        return await self._run(request, collect)

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
            arguments = dict(arguments)
            context = arguments.pop("_context", None) or {}
            if not isinstance(context, dict):
                context = {}
            from ncatbot.skill import SkillContext

            capabilities = {"network", "fs.read", "fs.write", "llm", "media", "event"}
            if getattr(self.bot.agents.runner, "allow_subprocess", False):
                capabilities.add("subprocess")
            ctx = SkillContext(
                session_key=str(context.get("session_key") or "mcp"),
                run_id="mcp",
                roles={"owner"},
                group_id=str(context.get("group_id") or ""),
                user_id=str(context.get("user_id") or ""),
                capabilities=capabilities,
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
