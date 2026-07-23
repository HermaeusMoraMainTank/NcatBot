"""AstrBot message_components 精简兼容。"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional, Union


class BaseMessageComponent:
    pass


@dataclass
class Plain(BaseMessageComponent):
    text: str = ""

    def __str__(self) -> str:
        return self.text


@dataclass
class Reply(BaseMessageComponent):
    id: Any = None
    chain: List[BaseMessageComponent] = field(default_factory=list)


@dataclass
class Image(BaseMessageComponent):
    file: str = ""
    url: Optional[str] = None
    _base64: Optional[str] = None

    @classmethod
    def fromBase64(cls, b64: str) -> "Image":
        raw = b64
        if "," in b64 and b64.strip().startswith("data:"):
            raw = b64.split(",", 1)[1]
        return cls(_base64=raw)

    async def convert_to_base64(self) -> str:
        if self._base64:
            return self._base64
        if self.file and not self.file.startswith("http"):
            from pathlib import Path

            data = Path(self.file).read_bytes()
            return base64.b64encode(data).decode("ascii")
        src = self.url or self.file
        if src and src.startswith("http"):
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.get(src) as resp:
                    data = await resp.read()
                    return base64.b64encode(data).decode("ascii")
        return ""


@dataclass
class Node(BaseMessageComponent):
    uin: Any = 0
    name: str = "GalgameBox"
    content: List[BaseMessageComponent] = field(default_factory=list)


@dataclass
class Nodes(BaseMessageComponent):
    nodes: List[Node] = field(default_factory=list)

    def __init__(self, nodes: Optional[List[Node]] = None):
        self.nodes = nodes or []


@dataclass
class MessageResult:
    """yield / send 的统一结果容器。"""

    kind: str  # plain | image | chain | raw
    payload: Any = None
    chain: List[BaseMessageComponent] = field(default_factory=list)


def result_to_ncatbot_segments(
    result: Union[MessageResult, str, None],
) -> list:
    """转为 NcatBot 消息段列表（延迟导入避免循环）。"""
    from ncatbot.types import Image as NbImage
    from ncatbot.types import PlainText
    from ncatbot.types.qq.segment.forward import Forward, ForwardNode

    if result is None:
        return []
    if isinstance(result, str):
        return [PlainText(text=result)] if result else []
    if not isinstance(result, MessageResult):
        return [PlainText(text=str(result))]

    if result.kind == "plain":
        return [PlainText(text=str(result.payload or ""))]
    if result.kind == "image":
        path = str(result.payload or "")
        if path.startswith("http"):
            return [NbImage(file=path, url=path)]
        return [NbImage(file=path)]
    if result.kind == "raw":
        return [PlainText(text=str(result.payload or ""))]

    segs: list = []
    nodes: list[ForwardNode] = []
    for item in result.chain:
        if isinstance(item, Nodes):
            for n in item.nodes:
                nodes.append(
                    ForwardNode(
                        user_id=str(n.uin),
                        nickname=n.name or "GalgameBox",
                        content=_comps_to_nb(n.content),
                    )
                )
        elif isinstance(item, Node):
            nodes.append(
                ForwardNode(
                    user_id=str(item.uin),
                    nickname=item.name or "GalgameBox",
                    content=_comps_to_nb(item.content),
                )
            )
        else:
            segs.extend(_comps_to_nb([item]))
    if nodes:
        return [Forward(content=nodes)]
    return segs


def _comps_to_nb(comps: List[BaseMessageComponent]) -> list:
    from ncatbot.types import Image as NbImage
    from ncatbot.types import PlainText

    out: list = []
    for c in comps:
        if isinstance(c, Plain):
            out.append(PlainText(text=c.text))
        elif isinstance(c, Image):
            if c._base64:
                out.append(NbImage(file=f"base64://{c._base64}"))
            elif c.url:
                out.append(NbImage(file=c.url, url=c.url))
            elif c.file:
                out.append(NbImage(file=c.file))
        elif isinstance(c, str):
            out.append(PlainText(text=c))
    return out


def strip_data_url(b64: str) -> str:
    m = re.match(r"^data:[^;]+;base64,(.+)$", b64.strip(), re.S)
    return m.group(1) if m else b64
