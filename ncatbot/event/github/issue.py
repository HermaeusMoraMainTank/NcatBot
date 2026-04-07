"""Issue 事件实体"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..common.mixins import Replyable, Deletable

from .base import GitHubBaseEvent

if TYPE_CHECKING:
    from ncatbot.types.github.events import (
        GitHubIssueEventData,
        GitHubIssueCommentEventData,
    )

__all__ = [
    "GitHubIssueEvent",
    "GitHubIssueCommentEvent",
]


class GitHubIssueEvent(GitHubBaseEvent, Replyable):
    """GitHub Issue 事件"""

    _data: "GitHubIssueEventData"

    @property
    def issue_number(self) -> int:
        return self._data.issue_number

    @property
    def issue_title(self) -> str:
        return self._data.issue_title

    @property
    def labels(self) -> list:
        return self._data.labels

    async def reply(self, text: str, **kwargs: Any) -> Any:
        return await self._api.create_issue_comment(
            repo=self._data.repo.full_name,
            issue_number=self._data.issue_number,
            body=text,
        )


class GitHubIssueCommentEvent(GitHubBaseEvent, Replyable, Deletable):
    """GitHub Issue/PR 评论事件"""

    _data: "GitHubIssueCommentEventData"

    @property
    def comment_body(self) -> str:
        return self._data.comment_body

    @property
    def issue_number(self) -> int:
        return self._data.issue_number

    async def reply(self, text: str, **kwargs: Any) -> Any:
        return await self._api.create_issue_comment(
            repo=self._data.repo.full_name,
            issue_number=self._data.issue_number,
            body=text,
        )

    async def delete(self) -> Any:
        return await self._api.delete_comment(
            repo=self._data.repo.full_name,
            comment_id=int(self._data.comment_id),
        )
