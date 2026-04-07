"""Push 事件实体"""

from __future__ import annotations

from typing import Any, List, TYPE_CHECKING

from .base import GitHubBaseEvent

if TYPE_CHECKING:
    from ncatbot.types.github.events import GitHubPushEventData

__all__ = [
    "GitHubPushEvent",
]


class GitHubPushEvent(GitHubBaseEvent):
    """GitHub Push 事件"""

    _data: "GitHubPushEventData"

    @property
    def ref(self) -> str:
        return self._data.ref

    @property
    def before(self) -> str:
        return self._data.before

    @property
    def after(self) -> str:
        return self._data.after

    @property
    def commits(self) -> List[Any]:
        return self._data.commits

    @property
    def head_commit(self) -> Any:
        return self._data.head_commit
