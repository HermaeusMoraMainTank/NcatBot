from ncatbot.core import GroupMessage, MessageChain, Text
from ncatbot.plugin_system import NcatBotPlugin, on_message
from ncatbot.core.helper.forward_constructor import ForwardConstructor
from ncatbot.utils.logger import get_log

_log = get_log()


class FakeForward(NcatBotPlugin):
    name = "FakeForward"  # 插件名称
    version = "1.0"  # 插件版本

    # 使用说明
    usage_instructions = """构造虚假合并消息指令：
发送"合并消息"即可生成一条虚假的合并转发消息
"""

    async def on_load(self):
        """异步加载插件"""
        _log.info(f"开始加载 {self.name} 插件 v{self.version}")
        _log.info(f"{self.name} 插件加载完成")

    @on_message
    async def handle_fake_forward(self, input: GroupMessage):
        """处理合并消息命令"""
        if input.raw_message.strip() != "合并消息":
            return

        try:
            # 构造虚假合并转发消息
            fcr = ForwardConstructor(user_id="1095216448", nickname="喵喵喵")
            fcr.attach_text("饱饱我爱你")
            forward = fcr.to_forward()

            # 发送合并转发消息到群
            await self.api.post_group_forward_msg(input.group_id, forward)
            _log.info(f"[FakeForward] 成功发送合并消息到群 {input.group_id}")

        except Exception as e:
            _log.error(f"[FakeForward] 发送合并消息失败: {e}")
            await self.api.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain([Text("合并消息发送失败，请稍后重试")]),
            )
