"""仿 AI 水印插件。

移植自 https://github.com/FenChen0211/astrbot-plugin-fake-ai-watermark
命令：豆包水印 / gemini水印
图片来源（同 Meme）：当前消息图 > 回复图 > @ 头像 > 发送者头像
"""

from __future__ import annotations

import asyncio
import re
import ssl
import tempfile
from io import BytesIO
from pathlib import Path
from typing import List, Optional

import requests
from PIL import Image as PILImage

from ncatbot.core import registrar
from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.plugin import NcatBotPlugin
from ncatbot.types import At, Image, MessageArray as MessageChain, Reply
from ncatbot.utils import get_log
from common.utils.plugin_commands import format_help, is_help_message

from .processor import WatermarkProcessor

_log = get_log()

PLUGIN_DIR = Path(__file__).resolve().parent
ASSETS_DIR = PLUGIN_DIR / "assets"
QQ_AVATAR_URL = "http://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"

COMMANDS: dict[str, str] = {
    "豆包水印": "doubao",
    "gemini水印": "gemini",
    "Gemini水印": "gemini",
    "GEMINI水印": "gemini",
}
COMMAND_NAMES = tuple(COMMANDS.keys())

HELP_TEXT = format_help(
    "FakeAiWatermark 仿 AI 水印",
    [
        "豆包水印：为图片添加豆包风格水印",
        "gemini水印：为图片添加 Gemini 风格水印",
        "图片来源：当前消息图 > 回复图 > @ 头像 > 发送者头像",
    ],
)


class FakeAiWatermark(NcatBotPlugin):
    name = "FakeAiWatermark"
    version = "1.0.0"

    timeout = 30
    session = requests.Session()
    gemini_opacity = 0.25
    doubao_opacity = 0.7

    async def on_load(self):
        self.processor = WatermarkProcessor(ASSETS_DIR)
        _log.info(
            "[FakeAiWatermark] 已加载（Gemini 透明度=%.2f, 豆包=%.2f）",
            self.gemini_opacity,
            self.doubao_opacity,
        )

    @registrar.qq.on_group_message()
    async def handle_watermark(self, event: GroupMessage):
        text = self._get_command_text(event)
        if not text:
            return

        # 去掉开头的 / 前缀（兼容）
        if text.startswith("/"):
            text = text[1:].lstrip()

        if is_help_message(
            text,
            command_names=COMMAND_NAMES):
            await event.reply(text=HELP_TEXT, at_sender=False)
            return

        first = text.split(None, 1)[0] if text else ""
        watermark_type = COMMANDS.get(first)
        if watermark_type is None:
            # 兼容「gemini水印」大小写混写
            lower_map = {k.lower(): v for k, v in COMMANDS.items()}
            watermark_type = lower_map.get(first.lower())
        if watermark_type is None:
            return

        try:
            image = await self._resolve_image(event)
        except Exception as e:
            _log.error("[FakeAiWatermark] 取图失败: %s", e, exc_info=True)
            await event.reply(text="图片获取失败，请换一张再试")
            return

        if image is None:
            await event.reply(text="未检测到图片，请发送图片、回复图片或 @ 某人")
            return

        out_path: Optional[Path] = None
        try:
            result = await asyncio.to_thread(self._apply, image, watermark_type)
            if result is None:
                await event.reply(text="水印处理失败")
                return

            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                out_path = Path(tmp.name)
            await asyncio.to_thread(result.save, str(out_path), "JPEG", quality=95)
            await self.api.qq.post_group_msg(
                group_id=event.group_id,
                rtf=MessageChain([Image(file=str(out_path))]),
            )
        except Exception as e:
            _log.error("[FakeAiWatermark] 处理异常: %s", e, exc_info=True)
            await event.reply(text=f"处理失败: {e}")
        finally:
            if out_path and out_path.exists():
                try:
                    out_path.unlink()
                except OSError as e:
                    _log.warning("[FakeAiWatermark] 删除临时文件失败: %s", e)

    def _apply(
        self, image: PILImage.Image, watermark_type: str
    ) -> Optional[PILImage.Image]:
        if watermark_type == "gemini":
            return self.processor.apply_gemini(image, self.gemini_opacity)
        return self.processor.apply_doubao(image, self.doubao_opacity)

    def _get_command_text(self, event: GroupMessage) -> str:
        for segment in event.message:
            if hasattr(segment, "text") and segment.text and segment.text.strip():
                return segment.text.strip()
        return re.sub(r"\[CQ:[^\]]+\]", "", event.raw_message or "").strip()

    async def _resolve_image(self, event: GroupMessage) -> Optional[PILImage.Image]:
        """优先级：当前消息图片 > 回复图片 > @ 头像 > 发送者头像。"""
        current = event.message.filter(Image)
        if current and getattr(current[0], "url", None):
            return await self._download_image(current[0].url)

        reply_urls = await self._get_images_from_reply(event)
        if reply_urls:
            return await self._download_image(reply_urls[0])

        for msg in event.message:
            if isinstance(msg, At):
                return await self._download_image(
                    QQ_AVATAR_URL.format(user_id=msg.user_id)
                )

        return await self._download_image(
            QQ_AVATAR_URL.format(user_id=event.sender.user_id)
        )

    async def _get_images_from_reply(self, event: GroupMessage) -> List[str]:
        urls: List[str] = []
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

    async def _download_image(self, url: str) -> PILImage.Image:
        url = re.sub(r"&amp;", "&", url)

        def _fetch() -> PILImage.Image:
            ssl_context = ssl.create_default_context()
            ssl_context.options |= (
                ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1 | ssl.OP_NO_TLSv1_3
            )
            ssl_context.set_ciphers("HIGH:!aNULL:!MD5")
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/114.0.0.0 Safari/537.36"
                ),
            }
            response = self.session.get(
                url, headers=headers, timeout=self.timeout, verify=False
            )
            response.raise_for_status()
            return PILImage.open(BytesIO(response.content))

        return await asyncio.to_thread(_fetch)
