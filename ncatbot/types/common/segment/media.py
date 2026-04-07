"""通用媒体类消息段"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Optional

from .base import MessageSegment

if TYPE_CHECKING:
    from ..attachment import Attachment

__all__ = [
    "DownloadableSegment",
    "Image",
    "Record",
    "Video",
    "File",
]


class DownloadableSegment(MessageSegment):
    """可下载资源的消息段基类"""

    file: str
    url: Optional[str] = None
    file_id: Optional[str] = None
    file_size: Optional[int] = None
    file_name: Optional[str] = None

    def to_attachment(self) -> "Attachment":
        """转为跨平台 Attachment（基类实现，子类覆盖返回具体类型）"""
        from ..attachment import Attachment

        return Attachment(
            name=self.file_name or self.file,
            url=self.url or "",
            size=self.file_size,
            extra={"file_id": self.file_id} if self.file_id else {},
        )


class Image(DownloadableSegment):
    _type: ClassVar[str] = "image"

    def to_attachment(self) -> "Attachment":
        from ..attachment import ImageAttachment

        return ImageAttachment(
            name=self.file_name or self.file,
            url=self.url or "",
            size=self.file_size,
            content_type="image/*",
            extra={"file_id": self.file_id} if self.file_id else {},
        )


class Record(DownloadableSegment):
    _type: ClassVar[str] = "record"

    def to_attachment(self) -> "Attachment":
        from ..attachment import AudioAttachment

        return AudioAttachment(
            name=self.file_name or self.file,
            url=self.url or "",
            size=self.file_size,
            content_type="audio/*",
            extra={"file_id": self.file_id} if self.file_id else {},
        )


class Video(DownloadableSegment):
    _type: ClassVar[str] = "video"

    def to_attachment(self) -> "Attachment":
        from ..attachment import VideoAttachment

        return VideoAttachment(
            name=self.file_name or self.file,
            url=self.url or "",
            size=self.file_size,
            content_type="video/*",
            extra={"file_id": self.file_id} if self.file_id else {},
        )


class File(DownloadableSegment):
    _type: ClassVar[str] = "file"

    def to_attachment(self) -> "Attachment":
        from ..attachment import FileAttachment

        return FileAttachment(
            name=self.file_name or self.file,
            url=self.url or "",
            size=self.file_size,
            extra={"file_id": self.file_id} if self.file_id else {},
        )
