import asyncio
import os
import json
from datetime import datetime, date
from typing import Dict, List, Optional
from dataclasses import dataclass
from PIL import Image as PILImage
from common.utils.CommonUtil import CommonUtil
from common.utils.QqSendUtil import QqSendUtil
from common.utils.json_io import atomic_write_json, resolve_data_json
from common.stats_render.word_analysis import process_message_text
from common.stats_render.paths import (
    cleanup_temp as cleanup_stats_temp,  # noqa: F401
)
from ncatbot.types import PlainText as PlainTextSeg
import re
import threading

from .report_builder import build_group_report, build_personal_report

from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.types import MessageArray as MessageChain, PlainText, Image
from ncatbot.plugin import NcatBotPlugin
from ncatbot.core import registrar
from ncatbot.utils import get_log
from common.stats_render.helpers import (
    filter_daily_by_period,
    period_display_label,
    sum_daily_by_period,
)
from common.constants.HMMT import HMMT
from common.utils.plugin_commands import format_help, is_help_message

_log = get_log()

COMMAND_PREFIX = "发言统计"

HELP_TEXT = format_help(
    "MessageStats 发言统计",
    [
        f"{COMMAND_PREFIX} <时间范围> <统计对象> [@用户]",
        "时间范围：今日、本周、本月、全部",
        "统计对象：群组、个人",
    ],
)


class DateTimeEncoder(json.JSONEncoder):
    """自定义 JSON 编码器，用于处理 datetime 对象"""

    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


@dataclass
class MessageStats:
    daily_counts: Dict[str, int] = None
    daily_hourly_counts: Dict[str, Dict[str, int]] = None
    daily_word_counts: Dict[str, Dict[str, int]] = None
    daily_pos_counts: Dict[str, Dict[str, int]] = None
    daily_char_totals: Dict[str, int] = None
    daily_max_message: Dict[str, int] = None
    hourly_counts: Dict[str, int] = None
    last_message: datetime = None

    def __post_init__(self):
        if self.daily_counts is None:
            self.daily_counts = {}
        if self.daily_hourly_counts is None:
            self.daily_hourly_counts = {}
        if self.daily_word_counts is None:
            self.daily_word_counts = {}
        if self.daily_pos_counts is None:
            self.daily_pos_counts = {}
        if self.daily_char_totals is None:
            self.daily_char_totals = {}
        if self.daily_max_message is None:
            self.daily_max_message = {}
        if self.hourly_counts is None:
            self.hourly_counts = {}
        if self.last_message is None:
            self.last_message = datetime.now()

    def to_dict(self) -> dict:
        return {
            "daily_counts": self.daily_counts,
            "daily_hourly_counts": self.daily_hourly_counts,
            "daily_word_counts": self.daily_word_counts,
            "daily_pos_counts": self.daily_pos_counts,
            "daily_char_totals": self.daily_char_totals,
            "daily_max_message": self.daily_max_message,
            "hourly_counts": self.hourly_counts,
            "last_message": self.last_message.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MessageStats":
        daily_counts = data.get("daily_counts", {})
        if not isinstance(daily_counts, dict):
            daily_counts = {}

        daily_hourly_counts = data.get("daily_hourly_counts", {})
        if not isinstance(daily_hourly_counts, dict):
            daily_hourly_counts = {}

        daily_word_counts = data.get("daily_word_counts", {})
        if not isinstance(daily_word_counts, dict):
            daily_word_counts = {}

        daily_pos_counts = data.get("daily_pos_counts", {})
        if not isinstance(daily_pos_counts, dict):
            daily_pos_counts = {}

        daily_char_totals = data.get("daily_char_totals", {})
        if not isinstance(daily_char_totals, dict):
            daily_char_totals = {}

        daily_max_message = data.get("daily_max_message", {})
        if not isinstance(daily_max_message, dict):
            daily_max_message = {}

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
            daily_hourly_counts=daily_hourly_counts,
            daily_word_counts=daily_word_counts,
            daily_pos_counts=daily_pos_counts,
            daily_char_totals=daily_char_totals,
            daily_max_message=daily_max_message,
            hourly_counts=hourly_counts,
            last_message=last_message,
        )

    def get_count(self, days: int = None) -> int:
        if days is None:
            return sum(self.daily_counts.values())
        return sum_daily_by_period(self.daily_counts, days)

    def record_message(
        self,
        date_str: str,
        hour: int,
        text: str = "",
        *,
        track_words: bool = False,
        track_user_metrics: bool = False,
    ):
        if date_str not in self.daily_counts:
            self.daily_counts[date_str] = 0
        self.daily_counts[date_str] += 1

        hour_str = str(hour)
        if date_str not in self.daily_hourly_counts:
            self.daily_hourly_counts[date_str] = {}
        bucket = self.daily_hourly_counts[date_str]
        bucket[hour_str] = bucket.get(hour_str, 0) + 1

        if text and track_words:
            word_c, pos_c, _ = process_message_text(text)
            if date_str not in self.daily_word_counts:
                self.daily_word_counts[date_str] = {}
            if date_str not in self.daily_pos_counts:
                self.daily_pos_counts[date_str] = {}
            for w, c in word_c.items():
                self.daily_word_counts[date_str][w] = (
                    self.daily_word_counts[date_str].get(w, 0) + c
                )
            for p, c in pos_c.items():
                self.daily_pos_counts[date_str][p] = (
                    self.daily_pos_counts[date_str].get(p, 0) + c
                )

        if text and track_user_metrics:
            plain = text.strip()
            if plain and not plain.startswith("/"):
                chars = len(plain)
                self.daily_char_totals[date_str] = (
                    self.daily_char_totals.get(date_str, 0) + chars
                )
                prev_max = self.daily_max_message.get(date_str, 0)
                if chars > prev_max:
                    self.daily_max_message[date_str] = chars

    def increment_count(self, date_str: str, hour: int):
        """兼容旧调用。"""
        self.record_message(date_str, hour)


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

    @staticmethod
    def _safe_display_name(name, user_id) -> str:
        if name:
            return str(name).encode("utf-8", errors="ignore").decode("utf-8")
        return str(user_id)

    def _init_(self) -> None:
        if not hasattr(self, "group_stats") or self.group_stats is None:
            self.group_stats = {}
        if not hasattr(self, "user_stats") or self.user_stats is None:
            self.user_stats = {}
        self.GROUP_DATA_FILE = resolve_data_json("message_group_stats.json")
        self.USER_DATA_FILE = resolve_data_json("message_user_stats.json")

    async def on_load(self):
        """异步加载插件"""
        # 开始加载插件
        # 初始化实例变量
        if self.group_stats is None:
            self.group_stats = {}
        if self.user_stats is None:
            self.user_stats = {}
        # 不要清空内存中的数据，保持现有数据
        self._load_message_stats_json()
        if not self.add_scheduled_task(
            "daily_message_stats",
            "18:00",
            callback=self._send_daily_stats_to_all_groups,
        ):
            _log.warning("[MessageStats] 每日 18:00 发言统计定时任务注册失败")
        if not self.add_scheduled_task(
            "message_stats_persist",
            "5m",
            callback=self._scheduled_persist,
        ):
            _log.warning("[MessageStats] 5 分钟刷盘定时任务注册失败")
        _log.info(
            "[MessageStats] 定时任务已注册: %s",
            self.list_scheduled_tasks(),
        )
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

    async def _scheduled_persist(self) -> None:
        self._persist_message_stats()

    async def on_close(self) -> None:
        self._persist_message_stats()

    def _save_data_to_file(self, data: dict, file_path: str) -> bool:
        """数据保存（原子写入）。"""
        try:
            ok = atomic_write_json(file_path, data, encoder=DateTimeEncoder)
            if not ok:
                _log.error(f"[MessageStats] 保存数据失败: {file_path}")
            return ok
        except Exception as e:
            _log.error(f"[MessageStats] 保存数据失败: {e}")
            return False

    def _load_message_stats_json(self):
        """从 data/json 加载发言统计（勿命名为 _load_data，以免覆盖 DataMixin）。"""
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(self.GROUP_DATA_FILE), exist_ok=True)
            os.makedirs(os.path.dirname(self.USER_DATA_FILE), exist_ok=True)

            if self.group_stats is None:
                self.group_stats = {}
            if self.user_stats is None:
                self.user_stats = {}

            # 加载群组数据
            if os.path.exists(self.GROUP_DATA_FILE):
                try:
                    with open(self.GROUP_DATA_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)

                        # 加载群组统计
                        new_group_stats = {}
                        group_stats_data = data.get("group_stats")
                        if not isinstance(group_stats_data, dict):
                            group_stats_data = {}

                        for k, v in group_stats_data.items():
                            try:
                                group_id = k  # 直接使用字符串，不转换
                                if not isinstance(v, dict):
                                    continue
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

    def _persist_message_stats(self, group_id: int = None):
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

    @registrar.qq.on_group_message()
    async def handle_message(self, input: GroupMessage) -> None:
        """处理群消息"""
        # 确保 group_id 和 user_id 都是字符串类型
        group_id = str(input.group_id)
        user_id = str(input.sender.user_id)
        now = datetime.now()
        today = now.date().isoformat()
        hour = now.hour
        plain_text = self._extract_plain_text(input.message)

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

        self.group_stats[group_id].record_message(
            today, hour, plain_text, track_words=True, track_user_metrics=False
        )
        self.group_stats[group_id].last_message = now

        # 更新用户统计
        if group_id not in self.user_stats:
            self.user_stats[group_id] = {}
        if user_id not in self.user_stats[group_id]:
            self.user_stats[group_id][user_id] = MessageStats()

        self.user_stats[group_id][user_id].record_message(
            today, hour, plain_text, track_words=False, track_user_metrics=True
        )
        self.user_stats[group_id][user_id].last_message = now

        # 保存数据
        self._persist_message_stats(group_id)

    def _extract_plain_text(self, message) -> str:
        if message is None:
            return ""
        parts = []
        for seg in message:
            if isinstance(seg, PlainTextSeg):
                parts.append(seg.text)
        return "".join(parts)

    async def _resolve_user_names(
        self, group_id, user_ids: List[str]
    ) -> Dict[str, str]:
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
            _log.error(f"[MessageStats] 获取群成员列表失败: {e}")
        return user_names

    async def _send_flip_and_report(
        self,
        group_id,
        reply_id,
        total_count: int,
        report_path: Optional[str],
        header: str = "",
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

    def _get_time_range_stats(self, stats: MessageStats, days: int) -> Dict[str, int]:
        """获取指定时间范围内的统计"""
        if days is None:
            return stats.daily_counts
        return filter_daily_by_period(stats.daily_counts, days)

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
        """每日定时发送发言统计到所有群组"""
        _log.info("[MessageStats] 开始执行每日发言统计定时任务")

        # 遍历所有有统计数据的群组
        for group_id in list(self.group_stats.keys()):
            try:
                # 检查是否在黑名单中
                if str(group_id) in HMMT.BLACKLIST_GROUPS:
                    _log.info(f"[MessageStats] 跳过黑名单群组 {group_id}")
                    continue

                await self._send_group_daily_stats(int(group_id))
                await asyncio.sleep(1.0)
            except Exception as e:
                _log.error(f"[MessageStats] 发送群组 {group_id} 每日统计失败: {e}")

        _log.info("[MessageStats] 每日发言统计定时任务执行完成")

    async def _send_group_daily_stats(self, group_id: int):
        """发送单个群组的每日发言统计"""
        group_id_str = str(group_id)
        stats = self.group_stats.get(group_id_str)
        if not stats:
            return

        today = date.today().isoformat()
        total_count = stats.daily_counts.get(today, 0)
        if total_count == 0:
            _log.info(f"[MessageStats] 群组 {group_id} 今日无发言记录，跳过发送")
            return

        user_stats = self.user_stats.get(group_id_str, {})
        user_ids = [
            uid for uid, us in user_stats.items() if us.daily_counts.get(today, 0) > 0
        ]
        user_names = await self._resolve_user_names(group_id, user_ids)
        report_path = await build_group_report(
            group_id_str,
            1,
            stats,
            user_stats,
            user_names,
            group_label=f"群号：{group_id}",
        )
        try:
            await self._send_flip_and_report(
                group_id,
                None,
                total_count,
                report_path,
                header="=== 今日发言统计 ===\n今日发言数量:\n",
            )
            _log.info(f"[MessageStats] 成功发送群组 {group_id} 的每日统计")
        except Exception as e:
            _log.error(f"[MessageStats] 发送群组 {group_id} 消息失败: {e}")

    @registrar.qq.on_group_message()
    async def handle_message_stats(self, input: GroupMessage) -> None:
        """处理发言统计命令"""
        message = input.raw_message.strip()
        if is_help_message(
            message,
            command_names=(COMMAND_PREFIX,)):
            await input.reply(text=HELP_TEXT, at_sender=False)
            return
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

    async def _show_stats(
        self, input: GroupMessage, days: int, target: str, target_user_id: int
    ) -> None:
        """显示统计数据：翻牌 GIF + pillowmd 长图报告。"""
        await self.api.qq.post_group_msg(
            input.group_id,
            rtf=MessageChain([PlainText(text="正在生成统计图表，请稍候...")]),
        )

        if target == "群组":
            group_id = str(input.group_id)
            stats = self.group_stats.get(group_id)
            if not stats:
                await self.api.qq.post_group_msg(
                    input.group_id,
                    rtf=MessageChain([PlainText(text="暂无群组统计数据")]),
                )
                return

            time_range_stats = self._get_time_range_stats(stats, days)
            total_count = sum(time_range_stats.values())
            user_stats = self.user_stats.get(group_id, {})
            user_ids = [
                uid
                for uid, us in user_stats.items()
                if sum(self._get_time_range_stats(us, days).values()) > 0
            ]
            user_names = await self._resolve_user_names(input.group_id, user_ids)
            report_path = await build_group_report(
                group_id,
                days,
                stats,
                user_stats,
                user_names,
                group_label=f"群号：{group_id}",
            )
            period = period_display_label(days)
            header = f"=== 群组发言统计 ===\n{period}发言数量:\n"
            try:
                await self._send_flip_and_report(
                    input.group_id,
                    input.message_id,
                    total_count,
                    report_path,
                    header=header,
                )
            except Exception as e:
                _log.error(f"发送消息失败: {e}")
                await self.api.qq.post_group_msg(
                    input.group_id,
                    rtf=MessageChain([PlainText(text="统计信息发送失败，请稍后重试")]),
                    reply=input.message_id,
                )
        else:
            group_id = str(input.group_id)
            user_id = str(target_user_id)
            user_stat = self.user_stats.get(group_id, {}).get(user_id)
            if not user_stat:
                await input.reply("暂无个人发言统计")
                return

            count_stats = self._get_time_range_stats(user_stat, days)
            total_count = sum(count_stats.values())
            names = await self._resolve_user_names(input.group_id, [user_id])
            nickname = names.get(user_id, user_id)
            report_path = await build_personal_report(
                group_id, user_id, days, user_stat, nickname
            )
            period = period_display_label(days)
            header = f"=== 个人发言统计 ===\n{period}发言数量:\n"
            try:
                await self._send_flip_and_report(
                    input.group_id,
                    input.message_id,
                    total_count,
                    report_path,
                    header=header,
                )
            except Exception as e:
                _log.error(f"发送消息失败: {e}")
                await self.api.qq.post_group_msg(
                    input.group_id,
                    rtf=MessageChain([PlainText(text="统计信息发送失败，请稍后重试")]),
                    reply=input.message_id,
                )
