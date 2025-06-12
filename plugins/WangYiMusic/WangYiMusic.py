from ncatbot.core import GroupMessage, MessageChain, Text, Record
from ncatbot.plugin import CompatibleEnrollment, BasePlugin
import json

from plugins.WangYiMusic.utils import searcht, getmusic

bot = CompatibleEnrollment


class WangYiMusic(BasePlugin):
    name = "WangYiMusic"
    author = "xww"
    version = "1.0"

    @bot.group_event()
    async def grouphandle(self, input: GroupMessage):
        com = input.raw_message.split(" ")
        if len(com) < 2:
            return None
        if com[0].lower() == "点歌":
            if com[1].isdigit():
                musicdata = getmusic(com[1])
                print("音乐数据:", json.dumps(musicdata, ensure_ascii=False, indent=2))
                if musicdata.get("data") and musicdata["data"][0].get("url"):
                    mes = MessageChain([Record(musicdata["data"][0]["url"])])
                    await self.api.post_group_msg(group_id=input.group_id, rtf=mes)
                return None
            try:
                seadata = searcht(com[1])
                print("搜索结果:", json.dumps(seadata, ensure_ascii=False, indent=2))

                if seadata.get("code") != 200:
                    await self.api.post_group_msg(
                        group_id=input.group_id,
                        rtf=MessageChain(
                            [Text(f"搜索失败，错误码：{seadata.get('code')}")]
                        ),
                    )
                    return None

                if not seadata.get("result") or not seadata["result"].get("songs"):
                    await self.api.post_group_msg(
                        group_id=input.group_id,
                        rtf=MessageChain([Text("未找到相关歌曲")]),
                    )
                    return None

                for song in seadata["result"]["songs"]:
                    mes = MessageChain(
                        [
                            Text(
                                song["id"]
                                + " "
                                + song["name"]
                                + " "
                                + song["ar"][0]["name"]
                            )
                        ]
                    )
                    await self.api.post_group_msg(group_id=input.group_id, rtf=mes)
            except Exception as e:
                print("发生错误:", str(e))
                await self.api.post_group_msg(
                    group_id=input.group_id,
                    rtf=MessageChain([Text("搜索歌曲时发生错误")]),
                )
            return None
        return None
