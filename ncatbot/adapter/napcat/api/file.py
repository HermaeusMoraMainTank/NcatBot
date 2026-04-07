"""文件操作 API Mixin"""

from __future__ import annotations

from typing import Union

from ncatbot.types.napcat import CreateFolderResult, DownloadResult, GroupFileList


class FileAPIMixin:
    async def upload_group_file(
        self,
        group_id: Union[str, int],
        file: str,
        name: str,
        folder_id: str = "",
    ) -> None:
        await self._call(
            "upload_group_file",
            {
                "group_id": int(group_id),
                "file": file,
                "name": name,
                "folder_id": folder_id,
            },
        )

    async def get_group_root_files(self, group_id: Union[str, int]) -> GroupFileList:
        data = (
            await self._call_data("get_group_root_files", {"group_id": int(group_id)})
            or {}
        )
        return GroupFileList(**data)

    async def get_group_file_url(self, group_id: Union[str, int], file_id: str) -> str:
        data = await self._call_data(
            "get_group_file_url",
            {
                "group_id": int(group_id),
                "file_id": file_id,
            },
        )
        return (data or {}).get("url", "")

    async def delete_group_file(self, group_id: Union[str, int], file_id: str) -> None:
        await self._call(
            "delete_group_file",
            {
                "group_id": int(group_id),
                "file_id": file_id,
            },
        )

    async def create_group_file_folder(
        self,
        group_id: Union[str, int],
        name: str,
        parent_id: str = "",
    ) -> CreateFolderResult:
        params: dict = {"group_id": int(group_id), "name": name}
        if parent_id:
            params["parent_id"] = parent_id
        resp = await self._call("create_group_file_folder", params)
        data = resp.get("data") or {}
        return CreateFolderResult(**data)

    async def delete_group_folder(
        self, group_id: Union[str, int], folder_id: str
    ) -> None:
        await self._call(
            "delete_group_folder",
            {"group_id": int(group_id), "folder_id": folder_id},
        )

    async def upload_private_file(
        self,
        user_id: Union[str, int],
        file: str,
        name: str,
    ) -> None:
        await self._call(
            "upload_private_file",
            {"user_id": int(user_id), "file": file, "name": name},
        )

    async def download_file(
        self,
        url: str = "",
        file: str = "",
        headers: str = "",
    ) -> DownloadResult:
        params: dict = {}
        if url:
            params["url"] = url
        if file:
            params["file"] = file
        if headers:
            params["headers"] = headers
        data = await self._call_data("download_file", params) or {}
        return DownloadResult(**data)
