from ncatbot.core import GroupMessage, MessageChain, Text, Record
from ncatbot.plugin import CompatibleEnrollment, BasePlugin

from plugins.WangYiMusic.utils import searcht, getmusic

bot = CompatibleEnrollment
class WangYiMusic(BasePlugin):
    name = 'WangYiMusic'
    author = 'xww'
    version = '1.0'
    @bot.group_event()
    def grouphandle(self, input:GroupMessage):
        com=input.raw_message.split(" ")
        if len(com)<2:return
        if com[0].lower()=="点歌":
            if com[1].isdigit():
                musicdata=getmusic(com[1])
                if musicdata["data"][0]["url"]!=None:
                    mes=MessageChain[Record(musicdata["data"][0]["url"])]          ##这里我是传入一个地址如果不能发送你就缓存然后发文件绝对地址
                    self.api.post_group_msg(group_id=input.group_id, rtf=mes)
                return
                #传入id
            seadata=searcht(com[1])
            if len(seadata['result']['songs'])<1:return
            for song in seadata['result']['songs']:
                mes=MessageChain[[Text(song["id"]+" "+song["name"]+" "+song["ar"][0]["name"])]]
            self.api.post_group_msg(group_id=input.group_id, rtf=mes)