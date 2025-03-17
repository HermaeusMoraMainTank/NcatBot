from datetime import datetime
import logging
import os
import random
from ncatbot.core.message import GroupMessage
from ncatbot.core.element import Image, MessageChain
from ncatbot.plugin.base_plugin import BasePlugin
from ncatbot.plugin.compatible import CompatibleEnrollment

bot = CompatibleEnrollment

KEYWORD1 = "zmd"
KEYWORD2 = "zmd "
PATH = "data/image/zmd"  # 替换为实际图片目录

log = logging.getLogger(__name__)


class Zmd(BasePlugin):
    name = "Zmd"  # 插件名称
    version = "1.0"  # 插件版本

    @bot.group_event()
    async def handle_zmd(self, input: GroupMessage):
        message = input.raw_message

        if message.startswith(KEYWORD2):
            trimmed_message = message[len(KEYWORD2) :].strip()
            if not trimmed_message.isdigit():
                return

            count = int(trimmed_message)
            image_files = self.get_image_files(PATH)

            if count <= 3:
                for _ in range(count):
                    file = random.choice(image_files)
                    log.info(
                        f"Time:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {file}"
                    )
                    await self.api.post_group_msg(
                        group_id=input.group_id,
                        rtf=MessageChain(
                            [
                                Image(file),
                            ]
                        ),
                    )
            else:
                await self.api.post_group_msg(group_id=input.group_id, text="别太贪心")
        elif message == KEYWORD1:
            image_files = self.get_image_files(PATH)
            file = random.choice(image_files)
            log.info(f"Time:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {file}")
            await self.api.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain(
                    [
                        Image(file),
                    ]
                ),
            )

    @staticmethod
    def get_image_files(folder_path):
        if os.path.isdir(folder_path):
            return [
                os.path.join(folder_path, f)
                for f in os.listdir(folder_path)
                if f.lower().endswith((".jpg", ".png", ".jpeg"))
            ]
        return []
