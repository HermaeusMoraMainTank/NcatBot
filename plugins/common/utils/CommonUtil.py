from datetime import date
import matplotlib.pyplot as plt
import os
import requests
import time
from pathlib import Path
from PIL import Image as PILImage
from io import BytesIO
from common.entity.GroupMember import GroupMember


class CommonUtil:
    @staticmethod
    def cleanup_old_files(
        directory: os.PathLike | str,
        *,
        max_age_seconds: float = 86400,
    ) -> int:
        """删除目录中超过 max_age_seconds 的 regular 文件，返回删除数量。"""
        root = Path(directory)
        if not root.is_dir():
            return 0
        cutoff = time.time() - max_age_seconds
        removed = 0
        for entry in root.iterdir():
            if not entry.is_file():
                continue
            try:
                if entry.stat().st_mtime < cutoff:
                    entry.unlink()
                    removed += 1
            except OSError:
                pass
        return removed

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
    def get_group_avatar(group_id):
        """下载并缓存 QQ 群头像（与用户头像区分缓存）。"""
        avatar_dir = os.path.join("data", "image", "avatar")
        os.makedirs(avatar_dir, exist_ok=True)
        gid = str(group_id)
        avatar_path = os.path.join(avatar_dir, f"g_{gid}.jpg")

        if os.path.exists(avatar_path):
            file_mtime = os.path.getmtime(avatar_path)
            if time.time() - file_mtime < 86400:
                return avatar_path
            try:
                os.remove(avatar_path)
            except OSError:
                pass

        urls = [
            f"https://p.qlogo.cn/gh/{gid}/{gid}/640/",
            f"https://p.qlogo.cn/gh/{gid}/{gid}/100/",
        ]
        for url in urls:
            try:
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200 and resp.content:
                    img = PILImage.open(BytesIO(resp.content)).convert("RGB")
                    img.save(avatar_path)
                    return avatar_path
            except Exception:
                continue

        default_path = os.path.join(avatar_dir, "default_group.jpg")
        if not os.path.exists(default_path):
            img = PILImage.new("RGB", (64, 64), (180, 200, 230))
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
    def message_at_user_ids(message) -> list:
        """从 ``MessageArray`` 按顺序提取 @ 的用户 ID（字符串）。

        NcatBot 5 使用通用消息段 ``At``（``user_id`` 字段，协议 data 里可能是 ``qq``），
        不再有 ``msg_seg_type`` / ``qq`` 等旧段形态。
        """
        from ncatbot.types import At

        if message is None:
            return []
        return [str(s.user_id) for s in message if isinstance(s, At)]

    @staticmethod
    def parse_group_member_list(members_response):
        """
        解析群成员列表 API 返回值，转换为 GroupMember 对象列表。

        兼容：
        - NcatBot 5 / NapCat：``get_group_member_list`` 直接返回 ``list[GroupMemberInfo]``
        - 旧封装：带 ``.members`` 属性的列表容器
        """
        if members_response is None:
            return []
        if isinstance(members_response, list):
            raw_members = members_response
        elif hasattr(members_response, "members"):
            raw_members = members_response.members or []
        else:
            return []

        members = []
        for member_info in raw_members:
            if member_info is None:
                continue
            if isinstance(member_info, dict):
                members.append(GroupMember(member_info))
                continue
            if hasattr(member_info, "model_dump"):
                members.append(GroupMember(member_info.model_dump()))
                continue
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
                "qq_level": getattr(member_info, "qq_level", None),
                "join_time": member_info.join_time,
                "last_sent_time": member_info.last_sent_time,
                "title_expire_time": member_info.title_expire_time,
                "unfriendly": getattr(member_info, "unfriendly", None),
                "card_changeable": getattr(member_info, "card_changeable", None),
                "is_robot": getattr(member_info, "is_robot", None),
                "shut_up_timestamp": member_info.shut_up_timestamp,
            }
            members.append(GroupMember(member_dict))
        return members
