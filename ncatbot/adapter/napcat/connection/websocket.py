"""
NapCat WebSocket 连接管理

负责 WebSocket 连接的建立、维护、重连和数据收发。
不依赖任何外部 adapter/service 模块。
"""

import asyncio
import json
from typing import Optional, Callable, Awaitable

import websockets
from websockets.exceptions import ConnectionClosedError

from ncatbot.utils import get_log

LOG = get_log("NapCatWebSocket")

# 重连参数
_MAX_RECONNECT_ATTEMPTS = 5
_RECONNECT_BASE_DELAY = 1.0  # 秒
_RECONNECT_MAX_DELAY = 30.0


class NapCatWebSocket:
    """NapCat WebSocket 连接管理器

    纯 WebSocket 连接管理，重连使用指数退避策略，
    不依赖任何外部模块。
    """

    def __init__(self, uri: str):
        self._uri = uri
        self._client: Optional[websockets.ClientConnection] = None
        self._running = False
        self._send_lock = asyncio.Lock()

    async def connect(self) -> None:
        """建立 WebSocket 连接"""
        self._client = await websockets.connect(
            self._uri,
            close_timeout=0.2,
            max_size=2**30,
            open_timeout=5,
        )
        self._running = True
        LOG.info("WebSocket 已连接")

    async def disconnect(self) -> None:
        """关闭 WebSocket 连接"""
        self._running = False
        if self._client:
            await self._client.close()
            self._client = None
        LOG.info("WebSocket 连接已关闭")

    @property
    def connected(self) -> bool:
        return self._client is not None and self._running

    async def send(self, data: dict) -> None:
        """发送 JSON 数据"""
        async with self._send_lock:
            if not self._client:
                raise ConnectionError("WebSocket 未连接")
            await self._client.send(json.dumps(data))

    async def listen(self, on_message: Callable[[dict], Awaitable[None]]) -> None:
        """阻塞监听消息，连接断开时自动重连"""
        if not self._client:
            raise ConnectionError("WebSocket 未连接")

        while self._running:
            try:
                async for raw in self._client:
                    if not self._running:
                        break
                    data = json.loads(raw)
                    await on_message(data)

            except asyncio.CancelledError:
                break

            except ConnectionClosedError:
                if not self._running:
                    break
                LOG.info("连接断开，尝试重连...")
                if not await self._reconnect():
                    raise ConnectionError("WebSocket 重连失败")

            except Exception as e:
                LOG.error(f"WebSocket 错误: {e}")
                raise

    async def _reconnect(self) -> bool:
        """指数退避重连"""
        delay = _RECONNECT_BASE_DELAY
        for attempt in range(1, _MAX_RECONNECT_ATTEMPTS + 1):
            try:
                LOG.info(f"重连尝试 {attempt}/{_MAX_RECONNECT_ATTEMPTS}...")
                self._client = await websockets.connect(
                    self._uri,
                    close_timeout=0.2,
                    max_size=2**30,
                    open_timeout=5,
                )
                LOG.info("重连成功")
                return True
            except Exception as e:
                LOG.warning(f"重连失败: {e}")
                if attempt < _MAX_RECONNECT_ATTEMPTS:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, _RECONNECT_MAX_DELAY)
        return False
