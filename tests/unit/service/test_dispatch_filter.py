"""
DispatchFilter 子系统单元测试

DF-01 ~ DF-12：覆盖 FilterRule 匹配、Service CRUD、持久化、is_blocked 查询。
"""

import pytest

from ncatbot.service.builtin.dispatch_filter.model import FilterRule
from ncatbot.service.builtin.dispatch_filter.service import DispatchFilterService


@pytest.fixture
def svc() -> DispatchFilterService:
    """创建不持久化的 DispatchFilterService 实例"""
    return DispatchFilterService(storage_path=None)


# ====================================================================
# FilterRule 匹配 (DF-01 ~ DF-04)
# ====================================================================


class TestFilterRuleMatching:
    """DF-01 ~ DF-04: FilterRule.matches() 匹配"""

    def test_df01_group_scope_match(self):
        """DF-01: scope_type=group 匹配对应 group_id"""
        rule = FilterRule(scope_type="group", scope_id="100", plugin_name="my_plugin")
        assert rule.matches("my_plugin", None, group_id="100", user_id="1")
        assert not rule.matches("my_plugin", None, group_id="200", user_id="1")

    def test_df02_user_scope_match(self):
        """DF-02: scope_type=user 匹配对应 user_id"""
        rule = FilterRule(scope_type="user", scope_id="42", plugin_name="my_plugin")
        assert rule.matches("my_plugin", None, group_id=None, user_id="42")
        assert not rule.matches("my_plugin", None, group_id=None, user_id="99")

    def test_df03_wildcard_plugin(self):
        """DF-03: plugin_name='*' 匹配任何插件"""
        rule = FilterRule(scope_type="group", scope_id="100", plugin_name="*")
        assert rule.matches("plugin_a", None, group_id="100", user_id=None)
        assert rule.matches("plugin_b", None, group_id="100", user_id=None)

    def test_df04_command_filter(self):
        """DF-04: commands 列表精确过滤特定命令"""
        rule = FilterRule(
            scope_type="group",
            scope_id="100",
            plugin_name="my_plugin",
            commands=["cmd1", "cmd2"],
        )
        assert rule.matches("my_plugin", "cmd1", group_id="100", user_id=None)
        assert rule.matches("my_plugin", "cmd2", group_id="100", user_id=None)
        assert not rule.matches("my_plugin", "cmd3", group_id="100", user_id=None)
        # 空 commands = 整个插件；这里 commands 非空，command=None → 不拦截非命令 handler
        assert not rule.matches("my_plugin", None, group_id="100", user_id=None)


# ====================================================================
# Service CRUD (DF-05 ~ DF-08)
# ====================================================================


class TestServiceCRUD:
    """DF-05 ~ DF-08: Service 增删查清"""

    def test_df05_add_and_query(self, svc: DispatchFilterService):
        """DF-05: add_rule 后 list_rules 能查到"""
        rule = FilterRule(scope_type="group", scope_id="100", plugin_name="p1")
        svc.add_rule(rule)
        result = svc.list_rules()
        assert len(result) == 1
        assert result[0].plugin_name == "p1"

    def test_df06_remove_rule(self, svc: DispatchFilterService):
        """DF-06: remove_rule 移除指定规则"""
        rule = svc.add_rule(
            FilterRule(scope_type="group", scope_id="1", plugin_name="p1")
        )
        assert svc.remove_rule(rule.rule_id)
        assert svc.list_rules() == []

    def test_df06_remove_nonexistent(self, svc: DispatchFilterService):
        """DF-06b: remove_rule 不存在的 ID 返回 False"""
        assert not svc.remove_rule("nonexistent")

    def test_df07_list_rules_filtered(self, svc: DispatchFilterService):
        """DF-07: list_rules 支持按条件过滤"""
        svc.add_rule(FilterRule(scope_type="group", scope_id="1", plugin_name="p1"))
        svc.add_rule(FilterRule(scope_type="user", scope_id="2", plugin_name="p2"))
        svc.add_rule(FilterRule(scope_type="group", scope_id="3", plugin_name="p1"))

        assert len(svc.list_rules(scope_type="group")) == 2
        assert len(svc.list_rules(scope_type="user")) == 1
        assert len(svc.list_rules(plugin_name="p1")) == 2
        assert len(svc.list_rules(scope_type="group", plugin_name="p2")) == 0

    def test_df08_clear_rules(self, svc: DispatchFilterService):
        """DF-08: clear_rules 批量清除"""
        svc.add_rule(FilterRule(scope_type="group", scope_id="1", plugin_name="p1"))
        svc.add_rule(FilterRule(scope_type="group", scope_id="2", plugin_name="p2"))
        assert svc.clear_rules(plugin_name="p1") == 1
        assert len(svc.list_rules()) == 1
        assert svc.clear_rules() == 1
        assert len(svc.list_rules()) == 0


# ====================================================================
# is_blocked 查询 (DF-09 ~ DF-11)
# ====================================================================


class TestIsBlocked:
    """DF-09 ~ DF-11: is_blocked 端到端"""

    def test_df09_no_rules_passthrough(self, svc: DispatchFilterService):
        """DF-09: 无规则时所有查询返回 False"""
        assert not svc.is_blocked("any", "cmd", group_id="1", user_id="2")

    def test_df10_group_block(self, svc: DispatchFilterService):
        """DF-10: group 规则拦截对应群的插件"""
        svc.add_rule(FilterRule(scope_type="group", scope_id="100", plugin_name="p1"))
        assert svc.is_blocked("p1", None, group_id="100", user_id="1")
        assert not svc.is_blocked("p1", None, group_id="200", user_id="1")
        assert not svc.is_blocked("p2", None, group_id="100", user_id="1")

    def test_df10_user_block(self, svc: DispatchFilterService):
        """DF-10b: user 规则拦截对应用户的插件"""
        svc.add_rule(FilterRule(scope_type="user", scope_id="42", plugin_name="p1"))
        assert svc.is_blocked("p1", None, group_id=None, user_id="42")
        assert not svc.is_blocked("p1", None, group_id=None, user_id="99")

    def test_df11_command_level_block(self, svc: DispatchFilterService):
        """DF-11: 命令级拦截 — 仅拦截指定命令"""
        svc.add_rule(
            FilterRule(
                scope_type="group",
                scope_id="100",
                plugin_name="p1",
                commands=["forbidden"],
            )
        )
        assert svc.is_blocked("p1", "forbidden", group_id="100", user_id="1")
        assert not svc.is_blocked("p1", "allowed", group_id="100", user_id="1")


# ====================================================================
# 持久化 (DF-12)
# ====================================================================


class TestPersistence:
    """DF-12: 持久化与恢复"""

    def test_df12_save_and_load(self, tmp_path):
        """DF-12: 保存规则后新实例能恢复状态"""
        path = tmp_path / "filter.json"

        svc1 = DispatchFilterService(storage_path=str(path))
        svc1.add_rule(FilterRule(scope_type="group", scope_id="1", plugin_name="p1"))
        svc1.add_rule(
            FilterRule(
                scope_type="user",
                scope_id="2",
                plugin_name="p2",
                commands=["cmd"],
            )
        )
        svc1.save()

        # 新实例从文件恢复
        svc2 = DispatchFilterService(storage_path=str(path))
        svc2._load_from_file()
        assert len(svc2.list_rules()) == 2
        assert svc2.is_blocked("p1", None, group_id="1", user_id=None)
        assert svc2.is_blocked("p2", "cmd", group_id=None, user_id="2")
