from ncatbot.core import GroupMessage, Image, MessageChain
from ncatbot.plugin_system import NcatBotPlugin, on_message


class Moyu(NcatBotPlugin):
    name = "Moyu"  # 插件名称
    version = "1.0"  # 插件版本

    @on_message
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
