"""GitHub 平台事件工厂"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional, Type

from ncatbot.types import BaseEventData
from ncatbot.types.github import (
    GitHubPostType,
    GitHubIssueEventData,
    GitHubIssueCommentEventData,
    GitHubPREventData,
    GitHubPRReviewCommentEventData,
    GitHubPushEventData,
    GitHubStarEventData,
    GitHubForkEventData,
    GitHubReleaseEventData,
)
from ..common.base import BaseEvent
from .issue import GitHubIssueEvent, GitHubIssueCommentEvent
from .pr import GitHubPREvent, GitHubPRReviewCommentEvent
from .push import GitHubPushEvent
from .misc import GitHubStarEvent, GitHubForkEvent, GitHubReleaseEvent

if TYPE_CHECKING:
    from ncatbot.api import IAPIClient

__all__ = [
    "create_github_entity",
]

# 精确映射：数据模型类 → 实体类
_GITHUB_ENTITY_MAP: Dict[Type[BaseEventData], Type[BaseEvent]] = {
    GitHubIssueEventData: GitHubIssueEvent,
    GitHubIssueCommentEventData: GitHubIssueCommentEvent,
    GitHubPREventData: GitHubPREvent,
    GitHubPRReviewCommentEventData: GitHubPRReviewCommentEvent,
    GitHubPushEventData: GitHubPushEvent,
    GitHubStarEventData: GitHubStarEvent,
    GitHubForkEventData: GitHubForkEvent,
    GitHubReleaseEventData: GitHubReleaseEvent,
}

# post_type → 降级实体基类
_GITHUB_FALLBACK_MAP: Dict[str, Type[BaseEvent]] = {
    GitHubPostType.ISSUE: BaseEvent,
    GitHubPostType.PULL_REQUEST: BaseEvent,
    GitHubPostType.COMMENT: BaseEvent,
    GitHubPostType.PUSH: BaseEvent,
    GitHubPostType.STAR: BaseEvent,
    GitHubPostType.FORK: BaseEvent,
    GitHubPostType.RELEASE: BaseEvent,
}


def create_github_entity(data: BaseEventData, api: "IAPIClient") -> Optional[BaseEvent]:
    """GitHub 平台事件工厂"""
    entity_cls = _GITHUB_ENTITY_MAP.get(type(data))
    if entity_cls is None:
        entity_cls = _GITHUB_FALLBACK_MAP.get(data.post_type)
    if entity_cls is None:
        return None
    return entity_cls(data, api)
