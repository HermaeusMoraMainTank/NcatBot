"""
AdapterRegistry 单元测试

规范:
  AR-01: register + discover 已注册适配器可被发现
  AR-02: list_available 返回所有已注册名称
  AR-03: create 根据 AdapterEntry 创建实例
  AR-04: create 指定 platform 覆盖默认值
  AR-05: create 未知类型抛出 ValueError
"""

import pytest

from ncatbot.adapter.registry import AdapterRegistry
from ncatbot.adapter.base import BaseAdapter
from ncatbot.utils.config.models import AdapterEntry


# ---- Stub Adapter ----


class StubAdapter(BaseAdapter):
    name = "stub"
    description = "test stub"
    supported_protocols = ["ws"]
    platform = "test"

    async def setup(self):
        pass

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def listen(self, callback):
        pass

    def get_api(self):
        return None

    @property
    def connected(self):
        return False


# ---- AR-01 ----


def test_register_and_discover():
    """AR-01: 注册适配器后 discover() 返回包含该适配器"""
    reg = AdapterRegistry()
    reg.register("stub", StubAdapter)

    discovered = reg.discover()
    assert "stub" in discovered
    assert discovered["stub"] is StubAdapter


# ---- AR-02 ----


def test_list_available():
    """AR-02: list_available 返回所有已注册名称"""
    reg = AdapterRegistry()
    reg.register("alpha", StubAdapter)
    reg.register("beta", StubAdapter)

    available = reg.list_available()
    assert "alpha" in available
    assert "beta" in available
    assert len(available) >= 2


# ---- AR-03 ----


def test_create_returns_adapter_instance():
    """AR-03: create 根据 AdapterEntry 创建正确的适配器实例"""
    reg = AdapterRegistry()
    reg.register("stub", StubAdapter)

    entry = AdapterEntry(type="stub", config={"key": "val"})
    adapter = reg.create(entry, bot_uin="12345", websocket_timeout=10)

    assert isinstance(adapter, StubAdapter)
    assert adapter._raw_config == {"key": "val"}
    assert adapter._bot_uin == "12345"
    assert adapter._websocket_timeout == 10


# ---- AR-04 ----


def test_create_overrides_platform():
    """AR-04: AdapterEntry.platform 非空时覆盖适配器默认 platform"""
    reg = AdapterRegistry()
    reg.register("stub", StubAdapter)

    entry = AdapterEntry(type="stub", platform="custom")
    adapter = reg.create(entry)

    assert adapter.platform == "custom"


def test_create_keeps_default_platform():
    """AR-04b: AdapterEntry.platform 为空时保留适配器默认 platform"""
    reg = AdapterRegistry()
    reg.register("stub", StubAdapter)

    entry = AdapterEntry(type="stub")
    adapter = reg.create(entry)

    assert adapter.platform == "test"  # StubAdapter 默认 platform


# ---- AR-05 ----


def test_create_unknown_type_raises():
    """AR-05: 未知适配器类型抛出 ValueError"""
    reg = AdapterRegistry()

    entry = AdapterEntry(type="nonexistent")
    with pytest.raises(ValueError, match="未知的适配器类型"):
        reg.create(entry)
