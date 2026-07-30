import logging
import random
from typing import Dict

from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.types import Image, MessageArray as MessageChain, PlainText
from ncatbot.plugin import NcatBotPlugin
from ncatbot.core import registrar
from common.utils.plugin_commands import format_help, is_help_message

_log = logging.getLogger(__name__)

COMMAND_ROULETTE = "轮盘赌"
COMMAND_HIGH_NOON = "午时已到"
COMMANDS = (COMMAND_ROULETTE, COMMAND_HIGH_NOON)

HELP_TEXT = format_help(
    "RussianRoulette 轮盘赌",
    [
        f"{COMMAND_ROULETTE}：左轮一枪，中弹禁言",
        f"{COMMAND_HIGH_NOON}：连续开 6 枪直至有人中弹",
    ],
)


class RussianRoulette(NcatBotPlugin):
    name = "RussianRoulette"  # 插件名称
    version = "1.0"  # 插件版本
    CLIP_SIZE = 6  # 弹夹大小
    DAMAGE_TIME = 1  # 伤害时间（分钟）
    MALFUNCTION_PROBABILITY = 0.03  # 炸膛概率
    FILE_PATH = "data/txt/RussianRoulette.txt"

    async def on_load(self):
        """异步加载插件"""
        _log.info(f"开始加载 {self.name} 插件 v{self.version}")
        self.trigger_position_map: Dict[int, int] = {}  # 记录每个群聊的扳机位置
        self.bullet_position_map: Dict[int, int] = {}  # 记录每个群聊的子弹位置
        self.kill_count = 0  # 击杀用户数
        self.load_kill_count()
        _log.info(f"{self.name} 插件加载完成")

    @registrar.qq.on_group_message()
    async def handle_russian_roulette(self, input: GroupMessage):
        message = input.raw_message.strip()
        if is_help_message(
            message,
            command_names=COMMANDS):
            await input.reply(text=HELP_TEXT, at_sender=False)
            return
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
            _log.error(f"Error loading kill count: {e}")

    def save_kill_count(self):
        try:
            with open(self.FILE_PATH, "w") as file:
                file.write(str(self.kill_count))
        except IOError as e:
            _log.error(f"Error saving kill count: {e}")

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
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain(
                    [
                        PlainText(text=f"{user_name}很清楚这是必死之局。"),
                    ]
                ),
            )

        # 检查是否炸膛
        if random.random() < self.MALFUNCTION_PROBABILITY:
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain(
                    [
                        PlainText(
                            text="左轮手枪突然炸膛了...\n"
                            + bot_name
                            + "换了一把新的手枪。"
                        ),
                    ]
                ),
            )
            await self.reload(group_id)  # 使用 await 调用异步方法
            return False

        # 计算剩余子弹数量
        remaining_bullets = (self.CLIP_SIZE - trigger_position) - 1

        _log.info(f"{trigger_position} {bullet_position}")

        if trigger_position == bullet_position:
            # 如果用户死了，重新进行一次reload操作
            self.kill_count += 1
            self.save_kill_count()

            # 输出用户死亡信息
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain(
                    [
                        PlainText(
                            text=f"{user_name}的目光逐渐变得呆滞，他向后摔倒在地，看上去像是从来没有活过似的。\n{bot_name}枪下不幸的冤魂已有 {self.kill_count} 条，但他仍然重新装上了子弹。"
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
                await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    rtf=MessageChain(
                        [
                            Image(file=image_path),
                            PlainText(text=f"{bot_name}打出了暴击！"),
                        ]
                    ),
                )
                try:
                    await self.api.qq.manage.set_group_ban(
                        group_id=group_id,
                        user_id=input.sender.user_id,
                        duration=self.DAMAGE_TIME * 2 * 60,
                    )
                    return True
                except Exception as e:
                    _log.info(
                        f"无法禁言 {input.sender.user_id} {input.sender.nickname}"
                    )
                    _log.info(str(e))
                    return True

            try:
                await self.api.qq.manage.set_group_ban(
                    group_id=group_id,
                    user_id=input.sender.user_id,
                    duration=self.DAMAGE_TIME * 60,
                )
                return True
            except Exception as e:
                _log.info(f"无法禁言 {input.sender.user_id} {input.sender.nickname}")
                _log.info(str(e))
                return True

        else:
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain(
                    [
                        PlainText(
                            text=f"{user_name}侥幸活过了一轮，但他终究难逃死亡的结局，每个人都会死。\n{bot_name}的左轮手枪还剩 {remaining_bullets} 发。"
                        ),
                    ]
                ),
            )
            # 更新扳机位置
            self.trigger_position_map[group_id] = (
                trigger_position + 1
            ) % self.CLIP_SIZE
            return False
