from ncatbot.core.message import GroupMessage
from ncatbot.plugin.base_plugin import BasePlugin
from ncatbot.plugin.event import CompatibleEnrollment

bot = CompatibleEnrollment


class Moyu(BasePlugin):
    @bot.group_event()
    async def handle_moyu(self, input: GroupMessage):
        if input.raw_message in ["摸鱼", "moyu"]:
            return await input.add_image("https://api.vvhan.com/api/moyu").reply()
