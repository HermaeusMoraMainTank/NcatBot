import asyncio
import logging
import random
import os
from typing import Dict, List

from ncatpy.message import GroupMessage

log = logging.getLogger(__name__)


class Lottery:
    MAX_PARTICIPANTS = 5  # 每局最大参与人数
    FULL_GROUP_PUNISH_ALL_PROBABILITY = 0.25  # 全体禁言概率
    MIN_MUTE_MINUTES = 1  # 最小禁言时间
    MAX_MUTE_MINUTES = 10  # 最大禁言时间
    FILE_PATH = "data/txt/LotteryAwards.txt"  # 奖品记录文件路径

    def __init__(self):
        self.group_participants: Dict[int, List[int]] = {}  # 记录每个群的参与者
        self.total_awards = 0  # 奖品总数
        self.load_total_awards()

    def load_total_awards(self):
        """加载奖品总数"""
        try:
            with open(self.FILE_PATH, 'r') as file:
                line = file.readline()
                self.total_awards = int(line.strip()) if line.strip() else 0
        except (IOError, ValueError) as e:
            self.total_awards = 0
            log.warning(f"无法加载奖品总数，初始化为 0: {e}")

    def save_total_awards(self):
        """保存奖品总数"""
        try:
            with open(self.FILE_PATH, 'w') as file:
                file.write(str(self.total_awards))
        except IOError as e:
            log.warning(f"无法保存奖品总数: {e}")

    async def handle_lottery(self, input: GroupMessage):
        """处理大乐透指令"""
        if input.raw_message.startswith("大乐透"):
            await self.join_lottery(input)

    async def join_lottery(self, input: GroupMessage):
        """玩家加入大乐透"""
        group_id = input.group_id
        user_id = input.user_id

        if group_id not in self.group_participants:
            self.group_participants[group_id] = []

        participants = self.group_participants[group_id]

        if user_id in participants:
            await input.add_text(f"{input.sender.nickname}，你已经加入了本次大乐透！").reply()
            return

        participants.append(user_id)
        if len(participants) >= self.MAX_PARTICIPANTS:
            await input.add_text("人够了！人够了！让我们开始吧！").reply()
            await self.trigger_lottery(input, group_id)
            return

        await input.add_text(
            f"好了，好了！{input.sender.nickname} 加入了这次的大乐透！当前参与人数：{len(participants)}/{self.MAX_PARTICIPANTS}").reply()

    async def trigger_lottery(self, input: GroupMessage, group_id: int):
        """触发大乐透逻辑"""
        punish_all = random.random() < self.FULL_GROUP_PUNISH_ALL_PROBABILITY
        participants = self.group_participants.get(group_id, [])

        try:
            if punish_all:
                await self.handle_punish_all(input, participants)
            else:
                await self.handle_single_winner(input, participants)
        except Exception as e:
            log.error(f"处理大乐透逻辑时出错: {e}")
        finally:
            participants.clear()  # 清空该群的参与者

    async def handle_punish_all(self, input: GroupMessage, participants: List[int]):
        """全场禁言逻辑"""
        image_path = "data/image/Lottery/103543.gif"
        if os.path.exists(image_path):
            await input.add_image(image_path).reply()

        await input.add_text("恭喜这位……").reply()
        await asyncio.sleep(3)
        await input.add_text("没有玩家取得本次大乐透的优胜！\n小小赛娜达成了清场！").reply()

        for user_id in participants:
            await self.mute_user(input, user_id, self.random_mute_duration())

        self.total_awards += len(participants)
        self.save_total_awards()

        await input.add_text(f"时至今日，大乐透已经送出了 {self.total_awards} 份奖品！").reply()

    async def handle_single_winner(self, input: GroupMessage, participants: List[int]):
        """单人禁言逻辑"""
        unlucky_user = random.choice(participants)
        mute_duration = self.random_mute_duration()
        user_name = input.sender.nickname

        await self.mute_user(input, unlucky_user, mute_duration)

        self.total_awards += 1
        self.save_total_awards()
        await input.add_text(
            f"恭喜这位 {user_name} 取得了本次大乐透的优胜！\n"
            f"奖品是……禁言 {mute_duration} 分钟！恭喜！\n"
            f"时至今日，大乐透已经送出了 {self.total_awards} 份奖品！"
        ).reply()

    def random_mute_duration(self):
        """随机生成禁言时间"""
        return random.randint(self.MIN_MUTE_MINUTES, self.MAX_MUTE_MINUTES)

    async def mute_user(self, input: GroupMessage, user_id: int, seconds: int):
        """禁言指定用户"""
        try:
            await input.set_group_ban(group_id=input.group_id, user_id=user_id, duration=seconds * 60)
        except Exception as e:
            log.debug(f"无法禁言用户：{user_id}: {e}")