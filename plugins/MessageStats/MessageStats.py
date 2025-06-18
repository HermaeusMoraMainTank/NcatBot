import os
import json
import hashlib
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass
from PIL import Image as PILImage
import matplotlib.pyplot as plt
import matplotlib
import requests
from common.utils.CommonUtil import CommonUtil
from io import BytesIO
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from PIL import ImageDraw
import numpy as np
import re

matplotlib.use("Agg")  # 使用Agg后端，避免需要GUI
import io
import numpy as np
from matplotlib.font_manager import FontProperties

from ncatbot.core.element import MessageChain, Text, Image
from ncatbot.plugin import CompatibleEnrollment, BasePlugin
from ncatbot.utils.logger import get_log
from ncatbot.core.message import GroupMessage

_log = get_log()
bot = CompatibleEnrollment

# 设置 matplotlib 字体
CommonUtil.set_matplotlib_font()

# 设置中文字体
plt.rcParams["font.sans-serif"] = [
    "Microsoft YaHei",
    "SimHei",
    "Arial Unicode MS",
    "sans-serif",
]
plt.rcParams["axes.unicode_minus"] = False  # 解决负号显示问题


class DateTimeEncoder(json.JSONEncoder):
    """自定义 JSON 编码器，用于处理 datetime 对象"""

    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


@dataclass
class MessageStats:
    daily_counts: Dict[str, int] = None  # 按日期统计的发言次数
    hourly_counts: Dict[int, int] = None  # 按小时统计的发言次数
    last_message: datetime = datetime.now()

    def __post_init__(self):
        if self.daily_counts is None:
            self.daily_counts = {}
        if self.hourly_counts is None:
            self.hourly_counts = {}

    def to_dict(self) -> dict:
        """将对象转换为字典，处理 datetime 对象"""
        return {
            "daily_counts": self.daily_counts,
            "hourly_counts": self.hourly_counts,
            "last_message": self.last_message.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MessageStats":
        """从字典创建对象，处理 datetime 字符串"""
        # 处理可选字段
        daily_counts = data.get("daily_counts", {})
        if not isinstance(daily_counts, dict):
            daily_counts = {}

        hourly_counts = data.get("hourly_counts", {})
        if not isinstance(hourly_counts, dict):
            hourly_counts = {}

        last_message = data.get("last_message")
        if isinstance(last_message, str):
            try:
                last_message = datetime.fromisoformat(last_message)
            except ValueError:
                last_message = datetime.now()
        elif not isinstance(last_message, datetime):
            last_message = datetime.now()

        return cls(
            daily_counts=daily_counts,
            hourly_counts=hourly_counts,
            last_message=last_message,
        )

    def get_count(self, days: int = None) -> int:
        """获取指定天数内的发言次数"""
        if days is None:
            return sum(self.daily_counts.values())

        end_date = date.today()
        start_date = end_date - timedelta(days=days - 1)
        return sum(
            count
            for date_str, count in self.daily_counts.items()
            if start_date <= date.fromisoformat(date_str) <= end_date
        )

    def increment_count(self, date_str: str, hour: int):
        """增加指定日期和小时的发言次数"""
        if date_str not in self.daily_counts:
            self.daily_counts[date_str] = 0
        self.daily_counts[date_str] += 1

        hour_str = str(hour)
        if hour_str not in self.hourly_counts:
            self.hourly_counts[hour_str] = 0
        self.hourly_counts[hour_str] += 1


class MessageStatsPlugin(BasePlugin):
    name = "MessageStats"
    version = "1.0"

    # 数据存储路径
    GROUP_DATA_FILE = os.path.join("data", "json", "message_group_stats.json")
    USER_DATA_FILE = os.path.join("data", "json", "message_user_stats.json")

    # 统计数据
    group_stats: Dict[int, MessageStats] = {}  # 群组发言统计
    user_stats: Dict[int, Dict[int, MessageStats]] = {}  # 用户发言统计

    # 使用说明
    usage_instructions = """发言统计指令：
1. 查看统计：发言统计 [时间范围] [统计对象]
   
   时间范围可选：
   - 今日
   - 本周
   - 本月
   - 全部
   
   统计对象可选：
   - 群组
   - 个人
   
示例：
- 发言统计 今日 群组
- 发言统计 本周 个人
- 发言统计 全部 群组
"""

    async def on_load(self):
        """异步加载插件"""
        _log.info(f"开始加载 {self.name} 插件 v{self.version}")
        self._load_data()
        _log.info(f"{self.name} 插件加载完成")

    def _load_data(self):
        """加载保存的数据"""
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(self.GROUP_DATA_FILE), exist_ok=True)
            os.makedirs(os.path.dirname(self.USER_DATA_FILE), exist_ok=True)

            # 加载群组数据
            if os.path.exists(self.GROUP_DATA_FILE):
                try:
                    with open(self.GROUP_DATA_FILE, "r", encoding="utf-8") as f:
                        data = json.loads(f.read())
                        # 加载群组统计
                        self.group_stats = {}
                        for k, v in data.get("group_stats", {}).items():
                            try:
                                group_id = int(k)
                                self.group_stats[group_id] = MessageStats.from_dict(v)
                            except Exception as e:
                                _log.error(f"加载群组数据失败: {e}")
                                continue
                except Exception as e:
                    _log.error(f"加载群组数据失败: {e}")
                    self._save_group_data()

            # 加载用户数据
            if os.path.exists(self.USER_DATA_FILE):
                try:
                    with open(self.USER_DATA_FILE, "r", encoding="utf-8") as f:
                        data = json.loads(f.read())
                        # 加载用户统计
                        self.user_stats = {}
                        for k, v in data.get("user_stats", {}).items():
                            try:
                                group_id = int(k)
                                self.user_stats[group_id] = {}
                                for k2, v2 in v.items():
                                    try:
                                        user_id = int(k2)
                                        self.user_stats[group_id][user_id] = (
                                            MessageStats.from_dict(v2)
                                        )
                                    except Exception as e:
                                        _log.error(f"加载用户数据失败: {e}")
                                        continue
                            except Exception as e:
                                _log.error(f"加载群组数据失败: {e}")
                                continue
                except Exception as e:
                    _log.error(f"加载用户数据失败: {e}")
                    self._save_user_data()

        except Exception as e:
            _log.error(f"加载数据失败: {e}")
            self._save_group_data()
            self._save_user_data()

    def _save_group_data(self):
        """保存群组数据"""
        try:
            data = {
                "group_stats": {
                    str(k): v.to_dict() for k, v in self.group_stats.items()
                }
            }
            with open(self.GROUP_DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, cls=DateTimeEncoder)
        except Exception as e:
            _log.error(f"保存群组数据失败: {e}")
            raise

    def _save_user_data(self):
        """保存用户数据"""
        try:
            data = {
                "user_stats": {
                    str(k): {str(k2): v2.to_dict() for k2, v2 in v.items()}
                    for k, v in self.user_stats.items()
                }
            }
            with open(self.USER_DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, cls=DateTimeEncoder)
        except Exception as e:
            _log.error(f"保存用户数据失败: {e}")
            raise

    def _save_data(self):
        """保存所有数据"""
        self._save_group_data()
        self._save_user_data()

    @bot.group_event()
    async def handle_message(self, input: GroupMessage) -> None:
        """处理群消息"""
        group_id = input.group_id
        user_id = input.user_id
        now = datetime.now()
        today = now.date().isoformat()
        hour = now.hour

        # 更新群组统计
        if group_id not in self.group_stats:
            self.group_stats[group_id] = MessageStats()
        self.group_stats[group_id].increment_count(today, hour)
        self.group_stats[group_id].last_message = now

        # 更新用户统计
        if group_id not in self.user_stats:
            self.user_stats[group_id] = {}
        if user_id not in self.user_stats[group_id]:
            self.user_stats[group_id][user_id] = MessageStats()
        self.user_stats[group_id][user_id].increment_count(today, hour)
        self.user_stats[group_id][user_id].last_message = now

        # 保存数据
        self._save_data()

    def _get_time_range_stats(self, stats: MessageStats, days: int) -> Dict[str, int]:
        """获取指定时间范围内的统计"""
        if days is None:
            # 如果是全部时间，直接返回所有统计数据
            return stats.daily_counts

        end_date = date.today()
        if days == 7:  # 本周
            # 获取本周一的日期
            start_date = end_date - timedelta(days=end_date.weekday())
        else:
            start_date = end_date - timedelta(days=days - 1)  # 包含今天，所以减 days-1

        return {
            date_str: count
            for date_str, count in stats.daily_counts.items()
            if start_date <= date.fromisoformat(date_str) <= end_date
        }

    def _number_to_counter(self, number: int) -> List[Image]:
        """将数字转换为计数器图片形式"""
        # 获取所有数字图片
        digit_images = []
        for digit in str(number):
            digit_path = os.path.join("data", "image", "number", f"{digit}.gif")
            if os.path.exists(digit_path):
                digit_images.append(PILImage.open(digit_path))

        if not digit_images:
            return []

        # 获取所有帧
        frames = []
        durations = []

        # 获取每个GIF的帧数
        frame_counts = [img.n_frames for img in digit_images]
        max_frames = max(frame_counts)

        # 预处理：将所有GIF的帧提取到列表中
        digit_frames = []
        for img in digit_images:
            frames_list = []
            for i in range(img.n_frames):
                img.seek(i)
                # 确保每一帧都是完整的图像
                frame = img.copy()
                if frame.mode == "P":
                    frame = frame.convert("RGBA")
                frames_list.append(frame)
            # 如果帧数不足，复制最后一帧
            while len(frames_list) < max_frames:
                frames_list.append(frames_list[-1].copy())
            digit_frames.append(frames_list)

        # 处理每一帧
        for frame_idx in range(max_frames):
            # 计算总宽度和最大高度
            total_width = sum(frame_list[0].width for frame_list in digit_frames)
            max_height = max(frame_list[0].height for frame_list in digit_frames)

            # 创建新帧，使用完全透明的背景
            frame = PILImage.new("RGBA", (total_width, max_height), (0, 0, 0, 0))

            # 横向粘贴每个数字的当前帧
            x_offset = 0
            for digit_frame_list in digit_frames:
                current_frame = digit_frame_list[frame_idx]
                # 确保当前帧是RGBA模式
                if current_frame.mode != "RGBA":
                    current_frame = current_frame.convert("RGBA")
                # 使用alpha_composite来确保正确的透明度处理
                frame.paste(current_frame, (x_offset, 0), current_frame)
                x_offset += current_frame.width

            frames.append(frame)
            # 使用第一个GIF的持续时间
            durations.append(digit_images[0].info.get("duration", 100))

        # 保存到临时文件
        temp_path = os.path.join("data", "image", "temp", f"combined_{number}.gif")
        os.makedirs(os.path.dirname(temp_path), exist_ok=True)

        # 保存为GIF，保持动画效果
        frames[0].save(
            temp_path,
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0,
            optimize=False,
            disposal=2,  # 确保每一帧都完全清除
        )

        return [Image(temp_path)]

    def _generate_time_distribution_plot(
        self, stats: MessageStats, days: int
    ) -> Optional[str]:
        """生成时间分布图"""
        try:
            plt.clf()  # 清除当前图形
            plt.figure(figsize=(12, 6))

            if days == 1:  # 今日
                # 获取24小时数据
                hours = list(range(24))
                counts = [stats.hourly_counts.get(str(hour), 0) for hour in hours]

                plt.bar(hours, counts, alpha=0.6, color="skyblue")
                plt.plot(hours, counts, "r-", linewidth=2)

                plt.xlabel("小时", fontsize=12)
                plt.ylabel("发言次数", fontsize=12)
                plt.title("今日发言时间分布", fontsize=14)
                plt.xticks(hours)
                plt.grid(True, linestyle="--", alpha=0.7)

            elif days == 7:  # 本周
                # 获取本周数据
                today = date.today()
                start_date = today - timedelta(days=today.weekday())
                dates = [(start_date + timedelta(days=i)) for i in range(7)]
                counts = [stats.daily_counts.get(date.isoformat(), 0) for date in dates]

                plt.bar(range(7), counts, alpha=0.6, color="skyblue")
                plt.plot(range(7), counts, "r-", linewidth=2)

                plt.xlabel("星期", fontsize=12)
                plt.ylabel("发言次数", fontsize=12)
                plt.title("本周发言分布", fontsize=14)
                plt.xticks(
                    range(7), ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
                )
                plt.grid(True, linestyle="--", alpha=0.7)

            elif days == 30:  # 本月
                # 获取本月数据
                today = date.today()
                start_date = date(today.year, today.month, 1)
                dates = [(start_date + timedelta(days=i)) for i in range(today.day)]
                counts = [stats.daily_counts.get(date.isoformat(), 0) for date in dates]

                plt.bar(range(len(dates)), counts, alpha=0.6, color="skyblue")
                plt.plot(range(len(dates)), counts, "r-", linewidth=2)

                plt.xlabel("日期", fontsize=12)
                plt.ylabel("发言次数", fontsize=12)
                plt.title("本月发言分布", fontsize=14)
                plt.xticks(
                    range(len(dates)), [d.strftime("%d") for d in dates], rotation=45
                )
                plt.grid(True, linestyle="--", alpha=0.7)

            else:  # 全部
                # 获取所有月份数据
                all_dates = sorted(stats.daily_counts.keys())
                if not all_dates:
                    return None

                # 按月份统计
                monthly_counts = {}
                for date_str in all_dates:
                    month = date.fromisoformat(date_str).strftime("%Y-%m")
                    monthly_counts[month] = (
                        monthly_counts.get(month, 0) + stats.daily_counts[date_str]
                    )

                months = list(monthly_counts.keys())
                counts = list(monthly_counts.values())

                plt.bar(range(len(months)), counts, alpha=0.6, color="skyblue")
                plt.plot(range(len(months)), counts, "r-", linewidth=2)

                plt.xlabel("月份", fontsize=12)
                plt.ylabel("发言次数", fontsize=12)
                plt.title("发言月度分布", fontsize=14)
                plt.xticks(range(len(months)), months, rotation=45)
                plt.grid(True, linestyle="--", alpha=0.7)

            # 调整布局
            plt.tight_layout()

            # 保存图片到内存
            buf = io.BytesIO()
            plt.savefig(buf, format="png", dpi=100, bbox_inches="tight")
            buf.seek(0)

            # 保存到临时文件
            temp_path = os.path.join(
                "data",
                "image",
                "temp",
                f"time_dist_{datetime.now().strftime('%Y%m%d%H%M%S')}.png",
            )
            os.makedirs(os.path.dirname(temp_path), exist_ok=True)

            with open(temp_path, "wb") as f:
                f.write(buf.getvalue())

            plt.close()
            return temp_path

        except Exception as e:
            _log.error(f"生成时间分布图失败: {e}")
            plt.close()
            return None

    def _generate_top_users_barh(self, user_counts, user_names, top_n=10):
        plt.clf()
        # 只取前十
        top_items = sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[
            :top_n
        ]
        names = [user_names.get(uid, str(uid)) for uid, _ in top_items]
        counts = [cnt for _, cnt in top_items]
        user_ids = [uid for uid, _ in top_items]
        fig, ax = plt.subplots(figsize=(10, 0.8 * len(names) + 1))
        color_palette = [
            "#f7c873",
            "#f7a8b8",
            "#a3c9f7",
            "#b8a3f7",
            "#f7e3a3",
            "#a3f7d3",
            "#f7a3e3",
            "#a3f7f7",
            "#f7b8a3",
            "#d3a3f7",
        ]
        bar_colors = color_palette[: len(names)]
        bars = ax.barh(
            range(len(names)),
            counts,
            color=bar_colors,
            edgecolor="#fff",
            height=0.65,
            zorder=2,
        )
        ax.set_yticks(range(len(names)))
        ax.set_yticklabels([""] * len(names))  # 清空y轴标签
        ax.set_xlabel("发言次数", fontsize=14, fontweight="bold")
        ax.set_title(
            "发言TOP10", fontsize=18, fontweight="bold", color="#6c63ff", pad=15
        )
        ax.invert_yaxis()
        ax.set_facecolor("#f7f7fa")
        fig.patch.set_facecolor("#f7f7fa")
        ax.xaxis.grid(True, linestyle="--", color="#ccc", alpha=0.5, zorder=1)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_visible(False)
        ax.spines["bottom"].set_color("#aaa")

        # 添加数值标签
        for i, bar in enumerate(bars):
            width = bar.get_width()
            ax.text(
                width + 1,
                bar.get_y() + bar.get_height() / 2,
                f"{counts[i]}",
                va="center",
                fontsize=15,
                fontweight="bold",
                color="#6c63ff",
                zorder=3,
            )

        cn_font = FontProperties(
            fname="C:/Windows/Fonts/msyh.ttc", size=15, weight="bold"
        )
        emoji_font = FontProperties(fname="C:/Windows/Fonts/seguiemj.ttf", size=15)

        def circle_crop(img, size=44):
            img = img.resize((size, size), PILImage.LANCZOS).convert("RGBA")
            mask = PILImage.new("L", (size, size), 0)
            draw = ImageDraw.Draw(mask)
            draw.ellipse((0, 0, size, size), fill=255)
            img.putalpha(mask)
            return img

        def default_avatar(size=44):
            img = PILImage.new("RGBA", (size, size), (200, 200, 200, 255))
            draw = ImageDraw.Draw(img)
            draw.ellipse(
                (0, 0, size - 1, size - 1),
                fill=(220, 220, 220, 255),
                outline=(180, 180, 180, 255),
                width=2,
            )
            return img

        # 头像更靠左
        avatar_x = -max(counts) * 0.12 if counts else -1
        # 昵称紧贴柱子
        name_x = 0

        # 头像 AnnotationBbox 预览
        for i, user_id in enumerate(user_ids):
            try:
                avatar_path = CommonUtil.get_avatar(user_id)
                avatar = PILImage.open(avatar_path).convert("RGBA")
                avatar = circle_crop(avatar)
            except Exception as e:
                print(f"头像异常: {user_id} {e}")
                avatar = default_avatar()
            imagebox = OffsetImage(avatar, zoom=2.0)
            ab = AnnotationBbox(
                imagebox,
                (avatar_x, i),
                frameon=False,
                box_alignment=(0.5, 0.5),
                pad=0.1,
                zorder=20,
            )
            ax.add_artist(ab)

        # 渲染y轴标签（支持emoji混排）
        for i, name in enumerate(names):
            x = name_x
            for part_type, part in self.split_emoji(name):
                if part_type == "text":
                    ax.text(
                        x,
                        i,
                        part,
                        va="center",
                        ha="left",
                        fontproperties=cn_font,
                        color="#444",
                        zorder=12,
                    )
                    x += len(part) * 0.18 * max(counts) / 10 if counts else 0.2
                else:
                    ax.text(
                        x,
                        i,
                        part,
                        va="center",
                        ha="left",
                        fontproperties=emoji_font,
                        color="#444",
                        zorder=13,
                    )
                    x += len(part) * 0.18 * max(counts) / 10 if counts else 0.2

        # 在 plt.savefig 之前获取柱子的像素坐标和高度
        fig.canvas.draw()
        bar_pixel_boxes = []
        renderer = fig.canvas.get_renderer()
        for bar in bars:
            bbox = bar.get_window_extent(renderer)
            l, b, r, t = map(int, bbox.bounds)
            bar_pixel_boxes.append((l, b, r, t))

        plt.tight_layout(rect=[0.08, 0, 1, 1])
        plt.subplots_adjust(left=0.18)
        path = os.path.join(
            "data",
            "image",
            "temp",
            f"top_users_{datetime.now().strftime('%Y%m%d%H%M%S')}.png",
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        plt.savefig(path, bbox_inches="tight")
        plt.close()

        # 用 PIL 贴头像（精确布局）
        avatar_size = 46.2
        avatar_x = 118
        top_margin = 86
        bar_height = 45
        bar_gap = 28.2
        img = PILImage.open(path).convert("RGBA")
        for i, user_id in enumerate(user_ids):
            try:
                avatar_path = CommonUtil.get_avatar(user_id)
                avatar = PILImage.open(avatar_path).convert("RGBA")
                avatar = avatar.resize(
                    (int(round(avatar_size)), int(round(avatar_size))), PILImage.LANCZOS
                )
                avatar_y = top_margin + i * (bar_height + bar_gap)
                img.paste(avatar, (int(round(avatar_x)), int(round(avatar_y))), avatar)
            except Exception as e:
                print(f"头像PIL粘贴异常: {user_id} {e}")
        img.save(path)
        return path

    def split_emoji(self, text):
        emoji_pattern = re.compile(
            "["
            "\U0001f600-\U0001f64f"
            "\U0001f300-\U0001f5ff"
            "\U0001f680-\U0001f6ff"
            "\U0001f1e0-\U0001f1ff"
            "\U00002700-\U000027bf"
            "\U0001f900-\U0001f9ff"
            "\U00002600-\U000026ff"
            "\U00002b50"
            "\U0000231a"
            "]+",
            flags=re.UNICODE,
        )
        result = []
        last = 0
        for m in emoji_pattern.finditer(text):
            if m.start() > last:
                result.append(("text", text[last : m.start()]))
            result.append(("emoji", m.group()))
            last = m.end()
        if last < len(text):
            result.append(("text", text[last:]))
        return result

    @bot.group_event()
    async def handle_message_stats(self, input: GroupMessage) -> None:
        """处理发言统计命令"""
        message = input.raw_message.strip()
        if not message.startswith("发言统计"):
            return

        # 分割命令，处理多个空格的情况
        message_parts = [part for part in message.split() if part]
        if len(message_parts) < 3:
            await input.reply("命令格式错误，请使用：发言统计 [时间范围] [统计对象]")
            return

        time_range = message_parts[1]
        target = message_parts[2]

        # 检查是否有艾特消息
        target_user_id = input.user_id  # 默认为发送者
        for msg in input.message:
            if msg["type"] == "at":
                target_user_id = int(msg["data"]["qq"])
                break

        # 获取时间范围对应的天数
        days_map = {"今日": 1, "本周": 7, "本月": 30, "全部": None}
        days = days_map.get(time_range)
        if days is None and time_range != "全部":
            await input.reply("无效的时间范围，请使用：今日、本周、本月、全部")
            return

        if target not in ["群组", "个人"]:
            await input.reply("无效的统计对象，请使用：群组、个人")
            return

        await self._show_stats(input, days, target, target_user_id)

    async def _show_stats(
        self, input: GroupMessage, days: int, target: str, target_user_id: int
    ) -> None:
        """显示统计数据"""
        # 发送初始响应
        await self.api.post_group_msg(
            input.group_id, rtf=MessageChain([Text("正在生成统计图表，请稍候...")])
        )

        if target == "群组":
            stats = self.group_stats.get(input.group_id)
            if not stats:
                await self.api.post_group_msg(
                    input.group_id,
                    rtf=MessageChain([Text("暂无群组统计数据")]),
                )
                return

            # 获取时间范围内的统计数据
            time_range_stats = self._get_time_range_stats(stats, days)
            total_count = sum(time_range_stats.values())
            message = MessageChain([])
            message.chain.append(Text("=== 群组发言统计 ===\n"))
            message.chain.append(Text("最近"))
            if days is None:
                message.chain.append(Text("全部时间"))
            else:
                message.chain.append(Text(str(days)))
                message.chain.append(Text("天"))
            message.chain.append(Text("发言数量:\n"))
            for img in self._number_to_counter(total_count):
                message.chain.append(img)
            message.chain.append(Text("\n\n"))
            plot_path = self._generate_time_distribution_plot(stats, days)
            if plot_path:
                message.chain.append(Image(plot_path))
            message.chain.append(Text("\n"))
            # TOP10横向柱状图
            user_counts = {}
            user_names = {}
            for user_id, user_stat in self.user_stats.get(input.group_id, {}).items():
                user_time_stats = self._get_time_range_stats(user_stat, days)
                user_total = sum(user_time_stats.values())
                if user_total > 0:
                    user_counts[user_id] = user_total
                    try:
                        user_info = await self.api.get_group_member_info(
                            group_id=input.group_id, user_id=user_id, no_cache=True
                        )
                        if (
                            isinstance(user_info, dict)
                            and user_info.get("status") == "ok"
                        ):
                            user_data = user_info.get("data", {})
                            nickname = user_data.get("nickname", str(user_id))
                            user_names[user_id] = nickname
                        else:
                            user_names[user_id] = str(user_id)
                    except Exception:
                        user_names[user_id] = str(user_id)
            if user_counts:
                bar_path = self._generate_top_users_barh(
                    user_counts, user_names, top_n=10
                )
                message.chain.append(Text("发言最多的用户TOP10：\n"))
                message.chain.append(Image(bar_path))
                message.chain.append(Text("\n"))
            else:
                message.chain.append(Text("暂无用户发言数据\n"))

            # 发送消息
            try:
                await self.api.post_group_msg(
                    input.group_id, rtf=message, reply=input.message_id
                )
            except Exception as e:
                _log.error(f"发送消息失败: {e}")
                # 尝试发送纯文本消息
                error_message = MessageChain([Text("统计信息发送失败，请稍后重试")])
                await self.api.post_group_msg(
                    input.group_id, rtf=error_message, reply=input.message_id
                )
        else:
            # 获取用户统计
            user_stat = self.user_stats.get(input.group_id, {}).get(target_user_id)
            if not user_stat:
                await input.reply("暂无个人发言统计")
                return

            # 获取用户发言次数统计
            count_stats = self._get_time_range_stats(user_stat, days)
            total_count = sum(count_stats.values())

            # 添加消息元素
            message = MessageChain([])
            message.chain.append(Text("=== 个人发言统计 ===\n"))
            message.chain.append(Text("最近"))
            if days is None:
                message.chain.append(Text("全部时间"))
            else:
                message.chain.append(Text(str(days)))
                message.chain.append(Text("天"))
            message.chain.append(Text("发言数量:\n"))
            for img in self._number_to_counter(total_count):
                message.chain.append(img)
            message.chain.append(Text("\n\n"))

            # 添加发言时间分布图
            plot_path = self._generate_time_distribution_plot(user_stat, days)
            if plot_path:
                message.chain.append(Image(plot_path))

            # 发送消息
            try:
                await self.api.post_group_msg(
                    input.group_id, rtf=message, reply=input.message_id
                )
            except Exception as e:
                _log.error(f"发送消息失败: {e}")
                # 尝试发送纯文本消息
                error_message = MessageChain([Text("统计信息发送失败，请稍后重试")])
                await self.api.post_group_msg(
                    input.group_id, rtf=error_message, reply=input.message_id
                )
