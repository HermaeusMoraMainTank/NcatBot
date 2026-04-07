"""
数据持久化混入类

管理插件 data.json 的加载和保存。
通过 _mixin_load / _mixin_unload 钩子自动参与插件生命周期。
"""

import json
from pathlib import Path
from typing import Any, Dict

from ncatbot.utils import get_log

LOG = get_log("DataMixin")


class DataMixin:
    """
    数据持久化混入类

    - ``_mixin_load``: 从 data.json 加载数据到 ``self.data``
    - ``_mixin_unload``: 将 ``self.data`` 保存到 data.json

    使用示例::

        class MyPlugin(NcatBotPlugin):
            async def on_load(self):
                self.data["counter"] = self.data.get("counter", 0) + 1
                # on_close 后框架自动保存
    """

    name: str
    workspace: Path
    data: Dict[str, Any]

    # ------------------------------------------------------------------
    # Mixin 钩子
    # ------------------------------------------------------------------

    def _mixin_load(self) -> None:
        """从 JSON 加载数据。"""
        self.data = self._load_data()

    def _mixin_unload(self) -> None:
        """将数据保存到 JSON。"""
        self._save_data()

    # ------------------------------------------------------------------
    # 持久化实现
    # ------------------------------------------------------------------

    @property
    def _data_path(self) -> Path:
        return self.workspace / "data.json"

    def _load_data(self) -> Dict[str, Any]:
        """从 JSON 加载数据，文件不存在则返回空字典。"""
        if self._data_path.exists():
            try:
                with open(self._data_path, "r", encoding="utf-8") as f:
                    result = json.load(f)
                    return result if isinstance(result, dict) else {}
            except Exception as e:
                LOG.error("加载插件 %s 数据失败: %s", self.name, e)
        return {}

    def _save_data(self) -> None:
        """将数据写入 JSON。"""
        try:
            self.workspace.mkdir(exist_ok=True, parents=True)
            with open(self._data_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            LOG.error("保存插件 %s 数据失败: %s", self.name, e)
