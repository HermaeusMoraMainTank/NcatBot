"""QQFile — 文件操作功能分组"""

from __future__ import annotations

import logging
import shutil
import tempfile
from typing import Any, TYPE_CHECKING, Union

from ncatbot.types.napcat import CreateFolderResult, DownloadResult
from ncatbot.types import Attachment

if TYPE_CHECKING:
    from .interface import IQQAPIClient

log = logging.getLogger("QQFile")


class QQFile:
    """文件上传 / 下载 / 管理"""

    __slots__ = ("_api",)

    def __init__(self, api: IQQAPIClient) -> None:
        self._api = api

    # ---- 基础文件操作 ----

    async def upload_group_file(
        self,
        group_id: Union[str, int],
        file: "Union[str, Attachment]",
        name: str = "",
        folder_id: str = "",
    ) -> None:
        """上传文件到群

        ``file`` 可以是本地路径字符串，也可以直接传入 ``Attachment`` 对象
        （自动下载到临时目录后上传）。当 ``file`` 为 ``Attachment`` 且未指定
        ``name`` 时，自动使用 ``att.name``。
        """
        log.debug(
            "正在上传文件到群 %s: file=%s, name=%s, folder_id=%s",
            group_id,
            file,
            name,
            folder_id,
        )
        path_str, resolved_name, tmp_dir = await self._resolve_file(file, name)
        try:
            await self._api.upload_group_file(
                group_id, path_str, resolved_name, folder_id
            )
        finally:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    async def delete_group_file(self, group_id: Union[str, int], file_id: str) -> None:
        await self._api.delete_group_file(group_id, file_id)

    async def create_group_file_folder(
        self,
        group_id: Union[str, int],
        name: str,
        parent_id: str = "",
    ) -> CreateFolderResult:
        return await self._api.create_group_file_folder(group_id, name, parent_id)

    async def delete_group_folder(
        self, group_id: Union[str, int], folder_id: str
    ) -> None:
        await self._api.delete_group_folder(group_id, folder_id)

    async def upload_private_file(
        self, user_id: Union[str, int], file: "Union[str, Attachment]", name: str = ""
    ) -> None:
        """上传私聊文件

        ``file`` 可以是本地路径字符串，也可以直接传入 ``Attachment`` 对象。
        """
        path_str, resolved_name, tmp_dir = await self._resolve_file(file, name)
        try:
            await self._api.upload_private_file(user_id, path_str, resolved_name)
        finally:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    async def download_file(
        self, url: str = "", file: str = "", headers: str = ""
    ) -> DownloadResult:
        return await self._api.download_file(url, file, headers)

    # ---- 内部辅助 ----

    @staticmethod
    async def _resolve_file(
        file: "Union[str, Attachment]", fallback_name: str = ""
    ) -> "tuple[str, str, str | None]":
        """将 ``file`` 参数统一为 ``(path_str, name, tmp_dir_to_cleanup)``。

        - ``str`` → 直接返回，无需清理
        - ``Attachment`` → 下载到临时目录，返回路径 + att.name + 需清理的临时目录
        """
        if isinstance(file, Attachment):
            tmp_dir = tempfile.mkdtemp(prefix="ncatbot_upload_")
            local_path = await file.download(tmp_dir)
            name = fallback_name or file.name
            return str(local_path), name, tmp_dir
        return str(file), fallback_name, None

    # ---- 语法糖 ----

    async def upload_attachment(
        self,
        target_id: Union[str, int],
        attachment: "Attachment",
        *,
        target_type: str = "group",
        folder_id: str = "",
        folder: str = "",
    ) -> Any:
        """一步上传 Attachment 对象到 QQ 群 / 私聊

        自动处理下载 → 上传 → 清理临时文件的全部流程。

        Parameters
        ----------
        target_id : 群号或用户 ID
        attachment : Attachment 实例
        target_type : ``"group"``（默认）或 ``"private"``
        folder_id : 群文件夹 ID（仅 group）
        folder : 群文件夹名称（仅 group），自动查找 / 创建
        """
        if target_type == "group":
            if folder and not folder_id:
                log.debug(
                    "正在获取或创建群文件夹 '%s'（group_id=%s）", folder, target_id
                )
                folder_id = await self.get_or_create_group_folder(target_id, folder)
            await self.upload_group_file(
                target_id, file=attachment, name=attachment.name, folder_id=folder_id
            )
        else:
            await self.upload_private_file(
                target_id, file=attachment, name=attachment.name
            )

    async def get_or_create_group_folder(
        self,
        group_id: Union[str, int],
        folder_name: str,
        parent_id: str = "",
    ) -> str:
        """查找群文件夹，不存在则自动创建，返回 folder_id。

        支持路径格式 ``"parent/child"`` 自动拆分为两级查找/创建（仅限一级子目录）。
        当同时提供 *parent_id* 和含 ``/`` 的 *folder_name* 时，以 *parent_id* 优先。

        Args:
            group_id: 群号。
            folder_name: 文件夹名称，或 ``"parent/child"`` 路径。
            parent_id: 父文件夹 ID，为空表示根目录。

        Returns:
            文件夹 ID；失败返回空字符串。
        """
        # 路径拆分："parent/child" → 先获取 parent 的 folder_id，再在其下查找 child
        if "/" in folder_name and not parent_id:
            parts = folder_name.split("/", 1)
            if len(parts) == 2 and parts[0] and parts[1]:
                parent_folder_id = await self.get_or_create_group_folder(
                    group_id, parts[0]
                )
                if not parent_folder_id:
                    return ""
                return await self.get_or_create_group_folder(
                    group_id, parts[1], parent_id=parent_folder_id
                )

        # 查询现有文件夹
        try:
            if parent_id:
                file_list = await self._api.get_group_files_by_folder(
                    group_id, parent_id
                )
            else:
                file_list = await self._api.get_group_root_files(group_id)
            for folder in file_list.folders or []:
                if folder.folder_name == folder_name:
                    return folder.folder_id
        except Exception as exc:
            log.warning("获取群文件目录失败: %s", exc)
            return ""

        # 不存在，创建
        try:
            result = await self._api.create_group_file_folder(
                group_id, folder_name, parent_id
            )
            folder_id = (
                result.groupItem.folderInfo.folderId
                if result.groupItem and result.groupItem.folderInfo
                else ""
            ) or ""
            log.info("已创建群文件夹 '%s'，id=%s", folder_name, folder_id)
            return folder_id
        except Exception as exc:
            log.warning("创建群文件夹失败: %s", exc)
            return ""
