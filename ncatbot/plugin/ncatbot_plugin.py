"""
NcatBot 插件基类

组合 BasePlugin + 所有 Mixin，插件开发者继承此类即可获得全部便捷接口。

继承顺序决定 _mixin_load / _mixin_unload 钩子的执行顺序：
  load  (MRO 正序): EventMixin → TimeTaskMixin → RBACMixin → ConfigMixin → DataMixin
  unload (MRO 正序): EventMixin(关流) → TimeTaskMixin(清任务) → RBACMixin → ConfigMixin(存配置) → DataMixin(存数据)
"""

from .base import BasePlugin
from .mixin import (
    ConfigMixin,
    DataMixin,
    DispatchFilterMixin,
    EventMixin,
    RBACMixin,
    TimeTaskMixin,
)


class NcatBotPlugin(
    BasePlugin,
    EventMixin,
    TimeTaskMixin,
    RBACMixin,
    DispatchFilterMixin,
    ConfigMixin,
    DataMixin,
):
    """
    NcatBot 插件基类

    继承此类来创建功能完整的插件，自动获得:
    - 配置持久化 (config.yaml) + 数据持久化 (data.json)
    - 事件流消费: self.events(), self.wait_event()
    - 定时任务: self.add_scheduled_task(), self.remove_scheduled_task()
    - 配置管理: self.get_config(), self.set_config()
    - 权限管理: self.check_permission(), self.add_permission()

    使用示例::

        class MyPlugin(NcatBotPlugin):
            name = "my_plugin"
            version = "1.0.0"

            async def on_load(self):
                self.set_config("greeting", "hello")
                self.add_scheduled_task("tick", "60s")

            async def on_close(self):
                pass  # Mixin 钩子自动清理定时任务、关闭事件流、保存数据
    """
