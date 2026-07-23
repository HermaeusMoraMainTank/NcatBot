"""AstrBot session_waiter 兼容实现。"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Optional


class SessionFilter:
    def filter(self, event: Any) -> str:
        return "default"


class SessionController:
    def __init__(self, timeout: float):
        self._timeout = float(timeout)
        self._deadline = time.monotonic() + self._timeout
        self._stopped = False
        self._history: list = []

    def keep(self, timeout: float, reset_timeout: bool = True) -> None:
        if reset_timeout:
            if timeout <= 0:
                self._stopped = True
                return
            self._timeout = float(timeout)
            self._deadline = time.monotonic() + self._timeout
        else:
            self._deadline = time.monotonic() + max(0.0, self.remaining()) + float(timeout)

    def stop(self) -> None:
        self._stopped = True

    def remaining(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    @property
    def stopped(self) -> bool:
        return self._stopped

    def get_history_chains(self) -> list:
        return self._history


class _ActiveSession:
    def __init__(
        self,
        session_id: str,
        handler: Callable,
        controller: SessionController,
        done: asyncio.Future,
    ):
        self.session_id = session_id
        self.handler = handler
        self.controller = controller
        self.done = done
        self.queue: asyncio.Queue = asyncio.Queue()


_active: dict[str, _ActiveSession] = {}


def get_active_session(session_id: str) -> Optional[_ActiveSession]:
    return _active.get(session_id)


async def feed_session(session_id: str, event: Any) -> bool:
    """由主消息循环喂入会话事件。返回是否命中活跃会话。"""
    sess = _active.get(session_id)
    if sess is None:
        return False
    await sess.queue.put(event)
    return True


def session_waiter(timeout: float = 30, record_history_chains: bool = False):
    """装饰器：把 handler 变成可 await 的会话等待器。"""

    def decorator(handler: Callable):
        async def waiter(event: Any, session_filter: SessionFilter | None = None):
            filt = session_filter or SessionFilter()
            session_id = filt.filter(event)
            loop = asyncio.get_running_loop()
            done: asyncio.Future = loop.create_future()
            controller = SessionController(timeout)
            sess = _ActiveSession(session_id, handler, controller, done)
            _active[session_id] = sess

            try:
                while not controller.stopped:
                    remain = controller.remaining()
                    if remain <= 0:
                        raise TimeoutError("session timeout")
                    try:
                        next_event = await asyncio.wait_for(
                            sess.queue.get(), timeout=remain
                        )
                    except asyncio.TimeoutError as e:
                        raise TimeoutError("session timeout") from e

                    if record_history_chains:
                        controller._history.append(
                            getattr(next_event, "message_obj", None)
                            and getattr(next_event.message_obj, "message", [])
                            or []
                        )

                    await handler(controller, next_event)
                    if controller.stopped:
                        break
            finally:
                _active.pop(session_id, None)
                if not done.done():
                    done.set_result(True)

        return waiter

    return decorator
