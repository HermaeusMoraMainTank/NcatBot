"""AstrBot AstrMessageEvent 兼容包装。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, List

from . import message_components as comp
from .message_components import MessageResult, result_to_ncatbot_segments


@dataclass
class _MessageObj:
    message_id: Any = 0
    message: List[comp.BaseMessageComponent] = field(default_factory=list)
    group_id: str = ""
    sender_id: str = ""


class AstrMessageEvent:
    def __init__(
        self,
        *,
        native: Any,
        api: Any,
        message_obj: _MessageObj,
        message_str: str,
        group_id: str,
        sender_id: str,
        self_id: str,
        platform_name: str = "aiocqhttp",
        platform_id: str = "qq",
    ):
        self._native = native
        self._api = api
        self.message_obj = message_obj
        self.message_str = message_str
        self._group_id = group_id
        self._sender_id = sender_id
        self._self_id = self_id
        self._platform_name = platform_name
        self._platform_id = platform_id

    def get_group_id(self) -> str:
        return self._group_id

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_self_id(self) -> str:
        return self._self_id

    def get_platform_name(self) -> str:
        return self._platform_name

    def get_platform_id(self) -> str:
        return self._platform_id

    def plain_result(self, text: str) -> MessageResult:
        return MessageResult(kind="plain", payload=text)

    def image_result(self, url_or_path: str) -> MessageResult:
        return MessageResult(kind="image", payload=url_or_path)

    def chain_result(self, chain: list) -> MessageResult:
        return MessageResult(kind="chain", chain=list(chain))

    async def send(self, result: Any) -> None:
        segs = result_to_ncatbot_segments(result)
        if not segs:
            return
        from ncatbot.types import MessageArray as MessageChain

        await self._api.qq.post_group_msg(
            group_id=int(self._group_id),
            rtf=MessageChain(segs),
        )


async def from_ncatbot(event: Any, api: Any) -> AstrMessageEvent:
    """从 NcatBot GroupMessageEvent 构造兼容事件。"""
    from ncatbot.types import At, Image as NbImage, PlainText, Reply as NbReply

    group_id = str(event.group_id)
    sender_id = str(event.sender.user_id)
    self_id = str(getattr(getattr(event, "self_id", None), "__str__", lambda: "")())
    try:
        self_id = str(event.self_id)
    except Exception:
        self_id = "0"

    texts: list[str] = []
    chain: list[comp.BaseMessageComponent] = []

    for seg in event.message:
        if isinstance(seg, PlainText) and seg.text:
            texts.append(seg.text)
            chain.append(comp.Plain(seg.text))
        elif isinstance(seg, NbImage):
            img = comp.Image(
                file=getattr(seg, "file", "") or "", url=getattr(seg, "url", None)
            )
            chain.append(img)
        elif isinstance(seg, NbReply):
            reply = comp.Reply(id=seg.id)
            # 拉取被引用消息的图片链
            try:
                reply_msg = await api.qq.query.get_msg(seg.id)
                segs = getattr(reply_msg, "message", []) or []
                for s in segs:
                    if isinstance(s, NbImage) or (
                        isinstance(s, dict) and s.get("type") == "image"
                    ):
                        if isinstance(s, dict):
                            data = s.get("data", {})
                            reply.chain.append(
                                comp.Image(
                                    file=data.get("file", ""),
                                    url=data.get("url"),
                                )
                            )
                        else:
                            reply.chain.append(
                                comp.Image(
                                    file=getattr(s, "file", "") or "",
                                    url=getattr(s, "url", None),
                                )
                            )
            except Exception:
                pass
            chain.append(reply)
        elif isinstance(seg, At):
            pass

    message_str = "".join(texts).strip()
    if not message_str:
        message_str = re.sub(r"\[CQ:[^\]]+\]", "", event.raw_message or "").strip()

    msg_obj = _MessageObj(
        message_id=getattr(event, "message_id", 0),
        message=chain,
        group_id=group_id,
        sender_id=sender_id,
    )
    return AstrMessageEvent(
        native=event,
        api=api,
        message_obj=msg_obj,
        message_str=message_str,
        group_id=group_id,
        sender_id=sender_id,
        self_id=self_id,
        platform_name="aiocqhttp",
        platform_id="qq",
    )
