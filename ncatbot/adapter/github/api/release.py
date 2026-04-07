"""Release API Mixin"""

from __future__ import annotations

from typing import Any, List, Union

from ncatbot.types.github import GitHubReleaseAsset, GitHubReleaseInfo


class ReleaseAPIMixin:
    """Release 操作"""

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any: ...

    async def get_release(
        self, repo: str, release_id: Union[str, int]
    ) -> GitHubReleaseInfo:
        data = await self._request("GET", f"/repos/{repo}/releases/{release_id}")
        return GitHubReleaseInfo.model_validate(data)

    async def get_release_by_tag(self, repo: str, tag: str) -> GitHubReleaseInfo:
        data = await self._request("GET", f"/repos/{repo}/releases/tags/{tag}")
        return GitHubReleaseInfo.model_validate(data)

    async def get_latest_release(self, repo: str) -> GitHubReleaseInfo:
        data = await self._request("GET", f"/repos/{repo}/releases/latest")
        return GitHubReleaseInfo.model_validate(data)

    async def list_release_assets(
        self, repo: str, release_id: Union[str, int]
    ) -> List[GitHubReleaseAsset]:
        raw = await self._request("GET", f"/repos/{repo}/releases/{release_id}/assets")
        if not isinstance(raw, list):
            return []
        return [GitHubReleaseAsset.model_validate(item) for item in raw]
