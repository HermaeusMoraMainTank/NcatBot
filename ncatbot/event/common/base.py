from __future__ import annotations

from typing import TYPE_CHECKING

from ncatbot.types.common import BaseEventData

if TYPE_CHECKING:
    from ncatbot.api import IAPIClient

__all__ = [
    "BaseEvent",
]


class BaseEvent:
    """事件实体基类 — 包装数据模型 + API，提供行为方法"""

    __slots__ = ("_data", "_api")

    def __init__(self, data: BaseEventData, api: IAPIClient) -> None:
        self._data = data
        self._api = api

    # ---- 底层访问 ----

    @property
    def api(self) -> IAPIClient:
        return self._api

    @property
    def data(self) -> BaseEventData:
        return self._data

    # ---- BaseEventData 字段 ----

    @property
    def time(self) -> int:
        return self._data.time

    @property
    def self_id(self) -> str:
        return self._data.self_id

    @property
    def post_type(self) -> str:
        return self._data.post_type

    @property
    def platform(self) -> str:
        return self._data.platform

    def __repr__(self) -> str:
        return f"{type(self).__name__}(data={self._data!r})"
