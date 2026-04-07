"""
配置混入类

双层配置模型:
  - 低优先级: 插件源码目录下的 config.yaml (随附默认值)
  - 高优先级: 全局 config.yaml → plugin.plugin_configs.<name> (用户覆盖 + 运行时持久化)

通过 _mixin_load / _mixin_unload 钩子自动参与插件生命周期。
"""

from pathlib import Path
from typing import Any, Dict, TYPE_CHECKING

import yaml

from ncatbot.utils import get_config_manager, get_log

if TYPE_CHECKING:
    from ncatbot.plugin.manifest import PluginManifest

LOG = get_log("ConfigMixin")


class ConfigMixin:
    """
    配置混入类 — 双层配置模型

    加载优先级（低 → 高）:

    1. 插件源码目录 ``config.yaml`` — 开发者随附的默认值
    2. 全局 ``config.yaml`` 的 ``plugin.plugin_configs.<name>`` — 用户覆盖

    - ``set_config`` / ``update_config``: 写入全局 config.yaml 并立即持久化
    - ``init_defaults``: 仅在内存中补充缺失键，不持久化

    使用示例::

        class MyPlugin(NcatBotPlugin):
            async def on_load(self):
                self.init_defaults({"api_key": "", "timeout": 30})
                key = self.get_config("api_key")
    """

    name: str
    workspace: Path
    config: Dict[str, Any]
    _manifest: "PluginManifest"

    # ------------------------------------------------------------------
    # Mixin 钩子
    # ------------------------------------------------------------------

    def _mixin_load(self) -> None:
        """加载双层配置: 源码默认值 → 全局覆盖。"""
        self.config = self._load_source_defaults()
        self._apply_global_overrides()

    def _mixin_unload(self) -> None:
        """No-op: 所有持久化已通过 set_config/update_config 即时完成。"""

    # ------------------------------------------------------------------
    # 源码默认值加载
    # ------------------------------------------------------------------

    @property
    def _source_config_path(self) -> Path:
        """插件源码目录下的 config.yaml 路径。"""
        return self._manifest.plugin_path / "config.yaml"

    def _load_source_defaults(self) -> Dict[str, Any]:
        """从插件源码目录加载 config.yaml 作为默认值，文件不存在则返回空字典。"""
        path = self._source_config_path
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    result = yaml.safe_load(f)
                    return result if isinstance(result, dict) else {}
            except Exception as e:
                LOG.error("加载插件 %s 源码配置失败: %s", self.name, e)
        return {}

    # ------------------------------------------------------------------
    # 全局配置覆盖 + 持久化
    # ------------------------------------------------------------------

    def _apply_global_overrides(self) -> None:
        """如果全局配置 plugin.plugin_configs 中存在本插件的条目，用它覆盖当前 config。"""
        try:
            overrides = get_config_manager().config.plugin.plugin_configs.get(self.name)
            if overrides:
                self.config.update(overrides)
                LOG.debug(
                    "插件 %s: 已应用全局配置覆盖 %s",
                    self.name,
                    list(overrides.keys()),
                )
        except Exception as e:
            LOG.debug("插件 %s: 读取全局配置覆盖失败: %s", self.name, e)

    def _persist_to_global(self, updates: Dict[str, Any]) -> None:
        """将配置变更写入全局 config.yaml 的 plugin_configs 部分。"""
        try:
            mgr = get_config_manager()
            plugin_configs = mgr.config.plugin.plugin_configs
            if self.name not in plugin_configs:
                plugin_configs[self.name] = {}
            plugin_configs[self.name].update(updates)
            mgr.save()
        except Exception as e:
            LOG.error("插件 %s: 持久化配置到全局 config 失败: %s", self.name, e)

    def _remove_from_global(self, key: str) -> None:
        """从全局 config.yaml 的 plugin_configs 部分移除指定键。"""
        try:
            mgr = get_config_manager()
            plugin_configs = mgr.config.plugin.plugin_configs
            section = plugin_configs.get(self.name)
            if section and key in section:
                del section[key]
                if not section:
                    del plugin_configs[self.name]
                mgr.save()
        except Exception as e:
            LOG.error("插件 %s: 从全局 config 移除 %s 失败: %s", self.name, key, e)

    # ------------------------------------------------------------------
    # 便捷接口
    # ------------------------------------------------------------------

    def get_config(self, key: str, default: Any = None) -> Any:
        """读取配置值。"""
        return self.config.get(key, default)

    def set_config(self, key: str, value: Any) -> None:
        """设置配置值并立即持久化到全局 config.yaml。"""
        self.config[key] = value
        self._persist_to_global({key: value})

    def remove_config(self, key: str) -> bool:
        """移除配置项并持久化，键不存在返回 False。"""
        if key in self.config:
            del self.config[key]
            self._remove_from_global(key)
            return True
        return False

    def update_config(self, updates: Dict[str, Any]) -> None:
        """批量更新配置并持久化到全局 config.yaml。"""
        self.config.update(updates)
        self._persist_to_global(updates)

    def init_defaults(self, defaults: Dict[str, Any]) -> None:
        """补充缺失的默认配置项（仅内存，不持久化）。

        遍历 *defaults*，仅当 ``self.config`` 中不存在对应键时才写入。
        适用于在 ``on_load()`` 中声明插件所需的全部默认值。
        """
        for key, value in defaults.items():
            if key not in self.config:
                self.config[key] = value
