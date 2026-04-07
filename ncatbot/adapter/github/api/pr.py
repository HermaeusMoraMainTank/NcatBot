"""Pull Request API Mixin"""

from __future__ import annotations

from typing import Any, List, Optional, Union

from ncatbot.types.github import GitHubMergeMethod
from ncatbot.types.github import (
    GitHubCommentInfo,
    GitHubMergeResult,
    GitHubPullRequestInfo,
)


class PRAPIMixin:
    """PR 操作"""

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any: ...

    async def create_pr_comment(
        self, repo: str, pr_number: int, body: str
    ) -> GitHubCommentInfo:
        # GitHub API 中 PR 评论与 Issue 评论共用同一端点
        data = await self._request(
            "POST",
            f"/repos/{repo}/issues/{pr_number}/comments",
            json={"body": body},
        )
        return GitHubCommentInfo.model_validate(data)

    async def merge_pr(
        self,
        repo: str,
        pr_number: int,
        *,
        merge_method: Union[GitHubMergeMethod, str] = GitHubMergeMethod.MERGE,
        commit_title: Optional[str] = None,
        commit_message: Optional[str] = None,
    ) -> GitHubMergeResult:
        payload: dict = {"merge_method": str(merge_method)}
        if commit_title is not None:
            payload["commit_title"] = commit_title
        if commit_message is not None:
            payload["commit_message"] = commit_message
        data = await self._request(
            "PUT", f"/repos/{repo}/pulls/{pr_number}/merge", json=payload
        )
        return GitHubMergeResult.model_validate(data)

    async def close_pr(self, repo: str, pr_number: int) -> GitHubPullRequestInfo:
        data = await self._request(
            "PATCH",
            f"/repos/{repo}/pulls/{pr_number}",
            json={"state": "closed"},
        )
        return GitHubPullRequestInfo.model_validate(data)

    async def request_review(
        self, repo: str, pr_number: int, reviewers: List[str]
    ) -> GitHubPullRequestInfo:
        data = await self._request(
            "POST",
            f"/repos/{repo}/pulls/{pr_number}/requested_reviewers",
            json={"reviewers": reviewers},
        )
        return GitHubPullRequestInfo.model_validate(data)

    async def get_pr(self, repo: str, pr_number: int) -> GitHubPullRequestInfo:
        data = await self._request("GET", f"/repos/{repo}/pulls/{pr_number}")
        return GitHubPullRequestInfo.model_validate(data)
