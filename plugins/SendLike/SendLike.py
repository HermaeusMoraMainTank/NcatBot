from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.types import At, Image, MessageArray as MessageChain, Reply, PlainText
from ncatbot.plugin import NcatBotPlugin
from ncatbot.core import registrar


class SendLike(NcatBotPlugin):
    name = "SendLike"  # 插件名称
    version = "1.0"  # 插件版本

    @registrar.qq.on_group_message()
    async def handle_send_like(self, input: GroupMessage):
        if input.raw_message == "赞我":
            await self.api.qq.send_like(input.sender.user_id, 10)
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain(
                    [
                        At(user_id=input.sender.user_id),
                        PlainText(text="\n给你赞了10下哦，记得回我~ (如赞失败请添加好友)"),
                        Image(
                            "https://api.xingzhige.com/API/dingqiu/?qq="
                            + str(input.sender.user_id)
                        ),
                        Reply(id=input.message_id),
                    ]
                ),
            )
            return