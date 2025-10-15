from ncatbot.plugin_system import NcatBotPlugin
from ncatbot.plugin_system.builtin_plugin.unified_registry.filter_system.decorators import (
    group_only,
)
from ncatbot.core.message import GroupMessage
from ncatbot.utils.logger import get_log

_log = get_log()


class MessageRecall(NcatBotPlugin):
    name = "MessageRecall"
    version = "1.0"

    async def on_load(self):
        """
        异步加载插件
        """
        _log.info(f"开始加载 {self.name} 插件 v{self.version}")
        # 初始化需要撤回的关键词
        self.suicide_keywords = ["自杀了", "紫砂了"]
        self.alice_nsfw_keywords = ["物述有栖", "爱丽丝"]
        self.nsfw_keywords = ["色图"]
        _log.info(f"{self.name} 插件加载完成")

    async def delete_msg_by_group_id(self, message_id: int):
        """
        根据消息 ID 撤回消息
        """
        _log.info(f"正在撤回消息 ID: {message_id}")  # 添加日志
        await self.api.delete_msg(message_id)

    @group_only
    async def handle_message_recall(self, msg: GroupMessage):
        """
        处理群聊消息，自动撤回包含敏感内容的消息
        """
        # 获取消息内容
        message_text = msg.raw_message.lower()
        # 检查是否包含自杀相关关键词
        if any(keyword in message_text for keyword in self.suicide_keywords):
            await self.api.delete_msg(msg.message_id)
            return

        # 检查是否包含爱丽丝/物述有栖 + 色图
        has_alice = any(keyword in message_text for keyword in self.alice_nsfw_keywords)
        has_nsfw = any(keyword in message_text for keyword in self.nsfw_keywords)

        if has_alice and has_nsfw:
            await self.api.delete_msg(msg.message_id)
            return
