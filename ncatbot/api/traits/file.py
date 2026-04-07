"""IFileTransfer — 文件传输 trait"""

from __future__ import annotations

from typing import Any, Protocol, Union, runtime_checkable


@runtime_checkable
class IFileTransfer(Protocol):
    """跨平台文件操作协议"""

    async def upload_group_file(
        self,
        group_id: Union[str, int],
        file: str,
        name: str,
        folder_id: str = "",
    ) -> None: ...

    async def upload_private_file(
        self,
        user_id: Union[str, int],
        file: str,
        name: str,
    ) -> None: ...

    async def download_file(
        self, url: str = "", file: str = "", headers: str = ""
    ) -> Any: ...
