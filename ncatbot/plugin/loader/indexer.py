"""
插件清单索引器

扫描插件目录，解析 manifest.toml，返回清单集合。
"""

from pathlib import Path
from typing import Dict, Optional

from ncatbot.utils import get_log

from ..manifest import PluginManifest

LOG = get_log("PluginIndexer")


class PluginIndexer:
    """扫描目录并索引所有插件清单。"""

    def __init__(self) -> None:
        self._manifests: Dict[str, PluginManifest] = {}

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def scan(self, plugin_root: Path) -> Dict[str, PluginManifest]:
        """扫描目录下所有插件，返回 {name: PluginManifest}。"""
        plugin_root = Path(plugin_root).resolve()
        if not plugin_root.is_dir():
            LOG.warning("插件目录不存在: %s", plugin_root)
            return self._manifests

        for entry in plugin_root.iterdir():
            self.index_plugin(entry)

        return dict(self._manifests)

    def index_plugin(self, plugin_path: Path) -> Optional[PluginManifest]:
        """索引单个插件目录，返回 PluginManifest 或 None。"""
        plugin_path = Path(plugin_path).resolve()
        manifest_path = plugin_path / "manifest.toml"

        if not plugin_path.is_dir():
            return None
        if not manifest_path.exists():
            LOG.debug("跳过缺少 manifest.toml 的目录: %s", plugin_path.name)
            return None

        try:
            manifest = PluginManifest.from_toml(manifest_path)
        except (FileNotFoundError, ValueError) as e:
            LOG.warning("解析清单失败 [%s]: %s", plugin_path.name, e)
            return None
        except Exception:
            LOG.exception("解析清单异常: %s", manifest_path)
            return None

        if manifest.name in self._manifests:
            existing = self._manifests[manifest.name]
            LOG.warning(
                "插件名冲突: %s (已有: %s, 新: %s)，跳过",
                manifest.name,
                existing.plugin_path,
                plugin_path,
            )
            return None

        self._manifests[manifest.name] = manifest
        LOG.debug("已索引插件: %s (%s)", manifest.name, plugin_path)
        return manifest

    def rescan_plugin(self, name: str) -> Optional[PluginManifest]:
        """重新扫描指定插件（用于热重载时更新清单）。"""
        old = self._manifests.get(name)
        if old is None:
            return None

        plugin_path = old.plugin_path
        del self._manifests[name]
        return self.index_plugin(plugin_path)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get(self, name: str) -> Optional[PluginManifest]:
        return self._manifests.get(name)

    def get_by_folder(self, folder_name: str) -> Optional[PluginManifest]:
        """根据文件夹名查找插件清单。"""
        for manifest in self._manifests.values():
            if manifest.folder_name == folder_name:
                return manifest
        return None

    @property
    def all(self) -> Dict[str, PluginManifest]:
        return dict(self._manifests)
