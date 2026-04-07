"""
事件流

提供异步迭代器接口，支持按类型过滤事件、``async with`` 上下文管理
以及显式 ``aclose()`` 关闭。
"""

from __future__ import annotations

import asyncio
from typing import Optional, Union, TYPE_CHECKING

from ncatbot.types.qq import EventType
from ncatbot.utils import get_log

from .event import Event

if TYPE_CHECKING:
    from .dispatcher import AsyncEventDispatcher

LOG = get_log("EventStream")

# EventType 枚举 → dispatcher resolved type 前缀的映射
_EVENT_TYPE_PREFIX = {
    EventType.MESSAGE: "message",
    EventType.MESSAGE_SENT: "message_sent",
    EventType.NOTICE: "notice",
    EventType.REQUEST: "request",
    EventType.META: "meta_event",
}

# 哨兵值：通知流结束
_STOP = object()


def _build_filter(
    event_type: Optional[Union[str, EventType]],
) -> Optional[str]:
    """将用户传入的过滤参数转为前缀字符串，``None`` 表示不过滤。"""
    if event_type is None:
        return None
    if isinstance(event_type, EventType):
        prefix = _EVENT_TYPE_PREFIX.get(event_type)
        if prefix is None:
            raise ValueError(f"未知的 EventType: {event_type!r}")
        return prefix
    # str → 直接作为前缀
    return event_type


class EventStream:
    """异步事件流。

    通过 :meth:`AsyncEventDispatcher.events` 创建，不应直接实例化。

    支持三种用法::

        # 1. async with
        async with dispatcher.events(EventType.MESSAGE) as stream:
            async for event in stream:
                ...

        # 2. 直接 async for（需手动 aclose）
        stream = dispatcher.events()
        async for event in stream:
            ...
        await stream.aclose()

        # 3. 提前退出
        stream = dispatcher.events()
        try:
            async for event in stream:
                if should_stop(event):
                    break
        finally:
            await stream.aclose()
    """

    def __init__(
        self,
        dispatcher: "AsyncEventDispatcher",
        queue: asyncio.Queue,
        event_type: Optional[Union[str, EventType]] = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._queue = queue
        self._prefix = _build_filter(event_type)
        self._closed = False

    # ---- async iterator protocol ----

    def __aiter__(self) -> "EventStream":
        return self

    async def __anext__(self) -> Event:
        while True:
            if self._closed:
                raise StopAsyncIteration

            item = await self._queue.get()
            if item is _STOP:
                self._closed = True
                raise StopAsyncIteration

            assert isinstance(item, Event)

            # 过滤
            if self._prefix is not None:
                if not (
                    item.type == self._prefix
                    or item.type.startswith(self._prefix + ".")
                ):
                    continue

            return item

    # ---- async context manager ----

    async def __aenter__(self) -> "EventStream":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.aclose()

    # ---- explicit close ----

    async def aclose(self) -> None:
        """关闭事件流，从 dispatcher 注销队列。"""
        if self._closed:
            return
        self._closed = True
        self._dispatcher._unregister_stream(self._queue)
