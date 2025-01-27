import os
import random
import hashlib
from datetime import datetime, date
from typing import List, Dict
from PIL import Image, ImageDraw, ImageFont
import json

from ncatpy.common.constants.HMMT import HMMT
from ncatpy.message import GroupMessage

# 常量
LUCK_DESC_LIST = [
    "amazing_grace", "arknights", "asoul", "azure", "dc4", "einstein", "genshin", "hoshizora", "liqingge",
    "onmyoji", "pcr", "pretty_derby", "punishing", "sakura", "summer_pockets", "sweet_illusion", "touhou",
    "touhou_lostword", "touhou_old", "warship_girls_r"
]

# 记录用户最后一次调用的日期
last_invocation_date_by_user: Dict[int, date] = {}
last_reset_date: date = date.today()  # 记录上一次重置的日期


class Fortune:
    def __init__(self):
        """
        初始化运势插件
        """
        self.data_dir = "data"

    def get_file_path(self, *paths: str) -> str:
        """
        获取文件路径
        :param paths: 路径片段
        :return: 拼接后的完整路径
        """
        return os.path.join(self.data_dir, *paths)

    async def handle_fortune(self, input: GroupMessage):
        message = input.raw_message
        sender_id = input.user_id

        if message == "运势":
            luck_value = self.calculate_luck_value(sender_id)
            image_files = os.listdir(self.get_file_path("image", "amm"))
            fortune_image = image_files[luck_value % len(image_files)]
            image_path = self.get_file_path("image", "amm", fortune_image)
            await input.add_text(f"✨今日运势✨\n").add_at(sender_id).add_image(image_path).reply()

        elif message == "今日运势":
            current_date = date.today()

            # 检查日期是否已经跨天，如果是，则执行重置操作
            global last_reset_date
            if current_date != last_reset_date:
                last_reset_date = current_date
                last_invocation_date_by_user.clear()

            # 检查用户是否已经调用过
            last_invocation_date = last_invocation_date_by_user.get(sender_id, date.min)
            if current_date == last_invocation_date:
                await input.add_text("你今天已经获取过运势了，请明天再来吧。").reply()
                return

            # 生成运势图片
            try:
                pic = self.drawing_pic()
                output_path = self.get_file_path("image", "fortune", "output.png")
                pic.save(output_path)
                await input.add_text(f"✨今日运势✨\n").add_at(sender_id).add_image(output_path).reply()
            except Exception as e:
                print(f"Error generating fortune image: {e}")

            # 记录用户调用日期
            last_invocation_date_by_user[sender_id] = current_date

        elif message.startswith("重置") and message.endswith("的运势") and sender_id == HMMT.HMMT_ID:
            for isAt in input.message:
                if isAt.get("type") == "at":
                    target_user_id = int(isAt.get("data").get("qq"))

            target = await input.get_group_member_info(group_id=input.group_id, user_id=target_user_id)
            target_username = target.get("data").get("nickname")
            if target_user_id == 0:
                await input.add_text("找不到目标群友，请确认用户名是否正确。").reply()
                return

            if target_user_id in last_invocation_date_by_user:
                del last_invocation_date_by_user[target_user_id]
                await input.add_text(f"成功重置了 {target_username} 的运势。").reply()
                await input.add_at(target_user_id).add_text(f"你的运势被 {input.sender.nickname} 重置了。")
            else:
                await input.add_text(f"{target_username} 没有获取过今日运势，无法重置。").reply()

    def calculate_luck_value(self, user_id: int) -> int:
        """计算运势值"""
        message_digest = hashlib.sha256()
        message_digest.update(str(user_id).encode())
        message_digest.update(str(datetime.now().date()).encode())
        message_digest.update(str(42).encode())
        digest = message_digest.digest()
        luck_value = abs(int.from_bytes(digest, byteorder="big")) % 6
        return luck_value

    def drawing_pic(self) -> Image.Image:
        """生成运势图片"""
        font_title_path = self.get_file_path("font", "Mamelon.otf")
        font_text_path = self.get_file_path("font", "sakura.ttf")

        luck_desc = self.get_random_luck_desc()
        base_img_path = self.get_random_base_map(luck_desc)

        img = Image.open(base_img_path)
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

    def draw_vertical_text(self, draw: ImageDraw.Draw, text_lines: List[str], font_path: str):
        """绘制垂直文本"""
        font = ImageFont.truetype(font_path, 25)
        for i, line in enumerate(text_lines):
            font_height = len(line) * (25 + 4)
            draw_x = 140 + (len(text_lines) - 2) * 25 / 2 + (len(text_lines) - 1) * 4 - i * (25 + 4)
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
                result.append(text[len(text) // 2:])
            else:
                result.append(text[: (len(text) + 1) // 2])
                result.append(" " + text[(len(text) + 1) // 2:])
        else:
            for i in range(col_num):
                start = i * cardinality
                end = (i + 1) * cardinality if i < col_num - 1 else None
                result.append(text[start:end])

        return result
