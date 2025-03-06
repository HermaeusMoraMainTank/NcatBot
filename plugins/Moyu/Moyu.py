from ncatbot.core.message import GroupMessage
from ncatbot.core.element import Image, MessageChain
from ncatbot.plugin.base_plugin import BasePlugin
from ncatbot.plugin.compatible import CompatibleEnrollment

bot = CompatibleEnrollment


class Moyu(BasePlugin):
    name = "Moyu"  # 插件名称
    version = "1.0"  # 插件版本


    @bot.group_event()
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
