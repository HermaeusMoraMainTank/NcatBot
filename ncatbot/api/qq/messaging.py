"""QQMessaging — 消息操作功能分组"""

from __future__ import annotations

from typing import TYPE_CHECKING, Union

from ncatbot.types.napcat import MessageHistory, SendMessageResult

if TYPE_CHECKING:
    from .interface import IQQAPIClient


class QQMessaging:
    """消息收发 + poke + emoji + 已读标记 + 消息历史"""

    __slots__ = ("_api",)

    def __init__(self, api: IQQAPIClient) -> None:
        self._api = api

    async def send_group_msg(
        self, group_id: Union[str, int], message: list, **kwargs
    ) -> SendMessageResult:
        return await self._api.send_group_msg(group_id, message, **kwargs)

    async def send_private_msg(
        self, user_id: Union[str, int], message: list, **kwargs
    ) -> SendMessageResult:
        return await self._api.send_private_msg(user_id, message, **kwargs)

    async def delete_msg(self, message_id: Union[str, int]) -> None:
        await self._api.delete_msg(message_id)

    async def send_forward_msg(
        self,
        message_type: str,
        target_id: Union[str, int],
        messages: list,
        **kwargs,
    ) -> SendMessageResult:
        return await self._api.send_forward_msg(
            message_type, target_id, messages, **kwargs
        )

    async def send_poke(
        self, group_id: Union[str, int], user_id: Union[str, int]
    ) -> None:
        await self._api.send_poke(group_id, user_id)

    async def friend_poke(self, user_id: Union[str, int]) -> None:
        await self._api.friend_poke(user_id)

    async def send_like(self, user_id: Union[str, int], times: int = 1) -> None:
        await self._api.send_like(user_id, times)

    # ---- 消息扩展 ----

    async def set_msg_emoji_like(
        self, message_id: Union[str, int], emoji_id: str, set: bool = True
    ) -> None:
        await self._api.set_msg_emoji_like(message_id, emoji_id, set)

    async def mark_group_msg_as_read(self, group_id: Union[str, int]) -> None:
        await self._api.mark_group_msg_as_read(group_id)

    async def mark_private_msg_as_read(self, user_id: Union[str, int]) -> None:
        await self._api.mark_private_msg_as_read(user_id)

    async def mark_all_as_read(self) -> None:
        await self._api.mark_all_as_read()

    async def forward_friend_single_msg(
        self, user_id: Union[str, int], message_id: Union[str, int]
    ) -> None:
        await self._api.forward_friend_single_msg(user_id, message_id)

    async def forward_group_single_msg(
        self, group_id: Union[str, int], message_id: Union[str, int]
    ) -> None:
        await self._api.forward_group_single_msg(group_id, message_id)

    # ---- 消息历史 ----

    async def get_group_msg_history(
        self,
        group_id: Union[str, int],
        message_seq: Union[str, int, None] = None,
        count: int = 20,
    ) -> MessageHistory:
        return await self._api.get_group_msg_history(group_id, message_seq, count)

    async def get_friend_msg_history(
        self,
        user_id: Union[str, int],
        message_seq: Union[str, int, None] = None,
        count: int = 20,
    ) -> MessageHistory:
        return await self._api.get_friend_msg_history(user_id, message_seq, count)
