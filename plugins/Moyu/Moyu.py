from ncatbot.core.message import GroupMessage, Image, MessageChain
from ncatbot.plugin.base_plugin import BasePlugin
from ncatbot.plugin.event import CompatibleEnrollment

bot = CompatibleEnrollment


class Moyu(BasePlugin):
    @bot.group_event()
    async def handle_moyu(self, input: GroupMessage):
        if input.raw_message in ["摸鱼", "moyu"]:
            return await self.api.post_group_msg(group_id=input.group_id, rtf=MessageChain(
                [
                    Image("https://api.vvhan.com/api/moyu"),
                ]
            ))
