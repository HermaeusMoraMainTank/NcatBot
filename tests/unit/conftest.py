"""
单元测试公共 fixtures
"""

import pytest
import pytest_asyncio

from ncatbot.adapter.mock.api import MockBotAPI
from ncatbot.core.dispatcher import AsyncEventDispatcher
from ncatbot.core.registry.dispatcher import HandlerDispatcher
from ncatbot.core.registry.registrar import Registrar, _pending_handlers


@pytest.fixture
def mock_api():
    return MockBotAPI()


@pytest_asyncio.fixture
async def event_dispatcher():
    d = AsyncEventDispatcher()
    yield d
    await d.close()


@pytest_asyncio.fixture
async def handler_dispatcher(mock_api):
    hd = HandlerDispatcher(api=mock_api, platform_apis={"qq": mock_api})
    yield hd
    await hd.stop()


@pytest.fixture
def fresh_registrar():
    """每次测试使用全新 Registrar，并清理全局 pending"""
    _pending_handlers.clear()
    return Registrar()


@pytest.fixture
def tmp_plugin_workspace(tmp_path):
    """创建临时插件工作目录"""
    ws = tmp_path / "test_plugin"
    ws.mkdir()
    return ws
