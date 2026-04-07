"""
分发过滤混入类

代理 DispatchFilterService，简化插件中的过滤规则管理。
"""

from typing import List, Optional, TYPE_CHECKING

from ncatbot.utils import get_log

if TYPE_CHECKING:
    from ncatbot.service import ServiceManager
    from ncatbot.service.builtin.dispatch_filter import DispatchFilterService
    from ncatbot.service.builtin.dispatch_filter.model import FilterRule

LOG = get_log("DispatchFilterMixin")


class DispatchFilterMixin:
    """
    分发过滤混入类

    使用示例::

        class MyPlugin(NcatBotPlugin):
            async def on_load(self):
                # 在群 12345 禁用 some_plugin 的全部命令
                self.block_in_group("12345", "some_plugin")

                # 对用户 67890 禁用 some_plugin 的特定命令
                self.block_for_user("67890", "some_plugin", commands=["cmd1"])

                # 解除禁用
                self.unblock_in_group("12345", "some_plugin")
    """

    services: "ServiceManager"

    @property
    def dispatch_filter(self) -> Optional["DispatchFilterService"]:
        """获取分发过滤服务实例。"""
        if not hasattr(self, "services"):
            return None
        return self.services.get("dispatch_filter")  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # 便捷方法 — 群维度
    # ------------------------------------------------------------------

    def block_in_group(
        self,
        group_id: str,
        plugin_name: str,
        commands: Optional[List[str]] = None,
    ) -> Optional["FilterRule"]:
        """在群中禁用指定插件或命令。

        Args:
            group_id: 群号
            plugin_name: 目标插件名（``"*"`` = 全部）
            commands: 要禁用的命令列表（None = 整个插件）

        Returns:
            创建的 FilterRule，服务不可用时返回 None
        """
        return self._add_filter("group", group_id, plugin_name, commands)

    def unblock_in_group(
        self,
        group_id: str,
        plugin_name: str,
        commands: Optional[List[str]] = None,
    ) -> int:
        """解除群中对指定插件或命令的禁用。

        Returns:
            移除的规则数量
        """
        return self._remove_filter("group", group_id, plugin_name, commands)

    # ------------------------------------------------------------------
    # 便捷方法 — 用户维度
    # ------------------------------------------------------------------

    def block_for_user(
        self,
        user_id: str,
        plugin_name: str,
        commands: Optional[List[str]] = None,
    ) -> Optional["FilterRule"]:
        """对指定用户禁用插件或命令。

        Args:
            user_id: 用户号
            plugin_name: 目标插件名（``"*"`` = 全部）
            commands: 要禁用的命令列表（None = 整个插件）

        Returns:
            创建的 FilterRule，服务不可用时返回 None
        """
        return self._add_filter("user", user_id, plugin_name, commands)

    def unblock_for_user(
        self,
        user_id: str,
        plugin_name: str,
        commands: Optional[List[str]] = None,
    ) -> int:
        """解除对指定用户的插件或命令的禁用。

        Returns:
            移除的规则数量
        """
        return self._remove_filter("user", user_id, plugin_name, commands)

    # ------------------------------------------------------------------
    # 查询与批量操作
    # ------------------------------------------------------------------

    def list_filters(
        self,
        scope_type: Optional[str] = None,
        scope_id: Optional[str] = None,
        plugin_name: Optional[str] = None,
    ) -> List["FilterRule"]:
        """条件查询过滤规则。"""
        svc = self.dispatch_filter
        if svc is None:
            LOG.warning("分发过滤服务不可用")
            return []
        return svc.list_rules(
            scope_type=scope_type, scope_id=scope_id, plugin_name=plugin_name
        )

    def clear_filters(self, plugin_name: Optional[str] = None) -> int:
        """批量清除过滤规则。"""
        svc = self.dispatch_filter
        if svc is None:
            LOG.warning("分发过滤服务不可用")
            return 0
        return svc.clear_rules(plugin_name=plugin_name)

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _add_filter(
        self,
        scope_type: str,
        scope_id: str,
        plugin_name: str,
        commands: Optional[List[str]],
    ) -> Optional["FilterRule"]:
        from ncatbot.service.builtin.dispatch_filter.model import FilterRule

        svc = self.dispatch_filter
        if svc is None:
            LOG.warning("分发过滤服务不可用")
            return None
        rule = FilterRule(
            scope_type=scope_type,
            scope_id=scope_id,
            plugin_name=plugin_name,
            commands=list(commands or []),
        )
        return svc.add_rule(rule)

    def _remove_filter(
        self,
        scope_type: str,
        scope_id: str,
        plugin_name: str,
        commands: Optional[List[str]],
    ) -> int:
        svc = self.dispatch_filter
        if svc is None:
            LOG.warning("分发过滤服务不可用")
            return 0
        rules = svc.list_rules(scope_type=scope_type, scope_id=scope_id)
        removed = 0
        for rule in rules:
            if rule.plugin_name != plugin_name:
                continue
            if commands is not None and sorted(rule.commands) != sorted(commands):
                continue
            if svc.remove_rule(rule.rule_id):
                removed += 1
        return removed
