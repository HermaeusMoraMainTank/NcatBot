"""
BotAPIClient 规范测试

规范:
  A-02: __getattr__ 兆底透传未定义平台
"""

import pytest

from ncatbot.adapter.mock.api import MockBotAPI
from ncatbot.api.client import BotAPIClient
from ncatbot.api.qq import QQAPIClient


@pytest.fixture
def client():
    mock = MockBotAPI()
    api = BotAPIClient()
    api.register_platform("qq", QQAPIClient(mock))
    return api, mock


# ---- A-02: 未注册平台 ----


def test_unregistered_platform_raises(client):
    """A-02: 访问未注册的平台抛出 KeyError"""
    api, _ = client
    with pytest.raises(KeyError):
        api.platform("telegram")
