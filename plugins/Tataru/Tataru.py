"""NcatBot 塔塔露插件 — 统一 FF14 查询入口。

上游来源（同步更新对照）：
https://github.com/jawwe/astrbot_plugin_tataru
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from ncatbot.core import non_self, registrar
from ncatbot.event.qq import GroupMessageEvent, PrivateMessageEvent
from ncatbot.plugin import NcatBotPlugin
from ncatbot.types import Image, MessageArray, Reply

from .engine import ADMIN_COMMAND_FEATURES, SimpleEvent, feature_enabled
from .service import ReplyPart, TataruService

_log = logging.getLogger("Tataru")

DEFAULT_CONFIG = {
    "debug_mode": False,
    "proxy_enabled": False,
    "proxy_host": "",
    "proxy_port": 0,
    "proxy_username": "",
    "proxy_password": "",
    "use_global_calendar": False,
    "weibo_cookie": "",
    # 沿用原 FF14LogsInfo 内置凭据
    "fflogs_client_id": "9ae4db97-1f1b-4123-82d4-32aff00d3283",
    "fflogs_client_secret": "rAeNnp3Z1VFxYI92TcpBLggKPM3wtkmK9FxZX2C1",
    "use_global_fflogs": False,
    "font_path": "data/font/FZMiaoWuK.TTF",
    "ffxiv_icon_font_path": "",
    "risingstones_checkin_hour": 8,
    "risingstones_owner_curl": "",
}


class Tataru(NcatBotPlugin):
    name = "Tataru"
    version = "1.0.0"

    async def on_load(self):
        self.init_defaults(DEFAULT_CONFIG)
        self._svc = TataruService(dict(self.config))
        # 字体默认：若仓库有 FZMiaoWuK 则写入配置供引擎使用
        if not self._svc.config.get("font_path"):
            candidate = Path("data/font/FZMiaoWuK.TTF")
            if candidate.exists():
                self._svc.config["font_path"] = str(candidate)
        await self._svc.initialize()
        _log.info("Tataru 插件加载完成 v%s", self.version)

    async def on_close(self):
        if getattr(self, "_svc", None):
            await self._svc.terminate()

    def _sync_config(self):
        self._svc.config.update(dict(self.config))
        from .engine import configure_network_settings

        configure_network_settings(self._svc.config)

    def _make_event(self, event: GroupMessageEvent | PrivateMessageEvent) -> SimpleEvent:
        is_private = isinstance(event, PrivateMessageEvent)
        sender_id = str(getattr(event.sender, "user_id", "") or getattr(event, "user_id", ""))
        return SimpleEvent(
            platform_id="qq",
            sender_id=sender_id,
            private=is_private,
            message_str=event.raw_message or "",
        )

    async def _reply_parts(
        self,
        event: GroupMessageEvent | PrivateMessageEvent,
        parts: list[ReplyPart] | None,
    ):
        if not parts:
            return
        is_group = isinstance(event, GroupMessageEvent)
        texts: list[str] = []
        images: list[str] = []
        for part in parts:
            if part.kind == "text" and part.value:
                texts.append(part.value)
            elif part.kind in {"image", "image_url"} and part.value:
                images.append(part.value)

        # 先发文本（长文合并）
        if texts:
            body = "\n".join(texts)
            if is_group:
                await event.reply(text=body, at_sender=False)
            else:
                uid = str(
                    getattr(event, "user_id", None)
                    or getattr(event.sender, "user_id", "")
                )
                await self.api.qq.post_private_msg(user_id=uid, text=body)

        # 再发图片
        for img in images:
            if is_group:
                rtf = MessageArray([Image(file=img), Reply(id=event.message_id)])
                await self.api.qq.post_group_msg(group_id=event.group_id, rtf=rtf)
            else:
                uid = str(
                    getattr(event, "user_id", None)
                    or getattr(event.sender, "user_id", "")
                )
                rtf = MessageArray([Image(file=img)])
                await self.api.qq.post_private_msg(user_id=uid, rtf=rtf)

    async def _run_feature(
        self,
        event: GroupMessageEvent | PrivateMessageEvent,
        command: str,
        handler,
    ):
        self._sync_config()
        feature = ADMIN_COMMAND_FEATURES.get(command)
        if feature and not feature_enabled(self._svc.admin_store, feature):
            await self._reply_parts(
                event, [ReplyPart.text("该功能已在塔塔露功能开关中停用。")]
            )
            return
        try:
            simple = self._make_event(event)
            result = await handler(simple)
            if isinstance(result, list):
                await self._reply_parts(event, result)
        except Exception as exc:
            _log.exception("Tataru 命令 %s 失败: %s", command, exc)
            await self._reply_parts(
                event, [ReplyPart.text(f"处理失败：{type(exc).__name__}")]
            )

    async def _handle_risingstones_player(
        self, event: GroupMessageEvent | PrivateMessageEvent, args: str
    ) -> bool:
        """石之家 玩家 角色名 [服务器] — 保留原 FF14RisingStoneInfo 绘图。"""
        parts = args.split()
        if not parts or parts[0] != "玩家":
            return False
        if len(parts) < 2:
            await self._reply_parts(
                event, [ReplyPart.text("格式：石之家 玩家 角色名 [服务器]")]
            )
            return True

        character_name = parts[1]
        server = parts[2] if len(parts) > 2 else None
        try:
            from FF14RisingStoneInfo.FF14RisingStoneInfo import FF14RisingStoneInfo

            helper = FF14RisingStoneInfo()
            if server:
                server = helper.SERVER_ALIAS.get(server, server)
            cookie = await asyncio.to_thread(helper.read_cookie_from_file)
            if not cookie:
                await self._reply_parts(event, [ReplyPart.text("石之家 Cookie 获取失败，请检查 data/txt/cookie.txt")])
                return True
            players = await asyncio.to_thread(helper.search_player, character_name, cookie)
            if not players:
                await self._reply_parts(event, [ReplyPart.text("找不到该玩家")])
                return True
            if server:
                players = [p for p in players if p.get("group_name") == server]
            if not players:
                await self._reply_parts(event, [ReplyPart.text("未找到匹配的玩家")])
                return True
            if len(players) > 1 and not server:
                listing = "\n".join(
                    f"角色名: {p.get('character_name')} 区服: {p.get('group_name')}"
                    for p in players
                )
                await self._reply_parts(event, [ReplyPart.text("找到多个玩家:\n" + listing)])
                return True
            user_info = await asyncio.to_thread(
                helper.get_user_info, players[0].get("uuid"), cookie
            )
            if not user_info or user_info.get("code") != 10000:
                await self._reply_parts(event, [ReplyPart.text("获取玩家信息失败")])
                return True
            image_path = await asyncio.to_thread(
                helper.generate_image, user_info.get("data", {})
            )
            if not image_path:
                await self._reply_parts(event, [ReplyPart.text("生成玩家卡片失败")])
                return True
            await self._reply_parts(event, [ReplyPart.image(image_path)])
        except Exception as exc:
            _log.exception("石之家玩家查询失败: %s", exc)
            await self._reply_parts(event, [ReplyPart.text(f"玩家查询失败：{exc}")])
        return True

    # —— 群聊 + 私聊命令 —— #

    @non_self
    @registrar.on_message()
    async def dispatch(self, event: GroupMessageEvent | PrivateMessageEvent):
        if not isinstance(event, (GroupMessageEvent, PrivateMessageEvent)):
            return
        raw = (event.raw_message or "").strip()
        if not raw:
            return

        # 命令表：首 token 匹配
        first, _, rest = raw.partition(" ")
        handlers: dict[str, Any] = {
            "帮帮忙": self._svc.help,
            "选门": self._svc.precious,
            "仙人彩": self._svc.lottery,
            "日历": self._svc.calendar,
            "暖暖": self._svc.nuannuan,
            "攻略": self._svc.dungeon_note,
            "招募": self._svc.party_finder,
            "看看微博": self._svc.ff_weibo,
            "物品": self._svc.item,
            "价格": self._svc.market,
            "房子": self._svc.house,
            "房屋": self._svc.house_alias,
            "输出": self._svc.logs_dps,
            "logs": self._svc.character_logs,
            "抽卡": self._svc.tarot,
        }

        if first == "石之家":
            if await self._handle_risingstones_player(event, rest.strip()):
                return
            await self._run_feature(event, "石之家", self._svc.risingstones_posts)
            return

        handler = handlers.get(first)
        if handler is None:
            return
        await self._run_feature(event, first, handler)
