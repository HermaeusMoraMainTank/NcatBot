"""幻影坦克插件。

移植自 https://github.com/Yuzi-Liang/astrbot_plugin_mirage_tank
命令：幻影坦克 / 彩色幻影坦克 / 取消
支持：同条两张图 / 依次发表里图 / @ 头像（同 Meme）。
"""

from __future__ import annotations

import asyncio
import re
import tempfile
import time
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Literal, Optional

import requests
from PIL import Image as PILImage

from ncatbot.core import registrar
from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.plugin import NcatBotPlugin
from ncatbot.types import At, Image, MessageArray as MessageChain, Reply
from ncatbot.utils import get_log

from .inference import generate_mirage

_log = get_log()

PLUGIN_DIR = Path(__file__).resolve().parent
CACHE_DIR = PLUGIN_DIR / "data" / "cache"
QQ_AVATAR_URL = "http://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"

COMMAND_GRAY = "幻影坦克"
COMMAND_COLOR = "彩色幻影坦克"
COMMAND_CANCEL = "取消"
Mode = Literal["gray", "color"]


@dataclass
class PendingSession:
    mode: Mode
    group_id: int
    user_id: str
    expire_at: float
    front_path: Optional[str] = None
    paths_to_clean: list[str] = field(default_factory=list)


class MirageTank(NcatBotPlugin):
    name = "MirageTank"
    version = "1.0.0"

    timeout_sec = 30
    max_image_bytes = 10 * 1024 * 1024
    # 彩色模式参数（与上游默认接近）
    color_a = 0.5
    color_b = 20.0
    color_w = 0.7
    session = requests.Session()

    async def on_load(self):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # key: f"{group_id}:{user_id}"
        self._pending: dict[str, PendingSession] = {}
        _log.info("[MirageTank] 已加载")

    def _session_key(self, group_id: int | str, user_id: str | int) -> str:
        return f"{group_id}:{user_id}"

    def _purge_expired(self) -> None:
        now = time.time()
        expired = [k for k, s in self._pending.items() if s.expire_at <= now]
        for key in expired:
            session = self._pending.pop(key, None)
            if session:
                self._cleanup_paths(session.paths_to_clean)
                if session.front_path:
                    self._cleanup_paths([session.front_path])

    @staticmethod
    def _cleanup_paths(paths: list[str]) -> None:
        for p in paths:
            try:
                path = Path(p)
                if path.is_file():
                    path.unlink()
            except OSError as e:
                _log.warning("[MirageTank] 清理临时文件失败 %s: %s", p, e)

    def _get_command_text(self, event: GroupMessage) -> str:
        for segment in event.message:
            if hasattr(segment, "text") and segment.text and segment.text.strip():
                return segment.text.strip()
        return re.sub(r"\[CQ:[^\]]+\]", "", event.raw_message or "").strip()

    def _parse_mode(self, text: str) -> Optional[Mode]:
        if text.startswith("/"):
            text = text[1:].lstrip()
        first = text.split(None, 1)[0] if text else ""
        if first == COMMAND_COLOR:
            return "color"
        if first == COMMAND_GRAY:
            return "gray"
        return None

    @registrar.qq.on_group_message()
    async def handle(self, event: GroupMessage):
        self._purge_expired()

        group_id = int(event.group_id)
        user_id = str(event.sender.user_id)
        key = self._session_key(group_id, user_id)
        text = self._get_command_text(event)

        # 取消进行中的会话
        if text == COMMAND_CANCEL or text == f"/{COMMAND_CANCEL}":
            session = self._pending.pop(key, None)
            if session:
                self._cleanup_paths(session.paths_to_clean)
                if session.front_path:
                    self._cleanup_paths([session.front_path])
                await event.reply(text="已取消幻影坦克生成")
            return

        mode = self._parse_mode(text)
        if mode is not None:
            await self._start_or_run(event, mode, key)
            return

        # 会话中：等待下一张图
        session = self._pending.get(key)
        if session is None:
            return

        urls = await self._collect_image_urls(event)
        if not urls:
            # 会话等待中的闲聊不打扰；仅图片/取消有意义
            return

        await self._feed_session(event, session, key, urls)

    async def _start_or_run(self, event: GroupMessage, mode: Mode, key: str) -> None:
        # 覆盖旧会话
        old = self._pending.pop(key, None)
        if old:
            self._cleanup_paths(old.paths_to_clean)
            if old.front_path:
                self._cleanup_paths([old.front_path])

        urls = await self._collect_image_urls(event)

        # 同条消息两张及以上：立即生成
        if len(urls) >= 2:
            await event.reply(text="收到两张图，正在生成幻影坦克…")
            await self._generate_and_send(event, mode, urls[0], urls[1])
            return

        # 一张：作为表图，继续等里图
        if len(urls) == 1:
            try:
                front_path = await self._download_to_png(urls[0])
            except Exception as e:
                _log.error("[MirageTank] 下载表图失败: %s", e, exc_info=True)
                await event.reply(text=f"表图下载失败: {e}")
                return
            self._pending[key] = PendingSession(
                mode=mode,
                group_id=int(event.group_id),
                user_id=str(event.sender.user_id),
                expire_at=time.time() + self.timeout_sec,
                front_path=front_path,
                paths_to_clean=[front_path],
            )
            await event.reply(
                text=f"收到表图！请再发送里图（{self.timeout_sec}s 内有效，可发「取消」）"
            )
            return

        # 无图：进入等待表图
        self._pending[key] = PendingSession(
            mode=mode,
            group_id=int(event.group_id),
            user_id=str(event.sender.user_id),
            expire_at=time.time() + self.timeout_sec,
        )
        await event.reply(
            text=(
                f"请发送表图，或一次发送两张图（表图+里图）。"
                f"{self.timeout_sec}s 内有效，可发「取消」"
            )
        )

    async def _feed_session(
        self,
        event: GroupMessage,
        session: PendingSession,
        key: str,
        urls: list[str],
    ) -> None:
        # 刷新超时
        session.expire_at = time.time() + self.timeout_sec

        # 还没有表图
        if session.front_path is None:
            # 一次发两张也能在会话中直接完成
            if len(urls) >= 2:
                self._pending.pop(key, None)
                await event.reply(text="收到两张图，正在生成幻影坦克…")
                await self._generate_and_send(event, session.mode, urls[0], urls[1])
                return
            try:
                front_path = await self._download_to_png(urls[0])
            except Exception as e:
                _log.error("[MirageTank] 下载表图失败: %s", e, exc_info=True)
                await event.reply(text=f"表图下载失败: {e}")
                return
            session.front_path = front_path
            session.paths_to_clean.append(front_path)
            await event.reply(
                text=f"收到表图！请再发送里图（{self.timeout_sec}s 内有效）"
            )
            return

        # 已有表图 → 当前为里图
        try:
            back_path = await self._download_to_png(urls[0])
        except Exception as e:
            _log.error("[MirageTank] 下载里图失败: %s", e, exc_info=True)
            await event.reply(text=f"里图下载失败: {e}")
            return
        session.paths_to_clean.append(back_path)
        self._pending.pop(key, None)
        await event.reply(text="收到里图，正在生成…")
        try:
            await self._generate_and_send(
                event, session.mode, session.front_path, back_path
            )
        finally:
            self._cleanup_paths(session.paths_to_clean)

    async def _generate_and_send(
        self,
        event: GroupMessage,
        mode: Mode,
        front_url_or_path: str,
        back_url_or_path: str,
    ) -> None:
        front_path: Optional[str] = None
        back_path: Optional[str] = None
        result_path: Optional[str] = None
        created: list[str] = []
        try:
            if front_url_or_path.startswith("http"):
                front_path = await self._download_to_png(front_url_or_path)
                created.append(front_path)
            else:
                front_path = front_url_or_path

            if back_url_or_path.startswith("http"):
                back_path = await self._download_to_png(back_url_or_path)
                created.append(back_path)
            else:
                back_path = back_url_or_path

            result_path = await asyncio.to_thread(
                generate_mirage,
                front_path,
                back_path,
                mode=mode,
                a=self.color_a,
                b=self.color_b,
                w=self.color_w,
                save_dir=CACHE_DIR,
            )
            created.append(result_path)
            await self.api.qq.post_group_msg(
                group_id=event.group_id,
                rtf=MessageChain([Image(file=result_path)]),
            )
        except Exception as e:
            _log.error("[MirageTank] 生成失败: %s", e, exc_info=True)
            await event.reply(text=f"幻影坦克生成失败: {e}")
        finally:
            # 会话路径由 caller 清理；这里清本次新下的
            # 若 front/back 本就是 session 文件，不要在这里误删——用 created 列表
            # 但 result 必须清；若 front/back 是本次下载的也清
            to_clean = list(created)
            # front/back 若是已有本地路径且不在 created，留给 caller
            self._cleanup_paths(to_clean)

    async def _collect_image_urls(
        self, event: GroupMessage, *, max_count: int = 2
    ) -> list[str]:
        """图片来源：当前消息图 > 回复图 > @ 头像；有 @ 且不足 2 张时用自己头像补齐。"""
        urls: list[str] = []

        for img in event.message.filter(Image):
            if getattr(img, "url", None):
                urls.append(img.url)
                if len(urls) >= max_count:
                    return urls[:max_count]

        if len(urls) < max_count:
            reply_urls = await self._get_images_from_reply(event)
            for u in reply_urls:
                if u not in urls:
                    urls.append(u)
                if len(urls) >= max_count:
                    return urls[:max_count]

        has_at = False
        if len(urls) < max_count:
            for msg in event.message:
                if isinstance(msg, At):
                    has_at = True
                    avatar = QQ_AVATAR_URL.format(user_id=msg.user_id)
                    if avatar not in urls:
                        urls.append(avatar)
                    if len(urls) >= max_count:
                        return urls[:max_count]

        # 类似 Meme：带了 @ 但只凑到 1 张时，用发送者头像补第二张
        if has_at and 0 < len(urls) < max_count:
            self_avatar = QQ_AVATAR_URL.format(user_id=event.sender.user_id)
            if self_avatar not in urls:
                urls.append(self_avatar)

        return urls[:max_count] if len(urls) > max_count else urls

    async def _get_images_from_reply(self, event: GroupMessage) -> list[str]:
        urls: list[str] = []
        reply_list = event.message.filter(Reply)
        reply_id = reply_list[0].id if reply_list else None
        if reply_id is None:
            match = re.search(r"\[CQ:reply,id=(\d+)\]", event.raw_message or "")
            if match:
                reply_id = int(match.group(1))
        if reply_id is None:
            return urls

        reply_msg = await self.api.qq.query.get_msg(reply_id)
        segments = getattr(reply_msg, "message", [])
        if hasattr(segments, "filter"):
            for img in segments.filter(Image):
                if getattr(img, "url", None):
                    urls.append(img.url)
        elif isinstance(segments, list):
            for seg in segments:
                if isinstance(seg, Image) and getattr(seg, "url", None):
                    urls.append(seg.url)
                elif isinstance(seg, dict) and seg.get("type") == "image":
                    url = (seg.get("data") or {}).get("url")
                    if url:
                        urls.append(url)
        return urls

    async def _download_to_png(self, url: str) -> str:
        url = re.sub(r"&amp;", "&", url)

        def _fetch() -> str:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/114.0.0.0 Safari/537.36"
                ),
            }
            resp = self.session.get(url, headers=headers, timeout=30, verify=False)
            resp.raise_for_status()
            if len(resp.content) > self.max_image_bytes:
                raise ValueError(
                    f"图片过大（>{self.max_image_bytes // (1024 * 1024)}MB）"
                )
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            img = PILImage.open(BytesIO(resp.content))
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=".png", dir=str(CACHE_DIR)
            ) as tmp:
                img.save(tmp, format="PNG")
                return tmp.name

        return await asyncio.to_thread(_fetch)
