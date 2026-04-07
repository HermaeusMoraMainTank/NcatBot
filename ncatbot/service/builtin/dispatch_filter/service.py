"""
分发过滤服务

管理分发过滤规则：在群/用户维度禁用指定插件或命令。
规则持久化到 JSON 文件，并在 HandlerDispatcher 的全局 Hook 中生效。
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ...base import BaseService
from .model import FilterRule
from ncatbot.utils import get_log

LOG = get_log("DispatchFilter")


class DispatchFilterService(BaseService):
    """分发过滤服务

    通过维护 ``FilterRule`` 列表，在事件分发前判断是否拦截指定 handler。

    使用示例::

        svc = services.get("dispatch_filter")
        svc.add_rule(FilterRule(scope_type="group", scope_id="12345",
                                plugin_name="my_plugin"))
        assert svc.is_blocked("my_plugin", None, group_id="12345", user_id=None)
    """

    name = "dispatch_filter"
    description = "分发过滤服务：按群/用户禁用插件或命令"

    DEFAULT_STORAGE_PATH = "data/dispatch_filter.json"

    def __init__(
        self,
        storage_path: Optional[str] = DEFAULT_STORAGE_PATH,
        **config,
    ):
        super().__init__(**config)
        self._storage_path = Path(storage_path) if storage_path else None
        self._rules: List[FilterRule] = []
        # 快速索引: (scope_type, scope_id) → [rule, ...]
        self._index: Dict[Tuple[str, str], List[FilterRule]] = {}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def on_load(self) -> None:
        if self._storage_path:
            self._load_from_file()
        LOG.info(
            "分发过滤服务已加载，共 %d 条规则",
            len(self._rules),
        )

    async def on_close(self) -> None:
        if self._storage_path:
            self.save()
        LOG.info("分发过滤服务已关闭")

    # ------------------------------------------------------------------
    # 核心查询
    # ------------------------------------------------------------------

    def is_blocked(
        self,
        plugin_name: str,
        command: Optional[str],
        group_id: Optional[str],
        user_id: Optional[str],
    ) -> bool:
        """查询给定上下文是否被任一规则拦截。"""
        # 检查 group 维度
        if group_id is not None:
            for rule in self._index.get(("group", group_id), []):
                if rule.matches(plugin_name, command, group_id, user_id):
                    return True

        # 检查 user 维度
        if user_id is not None:
            for rule in self._index.get(("user", user_id), []):
                if rule.matches(plugin_name, command, group_id, user_id):
                    return True

        return False

    # ------------------------------------------------------------------
    # 规则管理
    # ------------------------------------------------------------------

    def add_rule(self, rule: FilterRule) -> FilterRule:
        """添加规则并持久化。返回添加的规则（含生成的 rule_id）。"""
        self._rules.append(rule)
        key = (rule.scope_type, rule.scope_id)
        self._index.setdefault(key, []).append(rule)
        self._auto_save()
        LOG.info("添加过滤规则: %s", rule)
        return rule

    def remove_rule(self, rule_id: str) -> bool:
        """按 rule_id 移除规则。"""
        for i, rule in enumerate(self._rules):
            if rule.rule_id == rule_id:
                self._rules.pop(i)
                key = (rule.scope_type, rule.scope_id)
                idx_list = self._index.get(key, [])
                try:
                    idx_list.remove(rule)
                except ValueError:
                    pass
                if not idx_list:
                    self._index.pop(key, None)
                self._auto_save()
                LOG.info("移除过滤规则: %s", rule_id)
                return True
        return False

    def list_rules(
        self,
        scope_type: Optional[str] = None,
        scope_id: Optional[str] = None,
        plugin_name: Optional[str] = None,
    ) -> List[FilterRule]:
        """条件查询规则。"""
        result = self._rules
        if scope_type is not None:
            result = [r for r in result if r.scope_type == scope_type]
        if scope_id is not None:
            result = [r for r in result if r.scope_id == scope_id]
        if plugin_name is not None:
            result = [r for r in result if r.plugin_name == plugin_name]
        return list(result)

    def clear_rules(self, plugin_name: Optional[str] = None) -> int:
        """批量清除规则。返回清除数量。"""
        if plugin_name is None:
            count = len(self._rules)
            self._rules.clear()
            self._index.clear()
        else:
            removed = [r for r in self._rules if r.plugin_name == plugin_name]
            count = len(removed)
            self._rules = [r for r in self._rules if r.plugin_name != plugin_name]
            self._rebuild_index()

        if count:
            self._auto_save()
            LOG.info("清除 %d 条过滤规则 (plugin=%s)", count, plugin_name)
        return count

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def save(self, path: Optional[Path] = None) -> None:
        target = path or self._storage_path
        if not target:
            raise ValueError("未指定存储路径")

        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = [rule.to_dict() for rule in self._rules]
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        LOG.debug("过滤规则已保存到 %s", target)

    def _load_from_file(self) -> None:
        if not self._storage_path or not self._storage_path.exists():
            return
        try:
            data = json.loads(self._storage_path.read_text())
            self._rules = [FilterRule.from_dict(d) for d in data]
            self._rebuild_index()
        except Exception:
            LOG.exception("加载过滤规则失败: %s", self._storage_path)

    def _auto_save(self) -> None:
        if self._storage_path:
            self.save()

    def _rebuild_index(self) -> None:
        self._index.clear()
        for rule in self._rules:
            key = (rule.scope_type, rule.scope_id)
            self._index.setdefault(key, []).append(rule)
