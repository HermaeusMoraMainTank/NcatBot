from ncatbot.core import Image, MessageChain, Reply, GroupMessage
from ncatbot.plugin_system import NcatBotPlugin, on_message


class Daily3Min(NcatBotPlugin):
    name = "Daily3Min"  # 插件名称
    version = "1.0"  # 插件版本

    @on_message
    async def handle_daily3min(self, input: GroupMessage):
        if input.raw_message in [
            "每天3分钟",
            "每天三分钟",
            "每日3分钟",
            "每日三分钟",
            "每天60秒",
            "每天六十秒",
            "每日60秒",
            "每日六十秒",
            "每天1分钟",
            "每天一分钟",
            "每日1分钟",
            "每日一分钟",
        ]:
            message = MessageChain(
                [
                    Image("https://api.03c3.cn/api/zb"),
                    Reply(input.message_id),
                ]
            )
            await self.api.post_group_msg(group_id=input.group_id, rtf=message)
