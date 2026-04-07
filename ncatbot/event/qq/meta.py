"""QQ 元事件实体"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ncatbot.types.qq import MetaEventData, MetaEventType

from ..common.base import BaseEvent

if TYPE_CHECKING:
    from ncatbot.api.qq import QQAPIClient

__all__ = [
    "MetaEvent",
]


class MetaEvent(BaseEvent):
    """QQ 元事件实体"""

    _data: MetaEventData
    _api: QQAPIClient

    @property
    def api(self) -> QQAPIClient:
        return self._api

    @property
    def meta_event_type(self) -> MetaEventType:
        return self._data.meta_event_type
