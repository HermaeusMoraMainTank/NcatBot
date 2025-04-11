from datetime import datetime
import logging
import os
import random
from ncatbot.core.message import GroupMessage
from ncatbot.core.element import Image, MessageChain
from ncatbot.plugin.base_plugin import BasePlugin
from ncatbot.plugin.compatible import CompatibleEnrollment

bot = CompatibleEnrollment
log = logging.getLogger(__name__)


class ImageSender(BasePlugin):
    name = "ImageSender"  # 插件名称
    version = "1.0"  # 插件版本

    max_count = 3  # 最大发送数量
    allowed_users = None  # 全局允许的用户ID列表，None表示所有用户都可以使用

    # 命令配置
    commands = {
        "母肥": {
            "path": "C:\\Users\\27342\\Downloads\\lalafell\\lalafell",
            "allowed_users": None,
        },
        "zmd": {
            "path": "data/image/zmd",
            "allowed_users": [273421673, 635773721, 510337095, 3420347160, 1508864751],
        },
        "doro": {
            "path": "data/image/doro",
            "allowed_users": None,
        },
        "柴郡": {
            "path": "data/image/cheshire",
            "allowed_users": None,
        },
        "llm": {
            "path": "data/image/llm",
            "allowed_users": [273421673, 2779893879, 361432025],
        },
    }

    @bot.group_event()
    async def handle_image(self, input: GroupMessage):
        message = input.raw_message.strip()

        # 检查消息是否以任何命令开头
        for command, config in self.commands.items():
            if message.startswith(command):
                # 检查全局权限
                if (
                    self.allowed_users
                    and input.sender.user_id not in self.allowed_users
                ):
                    return

                # 检查命令特定权限
                if (
                    config["allowed_users"]
                    and input.sender.user_id not in config["allowed_users"]
                ):
                    return

                # 处理带数量的情况
                if message.startswith(command + " "):
                    trimmed_message = message[len(command) + 1 :].strip()
                    if not trimmed_message.isdigit():
                        return

                    count = int(trimmed_message)
                    image_files = self.get_image_files(config["path"])

                    if count <= self.max_count:
                        for _ in range(count):
                            file = random.choice(image_files)
                            log.info(
                                f"Time:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {file}"
                            )
                            await self.api.post_group_msg(
                                group_id=input.group_id, rtf=MessageChain([Image(file)])
                            )
                    else:
                        await self.api.post_group_msg(
                            group_id=input.group_id, text="别太贪心"
                        )
                # 处理单个图片的情况
                elif message == command:
                    image_files = self.get_image_files(config["path"])
                    file = random.choice(image_files)
                    log.info(
                        f"Time:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {file}"
                    )
                    await self.api.post_group_msg(
                        group_id=input.group_id, rtf=MessageChain([Image(file)])
                    )
                return

    @staticmethod
    def get_image_files(folder_path):
        if os.path.isdir(folder_path):
            return [
                os.path.join(folder_path, f)
                for f in os.listdir(folder_path)
                if f.lower().endswith((".jpg", ".png", ".jpeg"))
            ]
        return []
