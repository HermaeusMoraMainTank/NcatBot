"""SystemManagerPlugin 心跳超时跳过逻辑测试。

验证：当没有适配器连接（从未收到心跳）时，超时检查不会产生警告。
"""

import time as time_mod

import pytest

import ncatbot.utils.config.manager as config_manager_mod
from ncatbot.core.registry.registrar import _pending_handlers
from ncatbot.testing import PluginTestHarness
from ncatbot.types.qq.meta import HeartbeatMetaEventData
from ncatbot.utils.config.manager import get_config_manager


@pytest.fixture(autouse=True)
def reset_config_singleton(monkeypatch):
    config_manager_mod._default_manager = None
    yield
    config_manager_mod._default_manager = None


@pytest.fixture(autouse=True)
def clean_pending():
    _pending_handlers.clear()
    yield
    _pending_handlers.clear()


def _minimal_config(tmp_path, monkeypatch) -> None:
    p = tmp_path / "cfg.yaml"
    p.write_text(
        "bot_uin: '111111'\n"
        "root: '10001'\n"
        "adapters:\n"
        "  - type: napcat\n"
        "    platform: qq\n"
        "    enabled: true\n"
        "    config: {}\n"
        "plugin:\n"
        "  enable_builtin_commands: true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NCATBOT_CONFIG_PATH", str(p))


def _make_heartbeat_event() -> HeartbeatMetaEventData:
    """构造一个心跳元事件。"""
    return HeartbeatMetaEventData.model_validate(
        {
            "time": int(time_mod.time()),
            "self_id": "111111",
            "platform": "qq",
            "post_type": "meta_event",
            "meta_event_type": "heartbeat",
            "status": {"online": True, "good": True},
            "interval": 30000,
        }
    )


@pytest.mark.asyncio
async def test_no_warning_when_no_heartbeat_received(tmp_path, monkeypatch, caplog):
    """未收到过心跳时，超时检查应静默跳过，不产生警告日志。"""
    _minimal_config(tmp_path, monkeypatch)
    get_config_manager(str(tmp_path / "cfg.yaml"))

    h = PluginTestHarness([], tmp_path, skip_builtin=False)
    await h.start()
    try:
        plugin = h.get_plugin("_system_manager")
        assert plugin is not None

        # 确认初始状态：_heartbeat_active 为 False
        assert plugin._heartbeat_active is False

        # 手动将 _last_heartbeat_time 设为很久以前，模拟"超时"场景
        plugin._last_heartbeat_time = time_mod.time() - 120

        # 手动触发超时检查
        caplog.clear()
        await plugin._check_heartbeat_timeout()

        # 不应产生心跳超时警告
        assert "心跳超时" not in caplog.text
    finally:
        await h.stop()


@pytest.mark.asyncio
async def test_warning_after_heartbeat_received_then_timeout(
    tmp_path, monkeypatch, caplog
):
    """收到心跳后若超时，应正常产生警告。"""
    _minimal_config(tmp_path, monkeypatch)
    get_config_manager(str(tmp_path / "cfg.yaml"))

    h = PluginTestHarness([], tmp_path, skip_builtin=False)
    await h.start()
    try:
        plugin = h.get_plugin("_system_manager")
        assert plugin is not None

        # 等待心跳监听任务注册事件流
        await h.settle(0.05)

        # 注入心跳事件以激活心跳监控
        await h.inject(_make_heartbeat_event())
        await h.settle(0.15)

        # 收到心跳后 _heartbeat_active 应为 True
        assert plugin._heartbeat_active is True

        # 将最后心跳时间设为过去（模拟超时）
        plugin._last_heartbeat_time = time_mod.time() - 120

        # 手动触发超时检查
        caplog.clear()
        await plugin._check_heartbeat_timeout()

        # 应该产生心跳超时警告
        assert "心跳超时" in caplog.text
    finally:
        await h.stop()


@pytest.mark.asyncio
async def test_heartbeat_active_after_first_heartbeat(tmp_path, monkeypatch):
    """收到第一个心跳事件后，_heartbeat_active 应被置为 True。"""
    _minimal_config(tmp_path, monkeypatch)
    get_config_manager(str(tmp_path / "cfg.yaml"))

    h = PluginTestHarness([], tmp_path, skip_builtin=False)
    await h.start()
    try:
        plugin = h.get_plugin("_system_manager")
        assert plugin is not None
        assert plugin._heartbeat_active is False

        # 等待心跳监听任务注册事件流
        await h.settle(0.05)

        await h.inject(_make_heartbeat_event())
        await h.settle(0.15)

        assert plugin._heartbeat_active is True
    finally:
        await h.stop()
