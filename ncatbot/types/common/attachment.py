"""跨平台通用附件模型"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Union

from pydantic import BaseModel, Field
from ncatbot.utils import async_download_to_bytes, async_download_to_file
from ncatbot.utils import get_config_manager

from .segment.base import MessageSegment
from .segment.media import File, Image, Video, Record

__all__ = [
    "AttachmentKind",
    "Attachment",
    "ImageAttachment",
    "VideoAttachment",
    "AudioAttachment",
    "FileAttachment",
]


class AttachmentKind(str, Enum):
    """附件类型标识"""

    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    FILE = "file"
    OTHER = "other"


class Attachment(BaseModel):
    """可下载附件的跨平台描述

    各平台事件实体通过 ``HasAttachments`` mixin 将平台原生附件
    转换为 ``Attachment``，插件侧可统一处理。
    """

    name: str
    url: str
    size: Optional[int] = None
    content_type: Optional[str] = None
    kind: AttachmentKind = AttachmentKind.OTHER
    extra: Dict[str, Any] = Field(default_factory=dict)

    async def download(
        self, dest: Union[str, Path], *, proxy: Optional[str] = None
    ) -> Path:
        """下载附件到本地目录

        Parameters
        ----------
        dest : str | Path
            目标**目录**。文件将保存为 ``dest / self.name``。
        proxy : str, optional
            HTTP/SOCKS5 代理地址。省略时自动读取主配置 ``http_proxy``；
            配置也为空则直连。

        Returns
        -------
        Path
            实际写入的文件路径。
        """
        if proxy is None:
            try:
                proxy = get_config_manager().config.http_proxy or None
            except Exception:
                proxy = None

        return await async_download_to_file(
            self.url, dest, filename=self.name, proxy=proxy
        )

    async def as_bytes(self, *, proxy: Optional[str] = None) -> bytes:
        """下载到内存，返回原始字节

        Parameters
        ----------
        proxy : str, optional
            HTTP/SOCKS5 代理地址。省略时自动读取主配置 ``http_proxy``。
        """
        if proxy is None:
            try:
                proxy = get_config_manager().config.http_proxy or None
            except Exception:
                proxy = None

        return await async_download_to_bytes(self.url, proxy=proxy)

    def to_segment(self) -> MessageSegment:
        """转为消息段（直接使用 URL，同平台内转发最快）"""
        return File(file=self.url, file_name=self.name, file_size=self.size)

    async def to_local_segment(
        self, cache_dir: Union[str, Path] = ".cache/attachments"
    ) -> MessageSegment:
        """先下载到本地，再转为消息段（跨平台安全）"""
        local = await self.download(cache_dir)
        return File(file=str(local), file_name=self.name, file_size=self.size)


class ImageAttachment(Attachment):
    """图片附件"""

    kind: Literal[AttachmentKind.IMAGE] = AttachmentKind.IMAGE
    width: Optional[int] = None
    height: Optional[int] = None

    def to_segment(self) -> MessageSegment:
        return Image(file=self.url, file_name=self.name, file_size=self.size)

    async def to_local_segment(
        self, cache_dir: Union[str, Path] = ".cache/attachments"
    ) -> MessageSegment:
        local = await self.download(cache_dir)
        return Image(file=str(local), file_name=self.name, file_size=self.size)


class VideoAttachment(Attachment):
    """视频附件"""

    kind: Literal[AttachmentKind.VIDEO] = AttachmentKind.VIDEO
    duration: Optional[int] = None

    def to_segment(self) -> MessageSegment:
        return Video(file=self.url, file_name=self.name, file_size=self.size)

    async def to_local_segment(
        self, cache_dir: Union[str, Path] = ".cache/attachments"
    ) -> MessageSegment:
        local = await self.download(cache_dir)
        return Video(file=str(local), file_name=self.name, file_size=self.size)


class AudioAttachment(Attachment):
    """音频附件"""

    kind: Literal[AttachmentKind.AUDIO] = AttachmentKind.AUDIO
    duration: Optional[int] = None

    def to_segment(self) -> MessageSegment:
        return Record(file=self.url, file_name=self.name, file_size=self.size)

    async def to_local_segment(
        self, cache_dir: Union[str, Path] = ".cache/attachments"
    ) -> MessageSegment:
        local = await self.download(cache_dir)
        return Record(file=str(local), file_name=self.name, file_size=self.size)


class FileAttachment(Attachment):
    """文件附件"""

    kind: Literal[AttachmentKind.FILE] = AttachmentKind.FILE

    def to_segment(self) -> MessageSegment:
        return File(file=self.url, file_name=self.name, file_size=self.size)

    async def to_local_segment(
        self, cache_dir: Union[str, Path] = ".cache/attachments"
    ) -> MessageSegment:
        local = await self.download(cache_dir)
        return File(file=str(local), file_name=self.name, file_size=self.size)
