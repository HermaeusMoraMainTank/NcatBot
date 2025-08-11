from datetime import date, datetime, timedelta
import matplotlib.pyplot as plt
import os
import requests
import time
from PIL import Image as PILImage
from io import BytesIO


class CommonUtil:
    @staticmethod
    def get_avatar(user_id):
        avatar_dir = os.path.join("data", "image", "avatar")
        os.makedirs(avatar_dir, exist_ok=True)
        avatar_path = os.path.join(avatar_dir, f"{user_id}.jpg")

        # 检查头像文件是否存在且未过期（1天过期时间）
        if os.path.exists(avatar_path):
            # 获取文件修改时间
            file_mtime = os.path.getmtime(avatar_path)
            current_time = time.time()
            # 1天的秒数：1 * 24 * 60 * 60 = 86400秒
            expire_time = 86400

            # 如果文件未过期，直接返回
            if current_time - file_mtime < expire_time:
                return avatar_path
            else:
                # 文件已过期，删除旧文件
                try:
                    os.remove(avatar_path)
                except Exception:
                    pass

        # 下载新头像
        url = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                img = PILImage.open(BytesIO(resp.content)).convert("RGB")
                img.save(avatar_path)
                return avatar_path
        except Exception:
            pass
        # 下载失败返回一个灰色默认头像
        default_path = os.path.join(avatar_dir, "default.jpg")
        if not os.path.exists(default_path):
            img = PILImage.new("RGB", (64, 64), (200, 200, 200))
            img.save(default_path)
        return default_path

    @staticmethod
    def calculate_current_day():
        """获取当前日期，格式为YYYY-MM-DD"""
        return date.today().strftime("%Y-%m-%d")

    @staticmethod
    def bytes_to_long(bytes):
        """将字节数组转换为长整型"""
        return int.from_bytes(bytes, byteorder="big")

    @staticmethod
    def set_matplotlib_font():
        plt.rcParams["font.sans-serif"] = [
            "Microsoft YaHei",
            "SimHei",
            "Arial Unicode MS",
            "Segoe UI Emoji",
            "sans-serif",
        ]
