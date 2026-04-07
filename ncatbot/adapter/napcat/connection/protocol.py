"""
OB11 请求-响应匹配协议层

使用 echo/UUID + asyncio.Future 实现 API 请求的请求-响应匹配。
同时将非响应消息（事件推送）转发给事件回调。
"""

import asyncio
import uuid
from typing import Dict, Optional, Callable, Awaitable

from ncatbot.utils import get_log

from .websocket import NapCatWebSocket

LOG = get_log("OB11Protocol")


class OB11Protocol:
    """OneBot11 请求-响应协议"""

    def __init__(self, ws: NapCatWebSocket):
        self._ws = ws
        self._event_handler: Optional[Callable[[dict], Awaitable[None]]] = None
        self._pending: Dict[str, asyncio.Future] = {}

    def set_event_handler(self, handler: Callable[[dict], Awaitable[None]]) -> None:
        """设置事件推送回调"""
        self._event_handler = handler

    async def call(
        self,
        action: str,
        params: Optional[dict] = None,
        timeout: float = 30.0,
    ) -> dict:
        """发送 API 请求并等待响应"""
        echo = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[echo] = future

        try:
            await self._ws.send(
                {
                    "action": action.replace("/", ""),
                    "params": params or {},
                    "echo": echo,
                }
            )
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"API 请求超时: {action}")
        finally:
            self._pending.pop(echo, None)

    def cancel_all(self) -> None:
        """取消所有挂起的请求"""
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()

    async def on_message(self, data: dict) -> None:
        """处理收到的消息：区分 API 响应和事件推送

        由 NapCatWebSocket.listen() 调用。
        """
        echo = data.get("echo")

        if echo:
            future = self._pending.get(echo)
            if future and not future.done():
                future.set_result(data)
                return

        # 非 API 响应 → 事件推送
        if self._event_handler:
            await self._event_handler(data)
