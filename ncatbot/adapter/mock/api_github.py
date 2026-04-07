"""
MockGitHubAPI — IGitHubAPIClient 的内存 Mock 实现
"""

from __future__ import annotations

from typing import List, Optional, Union

from ncatbot.api.github import IGitHubAPIClient
from ncatbot.types.github import GitHubMergeMethod
from ncatbot.types.github import (
    GitHubCommentInfo,
    GitHubIssueInfo,
    GitHubLabelInfo,
    GitHubMergeResult,
    GitHubPullRequestInfo,
    GitHubReleaseAsset,
    GitHubReleaseInfo,
    GitHubRepoInfo,
    GitHubUserInfo,
)

from .api_base import MockAPIBase


class MockGitHubAPI(MockAPIBase, IGitHubAPIClient):
    """IGitHubAPIClient 的完整 Mock 实现 — 命名参数录制"""

    def __init__(self) -> None:
        super().__init__(platform="github")

    # ---- Issue 操作 ----

    async def create_issue(
        self,
        repo: str,
        title: str,
        body: str = "",
        labels: Optional[List[str]] = None,
        assignees: Optional[List[str]] = None,
    ) -> GitHubIssueInfo:
        return self._record(
            "create_issue",
            repo=repo,
            title=title,
            body=body,
            labels=labels,
            assignees=assignees,
        )

    async def update_issue(
        self,
        repo: str,
        issue_number: int,
        *,
        title: Optional[str] = None,
        body: Optional[str] = None,
        state: Optional[str] = None,
        labels: Optional[List[str]] = None,
        assignees: Optional[List[str]] = None,
    ) -> GitHubIssueInfo:
        return self._record(
            "update_issue",
            repo=repo,
            issue_number=issue_number,
            title=title,
            body=body,
            state=state,
            labels=labels,
            assignees=assignees,
        )

    async def close_issue(self, repo: str, issue_number: int) -> GitHubIssueInfo:
        return self._record("close_issue", repo=repo, issue_number=issue_number)

    async def reopen_issue(self, repo: str, issue_number: int) -> GitHubIssueInfo:
        return self._record("reopen_issue", repo=repo, issue_number=issue_number)

    async def get_issue(self, repo: str, issue_number: int) -> GitHubIssueInfo:
        return self._record("get_issue", repo=repo, issue_number=issue_number)

    async def add_labels(
        self,
        repo: str,
        issue_number: int,
        labels: List[str],
    ) -> List[GitHubLabelInfo]:
        return self._record(
            "add_labels", repo=repo, issue_number=issue_number, labels=labels
        )

    async def remove_label(self, repo: str, issue_number: int, label: str) -> None:
        self._record("remove_label", repo=repo, issue_number=issue_number, label=label)

    async def set_assignees(
        self,
        repo: str,
        issue_number: int,
        assignees: List[str],
    ) -> GitHubIssueInfo:
        return self._record(
            "set_assignees", repo=repo, issue_number=issue_number, assignees=assignees
        )

    # ---- 评论操作 ----

    async def create_issue_comment(
        self,
        repo: str,
        issue_number: int,
        body: str,
    ) -> GitHubCommentInfo:
        return self._record(
            "create_issue_comment", repo=repo, issue_number=issue_number, body=body
        )

    async def update_comment(
        self,
        repo: str,
        comment_id: int,
        body: str,
    ) -> GitHubCommentInfo:
        return self._record(
            "update_comment", repo=repo, comment_id=comment_id, body=body
        )

    async def delete_comment(self, repo: str, comment_id: int) -> None:
        self._record("delete_comment", repo=repo, comment_id=comment_id)

    async def list_issue_comments(
        self,
        repo: str,
        issue_number: int,
        page: int = 1,
        per_page: int = 30,
    ) -> List[GitHubCommentInfo]:
        return self._record(
            "list_issue_comments",
            repo=repo,
            issue_number=issue_number,
            page=page,
            per_page=per_page,
        )

    # ---- PR 操作 ----

    async def create_pr_comment(
        self,
        repo: str,
        pr_number: int,
        body: str,
    ) -> GitHubCommentInfo:
        return self._record(
            "create_pr_comment", repo=repo, pr_number=pr_number, body=body
        )

    async def merge_pr(
        self,
        repo: str,
        pr_number: int,
        *,
        merge_method: Union[GitHubMergeMethod, str] = GitHubMergeMethod.MERGE,
        commit_title: Optional[str] = None,
        commit_message: Optional[str] = None,
    ) -> GitHubMergeResult:
        return self._record(
            "merge_pr",
            repo=repo,
            pr_number=pr_number,
            merge_method=merge_method,
            commit_title=commit_title,
            commit_message=commit_message,
        )

    async def close_pr(self, repo: str, pr_number: int) -> GitHubPullRequestInfo:
        return self._record("close_pr", repo=repo, pr_number=pr_number)

    async def request_review(
        self,
        repo: str,
        pr_number: int,
        reviewers: List[str],
    ) -> GitHubPullRequestInfo:
        return self._record(
            "request_review", repo=repo, pr_number=pr_number, reviewers=reviewers
        )

    async def get_pr(self, repo: str, pr_number: int) -> GitHubPullRequestInfo:
        return self._record("get_pr", repo=repo, pr_number=pr_number)

    # ---- 查询 ----

    async def get_repo(self, repo: str) -> GitHubRepoInfo:
        return self._record("get_repo", repo=repo)

    async def get_user(self, username: str) -> GitHubUserInfo:
        return self._record("get_user", username=username)

    async def get_authenticated_user(self) -> GitHubUserInfo:
        return self._record("get_authenticated_user")

    # ---- Release 操作 ----

    async def get_release(
        self,
        repo: str,
        release_id: Union[str, int],
    ) -> GitHubReleaseInfo:
        return self._record("get_release", repo=repo, release_id=release_id)

    async def get_release_by_tag(self, repo: str, tag: str) -> GitHubReleaseInfo:
        return self._record("get_release_by_tag", repo=repo, tag=tag)

    async def get_latest_release(self, repo: str) -> GitHubReleaseInfo:
        return self._record("get_latest_release", repo=repo)

    async def list_release_assets(
        self,
        repo: str,
        release_id: Union[str, int],
    ) -> List[GitHubReleaseAsset]:
        return self._record("list_release_assets", repo=repo, release_id=release_id)
