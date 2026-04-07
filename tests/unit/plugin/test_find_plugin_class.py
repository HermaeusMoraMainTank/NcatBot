"""
find_plugin_class 单元测试

FC-01: 指定 entry_class → 正确找到
FC-02: 指定 entry_class 不存在 → 带诊断的 ImportError
FC-03: 指定 entry_class 不是 BasePlugin 子类 → 带诊断的 ImportError
FC-04: 自动发现 → 找到模块中定义的 BasePlugin 子类
FC-05: 自动发现 → 排除导入的框架类（如 NcatBotPlugin）
FC-06: 自动发现 fallback → __module__ 不匹配但唯一候选时采用并警告
FC-07: 自动发现 → 模块中无 BasePlugin 子类时报错带诊断
"""

from types import ModuleType

import pytest

from ncatbot.plugin.base import BasePlugin
from ncatbot.plugin.loader.importer import ModuleImporter
from ncatbot.plugin.ncatbot_plugin import NcatBotPlugin


# ── 测试用桩类 ──────────────────────────────────────────


class _GoodPlugin(BasePlugin):
    name = "good"
    version = "1.0.0"


class _NotAPlugin:
    pass


# ── Helpers ─────────────────────────────────────────────


def _make_module(name: str, attrs: dict) -> ModuleType:
    """创建虚拟模块并注入属性。"""
    mod = ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


def _make_local_plugin(module_name: str, cls_name: str = "MyPlugin"):
    """创建一个 __module__ 与给定 module_name 一致的 BasePlugin 子类。"""
    cls = type(
        cls_name,
        (BasePlugin,),
        {
            "name": cls_name.lower(),
            "version": "1.0.0",
            "__module__": module_name,
        },
    )
    return cls


@pytest.fixture
def importer() -> ModuleImporter:
    return ModuleImporter()


# ── FC-01: 指定 entry_class 成功 ────────────────────────


class TestFindByName:
    def test_fc01_find_by_name_success(self, importer: ModuleImporter):
        """FC-01: 指定 entry_class → 正确找到."""
        mod = _make_module("test_plugin.main", {"GoodPlugin": _GoodPlugin})
        result = importer.find_plugin_class(mod, "GoodPlugin")
        assert result is _GoodPlugin

    def test_fc02_find_by_name_missing(self, importer: ModuleImporter):
        """FC-02: entry_class 不在模块中 → ImportError 带诊断."""
        mod = _make_module("test_plugin.main", {})
        with pytest.raises(ImportError, match="类 NonExistent 不在模块命名空间中"):
            importer.find_plugin_class(mod, "NonExistent")

    def test_fc03_find_by_name_not_subclass(self, importer: ModuleImporter):
        """FC-03: entry_class 存在但不是 BasePlugin 子类 → ImportError 带诊断."""
        mod = _make_module("test_plugin.main", {"BadPlugin": _NotAPlugin})
        with pytest.raises(ImportError, match="不是 BasePlugin 子类"):
            importer.find_plugin_class(mod, "BadPlugin")

    def test_fc03b_find_by_name_not_a_type(self, importer: ModuleImporter):
        """FC-03b: entry_class 存在但不是类 → ImportError 带诊断."""
        mod = _make_module("test_plugin.main", {"BadPlugin": "not a class"})
        with pytest.raises(ImportError, match="不是类"):
            importer.find_plugin_class(mod, "BadPlugin")


# ── FC-04 ~ FC-07: 自动发现 ─────────────────────────────


class TestFindByScan:
    def test_fc04_auto_discover_local_class(self, importer: ModuleImporter):
        """FC-04: 自动发现 → 找到模块中定义的 BasePlugin 子类."""
        module_name = "my_plugin.main"
        local_cls = _make_local_plugin(module_name, "MyPlugin")
        mod = _make_module(module_name, {"MyPlugin": local_cls})
        result = importer.find_plugin_class(mod)
        assert result is local_cls

    def test_fc05_auto_discover_skip_framework_class(self, importer: ModuleImporter):
        """FC-05: 自动发现 → 排除 NcatBotPlugin（__module__ 不匹配）."""
        module_name = "my_plugin.main"
        local_cls = _make_local_plugin(module_name, "MyPlugin")
        mod = _make_module(
            module_name,
            {
                "NcatBotPlugin": NcatBotPlugin,
                "MyPlugin": local_cls,
            },
        )
        result = importer.find_plugin_class(mod)
        assert result is local_cls

    def test_fc06_auto_discover_fallback_relaxed(self, importer: ModuleImporter):
        """FC-06: __module__ 不匹配但唯一非框架候选 → 采用并警告."""
        module_name = "my_plugin.main"
        # 创建一个 __module__ 不匹配的类（模拟边界情况）
        mismatched_cls = type(
            "MyPlugin",
            (BasePlugin,),
            {
                "name": "my_plugin",
                "version": "1.0.0",
                "__module__": "other_module.main",  # 不匹配
            },
        )
        mod = _make_module(
            module_name,
            {
                "NcatBotPlugin": NcatBotPlugin,
                "MyPlugin": mismatched_cls,
            },
        )
        result = importer.find_plugin_class(mod)
        assert result is mismatched_cls

    def test_fc07_auto_discover_no_candidates(self, importer: ModuleImporter):
        """FC-07: 无 BasePlugin 子类 → 带诊断的 ImportError."""
        mod = _make_module(
            "my_plugin.main",
            {
                "SomeClass": _NotAPlugin,
                "value": 42,
            },
        )
        with pytest.raises(ImportError, match="无任何 BasePlugin 子类"):
            importer.find_plugin_class(mod)

    def test_fc07b_auto_discover_only_framework_classes(self, importer: ModuleImporter):
        """FC-07b: 仅有框架类（ncatbot.* 模块）→ ImportError 带诊断."""
        mod = _make_module(
            "my_plugin.main",
            {
                "NcatBotPlugin": NcatBotPlugin,
            },
        )
        with pytest.raises(ImportError, match="未找到 BasePlugin 子类"):
            importer.find_plugin_class(mod)
