import asyncio
import re
import ssl
from dataclasses import dataclass
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

from .rgb_processor import RoiMode, image_to_rgb_gif

_log = get_log()

COMMAND = "rgb"
QQ_AVATAR_URL = "http://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"
DEFAULT_TOTAL_SEC = 1.0
DEFAULT_THRESHOLD = 120
MIN_TOTAL_SEC = 0.2
MAX_TOTAL_SEC = 10.0

_ROI_MODE_ALIASES: dict[str, RoiMode] = {
    "auto": "auto",
    "自动": "auto",
    "dark": "dark",
    "暗": "dark",
    "暗部": "dark",
    "light": "light",
    "亮": "light",
    "亮部": "light",
}

_RGB_HELP = (
    "RGB 用法：rgb [秒数] [区域]\n"
    "秒数：0.2~10，默认 1\n"
    "区域：\n"
    "  数字 0~255 — 亮度阈值，暗部≤阈值做 RGB（越小范围越小）\n"
    "  暗/dark — 固定暗部\n"
    "  亮/light — 固定亮部\n"
    "  自动/auto — 自动选区（默认）\n"
    "示例：rgb 2 | rgb 80 | rgb 2 60 | rgb 2 亮 | rgb 2 暗80"
)


@dataclass(frozen=True)
class RgbOptions:
    total_sec: float = DEFAULT_TOTAL_SEC
    threshold: int = DEFAULT_THRESHOLD
    roi_mode: RoiMode = "auto"


class MemeToRGB(NcatBotPlugin):
    name = "MemeToRGB"
    version = "1.0.0"

    timeout = 30
    session = requests.Session()

    async def on_load(self):
        _log.info(f"开始加载 {self.name} 插件 v{self.version}")

    @registrar.qq.on_group_message()
    async def handle_rgb(self, input: GroupMessage):
        message_text = self._get_command_text(input)
        if not message_text:
            return

        parts = message_text.strip().split()
        if not parts or parts[0].lower() != COMMAND:
            return

        if len(parts) > 1 and parts[1].lower() in ("help", "?", "帮助"):
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text=_RGB_HELP
            )
            return

        try:
            options = self._parse_rgb_args(parts[1:])
        except ValueError as e:
            await self.api.qq.post_group_msg(group_id=input.group_id, text=str(e))
            return

        image = await self._resolve_image(input)
        if image is None:
            return

        await self.api.qq.post_group_msg(
            group_id=input.group_id,
            text=(
                f"RGB 制作中（{options.total_sec:g}s，"
                f"区域 {self._format_roi(options)}），请稍候..."
            ),
        )

        gif_path: Optional[Path] = None
        try:
            gif_path = await asyncio.to_thread(
                image_to_rgb_gif,
                image,
                total_sec=options.total_sec,
                threshold=options.threshold,
                roi_mode=options.roi_mode,
            )
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain([Image(file=str(gif_path))]),
            )
        except Exception as e:
            _log.error(f"RGB GIF 生成失败: {e}", exc_info=True)
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text=f"RGB 制作失败: {e}"
            )
        finally:
            if gif_path and gif_path.exists():
                try:
                    gif_path.unlink()
                except OSError as e:
                    _log.warning(f"删除临时 GIF 失败: {e}")

    def _parse_rgb_args(self, args: List[str]) -> RgbOptions:
        options = RgbOptions()
        if not args:
            return options

        idx = 0
        if self._looks_like_seconds(args[0]):
            options = RgbOptions(
                total_sec=self._parse_seconds(args[0]),
                threshold=options.threshold,
                roi_mode=options.roi_mode,
            )
            idx = 1

        if idx < len(args):
            region = args[idx].strip().lower()
            combined = re.fullmatch(r"(暗|dark|亮|light)(\d+)", region)
            if combined:
                mode: RoiMode = (
                    "dark" if combined.group(1) in ("暗", "dark") else "light"
                )
                threshold = int(combined.group(2))
            else:
                mode = _ROI_MODE_ALIASES.get(region)
                threshold = options.threshold

            if combined is not None or mode is not None:
                if not 0 <= threshold <= 255:
                    raise ValueError("区域阈值需在 0~255 之间")
                options = RgbOptions(
                    total_sec=options.total_sec,
                    threshold=threshold,
                    roi_mode=mode if mode is not None else "dark",
                )
            elif region.isdigit():
                threshold = int(region)
                if not 0 <= threshold <= 255:
                    raise ValueError("区域阈值需在 0~255 之间")
                options = RgbOptions(
                    total_sec=options.total_sec,
                    threshold=threshold,
                    roi_mode="dark",
                )
            else:
                raise ValueError(
                    f"无法识别区域参数「{args[idx]}」。发送 rgb help 查看用法"
                )

        if idx + 1 < len(args):
            raise ValueError("参数过多。发送 rgb help 查看用法")

        return options

    @staticmethod
    def _looks_like_seconds(token: str) -> bool:
        if not re.fullmatch(r"\d+(\.\d+)?", token):
            return False
        value = float(token)
        return MIN_TOTAL_SEC <= value <= MAX_TOTAL_SEC

    @staticmethod
    def _parse_seconds(token: str) -> float:
        value = float(token)
        if not MIN_TOTAL_SEC <= value <= MAX_TOTAL_SEC:
            raise ValueError(f"秒数需在 {MIN_TOTAL_SEC:g}~{MAX_TOTAL_SEC:g} 之间")
        return value

    @staticmethod
    def _format_roi(options: RgbOptions) -> str:
        if options.roi_mode == "auto":
            return "自动"
        if options.roi_mode == "light":
            return f"亮部>{options.threshold}"
        return f"暗部≤{options.threshold}"

    def _get_command_text(self, input: GroupMessage) -> str:
        for segment in input.message:
            if hasattr(segment, "text") and segment.text.strip():
                return segment.text.strip()
        return re.sub(r"\[CQ:[^\]]+\]", "", input.raw_message).strip()

    async def _resolve_image(self, input: GroupMessage) -> Optional[PILImage.Image]:
        """图片来源优先级：当前消息图片 > 回复图片 > @ 群友头像 > 发送者头像。"""
        current_images = input.message.filter(Image)
        if current_images and current_images[0].url:
            return await self._download_image(current_images[0].url)

        reply_images = await self._get_images_from_reply(input)
        if reply_images:
            return await self._download_image(reply_images[0])

        for msg in input.message:
            if isinstance(msg, At):
                return await self._download_image(
                    QQ_AVATAR_URL.format(user_id=msg.user_id)
                )

        return await self._download_image(
            QQ_AVATAR_URL.format(user_id=input.sender.user_id)
        )

    async def _get_images_from_reply(self, input: GroupMessage) -> List[str]:
        image_urls: List[str] = []
        reply_list = input.message.filter(Reply)
        reply_id = reply_list[0].id if reply_list else None
        if reply_id is None:
            match = re.search(r"\[CQ:reply,id=(\d+)\]", input.raw_message)
            if match:
                reply_id = int(match.group(1))

        if reply_id is None:
            return image_urls

        reply_msg = await self.api.qq.query.get_msg(reply_id)
        segments = getattr(reply_msg, "message", [])

        if hasattr(segments, "filter"):
            for img in segments.filter(Image):
                if hasattr(img, "url") and img.url:
                    image_urls.append(img.url)
        elif isinstance(segments, list):
            for seg in segments:
                if isinstance(seg, Image):
                    if getattr(seg, "url", None):
                        image_urls.append(seg.url)
                elif isinstance(seg, dict) and seg.get("type") == "image":
                    url = (seg.get("data") or {}).get("url")
                    if url:
                        image_urls.append(url)

        return image_urls

    async def _download_image(self, url: str) -> PILImage.Image:
        url = re.sub(r"&amp;", "&", url)

        def _fetch() -> PILImage.Image:
            ssl_context = ssl.create_default_context()
            ssl_context.options |= ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1 | ssl.OP_NO_TLSv1_3
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
