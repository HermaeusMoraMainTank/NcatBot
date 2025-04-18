import os
import random
import re
from datetime import datetime, date
from typing import Dict, Tuple
from dataclasses import dataclass

from ncatbot.core.element import At, MessageChain, Text, Image
from ncatbot.plugin import CompatibleEnrollment, BasePlugin
from ncatbot.utils.logger import get_log
from ncatbot.core.message import GroupMessage

_log = get_log()
bot = CompatibleEnrollment

# 定义时间词和餐次词
time_words = r"(今|明|后)?(天|日|晚)?"
meal_words = r"(早|中|晚)?(上|午|餐|饭|夜宵|宵夜)?"
action_words = r"(吃|喝)(什么|啥|点啥)?"


@dataclass
class UserOperation:
    count: int = 0
    reset_time: date = date.today()


class EatWhat(BasePlugin):
    name = "EatWhat"  # 插件名称
    version = "1.0"  # 插件版本

    # 配置参数
    USER_MAX_COUNT = 5  # 每个用户的最大次数限制
    USER_CD_DURATION = 5  # CD的时间限制（秒）

    # 用户操作记录
    user_cd_map: Dict[int, datetime] = {}  # 用户CD时间记录
    eat_operations: Dict[int, UserOperation] = {}  # 吃操作记录
    drink_operations: Dict[int, UserOperation] = {}  # 喝操作记录

    # 达到上限时的提示消息
    MAX_MESSAGES = [
        "你今天吃的够多了！不许再吃了(´-ωก`)",
        "吃吃吃，就知道吃，你都吃饱了！明天再来(▼皿▼#)",
        "(*｀へ´*)你猜我会不会再给你发好吃的图片",
        "没得吃的了，Bot的食物都被你这坏蛋吃光了！",
        "你在等我给你发好吃的？做梦哦！你都吃那么多了，不许再吃了！ヽ(≧Д≦)ノ",
    ]

    @bot.group_event()
    async def handle_eat_what(self, input: GroupMessage) -> None:
        """处理群消息"""
        content = input.raw_message.strip()

        # 定义操作映射
        operations = {
            # 匹配格式：时间词 + 餐次词 + 吃相关词
            rf"^(/)?{time_words}{meal_words}{action_words}$": self._handle_operation,
            # 匹配格式：时间词 + 吃相关词
            rf"^(/)?{time_words}{action_words}$": self._handle_operation,
            # 匹配格式：餐次词 + 吃相关词
            rf"^(/)?{meal_words}{action_words}$": self._handle_operation,
            # 匹配格式：直接吃相关词
            rf"^(/)?{action_words}$": self._handle_operation,
            # 菜单管理相关命令
            r"^(/)?查[看|寻]?全部(菜[单|品]|饮[料|品])$": self.view_all_dishes_operation,
            r"^(/)?查[看|寻]?(菜[单|品]|饮[料|品])\s?(.*)?": self.view_dish_operation,
            r"^(/)?添[加]?(菜[品|单]|饮[品|料])\s?(.*)?": self.add_dish_operation,
            r"^(/)?删[除]?(菜[品|单]|饮[品|料])\s?(.*)?": self.delete_dish_operation,
        }

        # 根据输入选择并执行相应的操作
        for pattern, operation in operations.items():
            if re.match(pattern, content):
                await operation(input)
                break

    async def _handle_operation(self, input: GroupMessage) -> None:
        """处理吃/喝操作"""
        content = input.raw_message.strip()
        is_drink = "喝" in content
        await self._process_operation(input, is_drink)

    def _check_cd(self, user_id: int) -> bool:
        """检查用户CD是否冷却完成"""
        last_time = self.user_cd_map.get(user_id)
        if not last_time:
            return True

        now = datetime.now()
        remaining_time = self.USER_CD_DURATION - (now - last_time).total_seconds()
        return remaining_time <= 0

    def _update_cd(self, user_id: int) -> None:
        """更新用户CD时间"""
        self.user_cd_map[user_id] = datetime.now()

    def _check_and_reset_operation(
        self, user_id: int, operation_type: str
    ) -> Tuple[bool, int]:
        """检查并重置操作次数，返回是否重置和当前次数"""
        today = date.today()
        operations = (
            self.eat_operations if operation_type == "eat" else self.drink_operations
        )
        operation = operations.get(user_id, UserOperation())

        if operation.reset_time != today:
            operation.count = 0
            operation.reset_time = today
            operations[user_id] = operation

        return operation.count >= self.USER_MAX_COUNT, operation.count

    async def _process_operation(self, input: GroupMessage, is_drink: bool) -> None:
        """处理吃/喝操作的核心逻辑"""
        user_id = input.user_id
        operation_type = "drink" if is_drink else "eat"

        # 检查CD
        if not self._check_cd(user_id):
            await input.reply("不满意？")
            return

        # 检查并重置操作次数
        is_max, count = self._check_and_reset_operation(user_id, operation_type)
        if is_max:
            message = random.choice(self.MAX_MESSAGES)
            await input.reply(message)
            return

        # 获取并发送图片
        image_folder = "data/image/drink_pic" if is_drink else "data/image/eat_pic"
        success = await self._send_random_image(input, image_folder, is_drink)

        if success:
            # 更新操作次数和CD
            operations = (
                self.eat_operations
                if operation_type == "eat"
                else self.drink_operations
            )
            # 确保用户记录存在
            if user_id not in operations:
                operations[user_id] = UserOperation()
            operations[user_id].count = count + 1
            self._update_cd(user_id)

    async def _send_random_image(
        self, input: GroupMessage, image_folder: str, is_drink: bool
    ) -> bool:
        """发送随机图片"""
        try:
            if not os.path.exists(image_folder):
                _log.warning(f"图片文件夹不存在: {image_folder}")
                return False

            image_files = [
                f
                for f in os.listdir(image_folder)
                if f.lower().endswith((".jpg", ".png", ".jpeg"))
            ]

            if not image_files:
                _log.warning(f"图片文件夹为空: {image_folder}")
                return False

            random_image = random.choice(image_files)
            image_path = os.path.join(image_folder, random_image)
            image_name = os.path.splitext(random_image)[0]

            # 发送消息和图片
            action = "喝" if is_drink else "吃"
            message = MessageChain(
                [
                    At(input.user_id),
                    Text(f"\n蓝晴建议你{action}:\n⭐{image_name}⭐\n"),
                    Image(image_path),
                ]
            )
            await self.api.post_group_msg(group_id=input.group_id, rtf=message)
            return True

        except Exception as e:
            _log.error(f"发送图片时发生错误: {e}")
            return False

    async def view_all_dishes_operation(self, input: GroupMessage) -> None:
        """查看全部菜单"""
        await input.reply("不会写这个功能")

    async def view_dish_operation(self, input: GroupMessage) -> None:
        """查看特定菜单"""
        await input.reply("不会写这个功能")

    async def add_dish_operation(self, input: GroupMessage) -> None:
        """添加菜单"""
        await input.reply("不会写这个功能")

    async def delete_dish_operation(self, input: GroupMessage) -> None:
        """删除菜单"""
        await input.reply("不会写这个功能")
