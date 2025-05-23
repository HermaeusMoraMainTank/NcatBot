from ncatbot.core import GroupMessage
from ncatbot.plugin import CompatibleEnrollment, BasePlugin

bot = CompatibleEnrollment
class WangYiMusic(BasePlugin):
    name = 'WangYiMusic'
    author = 'xww'
    version = '1.0'
    @bot.group_event()
    def grouphandle(self, input:GroupMessage):
        com=input.raw_message.split(" ")
        if com[0].lower()=="点歌":
