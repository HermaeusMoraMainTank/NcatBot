import os
import json
import hashlib
import requests
import urllib3
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass
from PIL import Image as PILImage, ImageDraw
import io
import matplotlib.pyplot as plt
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
import numpy as np
from matplotlib.font_manager import FontProperties
import re

from ncatbot.core.element import MessageChain, Text, Image
from ncatbot.plugin import CompatibleEnrollment, BasePlugin
from ncatbot.utils.logger import get_log
from ncatbot.core.message import GroupMessage
from common.utils.CommonUtil import CommonUtil

# 禁用 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_log = get_log()
bot = CompatibleEnrollment

# 设置 matplotlib 字体
CommonUtil.set_matplotlib_font()


class DateTimeEncoder(json.JSONEncoder):
    """自定义 JSON 编码器，用于处理 datetime 对象"""

    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


@dataclass
class EmojiStats:
    url: str
    cache_path: str
    daily_counts: Dict[str, int] = None  # 按日期统计的使用次数
    last_used: datetime = datetime.now()

    def __post_init__(self):
        if self.daily_counts is None:
            self.daily_counts = {}

    def to_dict(self) -> dict:
        """将对象转换为字典，处理 datetime 对象"""
        return {
            "url": self.url,
            "cache_path": self.cache_path,
            "daily_counts": self.daily_counts,
            "last_used": self.last_used.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EmojiStats":
        """从字典创建对象，处理 datetime 字符串"""
        # 确保所有必需的字段都存在
        if not all(k in data for k in ["url", "cache_path"]):
            raise ValueError("缺少必需的字段")

        # 处理可选字段
        daily_counts = data.get("daily_counts", {})
        if not isinstance(daily_counts, dict):
            daily_counts = {}

        last_used = data.get("last_used")
        if isinstance(last_used, str):
            try:
                last_used = datetime.fromisoformat(last_used)
            except ValueError:
                last_used = datetime.now()
        elif not isinstance(last_used, datetime):
            last_used = datetime.now()

        return cls(
            url=data["url"],
            cache_path=data["cache_path"],
            daily_counts=daily_counts,
            last_used=last_used,
        )

    def get_count(self, days: int = None) -> int:
        """获取指定天数内的使用次数"""
        if days is None:
            return sum(self.daily_counts.values())

        end_date = date.today()
        start_date = end_date - timedelta(days=days - 1)
        return sum(
            count
            for date_str, count in self.daily_counts.items()
            if start_date <= date.fromisoformat(date_str) <= end_date
        )

    def increment_count(self, date_str: str):
        """增加指定日期的使用次数"""
        if date_str not in self.daily_counts:
            self.daily_counts[date_str] = 0
        self.daily_counts[date_str] += 1


class EmojiStatsPlugin(BasePlugin):
    name = "EmojiStats"
    version = "1.0"

    # 数据存储路径
    GROUP_DATA_FILE = os.path.join("data", "json", "emoji_group_stats.json")
    USER_DATA_FILE = os.path.join("data", "json", "emoji_user_stats.json")
    CACHE_DIR = os.path.join("data", "image", "emoji_stats")

    # 统计数据
    group_stats: Dict[int, Dict[str, EmojiStats]] = {}  # 群组表情包统计
    user_stats: Dict[int, Dict[int, Dict[str, EmojiStats]]] = {}  # 用户表情包统计
    group_count: Dict[int, Dict[str, int]] = {}  # 群组发送次数统计
    user_count: Dict[int, Dict[int, Dict[str, int]]] = {}  # 用户发送次数统计

    # 使用说明
    usage_instructions = """表情包统计指令：
1. 查看统计：表情包统计 [时间范围] [统计对象]
   
   时间范围可选：
   - 今日
   - 本周
   - 本月
   - 全部
   
   统计对象可选：
   - 群组
   - 个人
   
示例：
- 表情包统计 今日 群组
- 表情包统计 本周 个人
- 表情包统计 全部 群组
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
                                self.group_stats[group_id] = {}
                                for k2, v2 in v.items():
                                    try:
                                        self.group_stats[group_id][k2] = (
                                            EmojiStats.from_dict(v2)
                                        )
                                    except Exception as e:
                                        _log.error(f"加载群组表情包数据失败: {e}")
                                        continue
                            except Exception as e:
                                _log.error(f"加载群组数据失败: {e}")
                                continue

                        # 加载群组计数
                        self.group_count = {}
                        for k, v in data.get("group_count", {}).items():
                            try:
                                group_id = int(k)
                                self.group_count[group_id] = {}
                                for k2, v2 in v.items():
                                    try:
                                        self.group_count[group_id][k2] = int(v2)
                                    except Exception as e:
                                        _log.error(f"加载群组计数数据失败: {e}")
                                        continue
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
                                        self.user_stats[group_id][user_id] = {}
                                        for k3, v3 in v2.items():
                                            try:
                                                self.user_stats[group_id][user_id][
                                                    k3
                                                ] = EmojiStats.from_dict(v3)
                                            except Exception as e:
                                                _log.error(
                                                    f"加载用户表情包数据失败: {e}"
                                                )
                                                continue
                                    except Exception as e:
                                        _log.error(f"加载用户数据失败: {e}")
                                        continue
                            except Exception as e:
                                _log.error(f"加载群组数据失败: {e}")
                                continue

                        # 加载用户计数
                        self.user_count = {}
                        for k, v in data.get("user_count", {}).items():
                            try:
                                group_id = int(k)
                                self.user_count[group_id] = {}
                                for k2, v2 in v.items():
                                    try:
                                        user_id = int(k2)
                                        self.user_count[group_id][user_id] = {}
                                        for k3, v3 in v2.items():
                                            try:
                                                self.user_count[group_id][user_id][
                                                    k3
                                                ] = int(v3)
                                            except Exception as e:
                                                _log.error(f"加载用户计数数据失败: {e}")
                                                continue
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
                    str(k): {k2: v2.to_dict() for k2, v2 in v.items()}
                    for k, v in self.group_stats.items()
                },
                "group_count": {str(k): v for k, v in self.group_count.items()},
            }
            with open(self.GROUP_DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, cls=DateTimeEncoder)
            _log.info("群组数据保存成功")
        except Exception as e:
            _log.error(f"保存群组数据失败: {e}")
            raise

    def _save_user_data(self):
        """保存用户数据"""
        try:
            data = {
                "user_stats": {
                    str(k): {
                        str(k2): {k3: v3.to_dict() for k3, v3 in v2.items()}
                        for k2, v2 in v.items()
                    }
                    for k, v in self.user_stats.items()
                },
                "user_count": {
                    str(k): {str(k2): v2 for k2, v2 in v.items()}
                    for k, v in self.user_count.items()
                },
            }
            with open(self.USER_DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, cls=DateTimeEncoder)
            _log.info("用户数据保存成功")
        except Exception as e:
            _log.error(f"保存用户数据失败: {e}")
            raise

    def _save_data(self):
        """保存所有数据"""
        self._save_group_data()
        self._save_user_data()

    async def _download_and_cache_image(self, image_url: str) -> Optional[str]:
        """下载并缓存图片，返回缓存路径"""
        try:
            # 创建缓存目录
            os.makedirs(self.CACHE_DIR, exist_ok=True)

            # 下载图片
            response = requests.get(image_url, verify=False, timeout=30)
            if response.status_code != 200:
                _log.error(f"下载图片失败: HTTP {response.status_code}")
                return None

            # 使用图片内容的 MD5 作为文件名
            image_hash = hashlib.md5(response.content).hexdigest()
            cache_path = os.path.join(self.CACHE_DIR, f"{image_hash}.jpg")

            # 如果文件已存在，直接返回路径
            if os.path.exists(cache_path):
                _log.info(f"图片已缓存: {cache_path}")
                return cache_path

            # 保存图片
            with open(cache_path, "wb") as f:
                f.write(response.content)
            _log.info(f"图片已缓存: {cache_path}")
            return cache_path

        except Exception as e:
            _log.error(f"缓存图片失败: {e}")
            return None

    @bot.group_event()
    async def handle_message(self, input: GroupMessage) -> None:
        """处理群消息"""
        for element in input.message:
            if element.get("type") == "image":
                data = element.get("data", {})
                # 判断是否为表情包：sub_type=1 或 有 emoji_id
                if data.get("sub_type") == 1 or data.get("emoji_id"):
                    await self._process_image(input, data.get("url"))

    async def _process_image(self, input: GroupMessage, image_url: str) -> None:
        """处理图片消息"""
        group_id = input.group_id
        user_id = input.user_id
        now = datetime.now()
        today = now.date().isoformat()

        # 检查图片是否已经存在于统计中
        for emoji in self.group_stats.get(group_id, {}).values():
            if emoji.url == image_url:
                # 更新群组统计
                emoji.increment_count(today)
                emoji.last_used = now

                # 更新用户统计
                if group_id not in self.user_stats:
                    self.user_stats[group_id] = {}
                if user_id not in self.user_stats[group_id]:
                    self.user_stats[group_id][user_id] = {}
                if emoji.cache_path not in self.user_stats[group_id][user_id]:
                    # 创建新的emoji对象，确保与群组统计完全独立
                    self.user_stats[group_id][user_id][emoji.cache_path] = EmojiStats(
                        url=emoji.url, cache_path=emoji.cache_path
                    )
                    # 初始化用户统计的daily_counts
                    self.user_stats[group_id][user_id][
                        emoji.cache_path
                    ].daily_counts = {}
                # 只增加当天的使用次数
                self.user_stats[group_id][user_id][emoji.cache_path].increment_count(
                    today
                )
                self.user_stats[group_id][user_id][emoji.cache_path].last_used = now

                # 更新发送次数统计
                if group_id not in self.group_count:
                    self.group_count[group_id] = {}
                if today not in self.group_count[group_id]:
                    self.group_count[group_id][today] = 0
                self.group_count[group_id][today] += 1

                if group_id not in self.user_count:
                    self.user_count[group_id] = {}
                if user_id not in self.user_count[group_id]:
                    self.user_count[group_id][user_id] = {}
                if today not in self.user_count[group_id][user_id]:
                    self.user_count[group_id][user_id][today] = 0
                self.user_count[group_id][user_id][today] += 1

                # 保存数据
                self._save_data()
                return

        # 如果图片不存在，则下载并缓存
        cache_path = await self._download_and_cache_image(image_url)
        if not cache_path:
            return

        # 使用缓存路径作为键，而不是 URL
        cache_key = os.path.basename(cache_path)

        # 更新群组统计
        if group_id not in self.group_stats:
            self.group_stats[group_id] = {}
        if cache_key not in self.group_stats[group_id]:
            self.group_stats[group_id][cache_key] = EmojiStats(
                url=image_url, cache_path=cache_path
            )
        self.group_stats[group_id][cache_key].increment_count(today)
        self.group_stats[group_id][cache_key].last_used = now

        # 更新用户统计
        if group_id not in self.user_stats:
            self.user_stats[group_id] = {}
        if user_id not in self.user_stats[group_id]:
            self.user_stats[group_id][user_id] = {}
        if cache_key not in self.user_stats[group_id][user_id]:
            self.user_stats[group_id][user_id][cache_key] = EmojiStats(
                url=image_url, cache_path=cache_path
            )
        self.user_stats[group_id][user_id][cache_key].increment_count(today)
        self.user_stats[group_id][user_id][cache_key].last_used = now

        # 更新发送次数统计
        if group_id not in self.group_count:
            self.group_count[group_id] = {}
        if today not in self.group_count[group_id]:
            self.group_count[group_id][today] = 0
        self.group_count[group_id][today] += 1

        if group_id not in self.user_count:
            self.user_count[group_id] = {}
        if user_id not in self.user_count[group_id]:
            self.user_count[group_id][user_id] = {}
        if today not in self.user_count[group_id][user_id]:
            self.user_count[group_id][user_id][today] = 0
        self.user_count[group_id][user_id][today] += 1

        # 保存数据
        self._save_data()

    def _get_top_emojis(
        self, stats: Dict[str, EmojiStats], days: int = None
    ) -> List[EmojiStats]:
        """获取最受欢迎的表情包"""
        # 按使用次数排序
        sorted_emojis = sorted(
            [emoji for emoji in stats.values() if emoji.get_count(days) > 0],
            key=lambda x: x.get_count(days),
            reverse=True,
        )
        return sorted_emojis[:3]

    def _get_time_range_stats(self, stats: Dict[str, int], days: int) -> Dict[str, int]:
        """获取指定时间范围内的统计"""
        if days is None:
            # 如果是全部时间，直接返回所有统计数据
            return stats

        end_date = date.today()
        start_date = end_date - timedelta(days=days - 1)  # 包含今天，所以减 days-1
        return {
            date_str: count
            for date_str, count in stats.items()
            if start_date <= date.fromisoformat(date_str) <= end_date
        }

    def _number_to_counter(self, number: int) -> List[Image]:
        """将数字转换为计数器图片形式，并横向合并为一张图片，保持GIF动画效果"""
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

    def _generate_top_emojis_barh(self, emoji_stats, top_n=10, days=None):
        plt.clf()
        top_items = sorted(emoji_stats, key=lambda x: x.get_count(days), reverse=True)[
            :top_n
        ]
        counts = [e.get_count(days) for e in top_items]
        paths = [e.cache_path for e in top_items]
        names = [f"表情{i + 1}" for i in range(len(top_items))]
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
        ax.set_yticklabels(names, fontsize=15, fontweight="bold", color="#444")
        ax.set_xlabel("使用次数", fontsize=14, fontweight="bold")
        ax.set_title(
            "表情包TOP10", fontsize=18, fontweight="bold", color="#6c63ff", pad=15
        )
        ax.invert_yaxis()
        ax.set_facecolor("#f7f7fa")
        fig.patch.set_facecolor("#f7f7fa")
        ax.xaxis.grid(True, linestyle="--", color="#ccc", alpha=0.5, zorder=1)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_visible(False)
        ax.spines["bottom"].set_color("#aaa")

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

        for i, path in enumerate(paths):
            try:
                emoji = PILImage.open(path).convert("RGBA")
                emoji = circle_crop(emoji)
            except Exception:
                emoji = default_avatar()
            imagebox = OffsetImage(emoji, zoom=1)
            ab = AnnotationBbox(
                imagebox,
                (-max(counts) * 0.04, i),
                frameon=False,
                box_alignment=(0.5, 0.5),
                pad=0.1,
            )
            ax.add_artist(ab)
        for i, bar in enumerate(bars):
            ax.text(
                bar.get_width() + 1,
                bar.get_y() + bar.get_height() / 2,
                f"{counts[i]}",
                va="center",
                fontsize=15,
                fontweight="bold",
                color="#6c63ff",
                zorder=3,
            )
        plt.tight_layout(rect=[0.08, 0, 1, 1])
        path = os.path.join(
            "data",
            "image",
            "temp",
            f"top_emojis_{datetime.now().strftime('%Y%m%d%H%M%S')}.png",
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        plt.savefig(path, bbox_inches="tight")
        plt.close()
        return path

    def _generate_top_users_barh(self, user_counts, user_names, top_n=10):
        plt.clf()
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
        ax.set_xlabel("发送表情包次数", fontsize=14, fontweight="bold")
        ax.set_title(
            "表情包发送TOP10", fontsize=18, fontweight="bold", color="#6c63ff", pad=15
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

        # 头像
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

    @bot.group_event()
    async def handle_emoji_stats(self, input: GroupMessage) -> None:
        """处理表情包统计命令"""
        message = input.raw_message.strip()
        if not message.startswith("表情包统计"):
            return

        message_parts = message.split(" ")
        if len(message_parts) < 3:
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
                    rtf=MessageChain([Text("暂无群组表情包统计数据")]),
                )
                return

            # 获取时间范围内的统计数据
            total_count = sum(emoji.get_count(days) for emoji in stats.values())
            message = MessageChain([])
            message.chain.append(Text("=== 群组表情包统计 ===\n"))
            message.chain.append(Text("最近"))
            if days is None:
                message.chain.append(Text("全部时间"))
            else:
                message.chain.append(Text(str(days)))
                message.chain.append(Text("天"))
            message.chain.append(Text("使用次数:\n"))
            for img in self._number_to_counter(total_count):
                message.chain.append(img)
            message.chain.append(Text("\n\n"))
            # 2. 发表情包最多的10个用户
            user_counts = {}
            for user_id, user_stats in self.user_count.get(input.group_id, {}).items():
                user_time_stats = self._get_time_range_stats(user_stats, days)
                user_total = sum(user_time_stats.values())
                if user_total > 0:
                    user_counts[user_id] = user_total
            # 只取前十
            top_users = sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[
                :10
            ]
            user_names = {}
            for user_id, _ in top_users:
                try:
                    user_info = await self.api.get_group_member_info(
                        group_id=input.group_id, user_id=user_id, no_cache=True
                    )
                    if isinstance(user_info, dict) and user_info.get("status") == "ok":
                        user_data = user_info.get("data", {})
                        nickname = user_data.get("nickname", str(user_id))
                        user_names[user_id] = nickname
                    else:
                        continue
                except Exception:
                    continue
            if top_users:
                # 头像和昵称只传前十
                bar_path = self._generate_top_users_barh(
                    dict(top_users), user_names, top_n=10
                )
                message.chain.append(Text("发表情包最多的用户TOP10：\n"))
                message.chain.append(Image(bar_path))
                message.chain.append(Text("\n"))
            else:
                message.chain.append(Text("暂无用户表情包数据\n"))
            # 3. 最受欢迎的3个表情包
            top_emojis = self._get_top_emojis(stats, days)[:3]
            message.chain.append(Text("最受欢迎表情包TOP3:\n"))
            for i, emoji in enumerate(top_emojis, 1):
                try:
                    message.chain.append(
                        Text(f"{i}. 使用次数: {emoji.get_count(days)}次\n")
                    )
                    if os.path.exists(emoji.cache_path):
                        message.chain.append(Image(emoji.cache_path))
                    else:
                        _log.error(f"表情包图片不存在: {emoji.cache_path}")
                        message.chain.append(Text("[图片已失效]\n"))
                    message.chain.append(Text("\n"))
                except Exception as e:
                    _log.error(f"添加表情包图片失败: {e}")
                    message.chain.append(Text(f"{i}. [图片加载失败]\n"))

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
            # 获取用户最受欢迎表情包
            top_emojis = self._get_top_emojis(
                self.user_stats.get(input.group_id, {}).get(target_user_id, {}), days
            )
            # 获取用户发送次数统计
            count_stats = self._get_time_range_stats(
                self.user_count.get(input.group_id, {}).get(target_user_id, {}), days
            )
            total_count = sum(count_stats.values())

            # 添加消息元素
            message = MessageChain([])
            message.chain.append(Text("=== 个人表情包统计 ===\n"))
            message.chain.append(Text("最近"))
            if days is None:
                message.chain.append(Text("全部时间"))
            else:
                message.chain.append(Text(str(days)))
                message.chain.append(Text("天"))
            message.chain.append(Text("发送表情包数量:\n"))
            for img in self._number_to_counter(total_count):
                message.chain.append(img)
            message.chain.append(Text("\n\n"))
            message.chain.append(Text("最常使用的表情包TOP3:\n"))

            # 添加表情包信息
            for i, emoji in enumerate(top_emojis, 1):
                try:
                    message.chain.append(
                        Text(f"{i}. 使用次数: {emoji.get_count(days)}次\n")
                    )
                    if os.path.exists(emoji.cache_path):
                        message.chain.append(Image(emoji.cache_path))
                    else:
                        _log.error(f"表情包图片不存在: {emoji.cache_path}")
                        message.chain.append(Text("[图片已失效]\n"))
                    message.chain.append(Text("\n"))
                except Exception as e:
                    _log.error(f"添加表情包图片失败: {e}")
                    message.chain.append(Text(f"{i}. [图片加载失败]\n"))

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
