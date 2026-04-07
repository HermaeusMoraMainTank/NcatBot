"""GitHub 平台专用类型"""

from .enums import (
    GitHubAction,
    GitHubIssueState,
    GitHubMergeMethod,
    GitHubPostType,
    GitHubPRState,
    GitHubUserType,
)
from .models import (
    GitHubBranchRef,
    GitHubCommentInfo,
    GitHubCommit,
    GitHubForkee,
    GitHubIssueInfo,
    GitHubLabelInfo,
    GitHubMergeResult,
    GitHubPullRequestInfo,
    GitHubRelease,
    GitHubReleaseAsset,
    GitHubReleaseInfo,
    GitHubRepo,
    GitHubRepoInfo,
    GitHubUserInfo,
)
from .sender import GitHubSender
from .events import (
    GitHubEventData,
    GitHubForkEventData,
    GitHubIssueCommentEventData,
    GitHubIssueEventData,
    GitHubPREventData,
    GitHubPRReviewCommentEventData,
    GitHubPushEventData,
    GitHubReleaseEventData,
    GitHubStarEventData,
)

__all__ = [
    # enums
    "GitHubPostType",
    "GitHubAction",
    "GitHubIssueState",
    "GitHubPRState",
    "GitHubUserType",
    "GitHubMergeMethod",
    # event-embedded models
    "GitHubRepo",
    "GitHubCommit",
    "GitHubRelease",
    "GitHubReleaseAsset",
    "GitHubForkee",
    # API response models
    "GitHubUserInfo",
    "GitHubLabelInfo",
    "GitHubBranchRef",
    "GitHubIssueInfo",
    "GitHubCommentInfo",
    "GitHubPullRequestInfo",
    "GitHubMergeResult",
    "GitHubReleaseInfo",
    "GitHubRepoInfo",
    # sender
    "GitHubSender",
    # events
    "GitHubEventData",
    "GitHubIssueEventData",
    "GitHubIssueCommentEventData",
    "GitHubPREventData",
    "GitHubPRReviewCommentEventData",
    "GitHubPushEventData",
    "GitHubStarEventData",
    "GitHubForkEventData",
    "GitHubReleaseEventData",
]
