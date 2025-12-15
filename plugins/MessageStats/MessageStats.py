import os
import json
import warnings
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass
from PIL import Image as PILImage
import matplotlib.pyplot as plt
import matplotlib
from common.utils.CommonUtil import CommonUtil
from PIL import ImageDraw
import re
import threading

matplotlib.use("Agg")  # 使用Agg后端，避免需要GUI
import io

from ncatbot.core import MessageChain, Text, Image, GroupMessage
from ncatbot.plugin_system import NcatBotPlugin, on_message
from ncatbot.utils.logger import get_log

_log = get_log()

# 设置 matplotlib 字体
CommonUtil.set_matplotlib_font()

# 设置中文字体
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
plt.rcParams["axes.unicode_minus"] = False  # 解决负号显示问题
# 禁用字体警告
warnings.filterwarnings("ignore", category=UserWarning, module="matplotlib")


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


class MessageStatsPlugin(NcatBotPlugin):
    name = "MessageStats"
    version = "1.0"

    # 数据存储路径
    GROUP_DATA_FILE = os.path.join("data", "json", "message_group_stats.json")
    USER_DATA_FILE = os.path.join("data", "json", "message_user_stats.json")

    # 统计数据
    group_stats: Dict[int, MessageStats] = None  # 群组发言统计
    user_stats: Dict[int, Dict[int, MessageStats]] = None  # 用户发言统计

    # 简化的数据保存控制
    _save_lock = threading.Lock()

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
        # 开始加载插件
        # 初始化实例变量
        if self.group_stats is None:
            self.group_stats = {}
        if self.user_stats is None:
            self.user_stats = {}
        # 不要清空内存中的数据，保持现有数据
        self._load_data()
        # 添加每日18点定时任务
        self.add_scheduled_task(
            self._send_daily_stats_to_all_groups,
            "daily_message_stats",
            "18:00",
        )
        _log.info("[MessageStats] 每日18点发言统计定时任务已注册")
        # 插件加载完成

    def _reinit_(self):
        """插件重新加载时同步处理钩子 - 保护内存中的数据"""
        # 保存当前内存中的数据
        temp_group_stats = self.group_stats.copy() if self.group_stats else {}
        temp_user_stats = self.user_stats.copy() if self.user_stats else {}

        # 重新初始化
        if self.group_stats is None:
            self.group_stats = {}
        if self.user_stats is None:
            self.user_stats = {}

        # 恢复数据
        self.group_stats.update(temp_group_stats)
        self.user_stats.update(temp_user_stats)

    def _save_data_to_file(self, data: dict, file_path: str) -> bool:
        """简单的数据保存"""
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(file_path), exist_ok=True)

            # 直接写入文件
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, cls=DateTimeEncoder)

            return True
        except Exception as e:
            _log.error(f"[MessageStats] 保存数据失败: {e}")
            return False

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
                        data = json.load(f)

                        # 加载群组统计
                        new_group_stats = {}
                        group_stats_data = data.get("group_stats", {})

                        for k, v in group_stats_data.items():
                            try:
                                group_id = k  # 直接使用字符串，不转换
                                new_group_stats[group_id] = MessageStats.from_dict(v)
                            except Exception as e:
                                _log.error(f"[MessageStats] 加载群组数据失败: {e}")
                                continue

                        # 合并数据而不是直接替换，避免覆盖现有数据
                        for group_id, stats in new_group_stats.items():
                            self.group_stats[group_id] = stats

                except Exception as e:
                    _log.error(f"[MessageStats] 加载群组数据失败: {e}")
                    # 不要清空内存中的数据，保持现有数据

            # 加载用户数据
            if os.path.exists(self.USER_DATA_FILE):
                try:
                    with open(self.USER_DATA_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)

                        # 加载用户统计
                        new_user_stats = {}
                        user_stats_data = data.get("user_stats", {})

                        for k, v in user_stats_data.items():
                            try:
                                group_id = k  # 直接使用字符串，不转换
                                new_user_stats[group_id] = {}

                                for k2, v2 in v.items():
                                    try:
                                        user_id = k2  # 直接使用字符串，不转换
                                        new_user_stats[group_id][user_id] = (
                                            MessageStats.from_dict(v2)
                                        )
                                    except Exception as e:
                                        _log.error(
                                            f"[MessageStats] 加载用户数据失败: {e}"
                                        )
                                        continue
                            except Exception as e:
                                _log.error(f"[MessageStats] 加载群组数据失败: {e}")
                                continue

                        # 合并数据而不是直接替换，避免覆盖现有数据
                        for group_id, users in new_user_stats.items():
                            if group_id not in self.user_stats:
                                self.user_stats[group_id] = {}
                            for user_id, stats in users.items():
                                self.user_stats[group_id][user_id] = stats

                        # 统计用户总数（用于调试）
                        # total_users = sum(
                        #     len(users) for users in self.user_stats.values()
                        # )
                except Exception as e:
                    _log.error(f"[MessageStats] 加载用户数据失败: {e}")
                    # 不要清空内存中的数据，保持现有数据

        except Exception as e:
            _log.error(f"[MessageStats] 加载数据失败: {e}")
            # 不要清空内存中的数据，保持现有数据

    def _save_group_data(self, group_id: int = None):
        """保存群组数据"""
        try:
            if group_id is not None:
                # 保存指定群聊的数据，只更新当前群组，不覆盖其他群组的数据
                if group_id in self.group_stats:
                    # 读取现有数据
                    existing_data = {}
                    if os.path.exists(self.GROUP_DATA_FILE):
                        try:
                            with open(self.GROUP_DATA_FILE, "r", encoding="utf-8") as f:
                                existing_data = json.load(f)
                        except Exception as e:
                            _log.error(f"[MessageStats] 读取现有群组数据失败: {e}")
                            existing_data = {"group_stats": {}}

                    # 确保有 group_stats 字段
                    if "group_stats" not in existing_data:
                        existing_data["group_stats"] = {}

                    # 只更新指定群聊的数据，不修改其他群组的数据
                    # 直接替换当前群组的数据，因为内存中的数据已经是累计数据
                    existing_data["group_stats"][group_id] = self.group_stats[
                        group_id
                    ].to_dict()
                    return self._save_data_to_file(existing_data, self.GROUP_DATA_FILE)
            else:
                # 保存所有群聊数据
                data = {
                    "group_stats": {k: v.to_dict() for k, v in self.group_stats.items()}
                }
                return self._save_data_to_file(data, self.GROUP_DATA_FILE)
        except Exception as e:
            _log.error(f"[MessageStats] 保存群组数据失败: {e}")
            return False

    def _save_user_data(self, group_id: int = None):
        """保存用户数据"""
        try:
            if group_id is not None:
                # 保存指定群聊的用户数据，只更新当前群组，不覆盖其他群组的数据
                if group_id in self.user_stats:
                    # 读取现有数据
                    existing_data = {}
                    if os.path.exists(self.USER_DATA_FILE):
                        try:
                            with open(self.USER_DATA_FILE, "r", encoding="utf-8") as f:
                                existing_data = json.load(f)
                        except Exception as e:
                            _log.error(f"[MessageStats] 读取现有用户数据失败: {e}")
                            existing_data = {"user_stats": {}}

                    # 确保有 user_stats 字段
                    if "user_stats" not in existing_data:
                        existing_data["user_stats"] = {}

                    # 只更新指定群聊的用户数据，不修改其他群组的数据
                    # 直接替换当前群组的用户数据，因为内存中的数据已经是累计数据
                    existing_data["user_stats"][group_id] = {
                        k2: v2.to_dict() for k2, v2 in self.user_stats[group_id].items()
                    }
                    return self._save_data_to_file(existing_data, self.USER_DATA_FILE)
            else:
                # 保存所有用户数据
                data = {
                    "user_stats": {
                        k: {k2: v2.to_dict() for k2, v2 in v.items()}
                        for k, v in self.user_stats.items()
                    }
                }
                return self._save_data_to_file(data, self.USER_DATA_FILE)
        except Exception as e:
            _log.error(f"[MessageStats] 保存用户数据失败: {e}")
            return False

    def _save_data(self, group_id: int = None):
        """保存数据 - 使用更安全的保存策略"""
        with self._save_lock:
            try:
                if group_id is not None:
                    # 保存指定群组的数据
                    self._save_group_data(group_id)
                    self._save_user_data(group_id)
                else:
                    # 保存所有数据
                    self._save_group_data()
                    self._save_user_data()
            except Exception as e:
                _log.error(f"[MessageStats] 保存数据时发生异常: {e}")

    @on_message
    async def handle_message(self, input: GroupMessage) -> None:
        """处理群消息"""
        # 确保 group_id 和 user_id 都是字符串类型
        group_id = str(input.group_id)
        user_id = str(input.sender.user_id)
        now = datetime.now()
        today = now.date().isoformat()
        hour = now.hour

        # 确保统计数据字典已初始化
        if self.group_stats is None:
            self.group_stats = {}
        if self.user_stats is None:
            self.user_stats = {}

        # 检查群组是否已存在
        if group_id in self.group_stats:
            pass  # 群组已存在

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
        self._save_data(group_id)

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

    def _generate_ranking_card(
        self,
        user_counts: Dict[str, int],
        user_names: Dict[str, str],
        total_messages: int,
        total_users: int,
        days: int = None,
        title: str = "发言排行",
        subtitle: str = "活跃用户 TOP 10",
        count_label: str = "条消息",
        total_label: str = "总发言消息数",
        users_label: str = "总参与人数",
        top_n: int = 10,
    ) -> str:
        """生成现代化排行榜卡片图片

        Args:
            user_counts: 用户ID到消息数的映射
            user_names: 用户ID到昵称的映射
            total_messages: 总消息数
            total_users: 总参与人数
            days: 时间范围（天数）
            title: 标题
            subtitle: 副标题
            count_label: 计数标签（如"条消息"）
            total_label: 总计标签（如"总发言消息数"）
            users_label: 参与人数标签
            top_n: 显示前N名

        Returns:
            生成的图片路径
        """
        from PIL import ImageFont

        # 排序并获取前N名
        top_items = sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[
            :top_n
        ]
        if not top_items:
            return None

        # 图片尺寸配置
        card_width = 540
        header_height = 120
        item_height = 85
        footer_height = 80
        padding = 30
        card_padding = 20

        # 计算卡片高度
        items_count = len(top_items)
        card_content_height = header_height + items_count * item_height + footer_height
        total_height = card_content_height + 2 * card_padding + 60  # 额外边距

        # 创建渐变背景
        img = PILImage.new("RGB", (card_width, total_height))
        draw = ImageDraw.Draw(img)

        # 绘制紫蓝色渐变背景
        for y in range(total_height):
            ratio = y / total_height
            r = int(138 + (88 - 138) * ratio)  # 从 #8a6bff 到 #5855d6
            g = int(107 + (85 - 107) * ratio)
            b = int(255 + (214 - 255) * ratio)
            draw.line([(0, y), (card_width, y)], fill=(r, g, b))

        # 绘制白色圆角卡片
        card_x = card_padding
        card_y = card_padding
        card_inner_width = card_width - 2 * card_padding
        card_inner_height = card_content_height + 20

        # 绘制圆角矩形（白色卡片）
        corner_radius = 20
        self._draw_rounded_rectangle(
            draw,
            card_x,
            card_y,
            card_x + card_inner_width,
            card_y + card_inner_height,
            corner_radius,
            fill=(255, 255, 255),
        )

        # 加载字体
        try:
            title_font = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 28)
            subtitle_font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 14)
            name_font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 16)
            count_font = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 14)
            percent_font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 13)
            rank_font = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 18)
            footer_num_font = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 32)
            footer_label_font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 12)
        except Exception:
            # 回退到默认字体
            title_font = ImageFont.load_default()
            subtitle_font = title_font
            name_font = title_font
            count_font = title_font
            percent_font = title_font
            rank_font = title_font
            footer_num_font = title_font
            footer_label_font = title_font

        # 绘制标题区域
        title_y = card_y + 25
        # 主标题
        title_text = title
        if days == -1:
            title_text = "昨日" + title
        elif days == 1:
            title_text = "今日" + title
        elif days == 7:
            title_text = "本周" + title
        elif days == 30:
            title_text = "本月" + title
        elif days is None:
            title_text = title + "（全部）"

        title_bbox = draw.textbbox((0, 0), title_text, font=title_font)
        title_width = title_bbox[2] - title_bbox[0]
        title_x = card_x + (card_inner_width - title_width) // 2
        draw.text((title_x, title_y), title_text, fill=(51, 51, 51), font=title_font)

        # 副标题
        subtitle_y = title_y + 40
        subtitle_bbox = draw.textbbox((0, 0), subtitle, font=subtitle_font)
        subtitle_width = subtitle_bbox[2] - subtitle_bbox[0]
        subtitle_x = card_x + (card_inner_width - subtitle_width) // 2
        draw.text(
            (subtitle_x, subtitle_y), subtitle, fill=(153, 153, 153), font=subtitle_font
        )

        # 日期时间
        date_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        date_y = subtitle_y + 20
        date_bbox = draw.textbbox((0, 0), date_text, font=subtitle_font)
        date_width = date_bbox[2] - date_bbox[0]
        date_x = card_x + (card_inner_width - date_width) // 2
        draw.text((date_x, date_y), date_text, fill=(180, 180, 180), font=subtitle_font)

        # 排名徽章颜色
        badge_colors = [
            (255, 193, 7),  # 金色 - 第1名
            (192, 192, 192),  # 银色 - 第2名
            (205, 127, 50),  # 铜色 - 第3名
        ]
        # 4-10名使用渐变紫色
        purple_gradient = [
            (147, 112, 219),
            (138, 107, 255),
            (123, 97, 255),
            (108, 87, 245),
            (93, 77, 235),
            (78, 67, 225),
            (63, 57, 215),
        ]

        # 绘制每个用户条目
        items_start_y = card_y + header_height
        for i, (user_id, count) in enumerate(top_items):
            item_y = items_start_y + i * item_height
            name = user_names.get(user_id, str(user_id))
            # 限制名称长度
            if len(name) > 18:
                name = name[:17] + "..."
            percentage = (count / total_messages * 100) if total_messages > 0 else 0

            # 绘制排名徽章
            badge_x = card_x + padding
            badge_y = item_y + 15
            badge_size = 36

            # 选择徽章颜色
            if i < 3:
                badge_color = badge_colors[i]
            else:
                badge_color = purple_gradient[min(i - 3, len(purple_gradient) - 1)]

            # 绘制圆角矩形徽章
            self._draw_rounded_rectangle(
                draw,
                badge_x,
                badge_y,
                badge_x + badge_size,
                badge_y + badge_size,
                8,
                fill=badge_color,
            )

            # 绘制排名数字
            rank_text = str(i + 1)
            rank_bbox = draw.textbbox((0, 0), rank_text, font=rank_font)
            rank_width = rank_bbox[2] - rank_bbox[0]
            rank_height = rank_bbox[3] - rank_bbox[1]
            rank_x = badge_x + (badge_size - rank_width) // 2
            rank_y = badge_y + (badge_size - rank_height) // 2 - 2
            draw.text((rank_x, rank_y), rank_text, fill=(255, 255, 255), font=rank_font)

            # 绘制头像
            avatar_x = badge_x + badge_size + 15
            avatar_y = item_y + 12
            avatar_size = 42

            try:
                avatar_path = CommonUtil.get_avatar(user_id)
                avatar = PILImage.open(avatar_path).convert("RGBA")
                avatar = self._circle_crop(avatar, avatar_size)
                # 创建临时RGBA图像用于粘贴
                img_rgba = img.convert("RGBA")
                img_rgba.paste(avatar, (avatar_x, avatar_y), avatar)
                img = img_rgba.convert("RGB")
                draw = ImageDraw.Draw(img)
            except Exception:
                # 绘制默认头像
                draw.ellipse(
                    [
                        avatar_x,
                        avatar_y,
                        avatar_x + avatar_size,
                        avatar_y + avatar_size,
                    ],
                    fill=(220, 220, 220),
                    outline=(200, 200, 200),
                )

            # 绘制用户名
            name_x = avatar_x + avatar_size + 15
            name_y = item_y + 15
            draw.text((name_x, name_y), name, fill=(51, 51, 51), font=name_font)

            # 绘制百分比进度条
            bar_x = name_x
            bar_y = name_y + 28
            bar_width = 180
            bar_height_px = 8

            # 进度条背景
            self._draw_rounded_rectangle(
                draw,
                bar_x,
                bar_y,
                bar_x + bar_width,
                bar_y + bar_height_px,
                4,
                fill=(230, 230, 240),
            )

            # 进度条填充
            fill_width = int(bar_width * (percentage / 100)) if percentage > 0 else 0
            if fill_width > 0:
                # 使用渐变色填充进度条
                progress_color = badge_color
                self._draw_rounded_rectangle(
                    draw,
                    bar_x,
                    bar_y,
                    bar_x + max(fill_width, 8),
                    bar_y + bar_height_px,
                    4,
                    fill=progress_color,
                )

            # 绘制百分比文字
            percent_text = f"{percentage:.1f}%"
            percent_x = bar_x
            percent_y = bar_y + 12
            draw.text(
                (percent_x, percent_y),
                percent_text,
                fill=badge_color,
                font=percent_font,
            )

            # 绘制消息数量
            count_text = f"{count} {count_label}"
            count_bbox = draw.textbbox((0, 0), count_text, font=count_font)
            count_width = count_bbox[2] - count_bbox[0]
            count_x = card_x + card_inner_width - padding - count_width
            count_y = item_y + 30
            draw.text(
                (count_x, count_y), count_text, fill=(120, 120, 120), font=count_font
            )

            # 绘制分隔线（除了最后一个）
            if i < len(top_items) - 1:
                line_y = item_y + item_height - 5
                draw.line(
                    [
                        (card_x + padding, line_y),
                        (card_x + card_inner_width - padding, line_y),
                    ],
                    fill=(240, 240, 245),
                    width=1,
                )

        # 绘制底部统计区域
        footer_y = items_start_y + items_count * item_height + 10

        # 绘制分隔线
        draw.line(
            [
                (card_x + padding, footer_y),
                (card_x + card_inner_width - padding, footer_y),
            ],
            fill=(230, 230, 240),
            width=2,
        )

        footer_y += 15

        # 左侧：总消息数
        left_center_x = card_x + card_inner_width // 4
        total_text = str(total_messages)
        total_bbox = draw.textbbox((0, 0), total_text, font=footer_num_font)
        total_width = total_bbox[2] - total_bbox[0]
        draw.text(
            (left_center_x - total_width // 2, footer_y),
            total_text,
            fill=(88, 85, 214),
            font=footer_num_font,
        )

        label_bbox = draw.textbbox((0, 0), total_label, font=footer_label_font)
        label_width = label_bbox[2] - label_bbox[0]
        draw.text(
            (left_center_x - label_width // 2, footer_y + 38),
            total_label,
            fill=(150, 150, 150),
            font=footer_label_font,
        )

        # 中间分隔线
        mid_x = card_x + card_inner_width // 2
        draw.line(
            [(mid_x, footer_y + 5), (mid_x, footer_y + 55)],
            fill=(230, 230, 240),
            width=1,
        )

        # 右侧：总参与人数
        right_center_x = card_x + card_inner_width * 3 // 4
        users_text = str(total_users)
        users_bbox = draw.textbbox((0, 0), users_text, font=footer_num_font)
        users_width = users_bbox[2] - users_bbox[0]
        draw.text(
            (right_center_x - users_width // 2, footer_y),
            users_text,
            fill=(88, 85, 214),
            font=footer_num_font,
        )

        users_label_bbox = draw.textbbox((0, 0), users_label, font=footer_label_font)
        users_label_width = users_label_bbox[2] - users_label_bbox[0]
        draw.text(
            (right_center_x - users_label_width // 2, footer_y + 38),
            users_label,
            fill=(150, 150, 150),
            font=footer_label_font,
        )

        # 保存图片
        path = os.path.join(
            "data",
            "image",
            "temp",
            f"ranking_card_{datetime.now().strftime('%Y%m%d%H%M%S%f')}.png",
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        img.save(path, quality=95)

        return path

    def _draw_rounded_rectangle(
        self, draw, x1, y1, x2, y2, radius, fill=None, outline=None
    ):
        """绘制圆角矩形"""
        # 绘制中间矩形
        draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill, outline=outline)
        draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill, outline=outline)
        # 绘制四个角
        draw.pieslice([x1, y1, x1 + 2 * radius, y1 + 2 * radius], 180, 270, fill=fill)
        draw.pieslice([x2 - 2 * radius, y1, x2, y1 + 2 * radius], 270, 360, fill=fill)
        draw.pieslice([x1, y2 - 2 * radius, x1 + 2 * radius, y2], 90, 180, fill=fill)
        draw.pieslice([x2 - 2 * radius, y2 - 2 * radius, x2, y2], 0, 90, fill=fill)

    def _circle_crop(self, img, size=44):
        """将图片裁剪为圆形"""
        img = img.resize((size, size), PILImage.LANCZOS).convert("RGBA")
        mask = PILImage.new("L", (size, size), 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((0, 0, size, size), fill=255)
        img.putalpha(mask)
        return img

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

    async def _send_daily_stats_to_all_groups(self):
        """每日定时发送发言统计到所有群组"""
        _log.info("[MessageStats] 开始执行每日发言统计定时任务")

        # 遍历所有有统计数据的群组
        for group_id in list(self.group_stats.keys()):
            try:
                await self._send_group_daily_stats(int(group_id))
            except Exception as e:
                _log.error(f"[MessageStats] 发送群组 {group_id} 每日统计失败: {e}")

        _log.info("[MessageStats] 每日发言统计定时任务执行完成")

    async def _send_group_daily_stats(self, group_id: int):
        """发送单个群组的每日发言统计"""
        group_id_str = str(group_id)
        stats = self.group_stats.get(group_id_str)
        if not stats:
            return

        # 获取今日的统计数据
        today = date.today().isoformat()
        total_count = stats.daily_counts.get(today, 0)

        # 如果今天没有发言记录，跳过
        if total_count == 0:
            _log.info(f"[MessageStats] 群组 {group_id} 今日无发言记录，跳过发送")
            return

        # 构建消息元素
        message_elements = []
        message_elements.append(Text("=== 今日发言统计 ===\n"))
        message_elements.append(Text("今日发言数量:\n"))
        for img in self._number_to_counter(total_count):
            message_elements.append(img)
        message_elements.append(Text("\n\n"))

        # 获取用户统计（今日）
        user_counts = {}
        user_names = {}
        for user_id, user_stat in self.user_stats.get(group_id_str, {}).items():
            user_today_count = user_stat.daily_counts.get(today, 0)
            if user_today_count > 0:
                user_counts[user_id] = user_today_count
                user_names[user_id] = str(user_id)

        # 批量获取群成员信息
        try:
            members_response = await self.api.get_group_member_list(group_id=group_id)
            if hasattr(members_response, "members") and members_response.members:
                members = CommonUtil.parse_group_member_list(members_response)
                for member in members:
                    if str(member.user_id) in user_names:
                        nickname = member.card if member.card else member.nickname
                        if nickname:
                            nickname = (
                                str(nickname)
                                .encode("utf-8", errors="ignore")
                                .decode("utf-8")
                            )
                        else:
                            nickname = str(member.user_id)
                        user_names[str(member.user_id)] = nickname
        except Exception as e:
            _log.error(f"[MessageStats] 获取群成员列表失败: {e}")

        # 计算总参与人数
        total_users = len(user_counts)

        if user_counts:
            # 使用新的排行榜卡片
            card_path = self._generate_ranking_card(
                user_counts=user_counts,
                user_names=user_names,
                total_messages=total_count,
                total_users=total_users,
                days=1,  # 今日
                title="发言排行",
                subtitle="活跃用户 TOP 10",
                count_label="条消息",
                total_label="总发言消息数",
                users_label="总参与人数",
                top_n=10,
            )
            if card_path:
                message_elements.append(Image(card_path))
                message_elements.append(Text("\n"))
        else:
            message_elements.append(Text("暂无用户发言数据\n"))

        # 发送消息
        try:
            message = MessageChain(message_elements)
            await self.api.post_group_msg(group_id, rtf=message)
            _log.info(f"[MessageStats] 成功发送群组 {group_id} 的每日统计")
        except Exception as e:
            _log.error(f"[MessageStats] 发送群组 {group_id} 消息失败: {e}")

    @on_message
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
        target_user_id = input.sender.user_id  # 默认为发送者
        for msg in input.message:
            if hasattr(msg, "msg_seg_type") and msg.msg_seg_type == "at":
                target_user_id = int(msg.qq)
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
            group_id = str(input.group_id)
            stats = self.group_stats.get(group_id)
            if not stats:
                _log.warning(f"[MessageStats] 群组 {group_id} 没有统计数据")
                await self.api.post_group_msg(
                    input.group_id,
                    rtf=MessageChain([Text("暂无群组统计数据")]),
                )
                return

            # 获取时间范围内的统计数据
            time_range_stats = self._get_time_range_stats(stats, days)
            total_count = sum(time_range_stats.values())
            message_elements = []
            message_elements.append(Text("=== 群组发言统计 ===\n"))
            message_elements.append(Text("最近"))
            if days is None:
                message_elements.append(Text("全部时间"))
            else:
                message_elements.append(Text(str(days)))
                message_elements.append(Text("天"))
            message_elements.append(Text("发言数量:\n"))
            for img in self._number_to_counter(total_count):
                message_elements.append(img)
            message_elements.append(Text("\n\n"))
            plot_path = self._generate_time_distribution_plot(stats, days)
            if plot_path:
                message_elements.append(Image(plot_path))
            message_elements.append(Text("\n"))
            # TOP10横向柱状图
            user_counts = {}
            user_names = {}
            for user_id, user_stat in self.user_stats.get(group_id, {}).items():
                user_time_stats = self._get_time_range_stats(user_stat, days)
                user_total = sum(user_time_stats.values())
                if user_total > 0:
                    user_counts[user_id] = user_total
                    # 先设置默认值
                    user_names[user_id] = str(user_id)

            # 批量获取群成员信息
            try:
                members_response = await self.api.get_group_member_list(
                    group_id=input.group_id
                )
                if hasattr(members_response, "members") and members_response.members:
                    members = CommonUtil.parse_group_member_list(members_response)
                    for member in members:
                        if str(member.user_id) in user_names:
                            # 优先使用群昵称，如果没有则使用QQ昵称
                            nickname = member.card if member.card else member.nickname
                            # 确保昵称是字符串类型，处理编码问题
                            if nickname:
                                nickname = (
                                    str(nickname)
                                    .encode("utf-8", errors="ignore")
                                    .decode("utf-8")
                                )
                            else:
                                nickname = str(member.user_id)
                            user_names[str(member.user_id)] = nickname
            except Exception as e:
                _log.error(f"获取群成员列表失败: {e}")
                # 如果批量获取失败，回退到单个获取
                for user_id in user_counts.keys():
                    try:
                        user_info = await self.api.get_group_member_info(
                            group_id=input.group_id, user_id=int(user_id), no_cache=True
                        )
                        if (
                            isinstance(user_info, dict)
                            and user_info.get("status") == "ok"
                        ):
                            user_data = user_info.get("data", {})
                            # 优先使用群昵称，如果没有则使用QQ昵称
                            nickname = user_data.get("card") or user_data.get(
                                "nickname", str(user_id)
                            )
                            # 确保昵称是字符串类型，处理编码问题
                            if nickname:
                                nickname = (
                                    str(nickname)
                                    .encode("utf-8", errors="ignore")
                                    .decode("utf-8")
                                )
                            else:
                                nickname = str(user_id)
                            user_names[user_id] = nickname
                    except Exception:
                        pass
            # 计算总参与人数
            total_users = len(user_counts)

            if user_counts:
                # 使用新的排行榜卡片
                card_path = self._generate_ranking_card(
                    user_counts=user_counts,
                    user_names=user_names,
                    total_messages=total_count,
                    total_users=total_users,
                    days=days,
                    title="发言排行",
                    subtitle="活跃用户 TOP 10",
                    count_label="条消息",
                    total_label="总发言消息数",
                    users_label="总参与人数",
                    top_n=10,
                )
                if card_path:
                    message_elements.append(Image(card_path))
                    message_elements.append(Text("\n"))
            else:
                message_elements.append(Text("暂无用户发言数据\n"))

            # 发送消息
            try:
                message = MessageChain(message_elements)
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
            group_id = str(input.group_id)
            user_id = str(target_user_id)
            user_stat = self.user_stats.get(group_id, {}).get(user_id)
            if not user_stat:
                await input.reply("暂无个人发言统计")
                return

            # 获取用户发言次数统计
            count_stats = self._get_time_range_stats(user_stat, days)
            total_count = sum(count_stats.values())

            # 添加消息元素
            message_elements = []
            message_elements.append(Text("=== 个人发言统计 ===\n"))
            message_elements.append(Text("最近"))
            if days is None:
                message_elements.append(Text("全部时间"))
            else:
                message_elements.append(Text(str(days)))
                message_elements.append(Text("天"))
            message_elements.append(Text("发言数量:\n"))
            for img in self._number_to_counter(total_count):
                message_elements.append(img)
            message_elements.append(Text("\n\n"))

            # 添加发言时间分布图
            plot_path = self._generate_time_distribution_plot(user_stat, days)
            if plot_path:
                message_elements.append(Image(plot_path))

            # 发送消息
            try:
                message = MessageChain(message_elements)
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
