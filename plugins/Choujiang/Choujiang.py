import random

from ncatbot.core.element import Image, MessageChain, Reply
from ncatbot.core.message import GroupMessage
from ncatbot.plugin import CompatibleEnrollment, BasePlugin


bot = CompatibleEnrollment


class Choujiang(BasePlugin):
    name = "Choujiang"  # 插件名称
    version = "1.0"  # 插件版本
    # 使用字典存储每个群组的抽奖状态
    map = {}

    # @bot.group_event()
    async def handle_choujiang(self, input: GroupMessage):
        group_id = input.group_id
        if group_id != 853963912:
            return

        # 如果该群组不存在抽奖数据，则初始化
        if group_id not in self.map:
            self._initmap(group_id)

        # 获取当前群组的抽奖数据
        group_data = self.map[group_id]
        current_probability = group_data["probability"]  # 当前中奖概率

        # 随机抽奖判断
        draw_number = random.uniform(0, 1)  # 生成一个0-1之间的随机数
        print(f"当前中奖概率：{current_probability * 100:.2f}%")

        # 判断是否中奖
        if draw_number <= current_probability:
            # 随机生成禁言时间（1秒到120秒之间）
            ban_time = random.randint(1, 60)

            # 禁言用户
            await self.api.set_group_ban(input.group_id, input.user_id, ban_time)
            message = MessageChain(
                [
                    Image("data/image/ba/haha.jpg"),
                    f"啊哈哈哈，你中奖啦~\n当前概率 {current_probability * 100:.2f}%",
                    Reply(input.message_id),
                ]
            )
            await self.api.post_group_msg(group_id=input.group_id, rtf=message)

            # 重新初始化群组的抽奖数据
            self._initmap(group_id)
        else:
            # 增加概率（每次发言增加0.01%的概率）
            group_data["probability"] = min(1.0, current_probability + 0.00005)
            group_data["list"].append(input.user_id)  # 将用户添加到参与列表

    def _initmap(self, group_id):
        # 初始化群组的抽奖数据
        self.map[group_id] = {"probability": 0, "list": []}
