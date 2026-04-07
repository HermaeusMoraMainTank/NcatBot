"""Pull Request 事件实体"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..common.mixins import Replyable, Deletable

from .base import GitHubBaseEvent

if TYPE_CHECKING:
    from ncatbot.types.github.events import (
        GitHubPREventData,
        GitHubPRReviewCommentEventData,
    )

__all__ = [
    "GitHubPREvent",
    "GitHubPRReviewCommentEvent",
]


class GitHubPREvent(GitHubBaseEvent, Replyable):
    """GitHub Pull Request 事件"""

    _data: "GitHubPREventData"

    @property
    def pr_number(self) -> int:
        return self._data.pr_number

    @property
    def pr_title(self) -> str:
        return self._data.pr_title

    @property
    def merged(self) -> bool:
        return self._data.merged

    async def reply(self, text: str, **kwargs: Any) -> Any:
        return await self._api.create_issue_comment(
            repo=self._data.repo.full_name,
            issue_number=self._data.pr_number,
            body=text,
        )


class GitHubPRReviewCommentEvent(GitHubBaseEvent, Replyable, Deletable):
    """GitHub PR Review 评论事件"""

    _data: "GitHubPRReviewCommentEventData"

    @property
    def comment_body(self) -> str:
        return self._data.comment_body

    @property
    def pr_number(self) -> int:
        return self._data.pr_number

    @property
    def path(self) -> str:
        return self._data.path

    async def reply(self, text: str, **kwargs: Any) -> Any:
        return await self._api.create_issue_comment(
            repo=self._data.repo.full_name,
            issue_number=self._data.pr_number,
            body=text,
        )

    async def delete(self) -> Any:
        return await self._api.delete_comment(
            repo=self._data.repo.full_name,
            comment_id=int(self._data.comment_id),
        )
