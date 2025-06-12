from datetime import date
import matplotlib.pyplot as plt
import os
import requests
from PIL import Image as PILImage
from io import BytesIO


class CommonUtil:
    @staticmethod
    def get_avatar(user_id):
        avatar_dir = os.path.join("data", "image", "avatar")
        os.makedirs(avatar_dir, exist_ok=True)
        avatar_path = os.path.join(avatar_dir, f"{user_id}.jpg")
        if os.path.exists(avatar_path):
            return avatar_path
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
