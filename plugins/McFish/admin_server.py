"""Authenticated aiohttp server for the McFish management panel."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from aiohttp import web
from common.utils.CommonUtil import CommonUtil

from .admin_service import McFishAdminService

LOG = logging.getLogger("McFishAdmin")
DEFAULT_ADMIN_TOKEN = "a26407983a"


class McFishAdminServer:
    """Serve the McFish admin UI inside the Bot process."""

    def __init__(self, plugin, *, host: str, port: int, token: str):
        self.plugin = plugin
        self.host = host or "127.0.0.1"
        self.port = int(port)
        self.token = token.strip()
        self.service = McFishAdminService(
            plugin,
            rod_names={"木竿", "铁竿", "金竿", "钻石竿", "下界合金竿", "三叉戟"},
            fixed_price_names={
                "木竿",
                "铁竿",
                "金竿",
                "钻石竿",
                "下界合金竿",
                "魔法鱼饵",
                "隐秘诱饵",
                "万能鱼饵",
            },
        )
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    @property
    def url(self) -> str:
        port = self.port
        if self._site is not None and getattr(self._site, "_server", None):
            sockets = self._site._server.sockets
            if sockets:
                port = int(sockets[0].getsockname()[1])
        return f"http://{self.host}:{port}"

    async def start(self) -> None:
        if self._runner is not None:
            return
        if not self.token:
            self.token = DEFAULT_ADMIN_TOKEN
            self.plugin.set_config("admin_token", self.token)
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
        LOG.warning(
            "McFish 管理界面已启动：%s；管理令牌：%s",
            self.url,
            self.token,
        )

    async def stop(self) -> None:
        if self._runner is None:
            return
        await self._runner.cleanup()
        self._runner = None
        self._site = None

    def _create_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/", self._index)
        app.router.add_get("/api/mcfish/session", self._session)
        app.router.add_get("/api/mcfish/groups", self._groups)
        app.router.add_get("/api/mcfish/groups/{group_id}/players", self._group_players)
        app.router.add_get("/api/mcfish/players/{user_id}", self._player)
        app.router.add_get("/api/mcfish/items", self._items)
        app.router.add_post("/api/mcfish/players/{user_id}/grant", self._grant)
        app.router.add_post("/api/mcfish/players/{user_id}/balance", self._balance)
        app.router.add_put("/api/mcfish/shop/today/listings", self._listing)
        app.router.add_get("/api/mcfish/shop/today", self._shop)
        app.router.add_put("/api/mcfish/shop/today/discounts", self._discounts)
        app.router.add_get("/api/mcfish/audit", self._audit)
        app.router.add_get("/mcfish-assets/{item_name}", self._item_asset)
        app.router.add_get("/mcfish-avatar/{kind}/{avatar_id}", self._avatar)
        app.router.add_get("/{path:.*}", self._index)
        return app

    def _authorized(self, request: web.Request) -> bool:
        value = request.headers.get("Authorization", "")
        scheme, _, candidate = value.partition(" ")
        return (
            scheme.lower() == "bearer"
            and bool(candidate)
            and hmac.compare_digest(candidate, self.token)
        )

    async def _require_auth(self, request: web.Request) -> web.Response | None:
        if self._authorized(request):
            return None
        return web.json_response(
            {"ok": False, "error": "需要有效的 McFish 管理令牌"},
            status=401,
        )

    @staticmethod
    def _operator_id(request: web.Request) -> str:
        return f"webui@{request.remote or 'local'}"

    @staticmethod
    async def _body(request: web.Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError("请求体必须是 JSON") from exc
        if not isinstance(body, dict):
            raise TypeError("请求体必须是 JSON 对象")
        return body

    @staticmethod
    def _ok(data: Any) -> web.Response:
        return web.json_response({"ok": True, "data": data})

    @staticmethod
    def _error(message: str, status: int = 400) -> web.Response:
        return web.json_response({"ok": False, "error": message}, status=status)

    async def _run(
        self,
        request: web.Request,
        callback: Callable[[], Awaitable[Any] | Any],
    ) -> web.Response:
        unauthorized = await self._require_auth(request)
        if unauthorized is not None:
            return unauthorized
        try:
            result = callback()
            if asyncio.iscoroutine(result):
                result = await result
            return self._ok(result)
        except KeyError as exc:
            return self._error(f"找不到对象：{exc}", 404)
        except (TypeError, ValueError) as exc:
            return self._error(str(exc), 400)
        except Exception:
            LOG.exception("McFish 管理请求失败：%s %s", request.method, request.path)
            return self._error("服务器内部错误", 500)

    async def _index(self, request: web.Request) -> web.Response:
        if request.path.startswith("/api/"):
            return self._error("接口不存在", 404)
        path = Path(__file__).parent / "admin" / "index.html"
        if not path.is_file():
            return self._error("管理界面资源不存在", 503)
        return web.FileResponse(path)

    async def _session(self, request: web.Request) -> web.Response:
        return await self._run(
            request,
            lambda: {
                "name": "McFish",
                "url": self.url,
                "admin_enabled": True,
            },
        )

    async def _groups(self, request: web.Request) -> web.Response:
        return await self._run(
            request, lambda: self.service.groups(request.query.get("search", ""))
        )

    async def _group_players(self, request: web.Request) -> web.Response:
        return await self._run(
            request,
            lambda: self.service.group_players(
                request.match_info["group_id"], request.query.get("search", "")
            ),
        )

    async def _player(self, request: web.Request) -> web.Response:
        return await self._run(
            request, lambda: self.service.player(request.match_info["user_id"])
        )

    async def _items(self, request: web.Request) -> web.Response:
        return await self._run(request, self.service.grantable_items)

    async def _item_asset(self, request: web.Request) -> web.Response:
        name = unquote(request.match_info["item_name"])
        if name not in self.plugin._grantable_item_names():
            return self._error("物品不存在", 404)
        path = self.plugin._item_image(name)
        if path is None or not path.is_file():
            return self._error("该物品暂无图标", 404)
        return web.FileResponse(path)

    async def _avatar(self, request: web.Request) -> web.Response:
        kind = request.match_info["kind"]
        avatar_id = unquote(request.match_info["avatar_id"])
        if kind not in {"user", "group"} or not re.fullmatch(r"\d+", avatar_id):
            return self._error("头像对象不存在", 404)
        getter = CommonUtil.get_avatar if kind == "user" else CommonUtil.get_group_avatar
        path = await asyncio.to_thread(getter, avatar_id)
        if not path or not Path(path).is_file():
            return self._error("头像暂不可用", 404)
        return web.FileResponse(path)

    async def _grant(self, request: web.Request) -> web.Response:
        async def action():
            body = await self._body(request)
            name = body.get("item") or body.get("name")
            if not name:
                raise ValueError("缺少 item 字段")
            return await self.service.grant_item(
                request.match_info["user_id"],
                str(name),
                int(body.get("quantity", 0)),
                self._operator_id(request),
                rod_attributes=body.get("rod_attributes") or body.get("attributes"),
            )

        return await self._run(request, action)

    async def _balance(self, request: web.Request) -> web.Response:
        async def action():
            body = await self._body(request)
            mode = body.get("mode")
            amount = body.get("amount")
            if amount is None and body.get("delta") is not None:
                delta = int(body["delta"])
                mode = "grant" if delta > 0 else "deduct"
                amount = abs(delta)
            if not mode or amount is None:
                raise ValueError("缺少金币操作类型或数量")
            return await self.service.adjust_balance(
                request.match_info["user_id"],
                str(mode),
                int(amount),
                self._operator_id(request),
                str(body.get("note", "")),
            )

        return await self._run(request, action)

    async def _shop(self, request: web.Request) -> web.Response:
        return await self._run(request, self.service.shop_today)

    async def _discounts(self, request: web.Request) -> web.Response:
        async def action():
            body = await self._body(request)
            updates = body.get("discounts", body)
            if not isinstance(updates, dict):
                raise TypeError("discounts 必须是商品名到倍率的对象")
            return self.service.set_discounts(updates, self._operator_id(request))

        return await self._run(request, action)

    async def _listing(self, request: web.Request) -> web.Response:
        async def action():
            body = await self._body(request)
            name = body.get("item") or body.get("name")
            if not name:
                raise ValueError("缺少 item 字段")
            return self.service.set_listing(
                str(name),
                int(body.get("price", 0)),
                int(body.get("stock", 0)),
                self._operator_id(request),
                rod_attributes=body.get("rod_attributes") or body.get("attributes"),
            )

        return await self._run(request, action)

    async def _audit(self, request: web.Request) -> web.Response:
        return await self._run(
            request, lambda: self.service.audit(request.query.get("limit", 100))
        )
