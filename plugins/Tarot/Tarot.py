import io
import logging
import random
from datetime import datetime
from pathlib import Path
from typing import List, Set

import yaml
from PIL import Image

from ncatbot.core.message import At, GroupMessage, MessageChain, Reply, Text
from ncatbot.plugin.base_plugin import BasePlugin
from ncatbot.plugin.event import CompatibleEnrollment

from NcatBot.common.constants import HMMT

bot = CompatibleEnrollment

log = logging.getLogger(__name__)


# 塔罗牌数据类
class TarotCard:
    def __init__(self, name: str, positive: str, negative: str, image_name: str):
        self.name = name
        self.positive = positive
        self.negative = negative
        self.image_name = image_name


# 塔罗牌逻辑处理类
class Tarot(BasePlugin):
    tarot_libraries = [
        "data/image/Tarot/Tarot3",
        "data/image/Tarot/Tarot5",
        "data/image/Tarot/Tarot6",
        "data/image/Tarot/Tarot7",
        "data/image/Tarot/Tarot8",
        "data/image/Tarot/Tarot9",
        "data/image/Tarot/Tarot9",
        "data/image/Tarot/Tarot10",
    ]
    current_library_index = 0  # 初始化索引为0

    @bot.group_event()
    async def handle_tarot(self, input: GroupMessage):
        message = input.raw_message
        if message == "占卜":
            probability = 0.05  # 触发概率为5%
            if input.user_id == HMMT.HMMT_ID:
                probability += 0.5

            if random.random() < probability:
                folder = Path("data/image/Tarot/TarotNeuro")
                if folder.exists() and folder.is_dir():
                    files = [
                        f
                        for f in folder.iterdir()
                        if f.suffix.lower() in [".png", ".jpg"]
                    ]
                    if files:
                        image = random.choice(files)
                        await self.api.post_group_msg(group_id=input.group_id, rtf=MessageChain(
                            [
                                At(input.user_id),
                                Image(str(image.resolve())),
                                Reply(input.message_id),
                            ]
                        ))
                        return

            random_tarots = self.get_random_tarots(1)
            for card in random_tarots:
                i = random.randint(0, 1)
                image_bytes = self.get_tarot_image(card.image_name, i)
                log.info(
                    f"Selected tarot card: {card.name}, orientation: {'正位' if i == 0 else '逆位'}"
                )
                await self.api.post_group_msg(group_id=input.group_id, rtf=MessageChain(
                    [
                        At(input.user_id),
                        Text("\n"),
                        Text(self.get_tarot_message(card, i)),
                        Image(image_bytes),
                        Reply(input.message_id),
                    ]
                ))

    def get_tarot_message(self, tarot: TarotCard, i: int) -> str:
        description = "正位\n" + tarot.positive if i == 0 else "逆位\n" + tarot.negative
        return TarotConstant.FORMAT.replace("%牌名%", tarot.name).replace(
            "%描述%", description
        )

    def get_tarot_message_by_sender(self, tarot: TarotCard, i: int) -> str:
        description = "正位\n" + tarot.positive if i == 0 else "逆位\n" + tarot.negative
        return TarotConstant.FORMAT2.replace("%牌名%", tarot.name).replace(
            "%描述%", description
        )

    def load_tarot_data(self) -> List[TarotCard]:
        tarot_list = []
        try:
            with open("data/yml/tarot.yml", "r", encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f)
                tarot_data = yaml_data.get("tarot", [])
                for t in tarot_data:
                    tarot = TarotCard(
                        t["name"], t["positive"], t["negative"], t["imageName"]
                    )
                    tarot_list.append(tarot)
        except Exception as e:
            log.error(f"Failed to load tarot data: {e}")
        return tarot_list

    def get_random_tarot(self) -> TarotCard:
        tarots = self.load_tarot_data()
        return random.choice(tarots)

    def get_tarot_image(self, image_name: str, random_flag: int) -> str:
        """
        获取塔罗牌图像的文件路径或 URL。

        :param image_name: 图像文件名
        :param random_flag: 随机标志（0 表示正位，1 表示逆位）
        :return: 文件路径或 URL
        """

        # 获取当前日期
        current_date = datetime.now().strftime("%Y%m%d")
        library_index = abs(hash(current_date)) % len(self.tarot_libraries)
        self.current_library_index = library_index

        folder_path = Path(self.tarot_libraries[self.current_library_index])
        if folder_path.exists() and folder_path.is_dir():
            for file in folder_path.iterdir():
                if file.name == image_name:
                    if random_flag == 0 or not TarotConstant.ROTATE:
                        # 返回文件路径
                        return str(file.resolve())  # 返回文件的绝对路径
                    else:
                        # 旋转图像并保存到临时文件，返回临时文件路径
                        rotated_file = self.rotate_image(file)
                        return rotated_file
        log.debug("文件夹不存在或未找到目标文件")
        return ""  # 返回空字符串表示未找到文件

    def rotate_image(self, file: Path) -> str:
        """
        旋转图像并保存到临时文件，返回临时文件路径。

        :param file: 原始图像文件路径
        :return: 临时文件路径
        """
        try:
            with Image.open(file) as img:
                rotated_img = img.rotate(180)
                # 创建临时文件
                temp_file = Path("temp") / f"rotated_{file.name}"
                temp_file.parent.mkdir(exist_ok=True)  # 确保临时文件夹存在
                rotated_img.save(temp_file, format="JPEG")
                return str(temp_file.resolve())  # 返回临时文件的绝对路径
        except Exception as e:
            log.error(f"图像旋转失败: {e}")
            return ""

    def get_random_tarots(self, tarot_num: int) -> Set["Tarot"]:
        tarots = set() if not TarotConstant.REPEATABLE else list()
        tarot_data = self.load_tarot_data()
        while len(tarots) < tarot_num and len(tarots) < len(tarot_data):
            tarots.add(self.get_random_tarot())
        return tarots


# 常量类
class TarotConstant:
    FORMAT = "%牌名%\n%描述%"
    FORMAT2 = "%牌名%\n%描述%"
    REPEATABLE = False
    ROTATE = True
