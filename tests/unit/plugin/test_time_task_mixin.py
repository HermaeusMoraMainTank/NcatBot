"""
TimeTaskMixin 规范测试

规范:
  M-30: add_scheduled_task → True (服务可用时)
  M-31: remove_scheduled_task → True (服务可用时)
  M-32: 服务不可用时优雅返回 False/None
  M-33: _mixin_unload 调用 cleanup
"""

import pytest

from ncatbot.plugin.mixin.time_task_mixin import TimeTaskMixin
from ncatbot.service.manager import ServiceManager


class FakeTimeTaskService:
    """模拟 TimeTaskService"""

    def __init__(self):
        self.name = "time_task"
        self.jobs = {}

    def add_job(
        self,
        name,
        interval,
        callback=None,
        conditions=None,
        max_runs=None,
        plugin_name="",
    ):
        self.jobs[name] = {
            "interval": interval,
            "conditions": conditions,
            "max_runs": max_runs,
        }
        return True

    def remove_job(self, name):
        if name in self.jobs:
            del self.jobs[name]
            return True
        return False

    def get_job_status(self, name):
        if name in self.jobs:
            return {"name": name, "next_run": None, "run_count": 0}
        return None


class FakePlugin(TimeTaskMixin):
    """最小 TimeTaskMixin 实例"""

    def __init__(self, service_manager):
        self.name = "test_plugin"
        self.services = service_manager

    async def heartbeat(self): ...

    async def task1(self): ...

    async def to_remove(self): ...

    async def monitor(self): ...


@pytest.fixture
def plugin_with_service():
    """带 TimeTask 服务的 plugin"""
    mgr = ServiceManager()
    svc = FakeTimeTaskService()
    mgr._services["time_task"] = svc
    plugin = FakePlugin(mgr)
    return plugin, svc


@pytest.fixture
def plugin_without_service():
    """不带 TimeTask 服务的 plugin"""
    mgr = ServiceManager()
    return FakePlugin(mgr)


# ---- M-30: add_scheduled_task ----


def test_add_task_returns_true(plugin_with_service):
    """M-30: 服务可用时 add_scheduled_task → True"""
    plugin, svc = plugin_with_service
    result = plugin.add_scheduled_task("heartbeat", "30s")
    assert result is True
    assert "heartbeat" in svc.jobs


def test_add_task_tracked(plugin_with_service):
    """M-30 补充: 添加的任务名被追踪"""
    plugin, _ = plugin_with_service
    plugin.add_scheduled_task("task1", 60)
    assert "task1" in plugin._scheduled_task_names


# ---- M-31: remove_scheduled_task ----


def test_remove_task_returns_true(plugin_with_service):
    """M-31: 移除存在的任务 → True"""
    plugin, svc = plugin_with_service
    plugin.add_scheduled_task("to_remove", "1m")
    result = plugin.remove_scheduled_task("to_remove")
    assert result is True
    assert "to_remove" not in svc.jobs


def test_remove_nonexistent_task(plugin_with_service):
    """M-31 补充: 移除不存在的任务 → False"""
    plugin, _ = plugin_with_service
    result = plugin.remove_scheduled_task("nonexistent")
    assert result is False


# ---- M-32: 服务不可用 ----


def test_add_task_no_service(plugin_without_service):
    """M-32: 无 TimeTask 服务 → False"""
    result = plugin_without_service.add_scheduled_task("task", "1m")
    assert result is False


def test_remove_task_no_service(plugin_without_service):
    """M-32: 无 TimeTask 服务 → False"""
    result = plugin_without_service.remove_scheduled_task("task")
    assert result is False


def test_get_status_no_service(plugin_without_service):
    """M-32: 无 TimeTask 服务 → None"""
    result = plugin_without_service.get_task_status("task")
    assert result is None


# ---- M-33: get_task_status ----


def test_get_task_status(plugin_with_service):
    """M-33: 获取已添加任务的状态"""
    plugin, _ = plugin_with_service
    plugin.add_scheduled_task("monitor", "5m")
    status = plugin.get_task_status("monitor")
    assert status is not None
    assert status["name"] == "monitor"


def test_get_missing_task_status(plugin_with_service):
    """M-33 补充: 不存在的任务 → None"""
    plugin, _ = plugin_with_service
    assert plugin.get_task_status("nonexistent") is None
