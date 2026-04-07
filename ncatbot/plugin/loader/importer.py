"""
模块导入器

负责插件模块的加载和卸载。
跨插件导入通过将插件根目录添加到 sys.path 实现，无需虚拟包或自定义 Finder。
"""

import importlib
import importlib.util
import shutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Optional, Set, Type

from ncatbot.utils import get_log

from ..base import BasePlugin
from ..manifest import PluginManifest

LOG = get_log("ModuleImporter")


class ModuleImporter:
    """插件模块的加载与卸载。

    跨插件导入机制：

    - 将插件根目录 ``append`` 到 ``sys.path``（低于 stdlib / 第三方包优先级）。
    - 每个插件文件夹即为一个普通 Python 包。
    - 插件 A 可通过 ``from plugin_b_folder.module import xxx`` 导入插件 B 的代码。
    - 插件内部相对导入 ``from . import utils`` 正常工作。
    """

    def __init__(self) -> None:
        self._plugin_roots: Set[str] = set()

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def add_plugin_root(self, root_dir: str | object) -> None:
        """将插件根目录添加到 sys.path（仅一次，append 避免遮蔽标准库）。"""
        root_str = str(Path(str(root_dir)).resolve())
        if root_str not in self._plugin_roots:
            self._plugin_roots.add(root_str)
            sys.path.append(root_str)
            LOG.debug("已添加插件根目录到 sys.path: %s", root_str)

    def load_module(self, manifest: PluginManifest) -> ModuleType:
        """加载插件入口模块并返回。

        Raises:
            ImportError: 无法加载模块
        """
        importlib.invalidate_caches()
        self._clear_pycache(manifest)

        pkg_name = manifest.folder_name
        module_name = f"{pkg_name}.{manifest.entry_stem}"
        plugin_path = str(manifest.plugin_path)

        created_pkg = False
        created_module = False
        try:
            # 确保插件包存在于 sys.modules
            if pkg_name not in sys.modules:
                init_file = manifest.plugin_path / "__init__.py"
                if init_file.exists():
                    pkg_spec = importlib.util.spec_from_file_location(
                        pkg_name,
                        str(init_file),
                        submodule_search_locations=[plugin_path],
                    )
                    if pkg_spec and pkg_spec.loader:
                        pkg = importlib.util.module_from_spec(pkg_spec)
                        pkg.__path__ = [plugin_path]
                        pkg.__package__ = pkg_name
                        sys.modules[pkg_name] = pkg
                        pkg_spec.loader.exec_module(pkg)
                    else:
                        raise ImportError(
                            f"无法为插件包 {pkg_name} 创建 spec: {init_file}"
                        )
                else:
                    # 隐式命名空间包
                    pkg = ModuleType(pkg_name)
                    pkg.__path__ = [plugin_path]
                    pkg.__package__ = pkg_name
                    sys.modules[pkg_name] = pkg
                created_pkg = True

            # 加载入口模块
            # __init__.py 中的 ``from .main import ...`` 可能已经触发了入口模块的
            # 导入并执行了装饰器。此时 sys.modules 中已有该模块，直接复用即可，
            # 避免重新 exec 导致装饰器注册出重复的 handler 函数对象。
            existing = sys.modules.get(module_name)
            if existing is not None:
                LOG.debug("入口模块 %s 已由包 __init__ 导入，复用现有模块", module_name)
                return existing

            entry_file = str(manifest.entry_file)
            spec = importlib.util.spec_from_file_location(module_name, entry_file)
            if spec is None or spec.loader is None:
                raise ImportError(
                    f"无法为插件 {manifest.name} 创建模块 spec: {entry_file}"
                )

            module = importlib.util.module_from_spec(spec)
            module.__spec__ = spec
            module.__package__ = pkg_name
            sys.modules[module_name] = module
            created_module = True

            spec.loader.exec_module(module)
            return module

        except Exception:
            if created_module:
                sys.modules.pop(module_name, None)
            if created_pkg:
                sys.modules.pop(pkg_name, None)
            raise

    def unload_module(self, manifest: PluginManifest) -> None:
        """从 sys.modules 中移除插件及其所有子模块。"""
        pkg_name = manifest.folder_name

        to_remove = [
            key
            for key in list(sys.modules)
            if key == pkg_name or key.startswith(f"{pkg_name}.")
        ]
        for key in to_remove:
            del sys.modules[key]

        importlib.invalidate_caches()
        if to_remove:
            LOG.info("已卸载模块 %s (共 %d 个)", manifest.name, len(to_remove))

    def find_plugin_class(
        self, module: ModuleType, class_name: Optional[str] = None
    ) -> Type[BasePlugin]:
        """在模块中查找 BasePlugin 子类。

        Args:
            module: 已加载的模块
            class_name: 指定类名（来自 manifest.entry_class），为 None 时自动发现

        Raises:
            ImportError: 未找到插件类
        """
        if class_name:
            return self._find_by_name(module, class_name)
        return self._find_by_scan(module)

    def _find_by_name(self, module: ModuleType, class_name: str) -> Type[BasePlugin]:
        """根据 manifest.entry_class 指定的类名查找。"""
        obj = getattr(module, class_name, None)
        if obj is not None and isinstance(obj, type) and issubclass(obj, BasePlugin):
            return obj

        # 查找失败，生成诊断信息
        candidates = self._collect_candidates(module)
        detail = self._format_diag(module, candidates)

        if obj is None:
            reason = f"类 {class_name} 不在模块命名空间中"
        elif not isinstance(obj, type):
            reason = f"{class_name} 存在但不是类（实际类型: {type(obj).__name__}）"
        else:
            reason = (
                f"{class_name} 存在但不是 BasePlugin 子类"
                f"（MRO: {[c.__name__ for c in obj.__mro__]}）"
            )
        raise ImportError(
            f"模块 {module.__name__} 中未找到 BasePlugin 子类: {class_name}\n"
            f"  原因: {reason}\n{detail}"
        )

    def _find_by_scan(self, module: ModuleType) -> Type[BasePlugin]:
        """自动发现：在模块中扫描 BasePlugin 子类。"""
        # 严格匹配：仅匹配在此模块中定义的类
        for obj in vars(module).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, BasePlugin)
                and obj is not BasePlugin
                and obj.__module__ == module.__name__
            ):
                return obj

        # 严格匹配失败，尝试宽松匹配（排除框架内部类）
        candidates = self._collect_candidates(module)
        relaxed = [
            (name, cls)
            for name, cls, mod in candidates
            if not mod.startswith("ncatbot.")
        ]

        if len(relaxed) == 1:
            name, cls = relaxed[0]
            LOG.warning(
                "模块 %s 中未找到严格匹配的 BasePlugin 子类，"
                "但宽松匹配发现 %s（__module__=%s != %s），已采用。"
                '建议在 manifest.toml 中显式指定 entry_class = "%s"',
                module.__name__,
                name,
                cls.__module__,
                module.__name__,
                name,
            )
            return cls

        # 完全失败，生成诊断信息
        detail = self._format_diag(module, candidates)
        raise ImportError(f"模块 {module.__name__} 中未找到 BasePlugin 子类\n{detail}")

    @staticmethod
    def _collect_candidates(
        module: ModuleType,
    ) -> list[tuple[str, type, str]]:
        """收集模块中所有 BasePlugin 子类（排除 BasePlugin 自身）。"""
        return [
            (name, obj, obj.__module__)
            for name, obj in vars(module).items()
            if isinstance(obj, type)
            and issubclass(obj, BasePlugin)
            and obj is not BasePlugin
        ]

    @staticmethod
    def _format_diag(
        module: ModuleType,
        candidates: list[tuple[str, type, str]],
    ) -> str:
        """格式化诊断信息。"""
        if not candidates:
            all_names = [k for k, v in vars(module).items() if isinstance(v, type)]
            return (
                f"  诊断: 模块中无任何 BasePlugin 子类（共 {len(all_names)} 个类: "
                f"{all_names}）"
            )
        lines = [f"  诊断: 发现 {len(candidates)} 个 BasePlugin 子类:"]
        for name, cls, mod in candidates:
            match = "✓" if mod == module.__name__ else "✗"
            lines.append(f"    {match} {name} (__module__={mod})")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _clear_pycache(manifest: PluginManifest) -> None:
        pycache = manifest.plugin_path / "__pycache__"
        if pycache.exists():
            try:
                shutil.rmtree(pycache)
            except Exception as e:
                LOG.debug("清除 __pycache__ 失败: %s", e)
