"""GitHub 平台事件数据模型"""

from __future__ import annotations

from typing import List, Optional

from pydantic import Field

from ..common.base import BaseEventData

from .enums import GitHubAction, GitHubIssueState, GitHubPRState, GitHubPostType
from .models import GitHubCommit, GitHubForkee, GitHubRelease, GitHubRepo
from .sender import GitHubSender

__all__ = [
    # base
    "GitHubEventData",
    # issue
    "GitHubIssueEventData",
    "GitHubIssueCommentEventData",
    # pr
    "GitHubPREventData",
    "GitHubPRReviewCommentEventData",
    # push
    "GitHubPushEventData",
    # misc
    "GitHubStarEventData",
    "GitHubForkEventData",
    "GitHubReleaseEventData",
]


class GitHubEventData(BaseEventData):
    """GitHub 事件基类"""

    platform: str = "github"
    action: GitHubAction = GitHubAction.CREATED
    repo: GitHubRepo = Field(default_factory=GitHubRepo)
    sender: GitHubSender = Field(default_factory=GitHubSender)


# ==================== Issue 事件 ====================


class GitHubIssueEventData(GitHubEventData):
    """Issue 事件"""

    post_type: str = Field(default=GitHubPostType.ISSUE)
    issue_number: int = 0
    issue_title: str = ""
    issue_body: str = ""
    issue_state: GitHubIssueState = GitHubIssueState.OPEN
    issue_url: str = ""
    labels: List[str] = Field(default_factory=list)
    assignees: List[str] = Field(default_factory=list)


class GitHubIssueCommentEventData(GitHubEventData):
    """Issue/PR 评论事件"""

    post_type: str = Field(default=GitHubPostType.COMMENT)
    comment_id: str = ""
    comment_body: str = ""
    comment_url: str = ""
    issue_number: int = 0
    issue_title: str = ""


# ==================== Pull Request 事件 ====================


class GitHubPREventData(GitHubEventData):
    """Pull Request 事件"""

    post_type: str = Field(default=GitHubPostType.PULL_REQUEST)
    pr_number: int = 0
    pr_title: str = ""
    pr_body: str = ""
    pr_state: GitHubPRState = GitHubPRState.OPEN
    pr_url: str = ""
    head_ref: str = ""
    base_ref: str = ""
    merged: bool = False
    draft: bool = False


class GitHubPRReviewCommentEventData(GitHubEventData):
    """PR Review 评论事件"""

    post_type: str = Field(default=GitHubPostType.COMMENT)
    comment_id: str = ""
    comment_body: str = ""
    comment_url: str = ""
    pr_number: int = 0
    diff_hunk: str = ""
    path: str = ""


# ==================== Push 事件 ====================


class GitHubPushEventData(GitHubEventData):
    """Push 事件"""

    post_type: str = Field(default=GitHubPostType.PUSH)
    ref: str = ""
    before: str = ""
    after: str = ""
    commits: List[GitHubCommit] = Field(default_factory=list)
    head_commit: Optional[GitHubCommit] = None
    pusher_name: str = ""
    pusher_email: str = ""


# ==================== 其他事件 ====================


class GitHubStarEventData(GitHubEventData):
    """Star 事件"""

    post_type: str = Field(default=GitHubPostType.STAR)
    starred_at: str = ""


class GitHubForkEventData(GitHubEventData):
    """Fork 事件"""

    post_type: str = Field(default=GitHubPostType.FORK)
    forkee: GitHubForkee = Field(default_factory=GitHubForkee)


class GitHubReleaseEventData(GitHubEventData):
    """Release 事件"""

    post_type: str = Field(default=GitHubPostType.RELEASE)
    release: GitHubRelease = Field(default_factory=GitHubRelease)
