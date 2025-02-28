from ncatbot.core.message import GroupMessage
from ncatbot.plugin.base_plugin import BasePlugin
from ncatbot.plugin.event import CompatibleEnrollment

bot = CompatibleEnrollment


class SendLike(BasePlugin):
    @bot.group_event()
    async def handle_send_like(self, input: GroupMessage):
        if input.raw_message == "赞我":
            await input.send_like(input.user_id, 10)
            await (
                input.add_at(input.user_id)
                .add_text("\n给你赞了10下哦，记得回我~ (如赞失败请添加好友)")
                .add_image(
                    "https://api.xingzhige.com/API/dingqiu/?qq=" + str(input.user_id)
                )
                .reply()
            )
            return
