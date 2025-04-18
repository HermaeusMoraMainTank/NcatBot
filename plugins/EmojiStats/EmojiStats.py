import os
import json
import hashlib
import requests
import urllib3
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass
from pathlib import Path

from ncatbot.core.element import MessageChain, Text, Image
from ncatbot.plugin import CompatibleEnrollment, BasePlugin
from ncatbot.utils.logger import get_log
from ncatbot.core.message import GroupMessage

# 禁用 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_log = get_log()
bot = CompatibleEnrollment


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
    count: int = 0
    last_used: datetime = datetime.now()

    def to_dict(self) -> dict:
        """将对象转换为字典，处理 datetime 对象"""
        return {
            "url": self.url,
            "cache_path": self.cache_path,
            "count": self.count,
            "last_used": self.last_used.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EmojiStats":
        """从字典创建对象，处理 datetime 字符串"""
        # 确保所有必需的字段都存在
        if not all(k in data for k in ["url", "cache_path"]):
            raise ValueError("缺少必需的字段")

        # 处理可选字段
        count = data.get("count", 0)
        if isinstance(count, str):
            try:
                count = int(count)
            except ValueError:
                count = 0

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
            count=count,
            last_used=last_used,
        )


class EmojiStatsPlugin(BasePlugin):
    name = "EmojiStats"
    version = "1.0"

    # 数据存储路径
    DATA_FILE = "data/json/emoji_stats.json"
    CACHE_DIR = "data/image/emoji_stats"

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
   
   统计对象可选：
   - 群组
   - 个人
   
示例：
- 表情包统计 今日 群组
- 表情包统计 本周 个人
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
            os.makedirs(os.path.dirname(self.DATA_FILE), exist_ok=True)

            # 如果文件不存在，初始化空数据
            if not os.path.exists(self.DATA_FILE):
                _log.info("数据文件不存在，创建新文件")
                self._save_data()
                return

            # 尝试不同的编码方式读取文件
            encodings = ["utf-8", "gbk", "gb2312", "gb18030"]
            for encoding in encodings:
                try:
                    _log.info(f"尝试使用 {encoding} 编码读取文件")
                    with open(self.DATA_FILE, "r", encoding=encoding) as f:
                        content = f.read()
                        _log.info(f"文件内容: {content[:100]}...")  # 只打印前100个字符
                        data = json.loads(content)

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

                        _log.info("数据加载成功")
                        return  # 如果成功读取，直接返回
                except UnicodeDecodeError as e:
                    _log.error(f"使用 {encoding} 编码读取失败: {e}")
                    continue
                except json.JSONDecodeError as e:
                    _log.error(f"JSON解析失败: {e}")
                    continue
                except Exception as e:
                    _log.error(f"加载数据时发生错误: {e}")
                    continue

            # 如果所有编码都失败，创建新的空文件
            _log.warning("无法读取数据文件，将创建新的空文件")
            self._save_data()

        except Exception as e:
            _log.error(f"加载数据失败: {e}")
            # 如果发生其他错误，也创建新的空文件
            self._save_data()

    def _save_data(self):
        """保存数据"""
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(self.DATA_FILE), exist_ok=True)

            # 准备要保存的数据
            data = {
                "group_stats": {
                    str(k): {
                        k2: v2.to_dict()  # 使用自定义的 to_dict 方法
                        for k2, v2 in v.items()
                    }
                    for k, v in self.group_stats.items()
                },
                "user_stats": {
                    str(k): {
                        str(k2): {
                            k3: v3.to_dict()  # 使用自定义的 to_dict 方法
                            for k3, v3 in v2.items()
                        }
                        for k2, v2 in v.items()
                    }
                    for k, v in self.user_stats.items()
                },
                "group_count": {str(k): v for k, v in self.group_count.items()},
                "user_count": {
                    str(k): {str(k2): v2 for k2, v2 in v.items()}
                    for k, v in self.user_count.items()
                },
            }

            # 保存数据，使用自定义的 JSON 编码器
            with open(self.DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, cls=DateTimeEncoder)
            _log.info("数据保存成功")

        except Exception as e:
            _log.error(f"保存数据失败: {e}")
            raise  # 重新抛出异常，让调用者知道保存失败

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

        # 检查图片是否已经存在于统计中
        for emoji in self.group_stats.get(group_id, {}).values():
            if emoji.url == image_url:
                # 更新群组统计
                emoji.count += 1
                emoji.last_used = now

                # 更新用户统计
                if group_id not in self.user_stats:
                    self.user_stats[group_id] = {}
                if user_id not in self.user_stats[group_id]:
                    self.user_stats[group_id][user_id] = {}
                if emoji.cache_path not in self.user_stats[group_id][user_id]:
                    self.user_stats[group_id][user_id][emoji.cache_path] = emoji
                self.user_stats[group_id][user_id][emoji.cache_path].count += 1
                self.user_stats[group_id][user_id][emoji.cache_path].last_used = now

                # 更新发送次数统计
                today = now.date().isoformat()
                if group_id not in self.group_count:
                    self.group_count[group_id] = {}
                self.group_count[group_id][today] = (
                    self.group_count[group_id].get(today, 0) + 1
                )

                if group_id not in self.user_count:
                    self.user_count[group_id] = {}
                if user_id not in self.user_count[group_id]:
                    self.user_count[group_id][user_id] = {}
                self.user_count[group_id][user_id][today] = (
                    self.user_count[group_id][user_id].get(today, 0) + 1
                )

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
        self.group_stats[group_id][cache_key].count += 1
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
        self.user_stats[group_id][user_id][cache_key].count += 1
        self.user_stats[group_id][user_id][cache_key].last_used = now

        # 更新发送次数统计
        today = now.date().isoformat()
        if group_id not in self.group_count:
            self.group_count[group_id] = {}
        self.group_count[group_id][today] = self.group_count[group_id].get(today, 0) + 1

        if group_id not in self.user_count:
            self.user_count[group_id] = {}
        if user_id not in self.user_count[group_id]:
            self.user_count[group_id][user_id] = {}
        self.user_count[group_id][user_id][today] = (
            self.user_count[group_id][user_id].get(today, 0) + 1
        )

        # 保存数据
        self._save_data()

    def _get_top_emojis(
        self, stats: Dict[str, EmojiStats], limit: int = 3
    ) -> List[EmojiStats]:
        """获取最受欢迎的表情包"""
        # 按使用次数排序
        sorted_emojis = sorted(stats.values(), key=lambda x: x.count, reverse=True)
        return sorted_emojis[:limit]

    def _get_time_range_stats(self, stats: Dict[str, int], days: int) -> Dict[str, int]:
        """获取指定时间范围内的统计"""
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        return {
            date_str: count
            for date_str, count in stats.items()
            if start_date <= date.fromisoformat(date_str) <= end_date
        }

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
        days_map = {"今日": 1, "本周": 7, "本月": 30}
        days = days_map.get(time_range)
        if not days:
            await input.reply("无效的时间范围，请使用：今日、本周、本月")
            return

        if target not in ["群组", "个人"]:
            await input.reply("无效的统计对象，请使用：群组、个人")
            return

        await self._show_stats(input, days, target, target_user_id)

    async def _show_stats(
        self, input: GroupMessage, days: int, target: str, target_user_id: int
    ) -> None:
        """显示统计信息"""
        group_id = input.group_id

        # 创建消息链
        message = MessageChain([])

        if target == "群组":
            # 获取群组最受欢迎表情包
            top_emojis = self._get_top_emojis(self.group_stats.get(group_id, {}))
            # 获取群组发送次数统计
            count_stats = self._get_time_range_stats(
                self.group_count.get(group_id, {}), days
            )
            total_count = sum(count_stats.values())

            # 添加消息元素
            message.chain.append(Text("=== 群组表情包统计 ===\n"))
            message.chain.append(Text(f"最近{days}天发送表情包: {total_count}次\n\n"))

            # 添加发送次数最多的用户统计
            message.chain.append(Text("发送表情包最多的用户TOP3:\n"))
            user_counts = {}
            for user_id, user_stats in self.user_count.get(group_id, {}).items():
                user_total = sum(self._get_time_range_stats(user_stats, days).values())
                if user_total > 0:
                    user_counts[user_id] = user_total

            # 按发送次数排序
            sorted_users = sorted(
                user_counts.items(), key=lambda x: x[1], reverse=True
            )[:3]
            for i, (user_id, count) in enumerate(sorted_users, 1):
                try:
                    user_info = await self.api.get_group_member_info(
                        group_id=group_id, user_id=user_id, no_cache=True
                    )
                    if isinstance(user_info, dict) and user_info.get("status") == "ok":
                        user_data = user_info.get("data", {})
                        if user_data:
                            nickname = user_data.get("nickname", str(user_id))
                            message.chain.append(Text(f"{i}. {nickname}: {count}次\n"))
                        else:
                            message.chain.append(Text(f"{i}. {user_id}: {count}次\n"))
                    else:
                        message.chain.append(Text(f"{i}. {user_id}: {count}次\n"))
                except Exception as e:
                    _log.error(f"获取用户信息失败: {e}")
                    message.chain.append(Text(f"{i}. {user_id}: {count}次\n"))
            message.chain.append(Text("\n"))

            # 添加最受欢迎表情包统计
            message.chain.append(Text("最受欢迎表情包TOP3:\n"))
            for i, emoji in enumerate(top_emojis, 1):
                message.chain.append(Text(f"{i}. 使用次数: {emoji.count}\n"))
                message.chain.append(Image(emoji.cache_path))
                message.chain.append(Text("\n"))

        else:  # 个人统计
            # 获取用户最受欢迎表情包
            top_emojis = self._get_top_emojis(
                self.user_stats.get(group_id, {}).get(target_user_id, {})
            )
            # 获取用户发送次数统计
            count_stats = self._get_time_range_stats(
                self.user_count.get(group_id, {}).get(target_user_id, {}), days
            )
            total_count = sum(count_stats.values())

            # 添加消息元素
            message.chain.append(Text("=== 个人表情包统计 ===\n"))
            message.chain.append(Text(f"最近{days}天发送表情包: {total_count}次\n\n"))
            message.chain.append(Text("最常使用的表情包TOP3:\n"))

            # 添加表情包信息
            for i, emoji in enumerate(top_emojis, 1):
                message.chain.append(Text(f"{i}. 使用次数: {emoji.count}\n"))
                message.chain.append(Image(emoji.cache_path))
                message.chain.append(Text("\n"))

        # 如果没有任何表情包，添加提示信息
        if not top_emojis:
            message.chain.append(Text("暂无表情包使用记录\n"))

        # 发送消息
        await self.api.post_group_msg(
            group_id=group_id, rtf=message, reply=input.message_id
        )
