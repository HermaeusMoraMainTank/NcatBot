import datetime
import logging
import os
import random
from ncatpy.message import GroupMessage

KEYWORD1 = "母肥"
KEYWORD2 = "母肥 "
PATH = "F:\\oobabooga_windows\\lalafell"  # 替换为实际图片目录

log = logging.getLogger(__name__)


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
                    log.info(f"Time:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {image_files}")
                    await input.add_image(random.choice(image_files)).reply()
            else:
                await input.add_text("别太贪心").reply()
        elif message == KEYWORD1:
            image_files = self.get_image_files(PATH)
            log.info(f"Time:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {image_files}")
            await input.add_image(random.choice(image_files)).reply()

    @staticmethod
    def get_image_files(folder_path):
        if os.path.isdir(folder_path):
            return [
                os.path.join(folder_path, f) for f in os.listdir(folder_path)
                if f.lower().endswith((".jpg", ".png", ".jpeg"))
            ]
        return []
