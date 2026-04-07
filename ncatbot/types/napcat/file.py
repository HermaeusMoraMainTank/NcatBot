"""文件操作 API 响应类型"""

from __future__ import annotations

from typing import List, Optional

from ._base import NapCatModel


class GroupFileSystemInfo(NapCatModel):
    """群文件系统信息

    对应: ``get_group_file_system_info``
    """

    file_count: int = 0
    limit_count: int = 0
    used_space: int = 0
    total_space: int = 0


class GroupFileInfo(NapCatModel):
    """群文件信息

    对应: ``get_group_root_files``, ``get_group_files_by_folder`` 中 files 的每一项
    """

    group_id: str = ""
    file_id: str = ""
    file_name: Optional[str] = None
    busid: Optional[int] = None
    size: Optional[int] = None
    file_size: Optional[int] = None
    upload_time: Optional[int] = None
    dead_time: Optional[int] = None
    modify_time: Optional[int] = None
    download_times: Optional[int] = None
    uploader: str = ""
    uploader_name: Optional[str] = None


class GroupFolderInfo(NapCatModel):
    """群文件夹信息

    对应: ``get_group_root_files``, ``get_group_files_by_folder`` 中 folders 的每一项。
    列表 API 返回 snake_case，创建 API 返回 camelCase，均可兼容。
    """

    group_id: str = ""
    folder_id: str = ""
    folder_name: Optional[str] = None
    create_time: Optional[int] = None
    creator: str = ""
    creator_name: Optional[str] = None
    total_file_count: Optional[int] = None


class CreateFolderResultItem(NapCatModel):
    """create_group_file_folder 返回的文件夹详情（camelCase 格式）"""

    folderId: Optional[str] = None
    parentFolderId: Optional[str] = None
    folderName: Optional[str] = None
    createTime: Optional[int] = None
    modifyTime: Optional[int] = None
    createUin: Optional[str] = None
    creatorName: Optional[str] = None
    totalFileCount: Optional[int] = None


class CreateFolderResultGroupItem(NapCatModel):
    """create_group_file_folder 返回的 groupItem 容器"""

    peerId: Optional[str] = None
    type: Optional[int] = None
    folderInfo: Optional[CreateFolderResultItem] = None


class CreateFolderResult(NapCatModel):
    """创建群文件夹的返回结果

    对应: ``create_group_file_folder``
    """

    groupItem: Optional[CreateFolderResultGroupItem] = None


class GroupFileList(NapCatModel):
    """群文件/文件夹列表

    对应: ``get_group_root_files``, ``get_group_files_by_folder``
    """

    files: Optional[List[GroupFileInfo]] = None
    folders: Optional[List[GroupFolderInfo]] = None


class FileData(NapCatModel):
    """通用文件数据

    对应: ``get_file`` — 字段由服务端决定, 通过 extra=allow 接收
    """

    file: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    url: Optional[str] = None


class DownloadResult(NapCatModel):
    """文件下载结果

    对应: ``download_file``
    """

    file: str = ""
