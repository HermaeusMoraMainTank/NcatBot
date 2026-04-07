"""QQ 平台专用消息段"""

from .text import Face
from .rich import Share, Location, Music, Json, Markdown
from .forward import ForwardNode, Forward
from .media import QQImage, QQRecord
from .cq import parse_cq_code_to_onebot11

__all__ = [
    "Face",
    "Share",
    "Location",
    "Music",
    "Json",
    "Markdown",
    "ForwardNode",
    "Forward",
    "QQImage",
    "QQRecord",
    "parse_cq_code_to_onebot11",
]
