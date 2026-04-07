"""消息操作 API Mixin"""

from typing import Any, Dict, Union

from ncatbot.types.napcat import SendMessageResult


class MessageAPIMixin:
    async def send_private_msg(
        self,
        user_id: Union[str, int],
        message: list,
        **kwargs,
    ) -> SendMessageResult:
        params: Dict[str, Any] = {"user_id": int(user_id), "message": message}
        params.update(kwargs)
        resp = await self._call("send_private_msg", params)
        return SendMessageResult(**(resp.get("data") or {}))

    async def send_group_msg(
        self,
        group_id: Union[str, int],
        message: list,
        **kwargs,
    ) -> SendMessageResult:
        params: Dict[str, Any] = {"group_id": int(group_id), "message": message}
        params.update(kwargs)
        resp = await self._call("send_group_msg", params)
        return SendMessageResult(**(resp.get("data") or {}))

    async def delete_msg(self, message_id: Union[str, int]) -> None:
        await self._call("delete_msg", {"message_id": int(message_id)})

    async def send_forward_msg(
        self,
        message_type: str,
        target_id: Union[str, int],
        messages: list,
        **kwargs,
    ) -> SendMessageResult:
        params: Dict[str, Any] = {
            "message_type": message_type,
            "messages": messages,
        }
        if message_type == "group":
            params["group_id"] = int(target_id)
        else:
            params["user_id"] = int(target_id)
        params.update(kwargs)
        resp = await self._call("send_forward_msg", params)
        return SendMessageResult(**(resp.get("data") or {}))

    # ---- 消息表情回应 ----

    async def set_msg_emoji_like(
        self,
        message_id: Union[str, int],
        emoji_id: str,
        set: bool = True,
    ) -> None:
        await self._call(
            "set_msg_emoji_like",
            {
                "message_id": int(message_id),
                "emoji_id": emoji_id,
                "set": set,
            },
        )

    # ---- 已读标记 ----

    async def mark_group_msg_as_read(self, group_id: Union[str, int]) -> None:
        await self._call("mark_group_msg_as_read", {"group_id": int(group_id)})

    async def mark_private_msg_as_read(self, user_id: Union[str, int]) -> None:
        await self._call("mark_private_msg_as_read", {"user_id": int(user_id)})

    async def mark_all_as_read(self) -> None:
        await self._call("_mark_all_as_read")

    # ---- 单条消息转发 ----

    async def forward_friend_single_msg(
        self, user_id: Union[str, int], message_id: Union[str, int]
    ) -> None:
        await self._call(
            "forward_friend_single_msg",
            {"user_id": int(user_id), "message_id": int(message_id)},
        )

    async def forward_group_single_msg(
        self, group_id: Union[str, int], message_id: Union[str, int]
    ) -> None:
        await self._call(
            "forward_group_single_msg",
            {"group_id": int(group_id), "message_id": int(message_id)},
        )
