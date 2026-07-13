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
from common.utils.QqSendUtil import QqSendUtil
from common.utils.AiUtil import AiUtil
from common.utils.json_io import atomic_write_json, resolve_data_json
from common.stats_render.helpers import (
    filter_daily_by_period,
    is_date_in_period,
    period_display_label,
    sum_daily_by_period,
)
from common.utils.AiStatsRecorder import (
    SOURCE_ROLLUP,
    SOURCE_ROLLUP_ORDER,
    get_balance_summary,
    get_rollup_breakdown,
    get_user_cost_ranking,
    record_balance_snapshot,
    record_ai_usage as recorder_record_usage,
)
from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.types import Image, MessageArray as MessageChain, PlainText
from ncatbot.plugin import NcatBotPlugin
from ncatbot.core import registrar
from ncatbot.utils import get_log

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
        self.daily_prompt_tokens = {}  # {date: prompt_tokens}
        self.daily_completion_tokens = {}  # {date: completion_tokens}
        self.daily_cost = {}  # {date: cost_cny}
        self.daily_by_source = {}  # {date: {source: {count, tokens, cost, ...}}}
        self.last_used = None
        self.total_count = 0
        self.total_tokens = 0
        self.total_cost = 0.0

    def increment_count(
        self,
        date_str: str,
        tokens: int = 0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost: float = 0.0,
        source: str = "active",
    ):
        """增加使用次数、token 数与费用（含来源）。"""
        if date_str not in self.daily_counts:
            self.daily_counts[date_str] = 0
        if date_str not in self.daily_tokens:
            self.daily_tokens[date_str] = 0
        if date_str not in self.daily_prompt_tokens:
            self.daily_prompt_tokens[date_str] = 0
        if date_str not in self.daily_completion_tokens:
            self.daily_completion_tokens[date_str] = 0
        if date_str not in self.daily_cost:
            self.daily_cost[date_str] = 0.0
        if date_str not in self.daily_by_source:
            self.daily_by_source[date_str] = {}
        if source not in self.daily_by_source[date_str]:
            self.daily_by_source[date_str][source] = {
                "count": 0,
                "tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost": 0.0,
            }

        self.daily_counts[date_str] += 1
        self.daily_tokens[date_str] += tokens
        self.daily_prompt_tokens[date_str] += prompt_tokens
        self.daily_completion_tokens[date_str] += completion_tokens
        self.daily_cost[date_str] = round(self.daily_cost[date_str] + cost, 6)

        bucket = self.daily_by_source[date_str][source]
        bucket["count"] += 1
        bucket["tokens"] += tokens
        bucket["prompt_tokens"] += prompt_tokens
        bucket["completion_tokens"] += completion_tokens
        bucket["cost"] = round(bucket["cost"] + cost, 6)

        self.total_count += 1
        self.total_tokens += tokens
        self.total_cost = round(self.total_cost + cost, 6)
        self.last_used = datetime.now()

    def get_count(self, days: int = None) -> int:
        """获取指定天数内的使用次数"""
        if days is None:
            if self.total_count:
                return self.total_count
            return sum(int(v) for v in self.daily_counts.values())
        return sum_daily_by_period(self.daily_counts, days)

    def get_tokens(self, days: int = None) -> int:
        """获取指定天数内的token使用量"""
        if days is None:
            if self.total_tokens:
                return self.total_tokens
            return sum(int(v) for v in self.daily_tokens.values())
        return sum_daily_by_period(self.daily_tokens, days)

    def _sum_daily_field(self, field: dict, days: int = None) -> int:
        if days is None:
            return sum(int(v) for v in field.values())
        return sum_daily_by_period(field, days)

    def get_prompt_tokens(self, days: int = None) -> int:
        return self._sum_daily_field(self.daily_prompt_tokens, days)

    def get_completion_tokens(self, days: int = None) -> int:
        return self._sum_daily_field(self.daily_completion_tokens, days)

    def get_cost(self, days: int = None) -> float:
        """获取指定天数内的估算费用（元）"""
        if days is None:
            if self.total_cost:
                return self.total_cost
            return round(sum(float(v) for v in self.daily_cost.values()), 4)
        return round(
            sum(
                float(v) for v in filter_daily_by_period(self.daily_cost, days).values()
            ),
            4,
        )

    def to_dict(self) -> dict:
        """转换为字典格式"""
        return {
            "daily_counts": self.daily_counts,
            "daily_tokens": self.daily_tokens,
            "daily_prompt_tokens": self.daily_prompt_tokens,
            "daily_completion_tokens": self.daily_completion_tokens,
            "daily_cost": self.daily_cost,
            "daily_by_source": self.daily_by_source,
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "total_count": self.total_count,
            "total_tokens": self.total_tokens,
            "total_cost": self.total_cost,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AiUsageStats":
        """从字典创建实例"""
        stats = cls()
        stats.daily_counts = data.get("daily_counts", {})
        stats.daily_tokens = data.get("daily_tokens", {})
        stats.daily_prompt_tokens = data.get("daily_prompt_tokens", {})
        stats.daily_completion_tokens = data.get("daily_completion_tokens", {})
        stats.daily_cost = data.get("daily_cost", {})
        stats.daily_by_source = data.get("daily_by_source", {})
        stats.total_count = data.get("total_count", 0)
        stats.total_tokens = data.get("total_tokens", 0)
        stats.total_cost = data.get("total_cost", 0.0)

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
    version = "1.1.0"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 统计数据存储
        self.group_stats: Dict[str, AiUsageStats] = {}  # 群组统计
        self.user_stats: Dict[
            str, Dict[str, AiUsageStats]
        ] = {}  # 用户统计 {group_id: {user_id: stats}}

        # 数据文件路径
        self.GROUP_DATA_FILE = resolve_data_json("ai_group_stats.json")
        self.USER_DATA_FILE = resolve_data_json("ai_user_stats.json")

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
        self._load_ai_stats_json()
        tasks = [
            ("ai_balance_poll", "30m", self._poll_balance),
            ("ai_daily_balance_finalize", "00:05", self._finalize_daily_balance),
            ("ai_stats_persist", "5m", self._scheduled_persist),
        ]
        for name, interval, cb in tasks:
            if not self.add_scheduled_task(name, interval, callback=cb):
                _log.warning("[AiStats] 定时任务 %s 注册失败", name)
        _log.info("[AiStats] 定时任务已注册: %s", self.list_scheduled_tasks())
        await self._poll_balance()
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
        """定时刷盘：先同步磁盘最新数据再写回，避免覆盖 AiStatsRecorder 的写入。"""
        self._load_ai_stats_json()
        self._persist_ai_stats()

    async def on_close(self) -> None:
        """插件卸载或进程退出前全量保存。"""
        self._load_ai_stats_json()
        self._persist_ai_stats()

    def _save_data_to_file(self, data: dict, file_path: str) -> bool:
        """简单的数据保存（原子写入）。"""
        try:
            ok = atomic_write_json(file_path, data, encoder=DateTimeEncoder)
            if not ok:
                _log.error(f"[AiStats] 保存数据失败: {file_path}")
            return ok
        except Exception as e:
            _log.error(f"[AiStats] 保存数据失败: {e}")
            return False

    def _load_ai_stats_json(self):
        """从 data/json 加载 AI 统计（勿命名为 _load_data，以免覆盖 DataMixin 的 data.json 加载）。"""
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
                        _log.debug(
                            "[AiStats] 从文件读取到群组数据: %s",
                            list(group_stats_data.keys()),
                        )

                        for k, v in group_stats_data.items():
                            try:
                                group_id = k  # 直接使用字符串，不转换
                                _log.debug("[AiStats] 正在加载群组 %s 的数据", group_id)
                                if not isinstance(v, dict):
                                    continue
                                new_group_stats[group_id] = AiUsageStats.from_dict(v)
                                _log.debug("[AiStats] 成功加载群组 %s 的数据", group_id)
                            except Exception as e:
                                _log.error(f"[AiStats] 加载群组 {k} 数据失败: {e}")
                                _log.error(f"[AiStats] 群组 {k} 的原始数据: {v}")
                                continue

                        # 合并数据而不是直接替换，避免覆盖现有数据
                        for group_id, stats in new_group_stats.items():
                            self.group_stats[group_id] = stats

                        _log.info(
                            "[AiStats] 成功加载群组数据: %s",
                            list(self.group_stats.keys()),
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

    def _persist_ai_stats(self, group_id: str = None):
        """将 AI 统计写入 data/json（勿命名为 _save_data，以免覆盖 DataMixin）。"""
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

    async def _poll_balance(self) -> None:
        """定时采样 API 账户余额。"""
        try:
            balance_data = await AiUtil.get_deepseek_balance()
            record_balance_snapshot(balance_data)
            if "error" not in balance_data:
                _log.debug("[AiStats] 余额采样成功")
            else:
                _log.warning(f"[AiStats] 余额采样失败: {balance_data.get('error')}")
        except Exception as e:
            _log.error(f"[AiStats] 余额采样异常: {e}")

    async def _finalize_daily_balance(self) -> None:
        """日结：刷新余额并记录前一日账户消耗。"""
        await self._poll_balance()
        _log.info("[AiStats] 日结余额采样完成")

    def record_ai_usage(
        self,
        group_id: str,
        user_id: str,
        tokens: int = 0,
        trigger_type: str = "active",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        prompt_cache_hit_tokens: int = 0,
        model: str = "deepseek-v4-flash",
    ):
        """记录AI使用情况（写入 JSON 并刷新内存）。"""
        del trigger_type
        recorder_record_usage(
            group_id=group_id,
            user_id=user_id,
            tokens=tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prompt_cache_hit_tokens=prompt_cache_hit_tokens,
            model=model,
        )
        self._load_ai_stats_json()

    def _get_time_range_stats(self, stats: AiUsageStats, days: int) -> Dict[str, int]:
        """获取指定时间范围内的统计"""
        if days is None:
            return stats.daily_counts.copy()
        return filter_daily_by_period(stats.daily_counts, days)

    def _get_time_range_cost(self, stats: AiUsageStats, days: int) -> Dict[str, float]:
        """获取指定时间范围内的费用统计"""
        if days is None:
            return {k: float(v) for k, v in stats.daily_cost.copy().items()}
        return {
            k: float(v)
            for k, v in filter_daily_by_period(stats.daily_cost, days).items()
        }

    def _days_label(self, days: Optional[int]) -> str:
        if days == 1:
            return "今日"
        if days == 7:
            return "本周"
        if days == 30:
            return "本月"
        if days is None:
            return "全部"
        return f"近{days}天"

    def _get_account_summary_data(self, days: Optional[int]) -> dict:
        """获取账户余额与消耗摘要（供图片渲染）。"""
        summary = get_balance_summary()
        last = summary.get("last_snapshot") or {}
        daily_account = summary.get("daily_account", {})
        currency = last.get("currency", "CNY") if last else "CNY"

        period_spend = 0.0
        if days is None:
            for acct in daily_account.values():
                period_spend += float(acct.get("actual_spend", 0))
        elif days == 1:
            today = datetime.now().date().isoformat()
            acct = daily_account.get(today, {})
            period_spend = float(acct.get("actual_spend", 0))
        elif days is not None:
            for date_str, acct in daily_account.items():
                if is_date_in_period(date_str, days):
                    period_spend += float(acct.get("actual_spend", 0))
                    currency = acct.get("currency", currency)

        return {
            "balance": float(last["total_balance"])
            if last.get("total_balance") is not None
            else None,
            "currency": currency,
            "is_available": last.get("is_available"),
            "period_spend": period_spend,
            "period_label": self._days_label(days),
        }

    def _load_card_fonts(self):
        from PIL import ImageFont

        try:
            return {
                "title": ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 26),
                "subtitle": ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 13),
                "name": ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 15),
                "detail": ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 12),
                "metric": ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 13),
                "rank": ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 16),
                "footer_num": ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 28),
                "footer_label": ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 11),
                "big_num": ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 36),
            }
        except Exception:
            default = ImageFont.load_default()
            return {
                k: default
                for k in (
                    "title",
                    "subtitle",
                    "name",
                    "detail",
                    "metric",
                    "rank",
                    "footer_num",
                    "footer_label",
                    "big_num",
                )
            }

    def _draw_rounded_rectangle(
        self, draw, x1, y1, x2, y2, radius, fill=None, outline=None
    ):
        draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill, outline=outline)
        draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill, outline=outline)
        draw.pieslice([x1, y1, x1 + 2 * radius, y1 + 2 * radius], 180, 270, fill=fill)
        draw.pieslice([x2 - 2 * radius, y1, x2, y1 + 2 * radius], 270, 360, fill=fill)
        draw.pieslice([x1, y2 - 2 * radius, x1 + 2 * radius, y2], 90, 180, fill=fill)
        draw.pieslice([x2 - 2 * radius, y2 - 2 * radius, x2, y2], 0, 90, fill=fill)

    def _circle_crop_avatar(self, img, size=42):
        img = img.resize((size, size), PILImage.LANCZOS).convert("RGBA")
        mask = PILImage.new("L", (size, size), 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((0, 0, size, size), fill=255)
        img.putalpha(mask)
        return img

    def _save_card_image(self, img: PILImage.Image, prefix: str) -> str:
        path = os.path.join(
            "data",
            "image",
            "temp",
            f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png",
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        img.save(path, quality=95)
        return path

    def _draw_source_grid(
        self,
        draw,
        img: PILImage.Image,
        fonts: dict,
        x: int,
        y: int,
        width: int,
        source_rows: List[dict],
    ) -> tuple[PILImage.Image, int]:
        """绘制 2x2 来源消耗面板，返回更新后的 img 与占用高度。"""
        panel_colors = {
            "active": (108, 87, 245),
            "passive": (72, 149, 239),
            "summary": (46, 184, 134),
            "impression": (255, 159, 67),
        }
        gap = 10
        cols, rows_n = 2, 2
        panel_w = (width - gap) // cols
        panel_h = 58

        draw.text((x, y), "消耗来源", fill=(51, 51, 51), font=fonts["metric"])
        y += 22

        row_map = {r["key"]: r for r in source_rows}
        for idx, key in enumerate(SOURCE_ROLLUP_ORDER):
            row = row_map.get(key)
            if not row:
                label = SOURCE_ROLLUP.get(key, (key, []))[0]
                row = {"key": key, "label": label, "count": 0, "tokens": 0, "cost": 0.0}

            col = idx % cols
            row_i = idx // cols
            px = x + col * (panel_w + gap)
            py = y + row_i * (panel_h + gap)
            color = panel_colors.get(key, (120, 120, 140))

            self._draw_rounded_rectangle(
                draw, px, py, px + panel_w, py + panel_h, 10, fill=(248, 248, 252)
            )
            self._draw_rounded_rectangle(
                draw, px, py, px + 5, py + panel_h, 2, fill=color
            )

            draw.text((px + 12, py + 8), row["label"], fill=color, font=fonts["metric"])
            stat = (
                f"{row['count']} 次  ·  {row['tokens']:,} tok  ·  "
                f"{self._format_money(row['cost'])}"
            )
            draw.text(
                (px + 12, py + 30), stat, fill=(100, 100, 110), font=fonts["detail"]
            )

        return img, 22 + rows_n * (panel_h + gap) + 8

    def _generate_detail_card(
        self,
        ranking: List[dict],
        user_names: Dict[str, str],
        days: Optional[int],
        account: dict,
        source_rows: List[dict],
        group_total_count: int = 0,
        group_total_tokens: int = 0,
        group_total_cost: float = 0.0,
        top_n: int = 15,
    ) -> Optional[str]:
        """生成 AI 使用明细图片：来源面板 + 用户排行。"""
        if not ranking and not source_rows:
            return None

        from PIL import ImageFont  # noqa: F401

        fonts = self._load_card_fonts()
        top_items = ranking[:top_n]

        card_width = 580
        padding = 28
        card_padding = 18
        source_section_h = 22 + 2 * (58 + 10) + 8  # 标题 + 2x2 面板
        user_title_h = 26 if top_items else 0
        item_height = 96
        footer_height = 88
        header_height = 98

        card_content_height = (
            header_height
            + source_section_h
            + user_title_h
            + len(top_items) * item_height
            + footer_height
        )
        total_height = card_content_height + 2 * card_padding + 40

        img = PILImage.new("RGB", (card_width, total_height))
        draw = ImageDraw.Draw(img)

        for y_pos in range(total_height):
            ratio = y_pos / max(total_height - 1, 1)
            r = int(108 + (88 - 108) * ratio)
            g = int(87 + (85 - 87) * ratio)
            b = int(245 + (214 - 245) * ratio)
            draw.line([(0, y_pos), (card_width, y_pos)], fill=(r, g, b))

        card_x = card_padding
        card_y = card_padding
        inner_w = card_width - 2 * card_padding
        inner_h = card_content_height + 16
        self._draw_rounded_rectangle(
            draw,
            card_x,
            card_y,
            card_x + inner_w,
            card_y + inner_h,
            18,
            fill=(255, 255, 255),
        )

        title = f"{self._days_label(days)} AI 使用明细"
        title_bbox = draw.textbbox((0, 0), title, font=fonts["title"])
        title_x = card_x + (inner_w - (title_bbox[2] - title_bbox[0])) // 2
        draw.text((title_x, card_y + 16), title, fill=(51, 51, 51), font=fonts["title"])

        date_text = datetime.now().strftime("%Y-%m-%d %H:%M")
        date_bbox = draw.textbbox((0, 0), date_text, font=fonts["subtitle"])
        date_x = card_x + (inner_w - (date_bbox[2] - date_bbox[0])) // 2
        draw.text(
            (date_x, card_y + 48),
            date_text,
            fill=(160, 160, 160),
            font=fonts["subtitle"],
        )

        acct_parts = []
        if account.get("balance") is not None:
            acct_parts.append(
                f"余额 {self._format_money(account['balance'], account['currency'])}"
            )
        acct_parts.append(
            f"{account['period_label']}消耗 "
            f"{self._format_money(account['period_spend'], account['currency'])}"
        )
        acct_text = "  ·  ".join(acct_parts)
        acct_bbox = draw.textbbox((0, 0), acct_text, font=fonts["subtitle"])
        acct_x = card_x + (inner_w - (acct_bbox[2] - acct_bbox[0])) // 2
        draw.text(
            (acct_x, card_y + 68),
            acct_text,
            fill=(108, 87, 245),
            font=fonts["subtitle"],
        )

        cursor_y = card_y + header_height
        img, used_h = self._draw_source_grid(
            draw,
            img,
            fonts,
            card_x + padding,
            cursor_y,
            inner_w - 2 * padding,
            source_rows,
        )
        draw = ImageDraw.Draw(img)
        cursor_y += used_h

        draw.line(
            [(card_x + padding, cursor_y), (card_x + inner_w - padding, cursor_y)],
            fill=(235, 235, 245),
            width=1,
        )
        cursor_y += 12

        if top_items:
            draw.text(
                (card_x + padding, cursor_y),
                "用户消耗排行",
                fill=(51, 51, 51),
                font=fonts["metric"],
            )
            cursor_y += user_title_h

        badge_colors = [
            (255, 193, 7),  # 金色 - 第1名
            (192, 192, 192),  # 银色 - 第2名
            (205, 127, 50),  # 铜色 - 第3名
        ]
        purples = [
            (147, 112, 219),
            (138, 107, 255),
            (123, 97, 255),
            (108, 87, 245),
            (93, 77, 235),
            (78, 67, 225),
            (63, 57, 215),
        ]

        items_start_y = cursor_y
        for i, item in enumerate(top_items):
            item_y = items_start_y + i * item_height
            user_id = item["user_id"]
            name = user_names.get(str(user_id), str(user_id))
            if len(name) > 16:
                name = name[:15] + "…"

            badge_color = (
                badge_colors[i] if i < 3 else purples[min(i - 3, len(purples) - 1)]
            )
            badge_x = card_x + padding
            badge_y = item_y + 14
            badge_size = 34
            self._draw_rounded_rectangle(
                draw,
                badge_x,
                badge_y,
                badge_x + badge_size,
                badge_y + badge_size,
                8,
                fill=badge_color,
            )
            rank_text = str(i + 1)
            rank_bbox = draw.textbbox((0, 0), rank_text, font=fonts["rank"])
            draw.text(
                (
                    badge_x + (badge_size - (rank_bbox[2] - rank_bbox[0])) // 2,
                    badge_y + (badge_size - (rank_bbox[3] - rank_bbox[1])) // 2 - 2,
                ),
                rank_text,
                fill=(255, 255, 255),
                font=fonts["rank"],
            )

            avatar_x = badge_x + badge_size + 12
            avatar_y = item_y + 10
            avatar_size = 40
            try:
                avatar_path = CommonUtil.get_avatar(user_id)
                avatar = self._circle_crop_avatar(
                    PILImage.open(avatar_path), avatar_size
                )
                img_rgba = img.convert("RGBA")
                img_rgba.paste(avatar, (avatar_x, avatar_y), avatar)
                img = img_rgba.convert("RGB")
                draw = ImageDraw.Draw(img)
            except Exception:
                draw.ellipse(
                    [
                        avatar_x,
                        avatar_y,
                        avatar_x + avatar_size,
                        avatar_y + avatar_size,
                    ],
                    fill=(220, 220, 220),
                )

            name_x = avatar_x + avatar_size + 12
            draw.text(
                (name_x, item_y + 12), name, fill=(51, 51, 51), font=fonts["name"]
            )

            pct = (item["cost"] / group_total_cost * 100) if group_total_cost > 0 else 0
            bar_x, bar_y = name_x, item_y + 36
            bar_w, bar_h = 200, 7
            self._draw_rounded_rectangle(
                draw,
                bar_x,
                bar_y,
                bar_x + bar_w,
                bar_y + bar_h,
                3,
                fill=(235, 235, 245),
            )
            fill_w = max(int(bar_w * pct / 100), 6) if pct > 0 else 0
            if fill_w:
                self._draw_rounded_rectangle(
                    draw,
                    bar_x,
                    bar_y,
                    bar_x + fill_w,
                    bar_y + bar_h,
                    3,
                    fill=badge_color,
                )

            detail = (
                f"调用 {item['count']} 次  ·  "
                f"Token {item['tokens']:,}  ·  "
                f"{self._format_money(item['cost'])}"
            )
            draw.text(
                (name_x, item_y + 50),
                detail,
                fill=(120, 120, 130),
                font=fonts["detail"],
            )

            src_tag = item.get("dominant_source") or ""
            sub = f"输入 {item['prompt_tokens']:,} / 输出 {item['completion_tokens']:,}"
            if src_tag:
                sub = f"{src_tag}  ·  {sub}"
            sub_bbox = draw.textbbox((0, 0), sub, font=fonts["detail"])
            sub_x = card_x + inner_w - padding - (sub_bbox[2] - sub_bbox[0])
            draw.text(
                (sub_x, item_y + 68), sub, fill=(160, 160, 170), font=fonts["detail"]
            )

            if i < len(top_items) - 1:
                line_y = item_y + item_height - 4
                draw.line(
                    [(card_x + padding, line_y), (card_x + inner_w - padding, line_y)],
                    fill=(240, 240, 245),
                    width=1,
                )

        footer_y = items_start_y + len(top_items) * item_height + 8
        draw.line(
            [(card_x + padding, footer_y), (card_x + inner_w - padding, footer_y)],
            fill=(230, 230, 240),
            width=2,
        )
        footer_y += 14

        footer_items = [
            (str(group_total_count), "总调用"),
            (f"{group_total_tokens:,}", "总 Token"),
            (self._format_money(group_total_cost), "总费用"),
        ]
        col_w = inner_w // 3
        for idx, (num, label) in enumerate(footer_items):
            col_x = card_x + idx * col_w + col_w // 2
            num_bbox = draw.textbbox((0, 0), num, font=fonts["footer_num"])
            draw.text(
                (col_x - (num_bbox[2] - num_bbox[0]) // 2, footer_y),
                num,
                fill=(108, 87, 245),
                font=fonts["footer_num"],
            )
            lbl_bbox = draw.textbbox((0, 0), label, font=fonts["footer_label"])
            draw.text(
                (col_x - (lbl_bbox[2] - lbl_bbox[0]) // 2, footer_y + 36),
                label,
                fill=(150, 150, 160),
                font=fonts["footer_label"],
            )

        if len(ranking) > top_n:
            note = f"仅显示前 {top_n} 名，共 {len(ranking)} 人"
            note_bbox = draw.textbbox((0, 0), note, font=fonts["subtitle"])
            draw.text(
                (
                    card_x + (inner_w - (note_bbox[2] - note_bbox[0])) // 2,
                    footer_y + 58,
                ),
                note,
                fill=(180, 180, 180),
                font=fonts["subtitle"],
            )

        return self._save_card_image(img, "ai_detail")

    def _get_group_cost_ranking(self, days: Optional[int]) -> List[dict]:
        """按费用汇总各群排行（不含成员明细）。"""
        result = []
        for group_id, stats in self.group_stats.items():
            count = stats.get_count(days)
            if count <= 0:
                continue
            source_rows = get_rollup_breakdown(stats.to_dict(), days)
            dominant = ""
            if source_rows:
                dominant = max(source_rows, key=lambda r: r.get("tokens", 0)).get(
                    "label", ""
                )
            result.append(
                {
                    "group_id": group_id,
                    "count": count,
                    "tokens": stats.get_tokens(days),
                    "prompt_tokens": stats.get_prompt_tokens(days),
                    "completion_tokens": stats.get_completion_tokens(days),
                    "cost": stats.get_cost(days),
                    "dominant_source": dominant,
                }
            )
        result.sort(key=lambda x: (x["cost"], x["tokens"]), reverse=True)
        return result

    def _aggregate_source_rows(self, days: Optional[int]) -> List[dict]:
        """汇总所有群的来源消耗。"""
        totals: Dict[str, dict] = {}
        for rollup_key in SOURCE_ROLLUP_ORDER:
            label = SOURCE_ROLLUP[rollup_key][0]
            totals[rollup_key] = {
                "key": rollup_key,
                "label": label,
                "count": 0,
                "tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost": 0.0,
            }
        for stats in self.group_stats.values():
            for row in get_rollup_breakdown(stats.to_dict(), days):
                bucket = totals.get(row["key"])
                if not bucket:
                    continue
                for field in ("count", "tokens", "prompt_tokens", "completion_tokens"):
                    bucket[field] += row[field]
                bucket["cost"] = round(bucket["cost"] + row["cost"], 6)
        return [totals[key] for key in SOURCE_ROLLUP_ORDER]

    def _generate_overview_card(
        self,
        ranking: List[dict],
        group_names: Dict[str, str],
        days: Optional[int],
        account: dict,
        source_rows: List[dict],
        total_count: int = 0,
        total_tokens: int = 0,
        total_cost: float = 0.0,
        active_groups: int = 0,
        top_n: int = 15,
    ) -> Optional[str]:
        """生成全群 AI 使用总览图片：来源面板 + 群组排行（无成员明细）。"""
        if not ranking and not source_rows:
            return None

        fonts = self._load_card_fonts()
        top_items = ranking[:top_n]

        card_width = 580
        padding = 28
        card_padding = 18
        source_section_h = 22 + 2 * (58 + 10) + 8
        group_title_h = 26 if top_items else 0
        item_height = 72
        footer_height = 88
        header_height = 98

        card_content_height = (
            header_height
            + source_section_h
            + group_title_h
            + len(top_items) * item_height
            + footer_height
        )
        total_height = card_content_height + 2 * card_padding + 40

        img = PILImage.new("RGB", (card_width, total_height))
        draw = ImageDraw.Draw(img)

        for y_pos in range(total_height):
            ratio = y_pos / max(total_height - 1, 1)
            r = int(108 + (88 - 108) * ratio)
            g = int(87 + (85 - 87) * ratio)
            b = int(245 + (214 - 245) * ratio)
            draw.line([(0, y_pos), (card_width, y_pos)], fill=(r, g, b))

        card_x = card_padding
        card_y = card_padding
        inner_w = card_width - 2 * card_padding
        inner_h = card_content_height + 16
        self._draw_rounded_rectangle(
            draw,
            card_x,
            card_y,
            card_x + inner_w,
            card_y + inner_h,
            18,
            fill=(255, 255, 255),
        )

        title = f"{self._days_label(days)} AI 使用总览"
        title_bbox = draw.textbbox((0, 0), title, font=fonts["title"])
        title_x = card_x + (inner_w - (title_bbox[2] - title_bbox[0])) // 2
        draw.text((title_x, card_y + 16), title, fill=(51, 51, 51), font=fonts["title"])

        date_text = datetime.now().strftime("%Y-%m-%d %H:%M")
        date_bbox = draw.textbbox((0, 0), date_text, font=fonts["subtitle"])
        date_x = card_x + (inner_w - (date_bbox[2] - date_bbox[0])) // 2
        draw.text(
            (date_x, card_y + 48),
            date_text,
            fill=(160, 160, 160),
            font=fonts["subtitle"],
        )

        acct_parts = []
        if account.get("balance") is not None:
            acct_parts.append(
                f"余额 {self._format_money(account['balance'], account['currency'])}"
            )
        acct_parts.append(
            f"{account['period_label']}消耗 "
            f"{self._format_money(account['period_spend'], account['currency'])}"
        )
        if active_groups > 0:
            acct_parts.append(f"{active_groups} 个活跃群")
        acct_text = "  ·  ".join(acct_parts)
        acct_bbox = draw.textbbox((0, 0), acct_text, font=fonts["subtitle"])
        acct_x = card_x + (inner_w - (acct_bbox[2] - acct_bbox[0])) // 2
        draw.text(
            (acct_x, card_y + 68),
            acct_text,
            fill=(108, 87, 245),
            font=fonts["subtitle"],
        )

        cursor_y = card_y + header_height
        img, used_h = self._draw_source_grid(
            draw,
            img,
            fonts,
            card_x + padding,
            cursor_y,
            inner_w - 2 * padding,
            source_rows,
        )
        draw = ImageDraw.Draw(img)
        cursor_y += used_h

        draw.line(
            [(card_x + padding, cursor_y), (card_x + inner_w - padding, cursor_y)],
            fill=(235, 235, 245),
            width=1,
        )
        cursor_y += 12

        if top_items:
            draw.text(
                (card_x + padding, cursor_y),
                "群组消耗排行",
                fill=(51, 51, 51),
                font=fonts["metric"],
            )
            cursor_y += group_title_h

        badge_colors = [
            (255, 193, 7),
            (192, 192, 192),
            (205, 127, 50),
        ]
        purples = [
            (147, 112, 219),
            (138, 107, 255),
            (123, 97, 255),
            (108, 87, 245),
            (93, 77, 235),
            (78, 67, 225),
            (63, 57, 215),
        ]

        items_start_y = cursor_y
        for i, item in enumerate(top_items):
            item_y = items_start_y + i * item_height
            group_id = item["group_id"]
            name = group_names.get(str(group_id), str(group_id))
            if len(name) > 20:
                name = name[:19] + "…"

            badge_color = (
                badge_colors[i] if i < 3 else purples[min(i - 3, len(purples) - 1)]
            )
            badge_x = card_x + padding
            badge_y = item_y + 10
            badge_size = 34
            self._draw_rounded_rectangle(
                draw,
                badge_x,
                badge_y,
                badge_x + badge_size,
                badge_y + badge_size,
                8,
                fill=badge_color,
            )
            rank_text = str(i + 1)
            rank_bbox = draw.textbbox((0, 0), rank_text, font=fonts["rank"])
            draw.text(
                (
                    badge_x + (badge_size - (rank_bbox[2] - rank_bbox[0])) // 2,
                    badge_y + (badge_size - (rank_bbox[3] - rank_bbox[1])) // 2 - 2,
                ),
                rank_text,
                fill=(255, 255, 255),
                font=fonts["rank"],
            )

            name_x = badge_x + badge_size + 12
            draw.text((name_x, item_y + 8), name, fill=(51, 51, 51), font=fonts["name"])

            pct = (item["cost"] / total_cost * 100) if total_cost > 0 else 0
            bar_x, bar_y = name_x, item_y + 30
            bar_w, bar_h = 220, 7
            self._draw_rounded_rectangle(
                draw,
                bar_x,
                bar_y,
                bar_x + bar_w,
                bar_y + bar_h,
                3,
                fill=(235, 235, 245),
            )
            fill_w = max(int(bar_w * pct / 100), 6) if pct > 0 else 0
            if fill_w:
                self._draw_rounded_rectangle(
                    draw,
                    bar_x,
                    bar_y,
                    bar_x + fill_w,
                    bar_y + bar_h,
                    3,
                    fill=badge_color,
                )

            detail = (
                f"调用 {item['count']} 次  ·  "
                f"Token {item['tokens']:,}  ·  "
                f"{self._format_money(item['cost'])}"
            )
            draw.text(
                (name_x, item_y + 44),
                detail,
                fill=(120, 120, 130),
                font=fonts["detail"],
            )

            src_tag = item.get("dominant_source") or ""
            sub = f"输入 {item['prompt_tokens']:,} / 输出 {item['completion_tokens']:,}"
            if src_tag:
                sub = f"{src_tag}  ·  {sub}"
            sub_bbox = draw.textbbox((0, 0), sub, font=fonts["detail"])
            sub_x = card_x + inner_w - padding - (sub_bbox[2] - sub_bbox[0])
            draw.text(
                (sub_x, item_y + 52), sub, fill=(160, 160, 170), font=fonts["detail"]
            )

            if i < len(top_items) - 1:
                line_y = item_y + item_height - 4
                draw.line(
                    [(card_x + padding, line_y), (card_x + inner_w - padding, line_y)],
                    fill=(240, 240, 245),
                    width=1,
                )

        footer_y = items_start_y + len(top_items) * item_height + 8
        draw.line(
            [(card_x + padding, footer_y), (card_x + inner_w - padding, footer_y)],
            fill=(230, 230, 240),
            width=2,
        )
        footer_y += 14

        footer_items = [
            (str(total_count), "总调用"),
            (f"{total_tokens:,}", "总 Token"),
            (self._format_money(total_cost), "总费用"),
        ]
        col_w = inner_w // 3
        for idx, (num, label) in enumerate(footer_items):
            col_x = card_x + idx * col_w + col_w // 2
            num_bbox = draw.textbbox((0, 0), num, font=fonts["footer_num"])
            draw.text(
                (col_x - (num_bbox[2] - num_bbox[0]) // 2, footer_y),
                num,
                fill=(108, 87, 245),
                font=fonts["footer_num"],
            )
            lbl_bbox = draw.textbbox((0, 0), label, font=fonts["footer_label"])
            draw.text(
                (col_x - (lbl_bbox[2] - lbl_bbox[0]) // 2, footer_y + 36),
                label,
                fill=(150, 150, 160),
                font=fonts["footer_label"],
            )

        if len(ranking) > top_n:
            note = f"仅显示前 {top_n} 个群，共 {len(ranking)} 个群有记录"
            note_bbox = draw.textbbox((0, 0), note, font=fonts["subtitle"])
            draw.text(
                (
                    card_x + (inner_w - (note_bbox[2] - note_bbox[0])) // 2,
                    footer_y + 58,
                ),
                note,
                fill=(180, 180, 180),
                font=fonts["subtitle"],
            )

        return self._save_card_image(img, "ai_overview")

    def _generate_personal_card(
        self,
        user_id: str,
        display_name: str,
        total_count: int,
        total_tokens: int,
        total_prompt: int,
        total_completion: int,
        total_cost: float,
        days: Optional[int],
        source_rows: List[dict],
    ) -> str:
        """生成个人 AI 统计卡片图片（含来源分布）。"""
        fonts = self._load_card_fonts()

        card_width = 480
        source_section_h = 22 + 2 * (58 + 10) + 8
        card_height = 300 + source_section_h
        card_padding = 20

        img = PILImage.new("RGB", (card_width, card_height))
        draw = ImageDraw.Draw(img)

        for y in range(card_height):
            ratio = y / max(card_height - 1, 1)
            r = int(138 + (108 - 138) * ratio)
            g = int(107 + (87 - 107) * ratio)
            b = int(255 + (245 - 255) * ratio)
            draw.line([(0, y), (card_width, y)], fill=(r, g, b))

        inner_w = card_width - 2 * card_padding
        inner_h = card_height - 2 * card_padding
        self._draw_rounded_rectangle(
            draw,
            card_padding,
            card_padding,
            card_padding + inner_w,
            card_padding + inner_h,
            18,
            fill=(255, 255, 255),
        )

        cx = card_padding + inner_w // 2
        title = f"{self._days_label(days)} 个人 AI 统计"
        title_bbox = draw.textbbox((0, 0), title, font=fonts["title"])
        draw.text(
            (cx - (title_bbox[2] - title_bbox[0]) // 2, card_padding + 20),
            title,
            fill=(51, 51, 51),
            font=fonts["title"],
        )

        avatar_size = 64
        avatar_x = cx - avatar_size // 2
        avatar_y = card_padding + 58
        try:
            avatar = self._circle_crop_avatar(
                PILImage.open(CommonUtil.get_avatar(user_id)), avatar_size
            )
            img_rgba = img.convert("RGBA")
            img_rgba.paste(avatar, (avatar_x, avatar_y), avatar)
            img = img_rgba.convert("RGB")
            draw = ImageDraw.Draw(img)
        except Exception:
            draw.ellipse(
                [avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size],
                fill=(220, 220, 220),
            )

        if len(display_name) > 14:
            display_name = display_name[:13] + "…"
        name_bbox = draw.textbbox((0, 0), display_name, font=fonts["name"])
        draw.text(
            (cx - (name_bbox[2] - name_bbox[0]) // 2, avatar_y + avatar_size + 10),
            display_name,
            fill=(80, 80, 90),
            font=fonts["name"],
        )

        metrics_y = avatar_y + avatar_size + 38
        metrics = [
            (str(total_count), "调用次数"),
            (f"{total_tokens:,}", "总 Token"),
            (self._format_money(total_cost), "估算费用"),
        ]
        col_w = inner_w // 3
        for idx, (num, label) in enumerate(metrics):
            col_x = card_padding + idx * col_w + col_w // 2
            num_bbox = draw.textbbox((0, 0), num, font=fonts["footer_num"])
            draw.text(
                (col_x - (num_bbox[2] - num_bbox[0]) // 2, metrics_y),
                num,
                fill=(108, 87, 245),
                font=fonts["footer_num"],
            )
            lbl_bbox = draw.textbbox((0, 0), label, font=fonts["footer_label"])
            draw.text(
                (col_x - (lbl_bbox[2] - lbl_bbox[0]) // 2, metrics_y + 34),
                label,
                fill=(150, 150, 160),
                font=fonts["footer_label"],
            )

        token_line = f"输入 {total_prompt:,} / 输出 {total_completion:,} Token"
        token_bbox = draw.textbbox((0, 0), token_line, font=fonts["detail"])
        draw.text(
            (cx - (token_bbox[2] - token_bbox[0]) // 2, metrics_y + 68),
            token_line,
            fill=(130, 130, 140),
            font=fonts["detail"],
        )

        grid_y = metrics_y + 92
        img, _ = self._draw_source_grid(
            draw, img, fonts, card_padding + 8, grid_y, inner_w - 16, source_rows
        )

        return self._save_card_image(img, "ai_personal")

    def _format_money(self, amount: float, currency: str = "CNY") -> str:
        symbol = "¥" if currency == "CNY" else ("$" if currency == "USD" else "")
        return f"{symbol}{amount:.4f}"

    @staticmethod
    def _safe_display_name(name, user_id) -> str:
        if name:
            return str(name).encode("utf-8", errors="ignore").decode("utf-8")
        return str(user_id)

    async def _resolve_user_names(
        self, group_id: int, user_ids: List[str]
    ) -> Dict[str, str]:
        user_names = {str(uid): str(uid) for uid in user_ids}
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
            _log.error(f"获取群成员列表失败: {e}")

        for uid in user_names:
            if user_names[uid] != uid:
                continue
            try:
                user_info = await self.api.qq.query.get_group_member_info(
                    group_id=group_id, user_id=int(uid), no_cache=True
                )
                user_data = {}
                if user_info:
                    if hasattr(user_info, "model_dump"):
                        user_data = user_info.model_dump()
                    elif isinstance(user_info, dict):
                        user_data = user_info.get("data", user_info)
                nickname = user_data.get("card") or user_data.get("nickname")
                user_names[uid] = self._safe_display_name(nickname, uid)
            except Exception:
                pass

        return user_names

    async def _resolve_group_names(self, group_ids: List[str]) -> Dict[str, str]:
        group_names = {str(gid): str(gid) for gid in group_ids}
        try:
            groups = await self.api.qq.query.get_group_list()
            for group in groups or []:
                gid = str(group.group_id)
                if gid in group_names and group.group_name:
                    group_names[gid] = self._safe_display_name(group.group_name, gid)
        except Exception as e:
            _log.error(f"获取群列表失败: {e}")

        for gid in group_names:
            if group_names[gid] != gid:
                continue
            try:
                info = await self.api.qq.query.get_group_info(group_id=int(gid))
                if info and info.group_name:
                    group_names[gid] = self._safe_display_name(info.group_name, gid)
            except Exception:
                pass

        return group_names

    def _get_time_range_tokens(self, stats: AiUsageStats, days: int) -> Dict[str, int]:
        """获取指定时间范围内的token统计"""
        if days is None:
            return stats.daily_tokens.copy()
        return filter_daily_by_period(stats.daily_tokens, days)

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
        names = [user_names.get(str(uid), str(uid)) for uid, _ in top_items]
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
                _log.warning(f"头像异常: {user_id} {e}")
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
                _log.warning(f"头像PIL粘贴异常: {user_id} {e}")
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

        return [Image(file=temp_path)]

    @registrar.qq.on_group_message()
    async def handle_ai_stats(plugin_instance, input: GroupMessage) -> None:
        """处理AI统计命令"""
        message = input.raw_message.strip()
        if not message.startswith("ai统计"):
            return

        # 分割命令，处理多个空格的情况
        message_parts = [part for part in message.split() if part]
        if len(message_parts) < 3:
            await input.reply(
                "命令格式：ai统计 [时间范围] [统计对象]\n"
                "时间范围：今日、本周、本月、全部\n"
                "统计对象：群组、个人、明细、总览、全群"
            )
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

        if target not in ["群组", "个人", "明细", "总览", "全群"]:
            await input.reply("无效的统计对象，请使用：群组、个人、明细、总览、全群")
            return

        if target == "明细":
            await plugin_instance._show_detail_stats(input, days)
            return

        if target in ("总览", "全群"):
            await plugin_instance._show_overview_stats(input, days)
            return

        # 使用传入的插件实例
        await plugin_instance._show_stats(input, days, target, target_user_id)

    async def _show_detail_stats(self, input: GroupMessage, days: int) -> None:
        """按用户生成 token 与费用明细图片。"""
        await self.api.qq.post_group_msg(
            input.group_id,
            rtf=MessageChain([PlainText(text="正在生成 AI 明细图表，请稍候...")]),
        )

        group_id = str(input.group_id)
        self._load_ai_stats_json()
        await self._poll_balance()

        user_stats_dict = {
            gid: {uid: s.to_dict() for uid, s in users.items()}
            for gid, users in self.user_stats.items()
        }
        ranking = get_user_cost_ranking(user_stats_dict, group_id, days)
        group_stat = self.group_stats.get(group_id)
        source_rows = (
            get_rollup_breakdown(group_stat.to_dict(), days, include_zero=True)
            if group_stat
            else get_rollup_breakdown({}, days, include_zero=True)
        )
        group_total_count = group_stat.get_count(days) if group_stat else 0
        group_total_tokens = group_stat.get_tokens(days) if group_stat else 0
        group_total_cost = group_stat.get_cost(days) if group_stat else 0.0

        if group_total_count == 0 and not ranking:
            await self.api.qq.post_group_msg(
                input.group_id,
                rtf=MessageChain([PlainText(text="暂无 AI 使用明细数据")]),
                reply=input.message_id,
            )
            return

        user_ids = [item["user_id"] for item in ranking]
        user_names = await self._resolve_user_names(input.group_id, user_ids)
        account = self._get_account_summary_data(days)
        if account.get("period_spend", 0) == 0 and group_total_cost > 0:
            account = {**account, "period_spend": group_total_cost}

        card_path = self._generate_detail_card(
            ranking,
            user_names,
            days,
            account,
            source_rows,
            group_total_count=group_total_count,
            group_total_tokens=group_total_tokens,
            group_total_cost=group_total_cost,
            top_n=15,
        )
        if not card_path:
            await input.reply("生成明细图表失败，请稍后重试")
            return

        await self.api.qq.post_group_msg(
            input.group_id,
            rtf=MessageChain([Image(file=card_path)]),
            reply=input.message_id,
        )

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

    async def _show_overview_stats(self, input: GroupMessage, days: int) -> None:
        from .report_builder import build_ai_overview_report

        await self.api.qq.post_group_msg(
            input.group_id,
            rtf=MessageChain([PlainText(text="正在生成 AI 总览图表，请稍候...")]),
        )
        self._load_ai_stats_json()
        await self._poll_balance()
        ranking = self._get_group_cost_ranking(days)
        source_rows = self._aggregate_source_rows(days)
        total_count = sum(s.get_count(days) for s in self.group_stats.values())
        total_tokens = sum(s.get_tokens(days) for s in self.group_stats.values())
        total_cost = round(sum(s.get_cost(days) for s in self.group_stats.values()), 4)
        active_groups = len(ranking)
        if total_count == 0 and not ranking:
            await self.api.qq.post_group_msg(
                input.group_id,
                rtf=MessageChain([PlainText(text="暂无 AI 使用总览数据")]),
                reply=input.message_id,
            )
            return
        group_ids = [item["group_id"] for item in ranking]
        group_names = await self._resolve_group_names(group_ids)
        report_paths = await build_ai_overview_report(
            days,
            ranking,
            group_names,
            source_rows,
            {
                "total_count": total_count,
                "total_tokens": total_tokens,
                "total_cost": total_cost,
                "active_groups": active_groups,
            },
        )
        if not report_paths:
            await self.api.qq.post_group_msg(
                input.group_id,
                rtf=MessageChain([PlainText(text="生成总览报告失败，请稍后重试")]),
                reply=input.message_id,
            )
            return
        elements = [
            PlainText(text="=== 全群 AI 使用总览 ===\n总调用次数:\n"),
        ]
        for img in self._number_to_counter(total_count):
            elements.append(img)
        await self.api.qq.post_group_msg(
            input.group_id, rtf=MessageChain(elements), reply=input.message_id
        )
        for report_path in report_paths:
            await self.api.qq.post_group_msg(
                input.group_id,
                rtf=MessageChain([Image(file=report_path)]),
                reply=input.message_id,
            )

    async def _show_stats(
        self, input: GroupMessage, days: int, target: str, target_user_id: int
    ) -> None:
        from .report_builder import build_ai_group_report, build_ai_personal_report

        self._load_ai_stats_json()
        await self.api.qq.post_group_msg(
            input.group_id,
            rtf=MessageChain([PlainText(text="正在生成AI统计图表，请稍候...")]),
        )
        if target == "群组":
            group_id = str(input.group_id)
            if group_id not in self.group_stats:
                self._load_ai_stats_json()
            stats = self.group_stats.get(group_id)
            if not stats:
                await self.api.qq.post_group_msg(
                    input.group_id,
                    rtf=MessageChain([PlainText(text="暂无群组AI统计数据")]),
                )
                return
            total_count = sum(self._get_time_range_stats(stats, days).values())
            user_counts = {}
            for user_id, user_stat in self.user_stats.get(group_id, {}).items():
                user_total = sum(self._get_time_range_stats(user_stat, days).values())
                if user_total > 0:
                    user_counts[user_id] = user_total
            user_names = await self._resolve_user_names(
                input.group_id, list(user_counts.keys())
            )
            source_rows = get_rollup_breakdown(stats.to_dict(), days, include_zero=True)
            report_path = await build_ai_group_report(
                group_id, days, stats, user_counts, user_names, source_rows
            )
            period = period_display_label(days)
            await self._send_flip_and_report(
                input.group_id,
                input.message_id,
                total_count,
                report_path,
                header=f"=== 群组 AI 统计 ===\n{period}调用次数:\n",
            )
        else:
            group_id = str(input.group_id)
            user_id = str(target_user_id)
            user_stat = self.user_stats.get(group_id, {}).get(user_id)
            if not user_stat:
                await input.reply("暂无个人AI使用统计")
                return
            total_count = sum(self._get_time_range_stats(user_stat, days).values())
            names = await self._resolve_user_names(input.group_id, [user_id])
            source_rows = get_rollup_breakdown(
                user_stat.to_dict(), days, include_zero=True
            )
            report_path = await build_ai_personal_report(
                group_id,
                user_id,
                days,
                user_stat,
                names.get(user_id, user_id),
                source_rows,
            )
            period = period_display_label(days)
            await self._send_flip_and_report(
                input.group_id,
                input.message_id,
                total_count,
                report_path,
                header=f"=== 个人 AI 统计 ===\n{period}调用次数:\n",
            )
