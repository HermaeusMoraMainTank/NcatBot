"""
RBACMixin 规范测试

规范:
  M-40: check_permission / add_permission 代理到 RBACService
  M-41: 服务不可用时返回 False
"""

import pytest

from ncatbot.plugin.mixin.rbac_mixin import RBACMixin
from ncatbot.service.manager import ServiceManager


class FakeRBACService:
    """模拟 RBACService"""

    def __init__(self):
        self.name = "rbac"
        self._permissions = set()
        self._roles = {}
        self._user_roles = {}

    def add_permission(self, path):
        self._permissions.add(path)

    def remove_permission(self, path):
        self._permissions.discard(path)

    def check(self, user, permission):
        user_roles = self._user_roles.get(user, set())
        for role in user_roles:
            if permission in self._roles.get(role, set()):
                return True
        return False

    def add_role(self, role, exist_ok=True):
        if role not in self._roles:
            self._roles[role] = set()

    def user_has_role(self, user, role):
        return role in self._user_roles.get(user, set())

    def assign_role_permission(self, role, permission):
        if role in self._roles:
            self._roles[role].add(permission)

    def assign_user_role(self, user, role):
        self._user_roles.setdefault(user, set()).add(role)


class FakePlugin(RBACMixin):
    """最小 RBACMixin 实例"""

    def __init__(self, service_manager):
        self.services = service_manager


@pytest.fixture
def plugin_with_rbac():
    """带 RBAC 服务的 plugin"""
    mgr = ServiceManager()
    svc = FakeRBACService()
    mgr._services["rbac"] = svc
    plugin = FakePlugin(mgr)
    return plugin, svc


@pytest.fixture
def plugin_without_rbac():
    """不带 RBAC 服务的 plugin"""
    mgr = ServiceManager()
    return FakePlugin(mgr)


# ---- M-40: 代理到 RBACService ----


def test_add_permission(plugin_with_rbac):
    """M-40: add_permission 代理到 RBACService"""
    plugin, svc = plugin_with_rbac
    plugin.add_permission("test.admin")
    assert "test.admin" in svc._permissions


def test_remove_permission(plugin_with_rbac):
    """M-40: remove_permission 代理到 RBACService"""
    plugin, svc = plugin_with_rbac
    svc._permissions.add("test.admin")
    plugin.remove_permission("test.admin")
    assert "test.admin" not in svc._permissions


def test_check_permission_true(plugin_with_rbac):
    """M-40: 权限存在 → True"""
    plugin, svc = plugin_with_rbac
    svc.add_role("admin")
    svc.assign_role_permission("admin", "test.perm")
    svc.assign_user_role("user123", "admin")

    assert plugin.check_permission("user123", "test.perm") is True


def test_check_permission_false(plugin_with_rbac):
    """M-40: 权限不存在 → False"""
    plugin, _ = plugin_with_rbac
    assert plugin.check_permission("user123", "nonexistent") is False


def test_add_role(plugin_with_rbac):
    """M-40: add_role 代理到 RBACService"""
    plugin, svc = plugin_with_rbac
    plugin.add_role("moderator")
    assert "moderator" in svc._roles


def test_user_has_role_true(plugin_with_rbac):
    """M-40: 用户有角色 → True"""
    plugin, svc = plugin_with_rbac
    svc.add_role("admin")
    svc.assign_user_role("user1", "admin")
    assert plugin.user_has_role("user1", "admin") is True


def test_user_has_role_false(plugin_with_rbac):
    """M-40: 用户无角色 → False"""
    plugin, _ = plugin_with_rbac
    assert plugin.user_has_role("user1", "nonexistent") is False


# ---- M-41: 服务不可用 ----


def test_check_permission_no_service(plugin_without_rbac):
    """M-41: 无 RBAC 服务 → False"""
    assert plugin_without_rbac.check_permission("user1", "perm") is False


def test_user_has_role_no_service(plugin_without_rbac):
    """M-41: 无 RBAC 服务 → False"""
    assert plugin_without_rbac.user_has_role("user1", "role") is False


def test_add_permission_no_service(plugin_without_rbac):
    """M-41: 无 RBAC 服务 → 不抛异常"""
    plugin_without_rbac.add_permission("test.perm")  # 应该只是静默忽略


def test_add_role_no_service(plugin_without_rbac):
    """M-41: 无 RBAC 服务 → 不抛异常"""
    plugin_without_rbac.add_role("test_role")  # 应该只是静默忽略
