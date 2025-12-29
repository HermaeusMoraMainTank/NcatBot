import os
import json
import hashlib
import requests
import urllib3
import threading
import time
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass
from PIL import Image as PILImage, ImageDraw
import matplotlib.pyplot as plt
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from matplotlib.font_manager import FontProperties
import re

from ncatbot.core import MessageArray, Text, Image, GroupMessage
from ncatbot.plugin_system import NcatBotPlugin, on_message
from ncatbot.utils.logger import get_log
from common.utils.CommonUtil import CommonUtil
from common.constants.HMMT import HMMT

# 禁用 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_log = get_log()

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


class EmojiStatsPlugin(NcatBotPlugin):
    name = "EmojiStats"
    version = "1.0"

    # 数据存储路径
    GROUP_DATA_FILE = os.path.join("data", "json", "emoji_group_stats.json")
    USER_DATA_FILE = os.path.join("data", "json", "emoji_user_stats.json")
    CACHE_DIR = os.path.join("data", "image", "emoji_stats")

    # 统计数据
    group_stats: Dict[int, Dict[str, EmojiStats]] = None  # 群组表情包统计
    user_stats: Dict[int, Dict[int, Dict[str, EmojiStats]]] = None  # 用户表情包统计
    group_count: Dict[int, Dict[str, int]] = None  # 群组发送次数统计
    user_count: Dict[int, Dict[int, Dict[str, int]]] = None  # 用户发送次数统计

    # 简化的数据保存控制
    _save_lock = threading.Lock()

    # 清理策略配置
    # 保留最近N天内有使用记录的图片（覆盖"本月"统计，并给一些缓冲）
    CLEANUP_KEEP_DAYS = 60
    # 保留使用次数排名前N的图片（因为统计显示TOP10，保留更多一些作为缓冲）
    CLEANUP_KEEP_TOP_COUNT = 30

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
        # 开始加载插件
        # 初始化实例变量
        if self.group_stats is None:
            self.group_stats = {}
        if self.user_stats is None:
            self.user_stats = {}
        if self.group_count is None:
            self.group_count = {}
        if self.user_count is None:
            self.user_count = {}
        # 不要清空内存中的数据，保持现有数据
        self._load_data()
        # 执行清理任务，删除不在统计数据中引用的图片
        self._cleanup_unused_images()
        # 启动定时清理任务
        self._start_cleanup_task()
        # 添加每日18点定时任务
        self.add_scheduled_task(
            self._send_daily_stats_to_all_groups,
            "daily_emoji_stats",
            "18:00",
        )
        _log.info("[EmojiStats] 每日18点表情包统计定时任务已注册")
        # 插件加载完成

    def _reinit_(self):
        """插件重新加载时同步处理钩子 - 保护内存中的数据"""
        # 保存当前内存中的数据
        temp_group_stats = self.group_stats.copy() if self.group_stats else {}
        temp_user_stats = self.user_stats.copy() if self.user_stats else {}
        temp_group_count = self.group_count.copy() if self.group_count else {}
        temp_user_count = self.user_count.copy() if self.user_count else {}

        # 重新初始化
        if self.group_stats is None:
            self.group_stats = {}
        if self.user_stats is None:
            self.user_stats = {}
        if self.group_count is None:
            self.group_count = {}
        if self.user_count is None:
            self.user_count = {}

        # 恢复数据
        self.group_stats.update(temp_group_stats)
        self.user_stats.update(temp_user_stats)
        self.group_count.update(temp_group_count)
        self.user_count.update(temp_user_count)

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
            _log.error(f"[EmojiStats] 保存数据失败: {e}")
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
                                new_group_stats[group_id] = {}

                                for k2, v2 in v.items():
                                    try:
                                        new_group_stats[group_id][k2] = (
                                            EmojiStats.from_dict(v2)
                                        )
                                    except Exception as e:
                                        _log.error(
                                            f"[EmojiStats] 加载群组表情包数据失败: {e}"
                                        )
                                        continue
                            except Exception as e:
                                _log.error(f"[EmojiStats] 加载群组数据失败: {e}")
                                continue

                        # 合并数据而不是直接替换，避免覆盖现有数据
                        for group_id, emojis in new_group_stats.items():
                            self.group_stats[group_id] = emojis

                        # 合并群组计数数据而不是直接替换
                        group_count_data = data.get("group_count", {})

                        for k, v in group_count_data.items():
                            try:
                                group_id = k  # 直接使用字符串，不转换
                                if group_id not in self.group_count:
                                    self.group_count[group_id] = {}
                                for k2, v2 in v.items():
                                    try:
                                        self.group_count[group_id][k2] = int(v2)
                                    except Exception as e:
                                        _log.error(
                                            f"[EmojiStats] 加载群组计数数据失败: {e}"
                                        )
                                        continue
                            except Exception as e:
                                _log.error(f"[EmojiStats] 加载群组数据失败: {e}")
                                continue

                except Exception as e:
                    _log.error(f"[EmojiStats] 加载群组数据失败: {e}")
                    # 不要清空内存中的数据，保持现有数据
            else:
                _log.warning(f"[EmojiStats] 群组数据文件不存在: {self.GROUP_DATA_FILE}")

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
                                        new_user_stats[group_id][user_id] = {}

                                        for k3, v3 in v2.items():
                                            try:
                                                new_user_stats[group_id][user_id][
                                                    k3
                                                ] = EmojiStats.from_dict(v3)
                                            except Exception as e:
                                                _log.error(
                                                    f"[EmojiStats] 加载用户表情包数据失败: {e}"
                                                )
                                                continue
                                    except Exception as e:
                                        _log.error(
                                            f"[EmojiStats] 加载用户数据失败: {e}"
                                        )
                                        continue
                            except Exception as e:
                                _log.error(f"[EmojiStats] 加载群组数据失败: {e}")
                                continue

                        # 合并数据而不是直接替换，避免覆盖现有数据
                        for group_id, users in new_user_stats.items():
                            if group_id not in self.user_stats:
                                self.user_stats[group_id] = {}
                            for user_id, emojis in users.items():
                                if user_id not in self.user_stats[group_id]:
                                    self.user_stats[group_id][user_id] = {}
                                for emoji_url, stats in emojis.items():
                                    self.user_stats[group_id][user_id][emoji_url] = (
                                        stats
                                    )

                        # 合并用户计数数据而不是直接替换
                        user_count_data = data.get("user_count", {})

                        for k, v in user_count_data.items():
                            try:
                                group_id = k  # 直接使用字符串，不转换
                                if group_id not in self.user_count:
                                    self.user_count[group_id] = {}
                                for k2, v2 in v.items():
                                    try:
                                        user_id = k2  # 直接使用字符串，不转换
                                        if user_id not in self.user_count[group_id]:
                                            self.user_count[group_id][user_id] = {}
                                        for k3, v3 in v2.items():
                                            try:
                                                self.user_count[group_id][user_id][
                                                    k3
                                                ] = int(v3)
                                            except Exception as e:
                                                _log.error(
                                                    f"[EmojiStats] 加载用户计数数据失败: {e}"
                                                )
                                                continue
                                    except Exception as e:
                                        _log.error(
                                            f"[EmojiStats] 加载用户数据失败: {e}"
                                        )
                                        continue
                            except Exception as e:
                                _log.error(f"[EmojiStats] 加载群组数据失败: {e}")
                                continue

                        total_users = sum(
                            len(users) for users in self.user_stats.values()
                        )
                except Exception as e:
                    _log.error(f"[EmojiStats] 加载用户数据失败: {e}")
                    # 不要清空内存中的数据，保持现有数据
            else:
                _log.warning(f"[EmojiStats] 用户数据文件不存在: {self.USER_DATA_FILE}")

        except Exception as e:
            _log.error(f"[EmojiStats] 加载数据失败: {e}")
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
                            _log.error(f"[EmojiStats] 读取现有群组数据失败: {e}")
                            existing_data = {"group_stats": {}, "group_count": {}}

                    # 确保有必要的字段
                    if "group_stats" not in existing_data:
                        existing_data["group_stats"] = {}
                    if "group_count" not in existing_data:
                        existing_data["group_count"] = {}

                    # 只更新指定群聊的数据，不修改其他群组的数据
                    # 直接替换当前群组的数据，因为内存中的数据已经是累计数据
                    existing_data["group_stats"][group_id] = {
                        k2: v2.to_dict()
                        for k2, v2 in self.group_stats[group_id].items()
                    }
                    existing_data["group_count"][group_id] = self.group_count.get(
                        group_id, {}
                    )
                    return self._save_data_to_file(existing_data, self.GROUP_DATA_FILE)
            else:
                # 保存所有群聊数据
                data = {
                    "group_stats": {
                        k: {k2: v2.to_dict() for k2, v2 in v.items()}
                        for k, v in self.group_stats.items()
                    },
                    "group_count": {k: v for k, v in self.group_count.items()},
                }
                return self._save_data_to_file(data, self.GROUP_DATA_FILE)
        except Exception as e:
            _log.error(f"[EmojiStats] 保存群组数据失败: {e}")
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
                            _log.error(f"[EmojiStats] 读取现有用户数据失败: {e}")
                            existing_data = {"user_stats": {}, "user_count": {}}

                    # 确保有必要的字段
                    if "user_stats" not in existing_data:
                        existing_data["user_stats"] = {}
                    if "user_count" not in existing_data:
                        existing_data["user_count"] = {}

                    # 只更新指定群聊的用户数据，不修改其他群组的数据
                    # 直接替换当前群组的用户数据，因为内存中的数据已经是累计数据
                    existing_data["user_stats"][group_id] = {
                        k2: {k3: v3.to_dict() for k3, v3 in v2.items()}
                        for k2, v2 in self.user_stats[group_id].items()
                    }
                    existing_data["user_count"][group_id] = {
                        k2: v2 for k2, v2 in self.user_count.get(group_id, {}).items()
                    }
                    return self._save_data_to_file(existing_data, self.USER_DATA_FILE)
            else:
                # 保存所有用户数据
                data = {
                    "user_stats": {
                        k: {
                            k2: {k3: v3.to_dict() for k3, v3 in v2.items()}
                            for k2, v2 in v.items()
                        }
                        for k, v in self.user_stats.items()
                    },
                    "user_count": {
                        k: {k2: v2 for k2, v2 in v.items()}
                        for k, v in self.user_count.items()
                    },
                }
                return self._save_data_to_file(data, self.USER_DATA_FILE)
        except Exception as e:
            _log.error(f"[EmojiStats] 保存用户数据失败: {e}")
            return False

    def _save_data(self, group_id: int = None):
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
                        _log.error(f"[EmojiStats] 群组 {group_id} 数据保存失败")
                else:
                    # 保存所有数据
                    # 保存群组数据
                    group_success = self._save_group_data()
                    # 保存用户数据
                    user_success = self._save_user_data()

                    if group_success and user_success:
                        pass  # 所有数据保存成功
                    else:
                        _log.error(f"[EmojiStats] 所有数据保存失败")
            except Exception as e:
                _log.error(f"[EmojiStats] 保存数据时发生异常: {e}")

    def _start_cleanup_task(self):
        """启动定时清理任务"""
        import threading

        def cleanup_task():
            """在单独线程中运行的清理任务"""
            while True:
                try:
                    time.sleep(3600)  # 每小时执行一次清理
                    self._cleanup_unused_images()
                except Exception as e:
                    _log.error(f"[EmojiStats] 清理任务异常: {e}")

        # 在单独线程中启动清理任务
        cleanup_thread = threading.Thread(target=cleanup_task, daemon=True)
        cleanup_thread.start()

    def _cleanup_unused_images(self):
        """清理不在统计数据中引用的图片文件

        清理策略：
        1. 保留最近N天内有使用记录的图片（覆盖"本月"统计，并给一些缓冲）
        2. 同时保留使用次数排名前N的图片（因为统计显示TOP10，保留更多一些作为缓冲）
        这样可以避免删除频率低但最近有使用的图片，以及删除后突然变热门的图片
        """
        try:
            # 确保缓存目录存在
            if not os.path.exists(self.CACHE_DIR):
                return

            # 收集所有需要保留的图片文件名
            keep_filenames = set()

            # 策略1：收集最近N天内有使用记录的图片
            cutoff_date = date.today() - timedelta(days=self.CLEANUP_KEEP_DAYS)

            # 从群组统计中收集最近使用的图片
            for group_id, emojis in self.group_stats.items():
                for emoji_key, emoji_stats in emojis.items():
                    # 检查最近是否有使用记录
                    has_recent_use = False
                    if emoji_stats.daily_counts:
                        for date_str, count in emoji_stats.daily_counts.items():
                            try:
                                use_date = date.fromisoformat(date_str)
                                if use_date >= cutoff_date and count > 0:
                                    has_recent_use = True
                                    break
                            except (ValueError, TypeError):
                                continue

                    # 如果最近有使用，保留
                    if has_recent_use:
                        if emoji_key:
                            keep_filenames.add(emoji_key)
                        if (
                            hasattr(emoji_stats, "cache_path")
                            and emoji_stats.cache_path
                        ):
                            filename = os.path.basename(emoji_stats.cache_path)
                            if filename:
                                keep_filenames.add(filename)

            # 从用户统计中收集最近使用的图片
            for group_id, users in self.user_stats.items():
                for user_id, emojis in users.items():
                    for emoji_key, emoji_stats in emojis.items():
                        # 检查最近是否有使用记录
                        has_recent_use = False
                        if emoji_stats.daily_counts:
                            for date_str, count in emoji_stats.daily_counts.items():
                                try:
                                    use_date = date.fromisoformat(date_str)
                                    if use_date >= cutoff_date and count > 0:
                                        has_recent_use = True
                                        break
                                except (ValueError, TypeError):
                                    continue

                        # 如果最近有使用，保留
                        if has_recent_use:
                            if emoji_key:
                                keep_filenames.add(emoji_key)
                            if (
                                hasattr(emoji_stats, "cache_path")
                                and emoji_stats.cache_path
                            ):
                                filename = os.path.basename(emoji_stats.cache_path)
                                if filename:
                                    keep_filenames.add(filename)

            # 策略2：收集使用次数排名前N的图片（按群组统计）
            # 收集所有表情包及其总使用次数
            all_emojis_with_count = []
            for group_id, emojis in self.group_stats.items():
                for emoji_key, emoji_stats in emojis.items():
                    total_count = emoji_stats.get_count()  # 获取总使用次数
                    if total_count > 0:
                        all_emojis_with_count.append(
                            (emoji_key, emoji_stats, total_count)
                        )

            # 按使用次数排序，取前N个
            all_emojis_with_count.sort(key=lambda x: x[2], reverse=True)
            top_emojis = all_emojis_with_count[: self.CLEANUP_KEEP_TOP_COUNT]

            # 将排名前N的图片加入保留列表
            for emoji_key, emoji_stats, _ in top_emojis:
                if emoji_key:
                    keep_filenames.add(emoji_key)
                if hasattr(emoji_stats, "cache_path") and emoji_stats.cache_path:
                    filename = os.path.basename(emoji_stats.cache_path)
                    if filename:
                        keep_filenames.add(filename)

            # 扫描缓存目录中的所有图片文件
            deleted_count = 0
            total_size_freed = 0

            if os.path.exists(self.CACHE_DIR):
                for filename in os.listdir(self.CACHE_DIR):
                    file_path = os.path.join(self.CACHE_DIR, filename)
                    if not os.path.isfile(file_path):
                        continue

                    # 只处理图片文件（.jpg, .png, .gif等）
                    if not filename.lower().endswith(
                        (".jpg", ".jpeg", ".png", ".gif", ".webp")
                    ):
                        continue

                    # 如果文件不在保留列表中，删除它
                    if filename not in keep_filenames:
                        try:
                            file_size = os.path.getsize(file_path)
                            os.remove(file_path)
                            deleted_count += 1
                            total_size_freed += file_size
                            _log.debug(f"[EmojiStats] 删除未引用的图片: {file_path}")
                        except Exception as e:
                            _log.error(f"[EmojiStats] 删除图片失败 {file_path}: {e}")

            if deleted_count > 0:
                size_mb = total_size_freed / (1024 * 1024)
                _log.info(
                    f"[EmojiStats] 清理了 {deleted_count} 张未引用的图片，释放了 {size_mb:.2f} MB 空间"
                )

        except Exception as e:
            _log.error(f"[EmojiStats] 清理图片时发生异常: {e}")

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
                # 图片已缓存
                return cache_path

            # 保存图片
            with open(cache_path, "wb") as f:
                f.write(response.content)
            # 图片已缓存
            return cache_path

        except Exception as e:
            _log.error(f"缓存图片失败: {e}")
            return None

    @on_message
    async def handle_message(self, input: GroupMessage) -> None:
        """处理群消息"""
        for element in input.message:
            # 检查是否为图片消息
            if isinstance(element, Image):
                # 检查是否为表情包：summary包含"表情"或"动画表情"
                summary = getattr(element, "summary", "")
                if "表情" in summary or "动画表情" in summary:
                    await self._process_image(input, element.url)

    async def _process_image(self, input: GroupMessage, image_url: str) -> None:
        """处理图片消息"""
        # 确保 group_id 和 user_id 都是字符串类型
        group_id = str(input.group_id)
        user_id = str(input.sender.user_id)
        now = datetime.now()
        today = now.date().isoformat()

        # 处理表情包消息

        # 检查图片是否已经存在于统计中
        for emoji in self.group_stats.get(group_id, {}).values():
            if emoji.url == image_url:
                # 找到现有表情包，更新统计
                # 更新群组统计
                emoji.increment_count(today)
                emoji.last_used = now

                # 更新用户统计
                if group_id not in self.user_stats:
                    self.user_stats[group_id] = {}
                    # 创建新群组用户统计
                if user_id not in self.user_stats[group_id]:
                    self.user_stats[group_id][user_id] = {}
                    # 创建新用户统计
                if emoji.cache_path not in self.user_stats[group_id][user_id]:
                    # 创建新的emoji对象，确保与群组统计完全独立
                    self.user_stats[group_id][user_id][emoji.cache_path] = EmojiStats(
                        url=emoji.url, cache_path=emoji.cache_path
                    )
                    # 初始化用户统计的daily_counts
                    self.user_stats[group_id][user_id][
                        emoji.cache_path
                    ].daily_counts = {}
                    # 创建新表情包统计
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

                # 数据更新完成，开始保存数据
                # 保存数据
                self._save_data(group_id)
                return

        # 未找到现有表情包，开始下载新表情包
        # 如果图片不存在，则下载并缓存
        cache_path = await self._download_and_cache_image(image_url)
        if not cache_path:
            _log.error(f"[EmojiStats] 表情包下载失败")
            return

        # 使用缓存路径作为键，而不是 URL
        cache_key = os.path.basename(cache_path)
        # 表情包下载成功

        # 更新群组统计
        if group_id not in self.group_stats:
            self.group_stats[group_id] = {}
        if cache_key not in self.group_stats[group_id]:
            self.group_stats[group_id][cache_key] = EmojiStats(
                url=image_url, cache_path=cache_path
            )
            # 创建新表情包统计
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
            # 创建新用户表情包统计
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

        # 新表情包数据更新完成，开始保存数据
        # 保存数据
        self._save_data(group_id)

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

    def _generate_ranking_card(
        self,
        user_counts: Dict[str, int],
        user_names: Dict[str, str],
        total_messages: int,
        total_users: int,
        days: int = None,
        title: str = "表情包排行",
        subtitle: str = "活跃用户 TOP 10",
        count_label: str = "次",
        total_label: str = "总使用次数",
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
            count_label: 计数标签（如"次"）
            total_label: 总计标签
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
        draw.text(
            (date_x, date_y), date_text, fill=(180, 180, 180), font=subtitle_font
        )

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
                    [avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size],
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
                    [(card_x + padding, line_y), (card_x + card_inner_width - padding, line_y)],
                    fill=(240, 240, 245),
                    width=1,
                )

        # 绘制底部统计区域
        footer_y = items_start_y + items_count * item_height + 10

        # 绘制分隔线
        draw.line(
            [(card_x + padding, footer_y), (card_x + card_inner_width - padding, footer_y)],
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
        draw.pieslice(
            [x2 - 2 * radius, y1, x2, y1 + 2 * radius], 270, 360, fill=fill
        )
        draw.pieslice(
            [x1, y2 - 2 * radius, x1 + 2 * radius, y2], 90, 180, fill=fill
        )
        draw.pieslice([x2 - 2 * radius, y2 - 2 * radius, x2, y2], 0, 90, fill=fill)

    def _circle_crop(self, img, size=44):
        """将图片裁剪为圆形"""
        img = img.resize((size, size), PILImage.LANCZOS).convert("RGBA")
        mask = PILImage.new("L", (size, size), 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((0, 0, size, size), fill=255)
        img.putalpha(mask)
        return img

    async def _send_daily_stats_to_all_groups(self):
        """每日定时发送表情包统计到所有群组"""
        _log.info("[EmojiStats] 开始执行每日表情包统计定时任务")

        # 遍历所有有统计数据的群组
        for group_id in list(self.group_stats.keys()):
            try:
                # 检查是否在黑名单中
                if str(group_id) in HMMT.BLACKLIST_GROUPS:
                    _log.info(f"[EmojiStats] 跳过黑名单群组 {group_id}")
                    continue
                
                await self._send_group_daily_stats(int(group_id))
            except Exception as e:
                _log.error(f"[EmojiStats] 发送群组 {group_id} 每日统计失败: {e}")

        _log.info("[EmojiStats] 每日表情包统计定时任务执行完成")

    async def _send_group_daily_stats(self, group_id: int):
        """发送单个群组的每日表情包统计"""
        group_id_str = str(group_id)
        stats = self.group_stats.get(group_id_str)
        if not stats:
            return

        # 获取今日的统计数据
        today = date.today().isoformat()
        total_count = sum(
            emoji.daily_counts.get(today, 0) for emoji in stats.values()
        )

        # 如果今天没有表情包记录，跳过
        if total_count == 0:
            _log.info(f"[EmojiStats] 群组 {group_id} 今日无表情包记录，跳过发送")
            return

        # 构建消息元素
        message = MessageArray()
        message.add_text("=== 今日表情包统计 ===\n")
        message.add_text("今日使用次数:\n")
        for img in self._number_to_counter(total_count):
            message.add_by_segment(img)
        message.add_text("\n\n")

        # 获取用户统计（今日）
        user_counts = {}
        user_names = {}
        for user_id, user_stats in self.user_count.get(group_id_str, {}).items():
            user_today_count = user_stats.get(today, 0)
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
            _log.error(f"[EmojiStats] 获取群成员列表失败: {e}")

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
                title="表情包排行",
                subtitle="活跃用户 TOP 10",
                count_label="次",
                total_label="总使用次数",
                users_label="总参与人数",
                top_n=10,
            )
            if card_path:
                message.add_image(card_path).add_text("\n")
        else:
            message.add_text("暂无用户表情包数据\n")

        # 获取今日最受欢迎的3个表情包
        top_emojis = []
        for emoji in stats.values():
            today_count = emoji.daily_counts.get(today, 0)
            if today_count > 0:
                top_emojis.append((emoji, today_count))
        top_emojis.sort(key=lambda x: x[1], reverse=True)
        top_emojis = top_emojis[:3]

        if top_emojis:
            message.add_text("今日最受欢迎表情包TOP3:\n")
            for i, (emoji, count) in enumerate(top_emojis, 1):
                try:
                    message.add_text(f"{i}. 使用次数: {count}次\n")
                    if os.path.exists(emoji.cache_path):
                        message.add_image(emoji.cache_path)
                    else:
                        message.add_text("[图片已失效]\n")
                    message.add_text("\n")
                except Exception as e:
                    _log.error(f"[EmojiStats] 添加表情包图片失败: {e}")
                    message.add_text(f"{i}. [图片加载失败]\n")

        # 发送消息
        try:
            await self.api.post_group_msg(group_id, rtf=message)
            _log.info(f"[EmojiStats] 成功发送群组 {group_id} 的每日统计")
        except Exception as e:
            _log.error(f"[EmojiStats] 发送群组 {group_id} 消息失败: {e}")

    @on_message
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
        # 显示统计数据

        # 发送初始响应
        await self.api.post_group_msg(
            input.group_id, rtf=MessageArray([Text("正在生成统计图表，请稍候...")])
        )

        if target == "群组":
            group_id = str(input.group_id)
            stats = self.group_stats.get(group_id)
            if not stats:
                _log.warning(f"[EmojiStats] 群组 {group_id} 没有统计数据")
                await self.api.post_group_msg(
                    input.group_id,
                    rtf=MessageArray([Text("暂无群组表情包统计数据")]),
                )
                return

            # 获取时间范围内的统计数据
            total_count = sum(emoji.get_count(days) for emoji in stats.values())
            message = MessageArray()
            message.add_text("=== 群组表情包统计 ===\n").add_text("最近")
            if days is None:
                message.add_text("全部时间")
            else:
                message.add_text(str(days)).add_text("天")
            message.add_text("使用次数:\n")
            for img in self._number_to_counter(total_count):
                message.add_by_segment(img)
            message.add_text("\n\n")
            # 2. 发表情包最多的10个用户
            user_counts = {}
            for user_id, user_stats in self.user_count.get(group_id, {}).items():
                user_time_stats = self._get_time_range_stats(user_stats, days)
                user_total = sum(user_time_stats.values())
                if user_total > 0:
                    user_counts[user_id] = user_total
            # 只取前十
            top_users = sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[
                :10
            ]
            user_names = {}
            # 先设置默认值
            for user_id, _ in top_users:
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
                            user_names[str(member.user_id)] = nickname
            except Exception as e:
                _log.error(f"获取群成员列表失败: {e}")
                # 如果批量获取失败，回退到单个获取
                for user_id, _ in top_users:
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
                            user_names[user_id] = nickname
                    except Exception:
                        pass
            # 计算总参与人数
            total_emoji_users = len(user_counts)

            if top_users:
                # 使用新的排行榜卡片
                card_path = self._generate_ranking_card(
                    user_counts=dict(top_users),
                    user_names=user_names,
                    total_messages=total_count,
                    total_users=total_emoji_users,
                    days=days,
                    title="表情包排行",
                    subtitle="活跃用户 TOP 10",
                    count_label="次",
                    total_label="总使用次数",
                    users_label="总参与人数",
                    top_n=10,
                )
                if card_path:
                    message.add_image(card_path).add_text("\n")
            else:
                message.add_text("暂无用户表情包数据\n")
            # 3. 最受欢迎的3个表情包
            top_emojis = self._get_top_emojis(stats, days)[:3]
            message.add_text("最受欢迎表情包TOP3:\n")
            for i, emoji in enumerate(top_emojis, 1):
                try:
                    message.add_text(f"{i}. 使用次数: {emoji.get_count(days)}次\n")
                    if os.path.exists(emoji.cache_path):
                        message.add_image(emoji.cache_path)
                    else:
                        _log.error(f"表情包图片不存在: {emoji.cache_path}")
                        message.add_text("[图片已失效]\n")
                    message.add_text("\n")
                except Exception as e:
                    _log.error(f"添加表情包图片失败: {e}")
                    message.add_text(f"{i}. [图片加载失败]\n")

            # 发送消息
            try:
                await self.api.post_group_msg(
                    input.group_id, rtf=message, reply=input.message_id
                )
            except Exception as e:
                _log.error(f"发送消息失败: {e}")
                # 尝试发送纯文本消息
                error_message = MessageArray([Text("统计信息发送失败，请稍后重试")])
                await self.api.post_group_msg(
                    input.group_id, rtf=error_message, reply=input.message_id
                )
        else:
            # 获取用户最受欢迎表情包
            group_id = str(input.group_id)
            user_id = str(target_user_id)
            top_emojis = self._get_top_emojis(
                self.user_stats.get(group_id, {}).get(user_id, {}), days
            )
            # 获取用户发送次数统计
            count_stats = self._get_time_range_stats(
                self.user_count.get(group_id, {}).get(user_id, {}), days
            )
            total_count = sum(count_stats.values())

            # 添加消息元素
            message = MessageArray()
            message.add_text("=== 个人表情包统计 ===\n").add_text("最近")
            if days is None:
                message.add_text("全部时间")
            else:
                message.add_text(str(days)).add_text("天")
            message.add_text("发送表情包数量:\n")
            for img in self._number_to_counter(total_count):
                message.add_by_segment(img)
            message.add_text("\n\n").add_text("最常使用的表情包TOP3:\n")

            # 添加表情包信息
            for i, emoji in enumerate(top_emojis, 1):
                try:
                    message.add_text(f"{i}. 使用次数: {emoji.get_count(days)}次\n")
                    if os.path.exists(emoji.cache_path):
                        message.add_image(emoji.cache_path)
                    else:
                        _log.error(f"表情包图片不存在: {emoji.cache_path}")
                        message.add_text("[图片已失效]\n")
                    message.add_text("\n")
                except Exception as e:
                    _log.error(f"添加表情包图片失败: {e}")
                    message.add_text(f"{i}. [图片加载失败]\n")

            # 发送消息
            try:
                await self.api.post_group_msg(
                    input.group_id, rtf=message, reply=input.message_id
                )
            except Exception as e:
                _log.error(f"发送消息失败: {e}")
                # 尝试发送纯文本消息
                error_message = MessageArray([Text("统计信息发送失败，请稍后重试")])
                await self.api.post_group_msg(
                    input.group_id, rtf=error_message, reply=input.message_id
                )
