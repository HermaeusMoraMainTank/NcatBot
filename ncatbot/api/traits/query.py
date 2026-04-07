"""IQuery — 信息查询 trait"""

from __future__ import annotations

from typing import Any, Protocol, Union, runtime_checkable


@runtime_checkable
class IQuery(Protocol):
    """跨平台信息查询协议"""

    async def get_login_info(self) -> Any: ...

    async def get_stranger_info(self, user_id: Union[str, int]) -> Any: ...

    async def get_friend_list(self) -> Any: ...

    async def get_group_info(self, group_id: Union[str, int]) -> Any: ...

    async def get_group_list(self) -> Any: ...

    async def get_group_member_info(
        self, group_id: Union[str, int], user_id: Union[str, int]
    ) -> Any: ...

    async def get_group_member_list(self, group_id: Union[str, int]) -> Any: ...
