"""插件入口。"""

from ncatbot.plugin import NcatBotPlugin


class PluginClassName(NcatBotPlugin):
    """PluginName 插件。

    注意：插件元数据由 manifest.toml 提供，不要在类中声明。
    """

    async def on_load(self):
        self.logger.info(f"{self.name} 已加载")

    async def on_close(self):
        self.logger.info(f"{self.name} 已卸载")
