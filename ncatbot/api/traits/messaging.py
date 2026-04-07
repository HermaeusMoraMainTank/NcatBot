"""IMessaging — 消息收发 trait"""

from __future__ import annotations

from typing import Any, Protocol, Union, runtime_checkable


@runtime_checkable
class IMessaging(Protocol):
    """跨平台消息操作协议"""

    async def send_private_msg(
        self, user_id: Union[str, int], message: list, **kwargs: Any
    ) -> Any: ...

    async def send_group_msg(
        self, group_id: Union[str, int], message: list, **kwargs: Any
    ) -> Any: ...

    async def delete_msg(self, message_id: Union[str, int]) -> None: ...

    async def send_forward_msg(
        self,
        message_type: str,
        target_id: Union[str, int],
        messages: list,
        **kwargs: Any,
    ) -> Any: ...
