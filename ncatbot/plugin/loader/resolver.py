"""
插件依赖解析器

基于拓扑排序确定插件加载顺序，检测循环依赖和缺失依赖。
"""

from collections import defaultdict, deque
from typing import Dict, List, Set

from packaging.specifiers import SpecifierSet
from packaging.version import parse as parse_version

from ncatbot.utils import get_log

from ..manifest import PluginManifest

LOG = get_log("DependencyResolver")


class PluginCircularDependencyError(Exception):
    """循环依赖错误"""

    def __init__(self, plugins: Set[str]):
        self.plugins = plugins
        super().__init__(f"检测到循环依赖: {plugins}")


class PluginMissingDependencyError(Exception):
    """缺失依赖错误"""

    def __init__(self, plugin: str, missing: str):
        self.plugin = plugin
        self.missing = missing
        super().__init__(f"插件 {plugin} 依赖的 {missing} 未找到")


class PluginVersionError(Exception):
    """版本约束不满足"""

    def __init__(self, plugin: str, dep: str, constraint: str, actual: str):
        super().__init__(f"插件 {plugin} 要求 {dep} {constraint}，实际版本 {actual}")


class DependencyResolver:
    """解析插件依赖图，返回拓扑排序的加载顺序。"""

    def resolve(self, manifests: Dict[str, PluginManifest]) -> List[str]:
        """返回按依赖排序后的插件名列表。

        Raises:
            PluginMissingDependencyError: 依赖的插件不在 manifests 中
            PluginCircularDependencyError: 检测到循环依赖
        """
        # 构建依赖图
        graph: Dict[str, Set[str]] = {}
        for name, manifest in manifests.items():
            deps = set(manifest.dependencies.keys())
            graph[name] = deps

        # 检测缺失依赖
        all_names = set(graph.keys())
        for name, deps in graph.items():
            for dep in deps:
                if dep not in all_names:
                    raise PluginMissingDependencyError(name, dep)

        # Kahn 拓扑排序
        in_degree = {k: 0 for k in graph}
        adj: Dict[str, List[str]] = defaultdict(list)
        for cur, deps in graph.items():
            for d in deps:
                adj[d].append(cur)
                in_degree[cur] += 1

        q = deque(k for k, v in in_degree.items() if v == 0)
        order: List[str] = []
        while q:
            cur = q.popleft()
            order.append(cur)
            for nxt in adj[cur]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    q.append(nxt)

        if len(order) != len(graph):
            raise PluginCircularDependencyError(set(graph) - set(order))

        return order

    def resolve_subset(
        self,
        manifests: Dict[str, PluginManifest],
        target_names: List[str],
    ) -> List[str]:
        """解析 target_names 及其传递依赖，返回按依赖排序的加载列表。

        Args:
            manifests: 全部已索引的插件清单
            target_names: 需要加载的目标插件名

        Raises:
            PluginMissingDependencyError: 目标或其依赖不在 manifests 中
            PluginCircularDependencyError: 子集内检测到循环依赖
        """
        # 递归收集传递依赖
        needed: Set[str] = set()

        def _collect(name: str) -> None:
            if name in needed:
                return
            manifest = manifests.get(name)
            if manifest is None:
                raise PluginMissingDependencyError(name, name)
            needed.add(name)
            for dep in manifest.dependencies:
                if dep not in manifests:
                    raise PluginMissingDependencyError(name, dep)
                _collect(dep)

        for t in target_names:
            _collect(t)

        # 对子集跑拓扑排序
        subset = {n: manifests[n] for n in needed}
        return self.resolve(subset)

    def validate_versions(self, manifests: Dict[str, PluginManifest]) -> None:
        """检查所有已加载插件间的版本约束是否满足。

        Raises:
            PluginVersionError: 版本约束不满足
        """
        for name, manifest in manifests.items():
            for dep_name, constraint in manifest.dependencies.items():
                dep = manifests.get(dep_name)
                if dep is None:
                    raise PluginMissingDependencyError(name, dep_name)
                if not SpecifierSet(constraint).contains(parse_version(dep.version)):
                    raise PluginVersionError(name, dep_name, constraint, dep.version)
