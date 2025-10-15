import json
import os
import threading
import warnings
from datetime import datetime
from typing import Dict, Optional, List

import matplotlib.pyplot as plt
import matplotlib
from matplotlib.font_manager import FontProperties
from matplotlib.offsetbox import AnnotationBbox, OffsetImage
from PIL import Image as PILImage, ImageDraw

matplotlib.use("Agg")  # 使用Agg后端，避免需要GUI
import io

from common.utils.CommonUtil import CommonUtil
from ncatbot.core import GroupMessage, Image, MessageChain, Text
from ncatbot.plugin_system import NcatBotPlugin, on_message
from ncatbot.utils.logger import get_log

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

_log = get_log()


class AiUsageStats:
    """AI使用统计数据结构"""

    def __init__(self):
        self.daily_counts = {}  # {date: count}
        self.daily_tokens = {}  # {date: tokens}
        self.last_used = None
        self.total_count = 0
        self.total_tokens = 0

    def increment_count(self, date_str: str, tokens: int = 0):
        """增加使用次数和token数"""
        if date_str not in self.daily_counts:
            self.daily_counts[date_str] = 0
        if date_str not in self.daily_tokens:
            self.daily_tokens[date_str] = 0

        self.daily_counts[date_str] += 1
        self.daily_tokens[date_str] += tokens
        self.total_count += 1
        self.total_tokens += tokens
        self.last_used = datetime.now()

    def get_count(self, days: int = None) -> int:
        """获取指定天数内的使用次数"""
        if days is None:
            return self.total_count

        cutoff_date = datetime.now().date()
        count = 0
        for date_str, cnt in self.daily_counts.items():
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
                if (cutoff_date - date_obj).days < days:
                    count += cnt
            except ValueError:
                continue
        return count

    def get_tokens(self, days: int = None) -> int:
        """获取指定天数内的token使用量"""
        if days is None:
            return self.total_tokens

        cutoff_date = datetime.now().date()
        tokens = 0
        for date_str, token_count in self.daily_tokens.items():
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
                if (cutoff_date - date_obj).days < days:
                    tokens += token_count
            except ValueError:
                continue
        return tokens

    def to_dict(self) -> dict:
        """转换为字典格式"""
        return {
            "daily_counts": self.daily_counts,
            "daily_tokens": self.daily_tokens,
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "total_count": self.total_count,
            "total_tokens": self.total_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AiUsageStats":
        """从字典创建实例"""
        stats = cls()
        stats.daily_counts = data.get("daily_counts", {})
        stats.daily_tokens = data.get("daily_tokens", {})
        stats.total_count = data.get("total_count", 0)
        stats.total_tokens = data.get("total_tokens", 0)

        last_used_str = data.get("last_used")
        if last_used_str:
            try:
                stats.last_used = datetime.fromisoformat(last_used_str)
            except ValueError:
                stats.last_used = None

        return stats


class DateTimeEncoder(json.JSONEncoder):
    """JSON编码器，支持datetime对象"""

    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


class AiStats(NcatBotPlugin):
    name = "AiStats"  # 插件名称
    version = "1.0"  # 插件版本

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 统计数据存储
        self.group_stats: Dict[str, AiUsageStats] = {}  # 群组统计
        self.user_stats: Dict[
            str, Dict[str, AiUsageStats]
        ] = {}  # 用户统计 {group_id: {user_id: stats}}

        # 数据文件路径
        self.GROUP_DATA_FILE = "data/json/ai_group_stats.json"
        self.USER_DATA_FILE = "data/json/ai_user_stats.json"

        # 保存锁
        self._save_lock = threading.Lock()

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
            _log.error(f"[AiStats] 保存数据失败: {e}")
            return False

    def _load_data(self):
        """加载数据"""
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
                        _log.info(
                            f"[AiStats] 从文件读取到群组数据: {list(group_stats_data.keys())}"
                        )

                        for k, v in group_stats_data.items():
                            try:
                                group_id = k  # 直接使用字符串，不转换
                                _log.info(f"[AiStats] 正在加载群组 {group_id} 的数据")
                                new_group_stats[group_id] = AiUsageStats.from_dict(v)
                                _log.info(f"[AiStats] 成功加载群组 {group_id} 的数据")
                            except Exception as e:
                                _log.error(f"[AiStats] 加载群组 {k} 数据失败: {e}")
                                _log.error(f"[AiStats] 群组 {k} 的原始数据: {v}")
                                continue

                        # 合并数据而不是直接替换，避免覆盖现有数据
                        for group_id, stats in new_group_stats.items():
                            self.group_stats[group_id] = stats

                        _log.info(
                            f"[AiStats] 成功加载群组数据: {list(self.group_stats.keys())}"
                        )

                except Exception as e:
                    _log.error(f"[AiStats] 加载群组数据失败: {e}")
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
                                            AiUsageStats.from_dict(v2)
                                        )
                                    except Exception as e:
                                        _log.error(f"[AiStats] 加载用户数据失败: {e}")
                                        continue
                            except Exception as e:
                                _log.error(f"[AiStats] 加载群组数据失败: {e}")
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
                    _log.error(f"[AiStats] 加载用户数据失败: {e}")
                    # 不要清空内存中的数据，保持现有数据

        except Exception as e:
            _log.error(f"[AiStats] 加载数据失败: {e}")
            # 不要清空内存中的数据，保持现有数据

    def _save_group_data(self, group_id: str = None):
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
                            _log.error(f"[AiStats] 读取现有群组数据失败: {e}")
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
            _log.error(f"[AiStats] 保存群组数据失败: {e}")
            return False

    def _save_user_data(self, group_id: str = None):
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
                            _log.error(f"[AiStats] 读取现有用户数据失败: {e}")
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
            _log.error(f"[AiStats] 保存用户数据失败: {e}")
            return False

    def _save_data(self, group_id: str = None):
        """保存数据 - 使用更安全的保存策略"""
        with self._save_lock:
            try:
                if group_id is not None:
                    # 保存指定群组的数据
                    # 保存群组数据
                    group_success = self._save_group_data(group_id)
                    # 保存用户数据
                    user_success = self._save_user_data(group_id)

                    if group_success and user_success:
                        pass  # 数据保存成功
                    else:
                        _log.error(f"[AiStats] 群组 {group_id} 数据保存失败")
                else:
                    # 保存所有数据
                    # 保存群组数据
                    group_success = self._save_group_data()
                    # 保存用户数据
                    user_success = self._save_user_data()

                    if group_success and user_success:
                        pass  # 所有数据保存成功
                    else:
                        _log.error("[AiStats] 所有数据保存失败")
            except Exception as e:
                _log.error(f"[AiStats] 保存数据时发生异常: {e}")

    def record_ai_usage(
        self, group_id: str, user_id: str, tokens: int = 0, trigger_type: str = "active"
    ):
        """记录AI使用情况"""
        # 确保统计数据字典已初始化
        if self.group_stats is None:
            self.group_stats = {}
        if self.user_stats is None:
            self.user_stats = {}

        now = datetime.now()
        today = now.date().isoformat()

        # 更新群组统计
        if group_id not in self.group_stats:
            self.group_stats[group_id] = AiUsageStats()

        self.group_stats[group_id].increment_count(today, tokens)

        # 更新用户统计
        if group_id not in self.user_stats:
            self.user_stats[group_id] = {}
        if user_id not in self.user_stats[group_id]:
            self.user_stats[group_id][user_id] = AiUsageStats()

        self.user_stats[group_id][user_id].increment_count(today, tokens)

        # 保存数据
        self._save_data(group_id)

    def _get_time_range_stats(self, stats: AiUsageStats, days: int) -> Dict[str, int]:
        """获取指定时间范围内的统计"""
        if days is None:
            # 如果是全部时间，直接返回所有统计数据
            return stats.daily_counts.copy()

        cutoff_date = datetime.now().date()
        result = {}
        for date_str, count in stats.daily_counts.items():
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
                if (cutoff_date - date_obj).days < days:
                    result[date_str] = count
            except ValueError:
                continue
        return result

    def _get_time_range_tokens(self, stats: AiUsageStats, days: int) -> Dict[str, int]:
        """获取指定时间范围内的token统计"""
        if days is None:
            # 如果是全部时间，直接返回所有统计数据
            return stats.daily_tokens.copy()

        cutoff_date = datetime.now().date()
        result = {}
        for date_str, tokens in stats.daily_tokens.items():
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
                if (cutoff_date - date_obj).days < days:
                    result[date_str] = tokens
            except ValueError:
                continue
        return result

    def _generate_time_distribution_plot(
        self, stats: AiUsageStats, days: int
    ) -> Optional[str]:
        """生成时间分布图"""
        try:
            plt.clf()  # 清除当前图形
            plt.figure(figsize=(12, 6))

            if days == 1:  # 今日
                # 获取24小时数据
                hours = list(range(24))
                counts = [
                    stats.daily_counts.get(datetime.now().date().isoformat(), 0)
                    for hour in hours
                ]

                plt.bar(hours, counts, alpha=0.6, color="skyblue")
                plt.plot(hours, counts, "r-", linewidth=2)

                plt.xlabel("小时", fontsize=12)
                plt.ylabel("AI调用次数", fontsize=12)
                plt.title("今日AI使用时间分布", fontsize=14)
                plt.xticks(hours)
                plt.grid(True, linestyle="--", alpha=0.7)

            elif days == 7:  # 本周
                # 获取本周数据
                from datetime import date, timedelta

                today = date.today()
                start_date = today - timedelta(days=today.weekday())
                dates = [(start_date + timedelta(days=i)) for i in range(7)]
                counts = [stats.daily_counts.get(date.isoformat(), 0) for date in dates]

                plt.bar(range(7), counts, alpha=0.6, color="skyblue")
                plt.plot(range(7), counts, "r-", linewidth=2)

                plt.xlabel("星期", fontsize=12)
                plt.ylabel("AI调用次数", fontsize=12)
                plt.title("本周AI使用分布", fontsize=14)
                plt.xticks(
                    range(7), ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
                )
                plt.grid(True, linestyle="--", alpha=0.7)

            elif days == 30:  # 本月
                # 获取本月数据
                from datetime import date, timedelta

                today = date.today()
                start_date = date(today.year, today.month, 1)
                dates = [(start_date + timedelta(days=i)) for i in range(today.day)]
                counts = [stats.daily_counts.get(date.isoformat(), 0) for date in dates]

                plt.bar(range(len(dates)), counts, alpha=0.6, color="skyblue")
                plt.plot(range(len(dates)), counts, "r-", linewidth=2)

                plt.xlabel("日期", fontsize=12)
                plt.ylabel("AI调用次数", fontsize=12)
                plt.title("本月AI使用分布", fontsize=14)
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
                from datetime import date

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
                plt.ylabel("AI调用次数", fontsize=12)
                plt.title("AI使用月度分布", fontsize=14)
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
                f"ai_time_dist_{datetime.now().strftime('%Y%m%d%H%M%S')}.png",
            )
            os.makedirs(os.path.dirname(temp_path), exist_ok=True)

            with open(temp_path, "wb") as f:
                f.write(buf.getvalue())

            plt.close()
            return temp_path
        except Exception as e:
            _log.error(f"[AiStats] 生成时间分布图失败: {e}")
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
        ax.set_xlabel("AI调用次数", fontsize=14, fontweight="bold")
        ax.set_title(
            "AI使用TOP10", fontsize=18, fontweight="bold", color="#6c63ff", pad=15
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
        avatar_x = -max(counts) * 0.12 - 1 if counts else -2
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
            left, bottom, right, top = map(int, bbox.bounds)
            bar_pixel_boxes.append((left, bottom, right, top))

        plt.tight_layout(rect=[0.08, 0, 1, 1])
        plt.subplots_adjust(left=0.18)
        path = os.path.join(
            "data",
            "image",
            "temp",
            f"ai_top_users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png",
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
        """分割文本和emoji"""
        import re

        # 匹配emoji的正则表达式
        emoji_pattern = re.compile(
            "["
            "\U0001f600-\U0001f64f"  # emoticons
            "\U0001f300-\U0001f5ff"  # symbols & pictographs
            "\U0001f680-\U0001f6ff"  # transport & map symbols
            "\U0001f1e0-\U0001f1ff"  # flags (iOS)
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

    @on_message
    async def handle_ai_stats(plugin_instance, input: GroupMessage) -> None:
        """处理AI统计命令"""
        message = input.raw_message.strip()
        if not message.startswith("ai统计"):
            return

        # 分割命令，处理多个空格的情况
        message_parts = [part for part in message.split() if part]
        if len(message_parts) < 3:
            await input.reply("命令格式错误，请使用：ai统计 [时间范围] [统计对象]")
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

        # 使用传入的插件实例
        await plugin_instance._show_stats(input, days, target, target_user_id)

    async def _show_stats(
        self, input: GroupMessage, days: int, target: str, target_user_id: int
    ) -> None:
        """显示统计数据"""
        # 发送初始响应
        await self.api.post_group_msg(
            input.group_id, rtf=MessageChain([Text("正在生成AI统计图表，请稍候...")])
        )

        if target == "群组":
            group_id = str(input.group_id)
            _log.info(f"[AiStats] 查找群组 {group_id} 的统计数据")
            _log.info(f"[AiStats] 当前加载的群组: {list(self.group_stats.keys())}")

            # 如果当前没有该群组的数据，尝试重新加载数据
            if group_id not in self.group_stats:
                _log.info(f"[AiStats] 群组 {group_id} 不在内存中，尝试重新加载数据")
                self._load_data()
                _log.info(
                    f"[AiStats] 重新加载后的群组: {list(self.group_stats.keys())}"
                )

            stats = self.group_stats.get(group_id)
            if not stats:
                _log.warning(f"[AiStats] 群组 {group_id} 没有统计数据")
                await self.api.post_group_msg(
                    input.group_id,
                    rtf=MessageChain([Text("暂无群组AI统计数据")]),
                )
                return

            # 获取时间范围内的统计数据
            time_range_stats = self._get_time_range_stats(stats, days)
            total_count = sum(time_range_stats.values())
            total_tokens = stats.get_tokens(days)

            message_elements = []
            message_elements.append(Text("=== 群组AI统计 ===\n"))
            message_elements.append(Text("最近"))
            if days is None:
                message_elements.append(Text("全部时间"))
            else:
                message_elements.append(Text(str(days)))
                message_elements.append(Text("天"))
            message_elements.append(Text("AI调用次数:\n"))
            for img in self._number_to_counter(total_count):
                message_elements.append(img)
            message_elements.append(Text(f"\n总Token使用量: {total_tokens:,}\n\n"))

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
            if user_counts:
                bar_path = self._generate_top_users_barh(
                    user_counts, user_names, top_n=10
                )
                message_elements.append(Text("AI使用最多的用户TOP10：\n"))
                message_elements.append(Image(bar_path))
                message_elements.append(Text("\n"))
            else:
                message_elements.append(Text("暂无用户AI使用数据\n"))

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
                await input.reply("暂无个人AI使用统计")
                return

            # 获取用户AI使用次数统计
            count_stats = self._get_time_range_stats(user_stat, days)
            token_stats = self._get_time_range_tokens(user_stat, days)
            total_count = sum(count_stats.values())
            total_tokens = sum(token_stats.values())

            # 添加消息元素
            message_elements = []
            message_elements.append(Text("=== 个人AI统计 ===\n"))
            message_elements.append(Text("最近"))
            if days is None:
                message_elements.append(Text("全部时间"))
            else:
                message_elements.append(Text(str(days)))
                message_elements.append(Text("天"))
            message_elements.append(Text(f"AI调用次数: {total_count}\n"))
            message_elements.append(Text(f"Token使用量: {total_tokens:,}\n"))

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
