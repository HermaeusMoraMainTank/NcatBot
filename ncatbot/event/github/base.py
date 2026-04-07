"""GitHub 事件基类"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..common.base import BaseEvent
from ..common.mixins import HasSender

if TYPE_CHECKING:
    from ncatbot.api.github.interface import IGitHubAPIClient
    from ncatbot.types.github.events import GitHubEventData

__all__ = [
    "GitHubBaseEvent",
]


class GitHubBaseEvent(BaseEvent, HasSender):
    """所有 GitHub 事件的公共基类

    提供 GitHub 事件共有的属性：api、user_id、sender、repo、action。
    子类只需关注各自独有的字段。
    """

    _data: "GitHubEventData"
    _api: "IGitHubAPIClient"

    @property
    def api(self) -> "IGitHubAPIClient":
        return self._api

    @property
    def user_id(self) -> str:
        return self._data.sender.user_id or ""

    @property
    def sender(self) -> Any:
        return self._data.sender

    @property
    def repo(self) -> str:
        return self._data.repo.full_name

    @property
    def action(self) -> str:
        return self._data.action
