from ncatbot.core.message import At, GroupMessage, Image, MessageChain, Reply, Text
from ncatbot.plugin.base_plugin import BasePlugin
from ncatbot.plugin.event import CompatibleEnrollment

bot = CompatibleEnrollment


class SendLike(BasePlugin):
    @bot.group_event()
    async def handle_send_like(self, input: GroupMessage):
        if input.raw_message == "赞我":
            await input.send_like(input.user_id, 10)
            await self.api.post_group_msg(group_id=input.group_id, rtf=MessageChain(
                [
                    At(input.user_id),
                    Text("\n给你赞了10下哦，记得回我~ (如赞失败请添加好友)"),
                    Image(
                        "https://api.xingzhige.com/API/dingqiu/?qq=" + str(input.user_id)
                    ),
                    Reply(input.message_id),
                ]
            ))
            return
