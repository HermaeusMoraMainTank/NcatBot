"""
MockAdapter — BaseAdapter 的内存实现

不连接任何外部服务，支持通过 inject_event() 注入事件，
按 platform 自动选择对应的 Mock API 实现。
"""

from __future__ import annotations

import asyncio
from typing import Dict, Optional, Type

from ..base import BaseAdapter
from ncatbot.api import IAPIClient
from ncatbot.types import BaseEventData

from .api_base import MockAPIBase
from .api import MockBotAPI
from .api_bilibili import MockBiliAPI
from .api_github import MockGitHubAPI

_MOCK_REGISTRY: Dict[str, Type[MockAPIBase]] = {
    "qq": MockBotAPI,
    "bilibili": MockBiliAPI,
    "github": MockGitHubAPI,
}


class MockAdapter(BaseAdapter):
    """Mock 适配器 — 按 platform 自动选择 Mock API 实现"""

    name = "mock"
    description = "Mock 适配器（测试用）"
    supported_protocols = ["mock"]
    platform = "mock"

    def __init__(self, platform: str = "qq", **kwargs) -> None:
        super().__init__(**kwargs)
        self.platform = platform
        mock_cls = _MOCK_REGISTRY.get(platform, MockAPIBase)
        self._mock_api = mock_cls()
        self._connected = False
        self._stop_event: Optional[asyncio.Event] = None

    @property
    def mock_api(self) -> MockAPIBase:
        return self._mock_api

    # ---- 生命周期 ----

    async def setup(self) -> None:
        pass

    async def connect(self) -> None:
        self._connected = True
        self._stop_event = asyncio.Event()

    async def disconnect(self) -> None:
        self._connected = False
        if self._stop_event:
            self._stop_event.set()

    async def listen(self) -> None:
        """阻塞直到 stop() 被调用"""
        if self._stop_event is None:
            raise RuntimeError("MockAdapter 尚未 connect")
        await self._stop_event.wait()

    def get_api(self) -> IAPIClient:
        return self._mock_api

    @property
    def connected(self) -> bool:
        return self._connected

    # ---- 测试专用 ----

    async def inject_event(self, data: BaseEventData) -> None:
        """注入事件，触发 dispatcher 回调"""
        if self._event_callback is None:
            raise RuntimeError("事件回调未设置（BotClient 尚未启动）")
        await self._event_callback(data)

    def stop(self) -> None:
        """停止 listen 循环"""
        if self._stop_event:
            self._stop_event.set()
