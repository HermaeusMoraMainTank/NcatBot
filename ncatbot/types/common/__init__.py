"""跨平台通用类型"""

from .attachment import (
    Attachment,
    AttachmentKind,
    AudioAttachment,
    FileAttachment,
    ImageAttachment,
    VideoAttachment,
)
from .attachment_list import AttachmentList
from .base import BaseEventData, register_platform_secondary_keys, get_secondary_key
from .sender import BaseSender
from .segment import (
    SEGMENT_MAP,
    At,
    DownloadableSegment,
    File,
    Image,
    MessageArray,
    MessageSegment,
    PlainText,
    Record,
    Reply,
    Video,
    parse_segment,
)

__all__ = [
    "Attachment",
    "AttachmentKind",
    "AttachmentList",
    "ImageAttachment",
    "VideoAttachment",
    "AudioAttachment",
    "FileAttachment",
    "BaseEventData",
    "register_platform_secondary_keys",
    "get_secondary_key",
    "BaseSender",
    # segments
    "SEGMENT_MAP",
    "MessageSegment",
    "parse_segment",
    "PlainText",
    "At",
    "Reply",
    "DownloadableSegment",
    "Image",
    "Record",
    "Video",
    "File",
    "MessageArray",
]
