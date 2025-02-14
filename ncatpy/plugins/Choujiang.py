from ncatpy.message import GroupMessage
import random


class Choujiang:
    def __init__(self):
        self.map = {853963912}  # 存储每个群组的抽奖状态

    async def handle_choujiang(self, input: GroupMessage):
        group_id = input.group_id

        # 如果该群组不存在抽奖数据，则初始化
        if group_id not in self.map:
            self._initmap(group_id)

        # 获取当前群组的抽奖数据
        group_data = self.map[group_id]
        current_probability = group_data['probability']  # 当前中奖概率
        total_users = len(group_data['list'])  # 当前参与人数

        # 随机抽奖判断
        draw_number = random.uniform(0, 1)  # 生成一个0-1之间的随机数
        print(f"当前中奖概率：{current_probability * 100:.2f}%")
        print(f"随机生成的中奖数：{draw_number:.4f}")

        # 判断是否中奖
        if draw_number <= current_probability:
            # 随机生成禁言时间（1秒到120秒之间）
            ban_time = random.randint(1, 120)

            # 禁言用户
            await input.set_group_ban(input.group_id, input.user_id, ban_time)
            input.add_reply(input.message_id)
            input.add_image("data/image/ba/haha.jpg")
            input.add_text(f"啊哈哈哈，你中奖啦~\n")
            input.add_text(f"当前概率 {current_probability * 100:.2f}%")

            await input.reply(input.group_id)

            # 重新初始化群组的抽奖数据
            self._initmap(group_id)
        else:
            # 增加概率（每次发言增加0.01%的概率）
            group_data['probability'] = min(1.0, current_probability + 0.0001)
            group_data['list'].append(input.user_id)  # 将用户添加到参与列表

    def _initmap(self, group_id):
        # 初始化群组的抽奖数据
        self.map[group_id] = {
            "probability": 0.005,  # 初始中奖概率 0.5%
            "list": []  # 参与抽奖的用户列表
        }

