from ncatbot.core import GroupMessage, At, Image, MessageChain, Reply, Text
from ncatbot.plugin_system import NcatBotPlugin, on_message


class SendLike(NcatBotPlugin):
    name = "SendLike"  # 插件名称
    version = "1.0"  # 插件版本

    @on_message
    async def handle_send_like(self, input: GroupMessage):
        if input.raw_message == "赞我":
            await self.api.send_like(input.sender.user_id, 10)
            await self.api.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain(
                    [
                        At(input.sender.user_id),
                        Text("\n给你赞了10下哦，记得回我~ (如赞失败请添加好友)"),
                        Image(
                            "https://api.xingzhige.com/API/dingqiu/?qq="
                            + str(input.sender.user_id)
                        ),
                        Reply(input.message_id),
                    ]
                ),
            )
            return
