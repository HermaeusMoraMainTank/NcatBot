"""IGroupManage — 群管理 trait"""

from __future__ import annotations

from typing import Protocol, Union, runtime_checkable


@runtime_checkable
class IGroupManage(Protocol):
    """跨平台群/频道管理协议"""

    async def set_group_kick(
        self,
        group_id: Union[str, int],
        user_id: Union[str, int],
        reject_add_request: bool = False,
    ) -> None: ...

    async def set_group_ban(
        self,
        group_id: Union[str, int],
        user_id: Union[str, int],
        duration: int = 1800,
    ) -> None: ...

    async def set_group_whole_ban(
        self, group_id: Union[str, int], enable: bool = True
    ) -> None: ...

    async def set_group_admin(
        self,
        group_id: Union[str, int],
        user_id: Union[str, int],
        enable: bool = True,
    ) -> None: ...

    async def set_group_name(self, group_id: Union[str, int], name: str) -> None: ...

    async def set_group_leave(
        self, group_id: Union[str, int], is_dismiss: bool = False
    ) -> None: ...
