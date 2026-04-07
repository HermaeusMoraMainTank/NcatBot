"""QQ 消息发送 sugar 方法"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Union

from ncatbot.types import (
    MessageArray,
    Image,
    Record,
    File,
    PlainText,
    Video,
)
from ncatbot.types.qq import Forward
from ncatbot.types.napcat import SendMessageResult

from .segment_validator import (
    check_forward_node_conflicts,
    validate_and_prepare,
)

if TYPE_CHECKING:
    from .interface import IQQAPIClient


class QQMessageSugarMixin:
    """QQ 消息发送 sugar 方法

    假设宿主类有 ``_api: IQQAPIClient`` 属性。
    """

    _api: IQQAPIClient

    # ---- 便捷发送（组装 MessageArray）----

    async def post_group_msg(
        self,
        group_id: Union[str, int],
        text: Optional[str] = None,
        at: Optional[Union[str, int]] = None,
        reply: Optional[Union[str, int]] = None,
        image: Optional[Union[str, Image]] = None,
        video: Optional[Union[str, Video]] = None,
        rtf: Optional[MessageArray] = None,
        *,
        strict: bool = False,
    ) -> SendMessageResult:
        """便捷群消息 — 关键字参数自动组装 MessageArray"""
        msg = _build_message_array(
            text=text, at=at, reply=reply, image=image, video=video, rtf=rtf
        )
        return await _send_with_validation(
            self._api.send_group_msg, group_id, msg, strict=strict
        )

    async def post_private_msg(
        self,
        user_id: Union[str, int],
        text: Optional[str] = None,
        reply: Optional[Union[str, int]] = None,
        image: Optional[Union[str, Image]] = None,
        video: Optional[Union[str, Video]] = None,
        rtf: Optional[MessageArray] = None,
        *,
        strict: bool = False,
    ) -> SendMessageResult:
        """便捷私聊消息"""
        msg = _build_message_array(
            text=text, reply=reply, image=image, video=video, rtf=rtf
        )
        return await _send_with_validation(
            self._api.send_private_msg, user_id, msg, strict=strict
        )

    async def post_group_array_msg(
        self,
        group_id: Union[str, int],
        msg: MessageArray,
        *,
        strict: bool = False,
    ) -> SendMessageResult:
        return await _send_with_validation(
            self._api.send_group_msg, group_id, msg, strict=strict
        )

    async def post_private_array_msg(
        self,
        user_id: Union[str, int],
        msg: MessageArray,
        *,
        strict: bool = False,
    ) -> SendMessageResult:
        return await _send_with_validation(
            self._api.send_private_msg, user_id, msg, strict=strict
        )

    # ---- 群消息 sugar ----

    async def send_group_text(
        self, group_id: Union[str, int], text: str
    ) -> SendMessageResult:
        return await self.post_group_msg(group_id, text=text)

    async def send_group_plain_text(
        self, group_id: Union[str, int], text: str
    ) -> SendMessageResult:
        msg = MessageArray([PlainText(text=text)])
        return await self._api.send_group_msg(group_id, msg.to_list())

    async def send_group_image(
        self, group_id: Union[str, int], image: Union[str, Image]
    ) -> SendMessageResult:
        return await self.post_group_msg(group_id, image=image)

    async def send_group_record(
        self, group_id: Union[str, int], file: str
    ) -> SendMessageResult:
        msg = MessageArray([Record(file=file)])
        return await self._api.send_group_msg(group_id, msg.to_list())

    async def send_group_file(
        self,
        group_id: Union[str, int],
        file: str,
        name: Optional[str] = None,
    ) -> SendMessageResult:
        msg = MessageArray([File(file=file, file_name=name)])
        return await self._api.send_group_msg(group_id, msg.to_list())

    async def send_group_video(
        self, group_id: Union[str, int], video: Union[str, Video]
    ) -> SendMessageResult:
        msg = MessageArray().add_video(video)
        return await self._api.send_group_msg(group_id, msg.to_list())

    async def send_group_sticker(
        self, group_id: Union[str, int], image: Union[str, Image]
    ) -> SendMessageResult:
        """发送群动画表情（sub_type=1 的图片）"""
        img = _to_sticker(image)
        msg = MessageArray().add_image(img)
        return await self._api.send_group_msg(group_id, msg.to_list())

    # ---- 私聊消息 sugar ----

    async def send_private_text(
        self, user_id: Union[str, int], text: str
    ) -> SendMessageResult:
        return await self.post_private_msg(user_id, text=text)

    async def send_private_plain_text(
        self, user_id: Union[str, int], text: str
    ) -> SendMessageResult:
        msg = MessageArray([PlainText(text=text)])
        return await self._api.send_private_msg(user_id, msg.to_list())

    async def send_private_image(
        self, user_id: Union[str, int], image: Union[str, Image]
    ) -> SendMessageResult:
        return await self.post_private_msg(user_id, image=image)

    async def send_private_record(
        self, user_id: Union[str, int], file: str
    ) -> SendMessageResult:
        msg = MessageArray([Record(file=file)])
        return await self._api.send_private_msg(user_id, msg.to_list())

    async def send_private_file(
        self,
        user_id: Union[str, int],
        file: str,
        name: Optional[str] = None,
    ) -> SendMessageResult:
        msg = MessageArray([File(file=file, file_name=name)])
        return await self._api.send_private_msg(user_id, msg.to_list())

    async def send_private_video(
        self, user_id: Union[str, int], video: Union[str, Video]
    ) -> SendMessageResult:
        msg = MessageArray().add_video(video)
        return await self._api.send_private_msg(user_id, msg.to_list())

    async def send_private_sticker(
        self, user_id: Union[str, int], image: Union[str, Image]
    ) -> SendMessageResult:
        """发送私聊动画表情（sub_type=1 的图片）"""
        img = _to_sticker(image)
        msg = MessageArray().add_image(img)
        return await self._api.send_private_msg(user_id, msg.to_list())

    async def send_private_dice(
        self, user_id: Union[str, int], value: int = 1
    ) -> SendMessageResult:
        return await self._api.send_private_msg(
            user_id,
            [{"type": "dice", "data": {"value": value}}],
        )

    async def send_private_rps(
        self, user_id: Union[str, int], value: int = 1
    ) -> SendMessageResult:
        return await self._api.send_private_msg(
            user_id,
            [{"type": "rps", "data": {"value": value}}],
        )

    # ---- Poke sugar ----

    async def send_poke(
        self, group_id: Union[str, int], user_id: Union[str, int]
    ) -> None:
        """群内戳一戳"""
        await self._api.send_poke(group_id, user_id)

    async def friend_poke(self, user_id: Union[str, int]) -> None:
        """好友戳一戳"""
        await self._api.friend_poke(user_id)

    # ---- 合并转发 sugar ----

    async def post_group_forward_msg(
        self,
        group_id: Union[str, int],
        forward: Forward,
    ) -> SendMessageResult:
        check_forward_node_conflicts(forward)
        return await self._api.send_forward_msg(
            "group",
            group_id,
            **forward.to_forward_dict(),
        )

    async def post_private_forward_msg(
        self,
        user_id: Union[str, int],
        forward: Forward,
    ) -> SendMessageResult:
        check_forward_node_conflicts(forward)
        return await self._api.send_forward_msg(
            "private",
            user_id,
            **forward.to_forward_dict(),
        )

    async def send_group_forward_msg_by_id(
        self,
        group_id: Union[str, int],
        message_ids: List[Union[str, int]],
    ) -> SendMessageResult:
        """通过消息 ID 列表转发群消息"""
        result = SendMessageResult(message_id="")
        for mid in message_ids:
            msg_data = await self._api.get_msg(int(mid))
            if not msg_data:
                continue
            raw_segs = msg_data.get("message", [])
            result = await self._api.send_group_msg(group_id, raw_segs)
        return result

    async def send_private_forward_msg_by_id(
        self,
        user_id: Union[str, int],
        message_ids: List[Union[str, int]],
    ) -> SendMessageResult:
        """通过消息 ID 列表转发私聊消息"""
        result = SendMessageResult(message_id="")
        for mid in message_ids:
            msg_data = await self._api.get_msg(int(mid))
            if not msg_data:
                continue
            raw_segs = msg_data.get("message", [])
            result = await self._api.send_private_msg(user_id, raw_segs)
        return result


def _build_message_array(
    *,
    text: Optional[str] = None,
    at: Optional[Union[str, int]] = None,
    reply: Optional[Union[str, int]] = None,
    image: Optional[Union[str, Image]] = None,
    video: Optional[Union[str, Video]] = None,
    rtf: Optional[MessageArray] = None,
) -> MessageArray:
    """从关键字参数组装 MessageArray"""
    msg = MessageArray()
    if reply is not None:
        msg.add_reply(reply)
    if at is not None:
        msg.add_at(at)
    if text is not None:
        msg.add_text(text)
    if image is not None:
        msg.add_image(image)
    if video is not None:
        msg.add_video(video)
    if rtf is not None:
        msg = msg + rtf
    return msg


def _to_sticker(image: Union[str, Image]) -> Image:
    """将 str 或 Image 转为 sub_type=1 的动画表情 Image"""
    if isinstance(image, Image):
        image.sub_type = 1
        return image
    return Image(file=image, sub_type=1)


async def _send_with_validation(
    send_fn,
    target_id: Union[str, int],
    msg: MessageArray,
    *,
    strict: bool = False,
) -> SendMessageResult:
    """验证消息段冲突后发送，必要时自动拆为多条"""
    parts = validate_and_prepare(msg, strict=strict)
    result = SendMessageResult(message_id="")
    for part in parts:
        result = await send_fn(target_id, part.to_list())
    return result
