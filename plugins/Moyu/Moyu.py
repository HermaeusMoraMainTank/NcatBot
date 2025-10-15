from ncatbot.core.message import GroupMessage
from ncatbot.core import Image, MessageChain
from ncatbot.plugin_system import NcatBotPlugin
from ncatbot.plugin_system.builtin_plugin.unified_registry.filter_system.decorators import (
    group_only,
)


class Moyu(NcatBotPlugin):
    name = "Moyu"  # 插件名称
    version = "1.0"  # 插件版本

    @group_only
    async def handle_moyu(self, input: GroupMessage):
        if input.raw_message in ["摸鱼", "moyu"]:
            return await self.api.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain(
                    [
                        Image("https://api.vvhan.com/api/moyu"),
                    ]
                ),
            )
