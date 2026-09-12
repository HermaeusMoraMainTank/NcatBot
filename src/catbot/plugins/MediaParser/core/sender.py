"""
消息发送器 - 适配 ncatbot

职责：
- 根据解析结果（ParseResult）规划发送策略
- 控制是否渲染卡片、是否强制合并转发
- 将不同类型的内容转换为 ncatbot 消息组件并发送
"""

from asyncio import Task
from itertools import chain
from pathlib import Path
from typing import List, Any, TYPE_CHECKING
import re

from .compat import ConfigWrapper as AstrBotConfig, video_max_duration_seconds
from .data import (
    AudioContent,
    DynamicContent,
    FileContent,
    GraphicsContent,
    ImageContent,
    ParseResult,
    VideoContent,
)
from .exception import (
    DownloadException,
    DownloadLimitException,
    DurationLimitException,
    SizeLimitException,
    ZeroSizeException,
)
from .render import Renderer

if TYPE_CHECKING:
    pass


class MessageSender:
    """
    消息发送器

    职责：
    - 根据解析结果（ParseResult）规划发送策略
    - 控制是否渲染卡片、是否强制合并转发
    - 将不同类型的内容转换为消息组件并发送
    """

    def __init__(self, config: AstrBotConfig, renderer: Renderer):
        self.config = config
        self.renderer = renderer

    def _build_send_plan(self, result: ParseResult) -> dict:
        """
        根据解析结果生成发送计划（plan）
        """
        light, heavy = [], []

        # 合并主内容 + 转发内容，统一参与发送策略计算
        for cont in chain(
            result.contents, result.repost.contents if result.repost else ()
        ):
            if isinstance(cont, (ImageContent, GraphicsContent)):
                light.append(cont)
            elif isinstance(
                cont, (VideoContent, AudioContent, FileContent, DynamicContent)
            ):
                heavy.append(cont)
            else:
                light.append(cont)

        # 仅在"单一重媒体且无其他内容"时，才允许渲染卡片
        is_single_heavy = len(heavy) == 1 and not light
        render_card = is_single_heavy and self.config.get(
            "single_heavy_render_card", False
        )

        # 实际消息段数量（卡片也算一个段）
        seg_count = len(light) + len(heavy) + (1 if render_card else 0)

        # 达到阈值后，强制合并转发，避免刷屏
        force_merge = seg_count >= self.config["forward_threshold"]

        return {
            "light": light,
            "heavy": heavy,
            "render_card": render_card,
            "preview_card": render_card and not force_merge,
            "force_merge": force_merge,
        }

    @staticmethod
    def _extract_url_from_task_name(cont: Any) -> str | None:
        """从下载任务名中提取原始 URL（用于下载失败时回退直链发送）。"""
        task = getattr(cont, "path_task", None)
        if task is None or not hasattr(task, "get_name"):
            return None
        name = task.get_name()
        if not isinstance(name, str):
            return None
        matched = re.search(r"https?://[^\s|]+", name)
        return matched.group(0) if matched else None

    @staticmethod
    def _cancel_media_download(cont: Any) -> None:
        """取消尚未完成的媒体下载任务，避免跳过发送后仍白下。"""
        task = getattr(cont, "path_task", None)
        if isinstance(task, Task) and not task.done():
            task.cancel()

    @staticmethod
    def _format_duration(seconds: float | int) -> str:
        total = int(seconds)
        minutes, secs = divmod(total, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    def _video_over_duration_tip(
        self,
        cont: VideoContent | None,
        limit: int,
        *,
        duration: float | int | None = None,
    ) -> str:
        actual = duration
        if actual is None and cont is not None and cont.duration:
            actual = cont.duration
        if actual and actual > 0 and limit > 0:
            return (
                f"视频过长（时长 {self._format_duration(actual)} / "
                f"上限 {self._format_duration(limit)}，即 {limit} 秒），"
                "已跳过发送视频"
            )
        if limit > 0:
            return (
                f"视频过长（上限 {self._format_duration(limit)}，即 {limit} 秒），"
                "已跳过发送视频"
            )
        return "视频过长，已跳过发送视频"

    def _video_over_size_tip(
        self,
        cont: Any,
        exc: SizeLimitException,
    ) -> str:
        limit_mb = exc.limit_mb
        if limit_mb is None:
            try:
                limit_mb = float(self.config.get("source_max_size") or 0) or None
            except (TypeError, ValueError):
                limit_mb = None

        parts: list[str] = []
        if exc.size_bytes is not None and limit_mb is not None:
            parts.append(
                f"大小 {exc.size_bytes / 1024 / 1024:.2f} MB / 上限 {limit_mb:g} MB"
            )
        elif limit_mb is not None:
            parts.append(f"已超过上限 {limit_mb:g} MB")
        else:
            parts.append("超过大小限制")

        duration = getattr(cont, "duration", None)
        if duration and duration > 0:
            parts.append(f"时长 {self._format_duration(duration)}")

        return f"视频过大（{'，'.join(parts)}），已跳过发送视频"

    async def build_message_parts(
        self,
        result: ParseResult,
    ) -> List[dict]:
        """
        构建消息部分列表

        返回格式：[{"type": "image/text/video/file", "data": path_or_text}, ...]
        """
        plan = self._build_send_plan(result)
        parts: List[dict] = []
        max_duration = video_max_duration_seconds(self.config)

        # 合并转发时，卡片以内联形式作为一个消息段参与合并
        if plan["render_card"] and plan["force_merge"]:
            if image_path := await self.renderer.render_card(result):
                parts.append({"type": "image", "data": str(image_path)})

        # 轻媒体处理
        for cont in plan["light"]:
            try:
                path: Path = await cont.get_path()
            except (DownloadLimitException, ZeroSizeException):
                continue
            except DownloadException:
                # 图片下载失败时，尝试回退直链发送，避免目标图床对机器人 IP 的拦截
                if isinstance(cont, ImageContent):
                    if fallback_url := self._extract_url_from_task_name(cont):
                        parts.append({"type": "image", "data": fallback_url})
                        continue
                if self.config["show_download_fail_tip"]:
                    parts.append({"type": "text", "data": "此项媒体下载失败"})
                continue

            if isinstance(cont, ImageContent):
                parts.append({"type": "image", "data": str(path)})
            elif isinstance(cont, GraphicsContent):
                parts.append({"type": "image", "data": str(path)})
                if cont.text:
                    parts.append({"type": "text", "data": cont.text})
                if cont.alt:
                    parts.append({"type": "text", "data": cont.alt})

        # 重媒体处理
        for cont in plan["heavy"]:
            # 已知时长超过阈值：不下载、不发视频，只提示（文案仍由上层发送）
            if (
                isinstance(cont, VideoContent)
                and max_duration > 0
                and cont.duration
                and cont.duration > max_duration
            ):
                self._cancel_media_download(cont)
                parts.append(
                    {
                        "type": "text",
                        "data": self._video_over_duration_tip(cont, max_duration),
                    }
                )
                continue

            try:
                path: Path = await cont.get_path()
            except SizeLimitException as exc:
                self._cancel_media_download(cont)
                parts.append(
                    {"type": "text", "data": self._video_over_size_tip(cont, exc)}
                )
                continue
            except DurationLimitException as exc:
                self._cancel_media_download(cont)
                limit = exc.limit_seconds or max_duration
                tip = self._video_over_duration_tip(
                    cont if isinstance(cont, VideoContent) else None,
                    int(limit) if limit else 0,
                    duration=exc.duration,
                )
                parts.append({"type": "text", "data": tip})
                continue
            except DownloadException:
                if self.config["show_download_fail_tip"]:
                    parts.append({"type": "text", "data": "此项媒体下载失败"})
                continue

            if isinstance(cont, (VideoContent, DynamicContent)):
                parts.append({"type": "video", "data": str(path)})
            elif isinstance(cont, AudioContent):
                if self.config["audio_to_file"]:
                    parts.append({"type": "file", "data": str(path), "name": path.name})
                else:
                    parts.append({"type": "record", "data": str(path)})
            elif isinstance(cont, FileContent):
                parts.append({"type": "file", "data": str(path), "name": path.name})

        return parts

    async def render_preview_card(self, result: ParseResult) -> Path | None:
        """
        渲染预览卡片

        返回：卡片图片路径，失败返回 None
        """
        plan = self._build_send_plan(result)
        if plan["preview_card"]:
            return await self.renderer.render_card(result)
        return None

    def should_force_merge(self, result: ParseResult) -> bool:
        """判断是否需要合并转发"""
        plan = self._build_send_plan(result)
        return plan["force_merge"]
