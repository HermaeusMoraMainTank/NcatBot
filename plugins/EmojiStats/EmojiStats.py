import asyncio
import os
import json
import hashlib
from shutil import copy2
from time import strftime
import re
import urllib3
import threading
import time
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass
from PIL import Image as PILImage
from common.utils.async_io import http_get_bytes
from common.constants.HMMT import HMMT
from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.types import MessageArray, PlainText, Image
from ncatbot.plugin import NcatBotPlugin
from ncatbot.core import registrar
from ncatbot.utils import get_log
from common.utils.CommonUtil import CommonUtil
from common.utils.QqSendUtil import QqSendUtil
from common.utils.json_io import atomic_write_json, get_project_root
from common.stats_render.helpers import (
    filter_daily_by_period,
    period_display_label,
    sum_daily_by_period,
)

# 禁用 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_log = get_log()


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
        return sum_daily_by_period(self.daily_counts, days)

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

    @staticmethod
    def _safe_display_name(name, user_id) -> str:
        if name:
            return str(name).encode("utf-8", errors="ignore").decode("utf-8")
        return str(user_id)

    def _init_(self) -> None:

        root = get_project_root()
        if not hasattr(self, "group_stats") or self.group_stats is None:
            self.group_stats = {}
        if not hasattr(self, "user_stats") or self.user_stats is None:
            self.user_stats = {}
        if not hasattr(self, "group_count") or self.group_count is None:
            self.group_count = {}
        if not hasattr(self, "user_count") or self.user_count is None:
            self.user_count = {}
        self.GROUP_DATA_FILE = str(root / "data" / "json" / "emoji_group_stats.json")
        self.USER_DATA_FILE = str(root / "data" / "json" / "emoji_user_stats.json")
        self.CACHE_DIR = str(root / "data" / "image" / "emoji_stats")

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
        # 数据量较大时同步加载会阻塞事件循环，放到线程池执行
        await asyncio.to_thread(self._load_emoji_stats_json)
        await asyncio.to_thread(self._cleanup_unused_images)
        # 启动定时清理任务
        self._start_cleanup_task()
        if not self.add_scheduled_task(
            "daily_emoji_stats",
            "18:00",
            callback=self._send_daily_stats_to_all_groups,
        ):
            _log.warning("[EmojiStats] 每日 18:00 表情包统计定时任务注册失败")
        if not self.add_scheduled_task(
            "emoji_stats_persist",
            "5m",
            callback=self._scheduled_persist,
        ):
            _log.warning("[EmojiStats] 5 分钟刷盘定时任务注册失败")
        _log.info(
            "[EmojiStats] 定时任务已注册: %s",
            self.list_scheduled_tasks(),
        )
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

    async def _scheduled_persist(self) -> None:
        self._persist_emoji_stats()

    async def on_close(self) -> None:
        self._persist_emoji_stats()

    def _save_data_to_file(self, data: dict, file_path: str) -> bool:
        """数据保存（原子写入）。"""
        try:
            ok = atomic_write_json(file_path, data, encoder=DateTimeEncoder)
            if not ok:
                _log.error(f"[EmojiStats] 保存数据失败: {file_path}")
            return ok
        except Exception as e:
            _log.error(f"[EmojiStats] 保存数据失败: {e}")
            return False

    @staticmethod
    def _quarantine_broken_stats_file(path: str, empty_doc: dict) -> None:
        """备份无法解析的 JSON，并写入空结构，避免每次启动重复报错。"""
        try:
            if not os.path.isfile(path):
                return
            backup = f"{path}.corrupt.{strftime('%Y%m%d_%H%M%S')}"
            copy2(path, backup)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    empty_doc, f, ensure_ascii=False, indent=2, cls=DateTimeEncoder
                )
            _log.warning(
                "[EmojiStats] 数据文件 JSON 损坏，已备份到 %s 并写入空模板，可从备份恢复",
                backup,
            )
        except OSError as e:
            _log.error("[EmojiStats] 备份或重置数据文件失败 %s: %s", path, e)

    def _load_emoji_stats_json(self):
        """从 data/json 加载表情包统计（勿命名为 _load_data，以免覆盖 DataMixin）。"""
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(self.GROUP_DATA_FILE), exist_ok=True)
            os.makedirs(os.path.dirname(self.USER_DATA_FILE), exist_ok=True)

            if self.group_stats is None:
                self.group_stats = {}
            if self.user_stats is None:
                self.user_stats = {}
            if self.group_count is None:
                self.group_count = {}
            if self.user_count is None:
                self.user_count = {}

            # 加载群组数据
            if os.path.exists(self.GROUP_DATA_FILE):
                try:
                    with open(self.GROUP_DATA_FILE, "r", encoding="utf-8") as f:
                        raw = f.read()
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError as e:
                        _log.error(
                            f"[EmojiStats] 群组数据文件 JSON 损坏（无法解析）: {e}"
                        )
                        self._quarantine_broken_stats_file(
                            self.GROUP_DATA_FILE,
                            {"group_stats": {}, "group_count": {}},
                        )
                        data = None

                    if data is not None:
                        if not isinstance(data, dict):
                            _log.warning(
                                "[EmojiStats] 群组数据文件根节点不是对象，已忽略并写入空模板"
                            )
                            self._quarantine_broken_stats_file(
                                self.GROUP_DATA_FILE,
                                {"group_stats": {}, "group_count": {}},
                            )
                            data = None

                    if data is not None:
                        # 加载群组统计
                        new_group_stats = {}
                        group_stats_data = data.get("group_stats")
                        if not isinstance(group_stats_data, dict):
                            group_stats_data = {}

                        for k, v in group_stats_data.items():
                            try:
                                group_id = k  # 直接使用字符串，不转换
                                new_group_stats[group_id] = {}
                                if not isinstance(v, dict):
                                    continue

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
                        group_count_data = data.get("group_count")
                        if not isinstance(group_count_data, dict):
                            group_count_data = {}

                        for k, v in group_count_data.items():
                            try:
                                group_id = k  # 直接使用字符串，不转换
                                if group_id not in self.group_count:
                                    self.group_count[group_id] = {}
                                if not isinstance(v, dict):
                                    continue
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
                        raw = f.read()
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError as e:
                        _log.error(
                            f"[EmojiStats] 用户数据文件 JSON 损坏（无法解析）: {e}"
                        )
                        self._quarantine_broken_stats_file(
                            self.USER_DATA_FILE,
                            {"user_stats": {}, "user_count": {}},
                        )
                        data = None

                    if data is not None:
                        if not isinstance(data, dict):
                            _log.warning(
                                "[EmojiStats] 用户数据文件根节点不是对象，已忽略并写入空模板"
                            )
                            self._quarantine_broken_stats_file(
                                self.USER_DATA_FILE,
                                {"user_stats": {}, "user_count": {}},
                            )
                            data = None

                    if data is not None:
                        # 加载用户统计
                        new_user_stats = {}
                        user_stats_data = data.get("user_stats")
                        if not isinstance(user_stats_data, dict):
                            user_stats_data = {}

                        for k, v in user_stats_data.items():
                            try:
                                group_id = k  # 直接使用字符串，不转换
                                new_user_stats[group_id] = {}
                                if not isinstance(v, dict):
                                    continue

                                for k2, v2 in v.items():
                                    try:
                                        user_id = k2  # 直接使用字符串，不转换
                                        new_user_stats[group_id][user_id] = {}
                                        if not isinstance(v2, dict):
                                            continue

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
                        user_count_data = data.get("user_count")
                        if not isinstance(user_count_data, dict):
                            user_count_data = {}

                        for k, v in user_count_data.items():
                            try:
                                group_id = k  # 直接使用字符串，不转换
                                if group_id not in self.user_count:
                                    self.user_count[group_id] = {}
                                if not isinstance(v, dict):
                                    continue
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

                        sum(len(users) for users in self.user_stats.values())
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

    def _persist_emoji_stats(self, group_id: int = None):
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
                        _log.error("[EmojiStats] 所有数据保存失败")
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
            os.makedirs(self.CACHE_DIR, exist_ok=True)

            status, content = await http_get_bytes(
                image_url, timeout=30, verify_ssl=False
            )
            if status != 200:
                _log.error(f"下载图片失败: HTTP {status}")
                return None

            image_hash = hashlib.md5(content).hexdigest()
            cache_path = os.path.join(self.CACHE_DIR, f"{image_hash}.jpg")

            if os.path.exists(cache_path):
                return cache_path

            def _write() -> None:
                with open(cache_path, "wb") as f:
                    f.write(content)

            await asyncio.to_thread(_write)
            return cache_path

        except Exception as e:
            _log.error(f"缓存图片失败: {e}")
            return None

    @registrar.qq.on_group_message()
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
                self._persist_emoji_stats(group_id)
                return

        # 未找到现有表情包，开始下载新表情包
        # 如果图片不存在，则下载并缓存
        cache_path = await self._download_and_cache_image(image_url)
        if not cache_path:
            _log.error("[EmojiStats] 表情包下载失败")
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
        self._persist_emoji_stats(group_id)

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
            return stats
        return filter_daily_by_period(stats, days)

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

        return [Image(file=temp_path)]

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
                await asyncio.sleep(1.0)
            except Exception as e:
                _log.error(f"[EmojiStats] 发送群组 {group_id} 每日统计失败: {e}")

        _log.info("[EmojiStats] 每日表情包统计定时任务执行完成")

    async def _send_group_daily_stats(self, group_id: int):
        """发送单个群组的每日表情包统计"""
        from .report_builder import build_emoji_group_report

        group_id_str = str(group_id)
        stats = self.group_stats.get(group_id_str)
        if not stats:
            return

        today = date.today().isoformat()
        total_count = sum(emoji.daily_counts.get(today, 0) for emoji in stats.values())
        if total_count == 0:
            _log.info(f"[EmojiStats] 群组 {group_id} 今日无表情包记录，跳过发送")
            return

        user_counts = {}
        for user_id, user_stats in self.user_count.get(group_id_str, {}).items():
            user_today_count = user_stats.get(today, 0)
            if user_today_count > 0:
                user_counts[user_id] = user_today_count
        user_names = await self._resolve_user_names(group_id, list(user_counts.keys()))
        report_path = await build_emoji_group_report(
            group_id_str,
            1,
            stats,
            user_counts,
            user_names,
            user_emoji_stats=self.user_stats.get(group_id_str, {}),
            user_daily_count=self.user_count.get(group_id_str, {}),
        )
        try:
            await self._send_flip_and_report(
                group_id,
                None,
                total_count,
                report_path,
                header="=== 今日表情包统计 ===\n今日使用次数:\n",
            )
            _log.info(f"[EmojiStats] 成功发送群组 {group_id} 的每日统计")
        except Exception as e:
            _log.error(f"[EmojiStats] 发送群组 {group_id} 消息失败: {e}")

    @registrar.qq.on_group_message()
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
        at_ids = CommonUtil.message_at_user_ids(input.message)
        if at_ids:
            target_user_id = int(at_ids[0])

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

    async def _resolve_user_names(self, group_id, user_ids):
        user_names = {uid: uid for uid in user_ids}
        try:
            members_response = await self.api.qq.query.get_group_member_list(
                group_id=group_id
            )
            members = CommonUtil.parse_group_member_list(members_response)
            for member in members:
                uid = str(member.user_id)
                if uid in user_names:
                    nickname = member.card if member.card else member.nickname
                    user_names[uid] = self._safe_display_name(nickname, uid)
        except Exception as e:
            _log.error(f"[EmojiStats] 获取群成员失败: {e}")
        return user_names

    async def _send_flip_and_report(
        self, group_id, reply_id, total_count, report_path, header=""
    ):
        await QqSendUtil.send_flip_and_report(
            self.api.qq,
            group_id,
            total_count=total_count,
            report_path=report_path,
            header=header,
            reply_id=reply_id,
            number_to_counter=self._number_to_counter,
        )

    async def _show_stats(
        self, input: GroupMessage, days: int, target: str, target_user_id: int
    ) -> None:
        from .report_builder import build_emoji_group_report

        await self.api.qq.post_group_msg(
            input.group_id,
            rtf=MessageArray([PlainText(text="正在生成统计图表，请稍候...")]),
        )
        if target == "群组":
            group_id = str(input.group_id)
            stats = self.group_stats.get(group_id)
            if not stats:
                await self.api.qq.post_group_msg(
                    input.group_id,
                    rtf=MessageArray([PlainText(text="暂无群组表情包统计数据")]),
                )
                return
            total_count = sum(emoji.get_count(days) for emoji in stats.values())
            user_counts = {}
            for user_id, user_stats in self.user_count.get(group_id, {}).items():
                user_total = sum(self._get_time_range_stats(user_stats, days).values())
                if user_total > 0:
                    user_counts[user_id] = user_total
            user_names = await self._resolve_user_names(
                input.group_id, list(user_counts.keys())
            )
            report_path = await build_emoji_group_report(
                group_id,
                days,
                stats,
                user_counts,
                user_names,
                user_emoji_stats=self.user_stats.get(group_id, {}),
                user_daily_count=self.user_count.get(group_id, {}),
            )
            period = period_display_label(days)
            header = f"=== 群组表情包统计 ===\n{period}使用次数:\n"
            await self._send_flip_and_report(
                input.group_id,
                input.message_id,
                total_count,
                report_path,
                header=header,
            )
        else:
            group_id = str(input.group_id)
            user_id = str(target_user_id)
            count_stats = self._get_time_range_stats(
                self.user_count.get(group_id, {}).get(user_id, {}), days
            )
            total_count = sum(count_stats.values())
            period = period_display_label(days)
            header = f"=== 个人表情包统计 ===\n{period}发送数量:\n"
            await self._send_flip_and_report(
                input.group_id, input.message_id, total_count, None, header=header
            )
