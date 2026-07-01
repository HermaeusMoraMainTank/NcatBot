from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.types import Image, MessageArray as MessageChain, Reply
from ncatbot.plugin import NcatBotPlugin
from ncatbot.core import registrar

import random


BASE_URL = "http://www.catsthatlooklikehitler.com/kitler/pics/kitler"
MAX_NUMBER = 8848

# 触发关键词列表
TRIGGER_WORDS = {"希儿", "希特勒", "Sieg Heil", "siegheil", "胜利万岁", "sieg", "Sieg"}


class CatsThatLookLikeHitler(NcatBotPlugin):
    name = "CatsThatLookLikeHitler"  # 插件名称
    version = "1.0"  # 插件版本

    @registrar.qq.on_group_message()
    async def hitler_cat(self, input: GroupMessage):
        """
        处理希特勒猫图片功能
        """
        message_content = input.raw_message
        if message_content not in TRIGGER_WORDS:
            return

        random_number = random.randint(1, MAX_NUMBER)
        image_url = f"{BASE_URL}{random_number}.jpg"
        return await self.api.qq.post_group_msg(
            group_id=input.group_id,
            rtf=MessageChain(
                [
                    Image(file=image_url),
                    Reply(id=input.message_id),
                ]
            ),
        )
