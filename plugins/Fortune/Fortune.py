import os
import random
import hashlib
import logging
from datetime import datetime, date
from typing import List, Dict
from PIL import Image as PILImage, ImageDraw, ImageFont
import json

from common.constants.HMMT import HMMT
from ncatbot.core.element import At, MessageChain, Text, Image
from ncatbot.core.message import GroupMessage
from ncatbot.plugin import CompatibleEnrollment, BasePlugin


bot = CompatibleEnrollment
log = logging.getLogger(__name__)

# 常量
LUCK_DESC_LIST = [
    "amazing_grace",
    "arknights",
    "asoul",
    "azure",
    "dc4",
    "einstein",
    "genshin",
    "hoshizora",
    "liqingge",
    "onmyoji",
    "pcr",
    "pretty_derby",
    "punishing",
    "sakura",
    "summer_pockets",
    "sweet_illusion",
    "touhou",
    "touhou_lostword",
    "touhou_old",
    "warship_girls_r",
]


class Fortune(BasePlugin):
    name = "Fortune"  # 插件名称
    version = "1.0"  # 插件版本

    # 记录用户最后一次调用的日期
    last_invocation_date_by_user: Dict[int, date] = {}
    last_reset_date: date = date.today()  # 记录上一次重置的日期
    data_dir = "data"

    async def on_load(self):
        """异步加载插件"""
        log.info(f"开始加载 {self.name} 插件 v{self.version}")
        log.info(f"{self.name} 插件加载完成")

    @bot.group_event()
    async def handle_fortune(self, input: GroupMessage):
        message = input.raw_message
        sender_id = input.user_id

        if message == "运势":
            luck_value = self.calculate_luck_value(sender_id)
            image_files = os.listdir(self.get_file_path("image", "amm"))
            fortune_image = image_files[luck_value % len(image_files)]
            image_path = self.get_file_path("image", "amm", fortune_image)
            message = MessageChain(
                [Text("✨今日运势✨\n"), At(sender_id), Image(image_path)]
            )
            await self.api.post_group_msg(group_id=input.group_id, rtf=message)

        elif message == "今日doro":
            luck_value = self.calculate_luck_value(sender_id)
            image_files = os.listdir(self.get_file_path("image", "doro结局"))
            fortune_image = image_files[luck_value % len(image_files)]
            image_path = self.get_file_path("image", "doro结局", fortune_image)
            message = MessageChain(
                [Text("✨今日doro结局✨\n"), At(sender_id), Image(image_path)]
            )
            await self.api.post_group_msg(group_id=input.group_id, rtf=message)

        elif message == "今日运势":
            current_date = date.today()

            # 检查日期是否已经跨天，如果是，则执行重置操作
            if current_date != self.last_reset_date:
                self.last_reset_date = current_date
                self.last_invocation_date_by_user.clear()

            # 检查用户是否已经调用过
            last_invocation_date = self.last_invocation_date_by_user.get(
                sender_id, date.min
            )
            if current_date == last_invocation_date:
                message = MessageChain([Text("你今天已经获取过运势了，请明天再来吧。")])
                await self.api.post_group_msg(group_id=input.group_id, rtf=message)
                return

            # 生成运势图片
            try:
                pic = self.drawing_pic()
                output_path = self.get_file_path("image", "fortune", "output.png")
                pic.save(output_path)
                message = MessageChain(
                    [Text("✨今日运势✨\n"), At(sender_id), Image(output_path)]
                )
                await self.api.post_group_msg(group_id=input.group_id, rtf=message)
            except Exception as e:
                print(f"Error generating fortune image: {e}")

            # 记录用户调用日期
            self.last_invocation_date_by_user[sender_id] = current_date

        elif (
            message.startswith("重置")
            and message.endswith("的运势")
            and sender_id == HMMT.HMMT_ID
        ):
            for isAt in input.message:
                if isAt.get("type") == "at":
                    target_user_id = int(isAt.get("data").get("qq"))

            target = await self.api.get_group_member_info(
                group_id=input.group_id, user_id=target_user_id, no_cache=True
            )
            target_username = target.get("data").get("nickname")
            if target_user_id == 0:
                message = MessageChain([Text("找不到目标群友，请确认用户名是否正确。")])
                await self.api.post_group_msg(group_id=input.group_id, rtf=message)
                return

            if target_user_id in self.last_invocation_date_by_user:
                del self.last_invocation_date_by_user[target_user_id]
                message = MessageChain([Text(f"成功重置了 {target_username} 的运势。")])
                await self.api.post_group_msg(group_id=input.group_id, rtf=message)

                message = MessageChain(
                    [
                        At(target_user_id),
                        Text(f"你的运势被 {input.sender.nickname} 重置了。"),
                    ]
                )
                await self.api.post_group_msg(group_id=input.group_id, rtf=message)
            else:
                message = MessageChain(
                    [Text(f"{target_username} 没有获取过今日运势，无法重置。")]
                )
                await self.api.post_group_msg(group_id=input.group_id, rtf=message)

    def calculate_luck_value(self, user_id: int) -> int:
        """计算运势值"""
        message_digest = hashlib.sha256()
        message_digest.update(str(user_id).encode())
        message_digest.update(str(datetime.now().date()).encode())
        message_digest.update(str(42).encode())
        digest = message_digest.digest()
        luck_value = abs(int.from_bytes(digest, byteorder="big")) % 6
        return luck_value

    def drawing_pic(self) -> PILImage.Image:
        """生成运势图片"""
        font_title_path = self.get_file_path("font", "Mamelon.otf")
        font_text_path = self.get_file_path("font", "sakura.ttf")

        luck_desc = self.get_random_luck_desc()
        base_img_path = self.get_random_base_map(luck_desc)

        img = PILImage.open(base_img_path)
        draw = ImageDraw.Draw(img)

        # 绘制标题
        title = self.get_luck_info()[0]
        self.draw_text(draw, title, font_title_path)

        # 绘制正文
        text_content = self.get_luck_info()[1]
        result = self.decrement(text_content)
        if result:
            self.draw_vertical_text(draw, result, font_text_path)

        return img

    def draw_text(self, draw: ImageDraw.Draw, text: str, font_path: str):
        """绘制水平文本"""
        font = ImageFont.truetype(font_path, 45)
        text_width = draw.textlength(text, font=font)
        centered_x = 140 - text_width / 2
        draw.text((centered_x, 80), text, font=font, fill="#F5F5F5")

    def draw_vertical_text(
        self, draw: ImageDraw.Draw, text_lines: List[str], font_path: str
    ):
        """绘制垂直文本"""
        font = ImageFont.truetype(font_path, 25)
        for i, line in enumerate(text_lines):
            font_height = len(line) * (25 + 4)
            draw_x = (
                140
                + (len(text_lines) - 2) * 25 / 2
                + (len(text_lines) - 1) * 4
                - i * (25 + 4)
            )
            draw_y = 300 - font_height / 2
            for j, char in enumerate(line):
                draw.text((draw_x, draw_y + j * 25), char, font=font, fill="#323232")

    def get_random_base_map(self, desc: str) -> str:
        """随机获取背景图片路径"""
        img_path = self.get_file_path("image", "fortune", desc)
        files = os.listdir(img_path)
        if files:
            return os.path.join(img_path, random.choice(files))
        return ""

    def get_random_luck_desc(self) -> str:
        """随机获取运势描述"""
        return random.choice(LUCK_DESC_LIST)

    def get_luck_info(self) -> List[str]:
        """获取运势信息"""
        file_path = self.get_file_path("json/copywriting.json")
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                copywriting = random.choice(data["copywriting"])
                good_luck = copywriting["good-luck"]
                content = random.choice(copywriting["content"])
                return [good_luck, content]
        except Exception as e:
            print(f"Error reading luck info: {e}")
            return ["", ""]

    def decrement(self, text: str) -> List[str]:
        """将文本分割为多行"""
        length = len(text)
        result = []
        cardinality = 9

        if length > 4 * cardinality:
            raise ValueError("Text is too long")

        col_num = 1
        while length > cardinality:
            col_num += 1
            length -= cardinality

        if col_num == 2:
            if len(text) % 2 == 0:
                result.append(text[: len(text) // 2])
                result.append(text[len(text) // 2 :])
            else:
                result.append(text[: (len(text) + 1) // 2])
                result.append(" " + text[(len(text) + 1) // 2 :])
        else:
            for i in range(col_num):
                start = i * cardinality
                end = (i + 1) * cardinality if i < col_num - 1 else None
                result.append(text[start:end])

        return result

    def get_file_path(self, *paths: str) -> str:
        """
        获取文件路径
        :param paths: 路径片段
        :return: 拼接后的完整路径
        """
        return os.path.join(self.data_dir, *paths)
