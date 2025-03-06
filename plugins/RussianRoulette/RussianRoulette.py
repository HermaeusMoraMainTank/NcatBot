import logging
import random
from typing import Dict

from ncatbot.core.message import GroupMessage
from ncatbot.core.element import Image, MessageChain, Text
from ncatbot.plugin.compatible import CompatibleEnrollment
from ncatbot.plugin.base_plugin import BasePlugin

log = logging.getLogger(__name__)

bot = CompatibleEnrollment


class RussianRoulette(BasePlugin):
    name = "RussianRoulette"  # 插件名称
    version = "1.0"  # 插件版本
    CLIP_SIZE = 6  # 弹夹大小
    DAMAGE_TIME = 60  # 伤害时间（秒）
    MALFUNCTION_PROBABILITY = 0.03  # 炸膛概率
    FILE_PATH = "data/txt/RussianRoulette.txt"

    def setup(self):
        """插件初始化设置"""
        self.trigger_position_map: Dict[int, int] = {}  # 记录每个群聊的扳机位置
        self.bullet_position_map: Dict[int, int] = {}  # 记录每个群聊的子弹位置
        self.kill_count = 0  # 击杀用户数
        self.load_kill_count()

    @bot.group_event()
    async def handle_russian_roulette(self, input: GroupMessage):
        message = input.raw_message
        if message.startswith("轮盘赌"):
            await self.shoot(input)
        if message == "午时已到":
            for _ in range(6):
                if await self.shoot(input):
                    break

    def load_kill_count(self):
        try:
            with open(self.FILE_PATH, "r") as file:
                line = file.readline()
                if line and line.strip():
                    self.kill_count = int(line.strip())
                else:
                    self.kill_count = 0
                    self.save_kill_count()
        except (IOError, ValueError) as e:
            log.error(f"Error loading kill count: {e}")

    def save_kill_count(self):
        try:
            with open(self.FILE_PATH, "w") as file:
                file.write(str(self.kill_count))
        except IOError as e:
            log.error(f"Error saving kill count: {e}")

    async def reload(self, group_id: int):
        bullet_position = random.randint(0, self.CLIP_SIZE - 1)
        self.trigger_position_map[group_id] = 0  # 重置扳机位置为0
        self.bullet_position_map[group_id] = bullet_position

    async def shoot(self, input: GroupMessage) -> bool:
        group_id = input.group_id
        user_name = input.sender.nickname
        bot_name = "蓝晴"

        trigger_position = self.trigger_position_map.get(group_id)
        bullet_position = self.bullet_position_map.get(group_id)

        if trigger_position is None or bullet_position is None:
            # 如果群聊不存在扳机位置或子弹位置，则进行装填
            await self.reload(group_id)  # 使用 await 调用异步方法
            trigger_position = self.trigger_position_map[group_id]
            bullet_position = self.bullet_position_map[group_id]

        # 判断是否触发特殊情况
        if trigger_position == self.CLIP_SIZE - 1:
            await self.api.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain(
                    [
                        Text(f"{user_name}很清楚这是必死之局。"),
                    ]
                ),
            )

        # 检查是否炸膛
        if random.random() < self.MALFUNCTION_PROBABILITY:
            await self.api.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain(
                    [
                        Text(
                            "左轮手枪突然炸膛了...\n" + bot_name + "换了一把新的手枪。"
                        ),
                    ]
                ),
            )
            await self.reload(group_id)  # 使用 await 调用异步方法
            return False

        # 计算剩余子弹数量
        remaining_bullets = (self.CLIP_SIZE - trigger_position) - 1

        log.info(f"{trigger_position} {bullet_position}")

        if trigger_position == bullet_position:
            # 如果用户死了，重新进行一次reload操作
            self.kill_count += 1
            self.save_kill_count()

            # 输出用户死亡信息
            await self.api.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain(
                    [
                        Text(
                            f"{user_name}的目光逐渐变得呆滞，他向后摔倒在地，看上去像是从来没有活过似的。\n{bot_name}枪下不幸的冤魂已有 {self.kill_count} 条，但她仍然重新装上了子弹。"
                        ),
                    ]
                ),
            )
            await self.reload(group_id)  # 使用 await 调用异步方法

            random_number = random.randint(0, 99)

            if random_number <= 20:
                # 生成一个随机数，范围在0到1之间
                random_image_index = random.randint(0, 1)
                image_path = (
                    "data/image/RussianRoulette/开枪.jpg"
                    if random_image_index == 0
                    else "data/image/RussianRoulette/开枪.gif"
                )
                await self.api.post_group_msg(
                    group_id=input.group_id,
                    rtf=MessageChain(
                        [
                            Image(image_path),
                            Text(f"{bot_name}打出了暴击！"),
                        ]
                    ),
                )
                try:
                    await input.set_group_ban(
                        group_id=group_id,
                        user_id=input.user_id,
                        duration=self.DAMAGE_TIME * 2,
                    )
                    return True
                except Exception as e:
                    log.info(f"无法禁言 {input.sender.id} {input.sender.nickname}")
                    log.info(str(e))
                    return True

            try:
                await input.set_group_ban(
                    group_id=group_id, user_id=input.user_id, duration=self.DAMAGE_TIME
                )
                return True
            except Exception as e:
                log.info(f"无法禁言 {input.sender.id} {input.sender.nickname}")
                log.info(str(e))
                return True

        else:
            await self.api.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain(
                    [
                        Text(
                            f"{user_name}侥幸活过了一轮，但他终究难逃死亡的结局，每个人都会死。\n{bot_name}的左轮手枪还剩 {remaining_bullets} 发。"
                        ),
                    ]
                ),
            )
            # 更新扳机位置
            self.trigger_position_map[group_id] = (
                trigger_position + 1
            ) % self.CLIP_SIZE
            return False
