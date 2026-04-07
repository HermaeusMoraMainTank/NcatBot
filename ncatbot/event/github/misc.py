"""Star / Fork / Release 事件实体"""

from __future__ import annotations

from typing import List

from .base import GitHubBaseEvent
from ..common.mixins import HasAttachments
from ncatbot.types.common import (
    AttachmentList,
    Attachment,
)
from ncatbot.types.github import (
    GitHubStarEventData,
    GitHubForkEventData,
    GitHubReleaseEventData,
)
from ncatbot.types.github import (
    GitHubForkee,
    GitHubRelease,
    GitHubReleaseAsset,
    GitHubReleaseInfo,
)

__all__ = [
    "GitHubStarEvent",
    "GitHubForkEvent",
    "GitHubReleaseEvent",
]


class GitHubStarEvent(GitHubBaseEvent):
    """GitHub Star 事件"""

    _data: "GitHubStarEventData"

    @property
    def starred_at(self) -> str:
        return self._data.starred_at


class GitHubForkEvent(GitHubBaseEvent):
    """GitHub Fork 事件"""

    _data: "GitHubForkEventData"

    @property
    def forkee(self) -> "GitHubForkee":
        return self._data.forkee

    @property
    def forkee_full_name(self) -> str:
        return self._data.forkee.full_name


class GitHubReleaseEvent(GitHubBaseEvent, HasAttachments):
    """GitHub Release 事件"""

    _data: "GitHubReleaseEventData"

    @property
    def release(self) -> "GitHubRelease":
        return self._data.release

    @property
    def release_tag(self) -> str:
        return self._data.release.tag_name

    @property
    def release_name(self) -> str:
        return self._data.release.name

    @property
    def release_body(self) -> str:
        return self._data.release.body

    @property
    def prerelease(self) -> bool:
        return self._data.release.prerelease

    async def get_assets(self) -> "List[GitHubReleaseAsset]":
        """获取该 Release 的所有 Assets"""
        return await self._api.list_release_assets(
            repo=self._data.repo.full_name,
            release_id=self._data.release.id,
        )

    async def get_full_release(self) -> "GitHubReleaseInfo":
        """通过 GitHub API 获取该 Release 的完整信息（含 assets 等）"""
        return await self._api.get_release_by_tag(
            repo=self._data.repo.full_name,
            tag=self._data.release.tag_name,
        )

    async def get_attachments(self) -> "AttachmentList[Attachment]":
        """将 Release Assets 转换为跨平台 Attachment 列表

        通过 BotAPI 统一转换，自动应用 network_mode 的镜像/代理策略。
        """
        assets = await self.get_assets()
        return self._api.assets_to_attachments(assets)
