"""GitHub 平台专用枚举"""

from enum import Enum

__all__ = [
    "GitHubPostType",
    "GitHubAction",
    "GitHubIssueState",
    "GitHubPRState",
    "GitHubUserType",
    "GitHubMergeMethod",
]


class GitHubPostType(str, Enum):
    ISSUE = "issue"
    PULL_REQUEST = "pull_request"
    COMMENT = "comment"
    PUSH = "push"
    STAR = "star"
    FORK = "fork"
    RELEASE = "release"


class GitHubAction(str, Enum):
    """GitHub webhook action 字段 — 值与 webhook payload 一致"""

    OPENED = "opened"
    EDITED = "edited"
    CLOSED = "closed"
    REOPENED = "reopened"
    CREATED = "created"
    DELETED = "deleted"
    SUBMITTED = "submitted"
    DISMISSED = "dismissed"
    PUBLISHED = "published"
    SYNCHRONIZE = "synchronize"
    MERGED = "merged"
    LABELED = "labeled"
    UNLABELED = "unlabeled"
    ASSIGNED = "assigned"
    UNASSIGNED = "unassigned"
    REVIEW_REQUESTED = "review_requested"
    REVIEW_REQUEST_REMOVED = "review_request_removed"
    STARTED = "started"


class GitHubIssueState(str, Enum):
    """Issue 状态"""

    OPEN = "open"
    CLOSED = "closed"


class GitHubPRState(str, Enum):
    """Pull Request 状态"""

    OPEN = "open"
    CLOSED = "closed"


class GitHubUserType(str, Enum):
    """GitHub 用户类型"""

    USER = "User"
    BOT = "Bot"
    ORGANIZATION = "Organization"


class GitHubMergeMethod(str, Enum):
    """PR 合并方式"""

    MERGE = "merge"
    SQUASH = "squash"
    REBASE = "rebase"
