from datetime import date
import matplotlib.pyplot as plt
import os
import requests
import time
from PIL import Image as PILImage
from io import BytesIO
from common.entity.GroupMember import GroupMember


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
        """设置matplotlib字体，支持更多Unicode字符"""
        plt.rcParams["font.sans-serif"] = [
            "Microsoft YaHei",
            "SimHei",
            "Arial Unicode MS",
            "Segoe UI Emoji",
            "Noto Sans CJK SC",  # Google Noto字体，支持更多Unicode字符
            "Source Han Sans SC",  # Adobe思源黑体
            "WenQuanYi Micro Hei",  # 文泉驿微米黑
            "DejaVu Sans",  # 支持更多Unicode字符
            "sans-serif",
        ]
        # 设置字体回退机制
        # plt.rcParams["font.fallback"] = ["DejaVu Sans"]  # 这个参数在当前matplotlib版本中不支持
        # 禁用字体警告
        import warnings

        warnings.filterwarnings("ignore", category=UserWarning, module="matplotlib")

    @staticmethod
    def parse_group_member_list(members_response):
        """
        解析 GroupMemberList 对象，转换为 GroupMember 对象列表

        Args:
            members_response: GroupMemberList 对象

        Returns:
            List[GroupMember]: GroupMember 对象列表
        """
        members = []
        for member_info in members_response.members:
            # 将 GroupMemberInfo 对象转换为字典
            member_dict = {
                "group_id": member_info.group_id,
                "user_id": member_info.user_id,
                "nickname": member_info.nickname,
                "card": member_info.card,
                "role": member_info.role,
                "title": member_info.title,
                "level": member_info.level,
                "sex": member_info.sex,
                "age": member_info.age,
                "area": member_info.area,
                "qq_level": member_info.qq_level,
                "join_time": member_info.join_time,
                "last_sent_time": member_info.last_sent_time,
                "title_expire_time": member_info.title_expire_time,
                "unfriendly": member_info.unfriendly,
                "card_changeable": member_info.card_changeable,
                "is_robot": member_info.is_robot,
                "shut_up_timestamp": member_info.shut_up_timestamp,
            }
            members.append(GroupMember(member_dict))
        return members
