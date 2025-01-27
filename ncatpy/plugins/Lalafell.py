import os
import random
from ncatpy.message import GroupMessage

KEYWORD1 = "母肥"
KEYWORD2 = "母肥 "
PATH = "F:\\oobabooga_windows\\lalafell"  # 替换为实际图片目录

class Lalafell:
    def __init__(self):
        pass

    async def handle_lalafell(self, input: GroupMessage):
        message = input.raw_message

        if message.startswith(KEYWORD2):
            trimmed_message = message[len(KEYWORD2):].strip()
            if not trimmed_message.isdigit():
                return

            count = int(trimmed_message)
            image_files = self.get_image_files(PATH)

            if count <= 3:
                for _ in range(count):
                    await input.add_image(random.choice(image_files)).reply()
            else:
                await input.add_text("别太贪心").reply()
        elif message == KEYWORD1:
            image_files = self.get_image_files(PATH)
            await input.add_image(random.choice(image_files)).reply()

    @staticmethod
    def get_image_files(folder_path):
        if os.path.isdir(folder_path):
            return [
                os.path.join(folder_path, f) for f in os.listdir(folder_path)
                if f.lower().endswith((".jpg", ".png", ".jpeg"))
            ]
        return []
