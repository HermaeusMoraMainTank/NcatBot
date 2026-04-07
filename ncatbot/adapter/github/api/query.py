"""Query API Mixin"""

from __future__ import annotations

from typing import Any

from ncatbot.types.github import GitHubRepoInfo, GitHubUserInfo


class QueryAPIMixin:
    """查询操作"""

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any: ...

    async def get_repo(self, repo: str) -> GitHubRepoInfo:
        data = await self._request("GET", f"/repos/{repo}")
        return GitHubRepoInfo.model_validate(data)

    async def get_user(self, username: str) -> GitHubUserInfo:
        data = await self._request("GET", f"/users/{username}")
        return GitHubUserInfo.model_validate(data)

    async def get_authenticated_user(self) -> GitHubUserInfo:
        data = await self._request("GET", "/user")
        return GitHubUserInfo.model_validate(data)
