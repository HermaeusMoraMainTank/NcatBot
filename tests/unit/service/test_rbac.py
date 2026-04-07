"""
RBAC 子系统单元测试

SC-01 ~ SC-12：覆盖 Trie 路径匹配、Entity 继承与安全、Assigner+Checker 端到端。
"""

import pytest

from ncatbot.service.builtin.rbac.service import RBACService


@pytest.fixture
def rbac() -> RBACService:
    """创建不持久化的 RBACService 实例"""
    return RBACService(storage_path=None, default_role=None)


# ====================================================================
# Trie 核心匹配 (SC-01 ~ SC-04)
# ====================================================================


class TestTrieMatching:
    """SC-01 ~ SC-04: PermissionTrie 路径匹配"""

    def test_sc01_add_and_exists(self, rbac: RBACService):
        """SC-01: add 多段路径后 exists 精确匹配"""
        rbac._permissions.add("plugin.admin.kick")
        rbac._permissions.add("plugin.admin.ban")

        assert rbac._permissions.exists("plugin.admin.kick")
        assert rbac._permissions.exists("plugin.admin.ban")
        assert not rbac._permissions.exists("plugin.admin.mute")

    def test_sc02_wildcard_matching(self, rbac: RBACService):
        """SC-02: * 匹配单层, ** 匹配任意深度"""
        rbac._permissions.add("plugin.admin.kick")
        rbac._permissions.add("plugin.admin.ban")
        rbac._permissions.add("plugin.user.query")

        # * 匹配单层
        assert rbac._permissions.exists("plugin.admin.*")
        assert rbac._permissions.exists("plugin.*.kick")
        # ** 匹配任意深度
        assert rbac._permissions.exists("plugin.**")
        assert rbac._permissions.exists("**")

    def test_sc03_remove(self, rbac: RBACService):
        """SC-03: remove 路径后 exists 返回 False"""
        rbac._permissions.add("a.b.c")
        assert rbac._permissions.exists("a.b.c")

        rbac._permissions.remove("a.b.c")
        assert not rbac._permissions.exists("a.b.c")

    def test_sc04_reject_wildcard_in_add(self, rbac: RBACService):
        """SC-04: add 时拒绝含通配符的路径"""
        with pytest.raises(ValueError):
            rbac._permissions.add("plugin.*.kick")
        with pytest.raises(ValueError):
            rbac._permissions.add("plugin.**")


# ====================================================================
# Entity 继承与安全 (SC-05 ~ SC-08)
# ====================================================================


class TestEntityInheritance:
    """SC-05 ~ SC-08: EntityManager 角色继承与安全"""

    def test_sc05_role_inheritance_chain(self, rbac: RBACService):
        """SC-05: set_role_inheritance A→B→C，user_has_role 递归返回全部"""
        rbac.add_role("admin")
        rbac.add_role("mod")
        rbac.add_role("vip")
        # admin 继承 mod，mod 继承 vip
        rbac.set_role_inheritance("admin", "mod")
        rbac.set_role_inheritance("mod", "vip")

        rbac.add_user("alice")
        rbac._entity_manager.assign_role("alice", "admin")

        assert rbac.user_has_role("alice", "admin")
        assert rbac.user_has_role("alice", "mod")
        assert rbac.user_has_role("alice", "vip")

    def test_sc06_cycle_detection(self, rbac: RBACService):
        """SC-06: _would_create_cycle 阻止循环继承"""
        rbac.add_role("a")
        rbac.add_role("b")
        rbac.set_role_inheritance("a", "b")

        with pytest.raises(ValueError, match="循环继承"):
            rbac.set_role_inheritance("b", "a")

    def test_sc07_remove_role_cascade(self, rbac: RBACService):
        """SC-07: remove_role 级联清理用户角色列表和继承关系"""
        rbac.add_role("admin")
        rbac.add_role("mod")
        rbac.set_role_inheritance("admin", "mod")

        rbac.add_user("alice")
        rbac._entity_manager.assign_role("alice", "admin")
        rbac._entity_manager.assign_role("alice", "mod")

        rbac.remove_role("mod")

        # mod 不再存在
        assert not rbac.role_exists("mod")
        # alice 的角色列表中也移除了 mod
        assert "mod" not in rbac._users["alice"]["roles"]
        # admin 的继承中也移除了 mod
        assert "mod" not in rbac._role_inheritance["admin"]

    def test_sc08_assign_unassign_consistency(self, rbac: RBACService):
        """SC-08: assign_role / unassign_role 双向追踪一致"""
        rbac.add_role("editor")
        rbac.add_user("bob")

        rbac._entity_manager.assign_role("bob", "editor")
        assert "editor" in rbac._users["bob"]["roles"]
        assert "bob" in rbac._role_users["editor"]

        rbac._entity_manager.unassign_role("bob", "editor")
        assert "editor" not in rbac._users["bob"]["roles"]
        assert "bob" not in rbac._role_users["editor"]


# ====================================================================
# Assigner + Checker 端到端 (SC-09 ~ SC-12)
# ====================================================================


class TestPermissionEndToEnd:
    """SC-09 ~ SC-12: grant/revoke + check 端到端"""

    def test_sc09_grant_whitelist_check_pass(self, rbac: RBACService):
        """SC-09: grant whitelist → check 通过"""
        rbac.add_role("user")
        rbac.add_user("alice")
        rbac._entity_manager.assign_role("alice", "user")

        rbac.grant("user", "alice", "plugin.hello", mode="white")
        assert rbac.check("alice", "plugin.hello")

    def test_sc10_grant_blacklist_check_deny(self, rbac: RBACService):
        """SC-10: grant blacklist → check 拒绝"""
        rbac.add_user("alice")
        rbac.grant("user", "alice", "plugin.danger", mode="black")
        assert not rbac.check("alice", "plugin.danger")

    def test_sc11_blacklist_removes_whitelist(self, rbac: RBACService):
        """SC-11: grant blacklist 自动从 whitelist 移除（互斥）"""
        rbac.add_user("alice")
        rbac.grant("user", "alice", "plugin.x", mode="white")
        assert "plugin.x" in rbac._users["alice"]["whitelist"]

        rbac.grant("user", "alice", "plugin.x", mode="black")
        assert "plugin.x" not in rbac._users["alice"]["whitelist"]
        assert "plugin.x" in rbac._users["alice"]["blacklist"]

    def test_sc12_inherited_role_permission(self, rbac: RBACService):
        """SC-12: 角色继承权限传递 — 用户通过继承角色获得权限"""
        rbac.add_role("admin")
        rbac.add_role("mod")
        rbac.set_role_inheritance("admin", "mod")

        # 给 mod 角色授权
        rbac.grant("role", "mod", "plugin.manage.kick", mode="white")

        # alice 只有 admin 角色
        rbac.add_user("alice")
        rbac._entity_manager.assign_role("alice", "admin")

        # 通过继承获得 mod 的权限
        assert rbac.check("alice", "plugin.manage.kick")
