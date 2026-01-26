import asyncio
import base64
import io
import json
import math
import os
import random
import re
import time
from datetime import datetime
from typing import Dict, List, Optional

import yaml
from PIL import Image as PILImage, ImageDraw, ImageFont
from common.utils.AiUtil import AiUtil
from common.utils.CommonUtil import CommonUtil
from ncatbot.core import At, MessageChain, Text, GroupMessage, Reply, Image
from ncatbot.plugin_system import NcatBotPlugin, on_message
from ncatbot.utils.logger import get_log

from .memory import (
    memory_manager,
    generate_summary_from_messages,
    generate_user_impression,
)

_log = get_log()


# ========== LearningChat 记录接口 ==========
async def _record_to_learning_chat(group_id: int, bot_id: int, message: str):
    """将 Bot 消息记录到 LearningChat 数据库（如果插件已加载）"""
    try:
        from plugins.LearningChat import record_bot_message

        await record_bot_message(group_id, bot_id, message)
    except ImportError:
        pass  # LearningChat 插件未加载，忽略
    except Exception as e:
        _log.debug(f"[FakeAi] 记录到 LearningChat 失败: {e}")


# ========== LearningChat 记录接口 - 结束 ==========

# ========== 全局配置参数 ==========
trigger_interval = 600
group_reply_caches: Dict[int, "ReplyCache"] = {}  # 存储每个群的 ReplyCache
last_trigger_times: Dict[int, datetime] = {}  # 存储每个群的上次触发时间
user_trigger_times: Dict[int, datetime] = {}  # 存储每个用户的上次触发时间
enable_group_cd = True  # 群聊冷却开关
enable_user_cd = True  # 用户冷却开关
enable_callback = False  # 回调功能开关
callback_timeout = 15  # 回调超时时间（秒）

# 模拟打字延迟开关（默认关闭）
enable_typing_delay = False
# 每个字符的延迟时间（秒）
typing_delay_per_char = 0.1


# 回调状态管理
class CallbackState:
    def __init__(self):
        self.waiting_users: Dict[
            str, Dict
        ] = {}  # {user_id: {"group_id": group_id, "start_time": datetime}}

    def add_waiting_user(self, user_id: str, group_id: int) -> None:
        self.waiting_users[user_id] = {
            "group_id": group_id,
            "start_time": datetime.now(),
        }

    def remove_waiting_user(self, user_id: str) -> None:
        if user_id in self.waiting_users:
            del self.waiting_users[user_id]

    def is_waiting(self, user_id: str) -> bool:
        return user_id in self.waiting_users

    def get_waiting_info(self, user_id: str) -> Optional[Dict]:
        return self.waiting_users.get(user_id)

    def check_timeout(self, user_id: str) -> bool:
        if user_id not in self.waiting_users:
            return False
        wait_time = (
            datetime.now() - self.waiting_users[user_id]["start_time"]
        ).total_seconds()
        return wait_time > callback_timeout


callback_state = CallbackState()


# ========== 好感度回复概率计算 ==========
def get_reply_probability(favorability: int) -> float:
    """根据好感度计算回复概率（指数衰减曲线）

    使用指数衰减公式：probability = min_prob + (1 - min_prob) * e^(k * favorability)
    - 好感度为正时：100% 回复
    - 好感度为负时：快速下降，然后逐渐趋于最低值

    Args:
        favorability: 用户好感度值

    Returns:
        回复概率 (0.0 ~ 1.0)

    曲线示例（k=0.03, min_prob=0.03）：
        好感度   0 → 100%
        好感度 -10 → 77%
        好感度 -20 → 59%
        好感度 -30 → 45%
        好感度 -50 → 26%
        好感度 -80 → 12%
        好感度 -100 → 8%
        好感度 -150 → 4%
        好感度 -200 → 3%
        好感度 -400 → 3%
    """
    if favorability >= 0:
        return 1.0  # 好感度为正，100% 回复

    # 指数衰减参数
    min_prob = 0.03  # 最低概率 3%
    k = 0.03  # 衰减系数，越大下降越快

    # 指数衰减公式
    probability = min_prob + (1.0 - min_prob) * math.exp(k * favorability)

    return max(min_prob, probability)


async def should_reply_by_favorability(user_id: int, skip_users: list = None) -> bool:
    """根据好感度判断是否应该回复

    Args:
        user_id: 用户ID
        skip_users: 跳过好感度检查的用户ID列表（如管理员）

    Returns:
        是否应该回复
    """
    # 冻结用户：回复概率为 0
    frozen_users = [794383252]
    if user_id in frozen_users:
        _log.info(f"[FakeAi] 冻结用户 {user_id}，回复概率为 0%，不回复")
        return False

    # 特定用户跳过检查（管理员等）
    if skip_users and str(user_id) in [str(u) for u in skip_users]:
        return True

    try:
        impression_data = await memory_manager.get_user_impression_full(user_id)
        if not impression_data:
            # 没有印象数据的新用户，100%回复
            return True

        favorability = impression_data.get("favorability", 0)
        probability = get_reply_probability(favorability)

        # 随机判断是否回复
        should_reply = random.random() < probability

        if not should_reply:
            _log.info(
                f"[FakeAi] 用户 {user_id} 好感度 {favorability}，概率 {probability * 100:.0f}%，本次不回复"
            )

        return should_reply
    except Exception as e:
        _log.debug(f"[FakeAi] 获取用户好感度失败: {e}")
        return True  # 出错时默认回复


class ReplyCache:
    def __init__(self, max_size: int = 20):
        self.replies = []
        self.max_size = max_size

    def add_reply(self, reply_json: str) -> None:
        if len(self.replies) >= self.max_size:
            self.replies.pop(0)
        self.replies.append(reply_json)

    def get_replies(self) -> List[str]:
        return self.replies


class GlobalReplyCacheManager:
    _instance = None
    reply_caches: Dict[int, ReplyCache] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(GlobalReplyCacheManager, cls).__new__(cls)
        return cls._instance

    def get_reply_cache(self, group_id: int) -> ReplyCache:
        if group_id not in self.reply_caches:
            self.reply_caches[group_id] = ReplyCache()
        return self.reply_caches[group_id]

    def add_reply(self, group_id: int, reply_json: str) -> None:
        cache = self.get_reply_cache(group_id)
        cache.add_reply(reply_json)

    def get_replies(self, group_id: int) -> List[str]:
        cache = self.get_reply_cache(group_id)
        return cache.get_replies()


class FakeAi(NcatBotPlugin):
    name = "FakeAi"  # 插件名称
    version = "1.0"  # 插件版本

    # 添加排除插件列表
    excluded_plugins = [
        "NetEaseCloudMusic",
        "VrChatInfo",
        "AnimeTrace",
        "Meme",
        "ImageSender",
    ]  # 在这里添加不需要触发FakeAi的插件名称

    # AnimeTrace 命令前缀
    anime_trace_prefixes = ["查询人物", "识别人物", "角色", "人物"]

    # ImageSender 上传命令前缀
    image_sender_prefixes = ["上传"]

    # Meme 关键词列表（动态加载）
    meme_keywords = []

    # 冻结用户列表（AI功能不生效，只保留查询好感度）
    frozen_users = [794383252]

    # 存储每个群最近的消息，用于生成总结
    _message_buffer: Dict[int, List[Dict]] = {}
    _last_summary_time: Dict[int, int] = {}

    # 存储每个用户的消息，用于生成印象 {user_id: {"name": str, "messages": [str]}}（全局）
    _user_message_buffer: Dict[int, Dict] = {}
    _last_impression_time: int = 0  # 全局时间戳

    # 长期记忆配置
    SUMMARY_INTERVAL = 30 * 60  # 每30分钟总结一次
    MIN_MESSAGES_FOR_SUMMARY = 20  # 至少20条消息才总结
    IMPRESSION_INTERVAL = 60 * 60  # 每60分钟更新一次用户印象
    MIN_USER_MESSAGES = 10  # 至少10条消息才生成印象

    async def on_load(self):
        """加载插件"""
        # 初始化长期记忆数据库
        await memory_manager.init_db()
        _log.info("[FakeAi] 长期记忆数据库初始化完成")

        # 清理知识库中的重复数据
        try:
            cleaned = await memory_manager.cleanup_duplicate_knowledge()
            if cleaned > 0:
                _log.info(f"[FakeAi] 已清理 {cleaned} 条重复知识")
        except Exception as e:
            _log.debug(f"[FakeAi] 清理重复知识失败: {e}")

        # 添加定时总结任务（每10分钟检查一次）
        self.add_scheduled_task(
            self._generate_summaries,
            "fakeai_memory_summary",
            "10m",
        )
        _log.info("[FakeAi] 长期记忆总结任务已注册")

        # 添加用户印象更新任务（每15分钟检查一次）
        self.add_scheduled_task(
            self._update_user_impressions,
            "fakeai_user_impression",
            "15m",
        )
        _log.info("[FakeAi] 用户印象更新任务已注册")

        # 加载 meme 关键词
        try:
            with open("data/json/memeKeys.json", "r", encoding="utf-8") as file:
                meme_data = json.load(file)
                for data in meme_data:
                    self.meme_keywords.extend(data.get("keywords", []))
            _log.info(f"FakeAi 已加载 {len(self.meme_keywords)} 个 meme 关键词排除")
        except Exception as e:
            _log.warning(f"FakeAi 加载 meme 关键词失败: {e}")

    def _add_to_message_buffer(
        self, group_id: int, user_id: int, name: str, content: str
    ):
        """将消息添加到缓冲区，用于后续生成总结和用户印象"""
        # 冻结用户不记录到用户消息缓冲区（不更新印象）
        if user_id in self.frozen_users:
            return

        # 添加到群消息缓冲区
        if group_id not in self._message_buffer:
            self._message_buffer[group_id] = []

        self._message_buffer[group_id].append(
            {
                "user_id": user_id,
                "name": name,
                "content": content,
                "time": int(time.time()),
            }
        )

        # 限制缓冲区大小
        if len(self._message_buffer[group_id]) > 100:
            self._message_buffer[group_id] = self._message_buffer[group_id][-100:]

        # 添加到全局用户消息缓冲区（用于生成印象，按用户ID统一管理）
        if user_id not in self._user_message_buffer:
            self._user_message_buffer[user_id] = {
                "name": name,
                "messages": [],
            }

        # 更新用户名（可能会变化）
        self._user_message_buffer[user_id]["name"] = name

        if content:  # 只存储非空内容
            self._user_message_buffer[user_id]["messages"].append(content)
            # 限制每个用户的消息数
            if len(self._user_message_buffer[user_id]["messages"]) > 50:
                self._user_message_buffer[user_id]["messages"] = (
                    self._user_message_buffer[user_id]["messages"][-50:]
                )

    async def _generate_summaries(self):
        """定时任务：为活跃的群生成消息总结"""
        current_time = int(time.time())

        for group_id, messages in list(self._message_buffer.items()):
            try:
                # 检查是否有足够的消息
                if len(messages) < self.MIN_MESSAGES_FOR_SUMMARY:
                    continue

                # 检查距离上次总结是否足够久
                last_time = self._last_summary_time.get(group_id, 0)
                if current_time - last_time < self.SUMMARY_INTERVAL:
                    continue

                # 生成总结
                result = await generate_summary_from_messages(messages)

                if result and result.get("summary"):
                    # 获取参与者ID列表
                    participant_ids = list(set(m["user_id"] for m in messages))

                    # 计算时间范围
                    start_time = messages[0]["time"]
                    end_time = messages[-1]["time"]

                    # 保存总结
                    await memory_manager.save_summary(
                        group_id=group_id,
                        summary=result["summary"],
                        key_topics=result.get("key_topics", []),
                        participant_ids=participant_ids,
                        message_count=len(messages),
                        start_time=start_time,
                        end_time=end_time,
                    )

                    # 保存重要事件
                    for event in result.get("important_events", []):
                        if event:
                            await memory_manager.save_important_event(
                                group_id=group_id,
                                event_type="chat",
                                description=event,
                                related_users=participant_ids[:5],
                            )

                    _log.info(f"[FakeAi] 群 {group_id} 生成了新的记忆总结")

                    # 更新时间并清空缓冲区
                    self._last_summary_time[group_id] = current_time
                    self._message_buffer[group_id] = []

            except Exception as e:
                _log.error(f"[FakeAi] 生成群 {group_id} 总结失败: {e}")

    async def _update_user_impressions(self):
        """定时任务：更新活跃用户的印象（全局管理）"""
        current_time = int(time.time())

        # 检查距离上次更新是否足够久
        if current_time - self._last_impression_time < self.IMPRESSION_INTERVAL:
            return

        try:
            updated_count = 0
            for user_id, user_data in list(self._user_message_buffer.items()):
                messages = user_data.get("messages", [])
                name = user_data.get("name", str(user_id))

                # 检查是否有足够的消息
                if len(messages) < self.MIN_USER_MESSAGES:
                    continue

                # 生成用户详细印象（传入user_id用于识别VIP用户）
                impression_data = await generate_user_impression(
                    name, messages, user_id=user_id
                )

                if impression_data:
                    await memory_manager.update_user_impression(
                        user_id=user_id,
                        impression_data=impression_data,
                        username=name,  # 传入用户名/群昵称
                    )
                    updated_count += 1
                    _log.debug(
                        f"[FakeAi] 更新用户 {name}({user_id}) 的印象: "
                        f"性别={impression_data.get('gender', '')}, "
                        f"印象={impression_data.get('impression', '')}, "
                        f"好感度变化={impression_data.get('favorability_change', 0)}"
                    )

                    # 清空该用户的消息缓冲
                    self._user_message_buffer[user_id]["messages"] = []

            if updated_count > 0:
                _log.info(f"[FakeAi] 全局更新了 {updated_count} 个用户印象")

            self._last_impression_time = current_time

        except Exception as e:
            _log.error(f"[FakeAi] 更新用户印象失败: {e}")

    async def _is_from_excluded_plugin(self, input: GroupMessage) -> bool:
        """检查消息是否来自排除的插件"""
        # 移除 CQ 码后的纯命令文本
        clean_message = re.sub(r"\[CQ:[^\]]+\]", "", input.raw_message).strip()

        # 检查是否是 AnimeTrace 命令
        for prefix in self.anime_trace_prefixes:
            if clean_message.startswith(prefix):
                return True

        # 检查是否是 ImageSender 上传命令
        for prefix in self.image_sender_prefixes:
            if clean_message.startswith(prefix):
                return True

        # 检查是否是 Meme 命令
        if clean_message == "meme":
            return True
        # 检查是否是 meme 关键词命令
        first_word = clean_message.split(" ")[0] if clean_message else ""
        if first_word in self.meme_keywords:
            return True

        # 检查是否是回复消息（用于 NetEaseCloudMusic 和 VrChatInfo）
        reply_list = input.message.filter(Reply)
        if reply_list:
            reply_id = reply_list[0].id
            # get_msg 返回的是 GroupMessageEvent 对象
            reply_msg = await self.api.get_msg(reply_id)
            raw_message = reply_msg.raw_message
            if raw_message and (
                "请回复数字选择要播放的歌曲" in raw_message
                or "请回复数字选择要查看的玩家" in raw_message
            ):
                return True
        return False

    async def _process_image_descriptions(self, content: list) -> list:
        """为消息中的图片生成描述（只处理最后一张图片）

        将图片替换为文本描述格式 [图片: 描述内容]，让 AI 能理解图片内容

        Args:
            content: 消息内容列表

        Returns:
            处理后的消息内容列表（图片会被替换为文本描述）
        """
        # 找到所有图片
        image_indices = []
        for i, item in enumerate(content):
            if isinstance(item, dict) and item.get("type") == "image":
                image_indices.append(i)

        if not image_indices:
            return content

        # 只处理最后一张图片（效率优先）
        last_image_idx = image_indices[-1]
        image_data = content[last_image_idx].get("data", {})
        image_url = image_data.get("url", "")

        if not image_url:
            # 没有 URL，标记为无法识别的图片
            content[last_image_idx] = {"type": "text", "data": {"text": "[图片]"}}
            return content

        try:
            # 调用多模态模型生成描述
            description = await AiUtil.describe_image_briefly(image_url)
            if description:
                # 把图片替换为文本描述，让 AI 能理解
                content[last_image_idx] = {
                    "type": "text",
                    "data": {"text": f"[图片: {description}]"},
                }
                _log.info(f"[FakeAi] 图片描述已生成: {description[:50]}...")
            else:
                # 描述生成失败，标记为普通图片
                content[last_image_idx] = {"type": "text", "data": {"text": "[图片]"}}
        except Exception as e:
            _log.debug(f"[FakeAi] 图片描述生成失败: {e}")
            content[last_image_idx] = {"type": "text", "data": {"text": "[图片]"}}

        # 其他未处理的图片也标记一下
        for idx in image_indices[:-1]:
            content[idx] = {"type": "text", "data": {"text": "[图片]"}}

        return content

    async def handle_balance_query(self, input: GroupMessage) -> None:
        """处理查询余额命令"""
        try:
            # 调用 AiUtil 查询余额
            balance_data = await AiUtil.get_deepseek_balance()

            if "error" in balance_data:
                # 查询失败，发送错误信息
                error_msg = Text(f"查询余额失败: {balance_data['error']}")
                await self.api.post_group_msg(
                    group_id=input.group_id, rtf=MessageChain([error_msg])
                )
                return

            # 解析余额信息
            is_available = balance_data.get("is_available", False)
            balance_infos = balance_data.get("balance_infos", [])

            # 构建回复消息
            message_parts = [Text("━━━━━━━━━━━━━━━━━━━━\n")]
            message_parts.append(Text("     💰 API 余额查询\n"))
            message_parts.append(Text("━━━━━━━━━━━━━━━━━━━━\n\n"))

            if is_available:
                message_parts.append(Text("📊 账户状态: ✅ 可用\n"))
            else:
                message_parts.append(Text("📊 账户状态: ❌ 不可用\n"))

            if balance_infos:
                for balance_info in balance_infos:
                    currency = balance_info.get("currency", "未知")
                    total_balance = balance_info.get("total_balance", "0")
                    granted_balance = balance_info.get("granted_balance", "0")
                    topped_up_balance = balance_info.get("topped_up_balance", "0")

                    currency_name = (
                        "人民币"
                        if currency == "CNY"
                        else "美元"
                        if currency == "USD"
                        else currency
                    )

                    message_parts.append(Text(f"💵 货币类型: {currency_name}\n"))
                    message_parts.append(Text(f"💎 总余额: {total_balance}\n"))
                    message_parts.append(Text(f"🎁 赠送额度: {granted_balance}\n"))
                    message_parts.append(Text(f"💳 充值余额: {topped_up_balance}\n"))
            else:
                message_parts.append(Text("❌ 未获取到余额信息\n"))

            # 添加模型信息和感谢语
            message_parts.append(Text("\n━━━━━━━━━━━━━━━━━━━━\n"))
            message_parts.append(Text("🤖 当前模型: DeepSeek-V3\n"))
            message_parts.append(Text("━━━━━━━━━━━━━━━━━━━━\n"))
            message_parts.append(Text("✨ 特别鸣谢 冰鲜柠檬汁(2606440373)\n"))
            message_parts.append(Text("   提供的免费 API 支持~\n"))

            # 发送消息
            message = MessageChain(message_parts)
            await self.api.post_group_msg(group_id=input.group_id, rtf=message)

        except Exception as e:
            _log.error(f"处理余额查询时发生错误: {e}")
            error_msg = Text(f"查询余额时发生错误: {str(e)}")
            await self.api.post_group_msg(
                group_id=input.group_id, rtf=MessageChain([error_msg])
            )

    async def handle_impression_query(
        self, input: GroupMessage, target_user_id: int = None
    ) -> None:
        """处理查看角色印象命令

        Args:
            input: 群消息对象
            target_user_id: 目标用户ID，如果为None则查询发送者自己
        """
        try:
            sender_id = input.sender.user_id

            # 确定查询目标：如果指定了目标用户，查询目标；否则查询自己
            query_user_id = target_user_id if target_user_id else int(sender_id)
            is_self = query_user_id == int(sender_id)

            # 获取用户的完整印象数据
            impression_data = await memory_manager.get_user_impression_full(
                query_user_id
            )

            if not impression_data:
                # 没有印象数据
                if is_self:
                    message = MessageChain(
                        [
                            At(int(sender_id)),
                            Text(" 蓝晴还没有对你形成印象呢~\n多聊聊天吧！"),
                        ]
                    )
                else:
                    message = MessageChain(
                        [
                            At(int(sender_id)),
                            Text(f" 蓝晴还没有对 "),
                            At(query_user_id),
                            Text(" 形成印象呢~"),
                        ]
                    )
                await self.api.post_group_msg(group_id=input.group_id, rtf=message)
                return

            # 构建印象展示消息
            if is_self:
                message_parts = [At(int(sender_id)), Text(" 蓝晴对你的印象：\n\n")]
            else:
                message_parts = [
                    At(int(sender_id)),
                    Text(" 蓝晴对 "),
                    At(query_user_id),
                    Text(" 的印象：\n\n"),
                ]

            # 用户名和ID
            username = impression_data.get("username", "")
            if username:
                message_parts.append(Text(f"👤 名字: {username}\n"))
            message_parts.append(Text(f"🆔 ID: {query_user_id}\n"))

            # 性别
            gender = impression_data.get("gender", "")
            if gender:
                gender_emoji = (
                    "👨" if gender == "男" else "👩" if gender == "女" else "🧑"
                )
                message_parts.append(Text(f"{gender_emoji} 性别: {gender}\n"))

            # 印象关键词
            impression = impression_data.get("impression", "")
            if impression:
                message_parts.append(Text(f"💭 印象: {impression}\n"))

            # 好感度（初始0，可为负数）
            favorability = impression_data.get("favorability", 0)
            pending = impression_data.get("pending_favorability", 0)
            fav_emoji = (
                "💖"
                if favorability >= 50
                else "💗"
                if favorability >= 20
                else "💙"
                if favorability >= 0
                else "💔"
                if favorability >= -20
                else "🖤"
            )
            fav_text = f"{fav_emoji} 好感度: {favorability}"
            if pending != 0:
                fav_text += f" (待结算: {'+' if pending > 0 else ''}{pending})"
            message_parts.append(Text(fav_text + "\n"))

            # 回复概率（冻结用户显示 0%）
            if query_user_id in self.frozen_users:
                reply_prob = 0.0
            else:
                reply_prob = get_reply_probability(favorability)
            prob_emoji = (
                "🎯" if reply_prob >= 0.8 else "🎲" if reply_prob >= 0.5 else "🌙"
            )
            message_parts.append(
                Text(f"{prob_emoji} 回复概率: {reply_prob * 100:.0f}%\n")
            )

            # 事件
            events = impression_data.get("events", [])
            if events:
                recent_events = events[-3:]  # 最近3个事件
                message_parts.append(Text(f"📝 事件: {', '.join(recent_events)}\n"))

            # 称呼
            nickname = impression_data.get("nickname", "")
            if nickname:
                message_parts.append(Text(f"📛 称呼: {nickname}\n"))

            # 新知识
            new_knowledge = impression_data.get("new_knowledge", [])
            if new_knowledge:
                recent_knowledge = new_knowledge[-3:]  # 最近3个知识点
                message_parts.append(
                    Text(f"📚 新知识: {', '.join(recent_knowledge)}\n")
                )

            # 重要事件
            important_events = impression_data.get("important_events", [])
            if important_events:
                recent_important = important_events[-2:]  # 最近2个重要事件
                message_parts.append(
                    Text(f"⭐ 重要事件: {', '.join(recent_important)}\n")
                )

            # 互动次数
            interaction_count = impression_data.get("interaction_count", 0)
            message_parts.append(Text(f"🔢 互动次数: {interaction_count}\n"))

            # 发送消息
            message = MessageChain(message_parts)
            await self.api.post_group_msg(group_id=input.group_id, rtf=message)

        except Exception as e:
            _log.error(f"处理印象查询时发生错误: {e}")
            error_msg = Text(f"查询印象时发生错误: {str(e)}")
            await self.api.post_group_msg(
                group_id=input.group_id, rtf=MessageChain([error_msg])
            )

    async def handle_knowledge_query(self, input: GroupMessage) -> None:
        """处理知识库查询命令（仅限管理员）"""
        try:
            sender_id = input.sender.user_id

            # 只允许管理员查询（273421673）
            if str(sender_id) != "273421673":
                await self.api.post_group_msg(
                    group_id=input.group_id, text="只有管理员可以查询知识库哦~"
                )
                return

            # 获取知识库统计
            total_count = await memory_manager.get_all_knowledge_count()

            # 获取最近添加的知识
            cursor = await memory_manager.db.execute(
                """SELECT keyword, content, source_username, hit_count, created_at 
                   FROM knowledge_base 
                   ORDER BY created_at DESC LIMIT 10"""
            )
            recent_knowledge = await cursor.fetchall()

            # 获取最常命中的知识
            cursor = await memory_manager.db.execute(
                """SELECT keyword, content, hit_count 
                   FROM knowledge_base 
                   WHERE hit_count > 0
                   ORDER BY hit_count DESC LIMIT 5"""
            )
            popular_knowledge = await cursor.fetchall()

            # 构建消息
            message_parts = [Text("📚 蓝晴的知识库\n\n")]
            message_parts.append(Text(f"📊 总知识量: {total_count} 条\n\n"))

            if recent_knowledge:
                message_parts.append(Text("🆕 最近学到的知识:\n"))
                for row in recent_knowledge[:5]:
                    keyword = row["keyword"]
                    content = row["content"]
                    source = row["source_username"] or "未知"
                    if content:
                        message_parts.append(
                            Text(f"  • {keyword}: {content} (来自{source})\n")
                        )
                    else:
                        message_parts.append(Text(f"  • {keyword} (来自{source})\n"))

            if popular_knowledge:
                message_parts.append(Text("\n🔥 常用知识:\n"))
                for row in popular_knowledge:
                    keyword = row["keyword"]
                    content = row["content"]
                    hit_count = row["hit_count"]
                    if content:
                        message_parts.append(
                            Text(f"  • {keyword}: {content} (命中{hit_count}次)\n")
                        )
                    else:
                        message_parts.append(
                            Text(f"  • {keyword} (命中{hit_count}次)\n")
                        )

            message = MessageChain(message_parts)
            await self.api.post_group_msg(group_id=input.group_id, rtf=message)

        except Exception as e:
            _log.error(f"处理知识库查询时发生错误: {e}")
            await self.api.post_group_msg(
                group_id=input.group_id, text=f"查询知识库时发生错误: {str(e)}"
            )

    def _generate_ranking_image(
        self, top_list: List[Dict], bottom_list: List[Dict]
    ) -> str:
        """生成好感度排行榜图片

        Args:
            top_list: 好感度最高的用户列表
            bottom_list: 好感度最低的用户列表

        Returns:
            图片的 base64 字符串
        """
        # 图片参数
        width = 800
        row_height = 45
        padding = 30
        title_height = 60
        section_gap = 30

        # 计算高度
        top_count = len(top_list)
        bottom_count = len(bottom_list)
        height = (
            padding * 2  # 上下边距
            + title_height  # 总标题
            + title_height  # 榜单标题
            + top_count * row_height  # 好感度榜
            + section_gap  # 两榜间隔
            + title_height  # 黑名单标题
            + bottom_count * row_height  # 黑名单
            + padding  # 底部边距
        )

        # 创建图片（深色背景）
        img = PILImage.new("RGB", (width, height), color=(30, 30, 35))
        draw = ImageDraw.Draw(img)

        # 尝试加载字体
        try:
            title_font = ImageFont.truetype("data/font/sakura.ttf", 32)
            subtitle_font = ImageFont.truetype("data/font/sakura.ttf", 24)
            text_font = ImageFont.truetype("data/font/sakura.ttf", 20)
        except Exception:
            try:
                title_font = ImageFont.truetype("simhei.ttf", 32)
                subtitle_font = ImageFont.truetype("simhei.ttf", 24)
                text_font = ImageFont.truetype("simhei.ttf", 20)
            except Exception:
                title_font = ImageFont.load_default()
                subtitle_font = title_font
                text_font = title_font

        y = padding

        # 绘制总标题
        title = "~ 蓝晴好感度排行榜 ~"
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_width = title_bbox[2] - title_bbox[0]
        draw.text(
            ((width - title_width) // 2, y),
            title,
            font=title_font,
            fill=(255, 200, 100),
        )
        y += title_height

        # 绘制分隔线
        draw.line(
            [(padding, y - 10), (width - padding, y - 10)], fill=(80, 80, 90), width=2
        )

        # 绘制好感度榜标题
        top_title = "[ 好感度 TOP 10 ]"
        draw.text((padding, y), top_title, font=subtitle_font, fill=(100, 200, 255))
        y += title_height

        # 绘制好感度榜内容
        for i, user in enumerate(top_list):
            rank = i + 1
            username = user["username"] or "未知用户"
            user_id = user["user_id"]
            fav = user["favorability"]

            # 排名颜色和文字
            if rank == 1:
                rank_color = (255, 215, 0)  # 金色
                rank_text = "TOP1"
            elif rank == 2:
                rank_color = (192, 192, 192)  # 银色
                rank_text = "TOP2"
            elif rank == 3:
                rank_color = (205, 127, 50)  # 铜色
                rank_text = "TOP3"
            else:
                rank_color = (150, 150, 150)
                rank_text = f" {rank}."

            # 好感度颜色
            if fav >= 50:
                fav_color = (255, 100, 150)  # 粉红
            elif fav >= 20:
                fav_color = (255, 150, 100)  # 橙色
            elif fav >= 0:
                fav_color = (100, 200, 100)  # 绿色
            else:
                fav_color = (150, 150, 150)  # 灰色

            # 绘制排名
            draw.text((padding, y), rank_text, font=text_font, fill=rank_color)

            # 绘制用户信息
            user_text = f"{username} ({user_id})"
            draw.text(
                (padding + 60, y), user_text, font=text_font, fill=(220, 220, 220)
            )

            # 绘制好感度（右对齐）
            fav_text = f"+{fav}" if fav > 0 else str(fav)
            fav_bbox = draw.textbbox((0, 0), fav_text, font=text_font)
            fav_width = fav_bbox[2] - fav_bbox[0]
            draw.text(
                (width - padding - fav_width, y),
                fav_text,
                font=text_font,
                fill=fav_color,
            )

            y += row_height

        y += section_gap

        # 绘制分隔线
        draw.line(
            [(padding, y - section_gap // 2), (width - padding, y - section_gap // 2)],
            fill=(80, 80, 90),
            width=2,
        )

        # 绘制黑名单标题
        bottom_title = "[ 好感度 BOTTOM 10 ]"
        draw.text((padding, y), bottom_title, font=subtitle_font, fill=(255, 100, 100))
        y += title_height

        # 绘制黑名单内容
        for i, user in enumerate(bottom_list):
            rank = i + 1
            username = user["username"] or "未知用户"
            user_id = user["user_id"]
            fav = user["favorability"]

            # 排名文字
            rank_text = f" {rank}."

            # 好感度颜色（负数越低越红）
            if fav <= -100:
                fav_color = (255, 50, 50)  # 深红
            elif fav <= -50:
                fav_color = (255, 100, 80)  # 红色
            elif fav < 0:
                fav_color = (255, 150, 100)  # 橙红
            else:
                fav_color = (150, 150, 150)  # 灰色

            # 绘制排名
            draw.text((padding, y), rank_text, font=text_font, fill=(100, 100, 100))

            # 绘制用户信息
            user_text = f"{username} ({user_id})"
            draw.text(
                (padding + 60, y), user_text, font=text_font, fill=(180, 180, 180)
            )

            # 绘制好感度（右对齐）
            fav_text = str(fav)
            fav_bbox = draw.textbbox((0, 0), fav_text, font=text_font)
            fav_width = fav_bbox[2] - fav_bbox[0]
            draw.text(
                (width - padding - fav_width, y),
                fav_text,
                font=text_font,
                fill=fav_color,
            )

            y += row_height

        # 保存到 BytesIO
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format="PNG")
        img_byte_arr.seek(0)
        img_base64 = base64.b64encode(img_byte_arr.getvalue()).decode()

        return f"base64://{img_base64}"

    async def handle_favorability_ranking(self, input: GroupMessage) -> None:
        """处理好感度排行榜命令"""
        try:
            # 获取排行榜数据
            ranking = await memory_manager.get_favorability_ranking(top_n=10)
            top_list = ranking["top"]
            bottom_list = ranking["bottom"]

            if not top_list and not bottom_list:
                await self.api.post_group_msg(
                    group_id=input.group_id, text="暂无好感度数据~"
                )
                return

            # 生成排行榜图片
            img_base64 = self._generate_ranking_image(top_list, bottom_list)

            # 发送图片
            message = MessageChain([Image(img_base64)])
            await self.api.post_group_msg(group_id=input.group_id, rtf=message)

        except Exception as e:
            _log.error(f"处理好感度排行榜时发生错误: {e}")
            await self.api.post_group_msg(
                group_id=input.group_id, text=f"生成排行榜时发生错误: {str(e)}"
            )

    async def handle_vision_test(self, input: GroupMessage) -> None:
        """处理图片测试命令 - 使用多模态模型描述图片"""
        try:
            # 从消息中提取图片
            image_urls = []
            for msg_segment in input.message:
                if (
                    hasattr(msg_segment, "msg_seg_type")
                    and msg_segment.msg_seg_type == "image"
                ):
                    url = getattr(msg_segment, "url", "")
                    if url:
                        image_urls.append(url)

            if not image_urls:
                await self.api.post_group_msg(
                    group_id=input.group_id, text="请发送图片让我看看~"
                )
                return

            # 发送等待提示
            await self.api.post_group_msg(
                group_id=input.group_id, text="正在分析图片，请稍候..."
            )

            # 先下载图片转 base64（NVIDIA API 无法访问 QQ 图片服务器）
            image_base64_list = await AiUtil.download_images_as_base64(image_urls)
            if not image_base64_list:
                await self.api.post_group_msg(
                    group_id=input.group_id, text="图片下载失败，请稍后再试~"
                )
                return

            # 调用多模态模型
            result = await AiUtil.chat_with_vision(
                prompt="请用中文详细描述这张图片的内容，包括主体、场景、颜色、氛围等。",
                image_base64_list=image_base64_list,
                system_prompt="你是一个图像分析专家，请用简洁但详细的中文描述图片内容。",
                max_tokens=1024,
                temperature=0.7,
            )

            if result and result.get("content"):
                description = result["content"]
                usage = result.get("usage", {})
                token_info = f"\n\n[Token: {usage.get('total_tokens', '?')}]"
                await self.api.post_group_msg(
                    group_id=input.group_id,
                    text=f"📷 图片描述：\n{description}{token_info}",
                )
            else:
                await self.api.post_group_msg(
                    group_id=input.group_id, text="图片分析失败，请稍后再试~"
                )

        except Exception as e:
            _log.error(f"处理图片测试时发生错误: {e}")
            await self.api.post_group_msg(
                group_id=input.group_id, text=f"图片分析出错: {str(e)}"
            )

    @on_message
    async def handle_fake_ai(self, input: GroupMessage) -> None:
        # 检查消息是否来自排除的插件
        if await self._is_from_excluded_plugin(input):
            return

        group_id = input.group_id
        sender_id = input.sender.user_id
        sender_name = input.sender.nickname

        # 将 MessageArray 转换为可序列化的格式
        content = []
        for msg_segment in input.message:
            if hasattr(msg_segment, "text"):
                content.append({"type": "text", "data": {"text": msg_segment.text}})
            elif (
                hasattr(msg_segment, "msg_seg_type")
                and msg_segment.msg_seg_type == "at"
            ):
                content.append({"type": "at", "data": {"qq": msg_segment.qq}})
            elif (
                hasattr(msg_segment, "msg_seg_type")
                and msg_segment.msg_seg_type == "image"
            ):
                content.append(
                    {
                        "type": "image",
                        "data": {
                            "url": getattr(msg_segment, "url", ""),
                            "file": getattr(msg_segment, "file", ""),
                            "summary": getattr(msg_segment, "summary", ""),
                        },
                    }
                )
            else:
                # 尝试处理其他类型的消息
                if (
                    hasattr(msg_segment, "data")
                    and "image" in str(msg_segment.data).lower()
                ):
                    content.append({"type": "image", "data": {"raw": str(msg_segment)}})

        is_at_message = "[CQ:at,qq=3555202423]" in input.raw_message

        # 检查是否是等待回调的用户
        if enable_callback and callback_state.is_waiting(sender_id):
            # 检查是否超时
            if callback_state.check_timeout(sender_id):
                callback_state.remove_waiting_user(sender_id)
                return

            # 获取等待信息
            wait_info = callback_state.get_waiting_info(sender_id)
            if wait_info and wait_info["group_id"] == group_id:
                # 如果是艾特消息，不触发回调（避免连续艾特导致的重复触发）
                if is_at_message:
                    callback_state.remove_waiting_user(sender_id)
                    return

                # 移除等待状态
                callback_state.remove_waiting_user(sender_id)
                # 直接回复，不检查CD
                reply_cache = group_reply_caches.setdefault(group_id, ReplyCache())

                # 对图片进行识别描述
                content = await self._process_image_descriptions(content)

                reply_json = json.dumps(
                    {
                        "name": sender_name,
                        "id": sender_id,
                        "content": content,
                        "group_nickname": input.sender.card or sender_name,
                    },
                    ensure_ascii=False,
                )
                reply_cache.add_reply(reply_json)
                # 添加到长期记忆缓冲区
                self._add_to_message_buffer(
                    group_id, int(sender_id), sender_name, content
                )
                answer = await answer_ai(
                    group_id, group_reply_caches, str(sender_id), "callback"
                )
                _log.info(answer)
                await send_typing_response(self, input, answer)
                return

        # 判断是否是功能测试的指令
        if input.raw_message == "蓝晴说话":
            if sender_id in [
                "273421673",
            ] or group_id in [719518427, 853963912]:
                answer = await answer_ai(
                    group_id, group_reply_caches, str(sender_id), "test"
                )
                _log.info(answer)
                await send_typing_response(self, input, answer)
                return

        # 处理查询余额命令
        if input.raw_message == "查询余额":
            await self.handle_balance_query(input)
            return

        # 处理查看角色印象命令（支持 @某人 查看别人的印象）
        if "蓝晴印象" in input.raw_message:
            # 提取消息中被@的用户（排除机器人自己 3555202423）
            target_user_id = None
            for msg_segment in input.message:
                if (
                    hasattr(msg_segment, "msg_seg_type")
                    and msg_segment.msg_seg_type == "at"
                ):
                    at_qq = getattr(msg_segment, "qq", None)
                    if at_qq and str(at_qq) != "3555202423":
                        target_user_id = int(at_qq)
                        break
            await self.handle_impression_query(input, target_user_id)
            return

        # 冻结用户：只允许查询好感度，其他 AI 功能不生效
        if int(sender_id) in self.frozen_users:
            _log.debug(f"[FakeAi] 冻结用户 {sender_id}，跳过 AI 功能")
            return

        # 处理知识库查询命令（仅限管理员）
        if input.raw_message == "蓝晴知识库":
            await self.handle_knowledge_query(input)
            return

        # 处理好感度排行榜命令
        if input.raw_message in ["蓝晴好感度排行榜", "蓝晴好感度排行"]:
            await self.handle_favorability_ranking(input)
            return

        # 处理图片测试命令（"测试" + 图片）
        clean_text = re.sub(r"\[CQ:[^\]]+\]", "", input.raw_message).strip()
        if clean_text == "测试":
            await self.handle_vision_test(input)
            return

        if is_at_message:
            # 检查用户CD（除了273421673用户）
            if sender_id != "273421673" and not check_user_cd(sender_id):
                return

            reply_cache = group_reply_caches.setdefault(group_id, ReplyCache())

            # 对图片进行识别描述（只处理最后一张，异步不阻塞太久）
            content = await self._process_image_descriptions(content)

            # 创建 JSON 格式的字符串并添加到 replyCache 中
            reply_json = json.dumps(
                {
                    "name": sender_name,
                    "id": sender_id,
                    "content": content,
                    "group_nickname": input.sender.card or sender_name,
                },
                ensure_ascii=False,
            )
            reply_cache.add_reply(reply_json)
            # 添加到长期记忆缓冲区
            self._add_to_message_buffer(group_id, int(sender_id), sender_name, content)

            # 根据好感度判断是否回复（管理员跳过检查）
            skip_users = ["273421673", "635773721"]  # 管理员列表
            if not await should_reply_by_favorability(int(sender_id), skip_users):
                # 不回复，但可以发一个冷淡的表情或不回复
                return

            answer = await answer_ai(
                group_id, group_reply_caches, str(sender_id), "active"
            )
            _log.info(answer)
            await send_typing_response(self, input, answer)

            # 如果启用了回调功能，添加用户到等待列表
            if enable_callback:
                callback_state.add_waiting_user(sender_id, group_id)

            # 更新用户触发时间
            if sender_id != "273421673":
                user_trigger_times[sender_id] = datetime.now()
            return

        # 获取或创建对应群的 ReplyCache
        reply_cache = group_reply_caches.setdefault(group_id, ReplyCache())

        # 创建 JSON 格式的字符串并添加到 replyCache 中
        reply_json = json.dumps(
            {
                "name": sender_name,
                "id": sender_id,
                "content": content,
                "group_nickname": input.sender.card or sender_name,
            },
            ensure_ascii=False,
        )
        reply_cache.add_reply(reply_json)
        # 添加到长期记忆缓冲区
        self._add_to_message_buffer(group_id, int(sender_id), sender_name, content)

        # 检查冷却时间
        if not check_cd(group_id):
            return

        # 随机触发逻辑
        if random.random() > 0.01:
            return

        # 根据好感度判断是否回复（被动触发也检查）
        skip_users = ["273421673", "635773721"]  # 管理员列表
        if not await should_reply_by_favorability(int(sender_id), skip_users):
            return

        # 记录本次触发的时间
        last_trigger_times[group_id] = datetime.now()
        answer = await answer_ai(
            group_id, group_reply_caches, str(sender_id), "passive"
        )
        _log.info(answer)
        reply_cache.add_reply(answer)
        if not answer or answer.strip() == "" or answer == '""':
            return
        await send_typing_response(self, input, answer)

        # 如果启用了回调功能，添加用户到等待列表
        if enable_callback:
            callback_state.add_waiting_user(sender_id, group_id)


async def send_typing_response(self: FakeAi, input: GroupMessage, answer: str) -> None:
    try:
        # 尝试解析 JSON
        try:
            replace = json.loads(answer.replace("{{", "{").replace("}}", "}"))
            content = replace.get("content", "")
        except json.JSONDecodeError:
            # 如果不是 JSON 格式，直接使用原始内容
            content = answer

        # 使用正则表达式分割句子
        # 先用句号、问号、感叹号分割，将逗号替换为空格，然后去掉句号
        sentence_split_pattern = r"([。！？!?]+)"  # 用于分割句子的标点
        comma_replace_pattern = r"[，]+"  # 需要替换为空格的中文逗号
        period_remove_pattern = r"[。]+"  # 需要移除的句号

        # 先按句号、问号、感叹号分割
        parts = re.split(sentence_split_pattern, content)
        sentences = []

        for i in range(0, len(parts), 2):
            if i + 1 < len(parts):
                # 将句子和标点组合在一起
                sentence = (parts[i] + parts[i + 1]).strip()
                if sentence:
                    # 保护CQ码中的标点
                    cq_codes = []

                    def save_cq(match):
                        cq_codes.append(match.group(0))
                        return f"__CQ_CODE_{len(cq_codes) - 1}__"

                    # 保存所有CQ码
                    sentence = re.sub(r"\[CQ:[^\]]+\]", save_cq, sentence)

                    # 将中文逗号替换为空格
                    sentence = re.sub(comma_replace_pattern, " ", sentence)
                    # 将多个连续空格替换为单个空格
                    sentence = re.sub(r"\s+", " ", sentence)

                    # 移除句号，但保留问号和感叹号
                    sentence = re.sub(period_remove_pattern, "", sentence)

                    # 恢复CQ码
                    for idx, cq_code in enumerate(cq_codes):
                        sentence = sentence.replace(f"__CQ_CODE_{idx}__", cq_code)

                    if sentence:
                        sentences.append(sentence)
            else:
                # 处理最后一个部分
                if parts[i].strip():
                    # 保护CQ码中的标点
                    cq_codes = []

                    def save_cq(match):
                        cq_codes.append(match.group(0))
                        return f"__CQ_CODE_{len(cq_codes) - 1}__"

                    # 保存所有CQ码
                    sentence = re.sub(r"\[CQ:[^\]]+\]", save_cq, parts[i].strip())

                    # 将中文逗号替换为空格
                    sentence = re.sub(comma_replace_pattern, " ", sentence)
                    # 将多个连续空格替换为单个空格
                    sentence = re.sub(r"\s+", " ", sentence)

                    # 移除句号，但保留问号和感叹号
                    sentence = re.sub(period_remove_pattern, "", sentence)

                    # 恢复CQ码
                    for idx, cq_code in enumerate(cq_codes):
                        sentence = sentence.replace(f"__CQ_CODE_{idx}__", cq_code)

                    if sentence:
                        sentences.append(sentence)

        # 如果没有分割出的句子，就把整个内容作为一个句子
        if not sentences:
            # 保护CQ码中的标点
            cq_codes = []

            def save_cq(match):
                cq_codes.append(match.group(0))
                return f"__CQ_CODE_{len(cq_codes) - 1}__"

            # 保存所有CQ码
            content = re.sub(r"\[CQ:[^\]]+\]", save_cq, content.strip())

            # 移除不需要保留的标点
            content = re.sub(comma_replace_pattern, " ", content)
            # 将多个连续空格替换为单个空格
            content = re.sub(r"\s+", " ", content)
            content = re.sub(period_remove_pattern, "", content)

            # 恢复CQ码
            for idx, cq_code in enumerate(cq_codes):
                content = content.replace(f"__CQ_CODE_{idx}__", cq_code)

            if content:
                sentences = [content]

        at_pattern = re.compile(r"\[CQ:at,qq=([\w\u4e00-\u9fff]+)]")
        group_id = input.group_id
        members_response = await self.api.get_group_member_list(group_id=group_id)
        members = CommonUtil.parse_group_member_list(members_response)

        # 遍历句子
        for sentence in sentences:
            message_elements = []  # 为每个句子创建新的消息元素列表
            last_match_end = 0

            # 处理 CQ 码格式的 @ 消息
            for match in at_pattern.finditer(sentence):
                # 处理 @ 之前的文本
                text_before_at = sentence[last_match_end : match.start()].strip()
                if text_before_at:
                    message_elements.append(Text(text_before_at))

                at_content = match.group(1)
                try:
                    # 尝试解析为 ID
                    user_id = int(at_content)
                except ValueError:
                    # 如果无法解析为 ID，从历史记录中查找对应的 ID
                    user_id = find_user_id_by_name(at_content, input.group_id)

                if user_id and any(
                    member.user_id == str(user_id) for member in members
                ):
                    # 添加 @ 的用户
                    message_elements.append(At(user_id))
                    message_elements.append(Text(" "))

                last_match_end = match.end()

            # 处理 @ 之后的文本
            text_after_last_at = sentence[last_match_end:].strip()
            if text_after_last_at:
                message_elements.append(Text(text_after_last_at))

            # 模拟打字的延时，根据句子的字符数设置延时（可通过开关控制）
            if enable_typing_delay:
                delay = len(sentence) * typing_delay_per_char
                await asyncio.sleep(delay)

            # 发送消息
            if message_elements:  # 确保消息链不为空
                message = MessageChain(message_elements)
                await self.api.post_group_msg(group_id=input.group_id, rtf=message)

        # 将AI的回复加入到reply_cache中
        reply_cache = group_reply_caches.setdefault(group_id, ReplyCache())
        reply_json = json.dumps(
            {"name": "蓝晴", "id": "0", "content": content},
            ensure_ascii=False,
        )
        reply_cache.add_reply(reply_json)

        # 记录到 LearningChat 数据库
        bot_id = int(self.api.self_id) if hasattr(self.api, "self_id") else 0
        if bot_id:
            await _record_to_learning_chat(group_id, bot_id, content)

    except Exception as e:
        _log.error(f"发送消息时发生错误: {e}")
        return


def find_user_id_by_name(name: str, group_id: int) -> Optional[int]:
    # 获取对应群的历史记录
    reply_cache = group_reply_caches.get(group_id)
    if not reply_cache:
        return None

    # 遍历历史记录，查找匹配的用户名和 ID
    for reply in reply_cache.get_replies():
        try:
            reply_json = json.loads(reply)
            if name in reply_json.get("name", ""):
                return reply_json.get("id")
        except json.JSONDecodeError:
            continue

    return None  # 未找到匹配的用户


def check_cd(group_id: int) -> bool:
    if not enable_group_cd:
        return True  # 如果群聊冷却被禁用，直接返回True
    last_trigger_time = last_trigger_times.get(group_id)
    if not last_trigger_time:
        return True  # 如果没有记录，则表示冷却完成

    now = datetime.now()
    remaining_time = trigger_interval - (now - last_trigger_time).total_seconds()
    _log.info(f"群CD检查: {group_id}, 剩余时间: {remaining_time:.2f}秒")

    # CD 冷却完成
    return remaining_time <= 0


def check_user_cd(user_id: str) -> bool:
    """检查用户CD是否冷却完成"""
    if not enable_user_cd:
        _log.info(f"用户CD被禁用，直接通过: {user_id}")
        return True  # 如果用户冷却被禁用，直接返回True
    last_trigger_time = user_trigger_times.get(user_id)
    if not last_trigger_time:
        _log.info(f"用户首次触发，CD通过: {user_id}")
        return True  # 如果没有记录，则表示冷却完成

    now = datetime.now()
    remaining_time = trigger_interval - (now - last_trigger_time).total_seconds()
    _log.info(f"用户CD检查: {user_id}, 剩余时间: {remaining_time:.2f}秒")
    return remaining_time <= 0


def record_ai_usage_to_json(
    group_id: str, user_id: str, tokens: int = 0, trigger_type: str = "active"
):
    """直接保存AI使用统计数据到JSON文件"""
    # json, os, datetime 已在文件顶部导入

    # 数据文件路径
    GROUP_DATA_FILE = "data/json/ai_group_stats.json"
    USER_DATA_FILE = "data/json/ai_user_stats.json"

    now = datetime.now()
    today = now.date().isoformat()

    # 保存群组统计
    try:
        # 确保目录存在
        os.makedirs(os.path.dirname(GROUP_DATA_FILE), exist_ok=True)

        # 读取现有数据
        group_data = {"group_stats": {}}
        if os.path.exists(GROUP_DATA_FILE):
            try:
                with open(GROUP_DATA_FILE, "r", encoding="utf-8") as f:
                    group_data = json.load(f)
            except Exception:
                pass

        # 确保有 group_stats 字段
        if "group_stats" not in group_data:
            group_data["group_stats"] = {}

        # 更新群组统计
        if group_id not in group_data["group_stats"]:
            group_data["group_stats"][group_id] = {
                "daily_counts": {},
                "daily_tokens": {},
                "last_used": None,
                "total_count": 0,
                "total_tokens": 0,
            }

        stats = group_data["group_stats"][group_id]
        if today not in stats["daily_counts"]:
            stats["daily_counts"][today] = 0
        if today not in stats["daily_tokens"]:
            stats["daily_tokens"][today] = 0

        stats["daily_counts"][today] += 1
        stats["daily_tokens"][today] += tokens
        stats["total_count"] += 1
        stats["total_tokens"] += tokens
        stats["last_used"] = now.isoformat()

        # 保存群组数据
        with open(GROUP_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(group_data, f, ensure_ascii=False, indent=2)

    except Exception as e:
        print(f"保存群组AI统计数据失败: {e}")

    # 保存用户统计
    try:
        # 确保目录存在
        os.makedirs(os.path.dirname(USER_DATA_FILE), exist_ok=True)

        # 读取现有数据
        user_data = {"user_stats": {}}
        if os.path.exists(USER_DATA_FILE):
            try:
                with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
                    user_data = json.load(f)
            except Exception:
                pass

        # 确保有 user_stats 字段
        if "user_stats" not in user_data:
            user_data["user_stats"] = {}
        if group_id not in user_data["user_stats"]:
            user_data["user_stats"][group_id] = {}

        # 更新用户统计
        if user_id not in user_data["user_stats"][group_id]:
            user_data["user_stats"][group_id][user_id] = {
                "daily_counts": {},
                "daily_tokens": {},
                "last_used": None,
                "total_count": 0,
                "total_tokens": 0,
            }

        user_stats = user_data["user_stats"][group_id][user_id]
        if today not in user_stats["daily_counts"]:
            user_stats["daily_counts"][today] = 0
        if today not in user_stats["daily_tokens"]:
            user_stats["daily_tokens"][today] = 0

        user_stats["daily_counts"][today] += 1
        user_stats["daily_tokens"][today] += tokens
        user_stats["total_count"] += 1
        user_stats["total_tokens"] += tokens
        user_stats["last_used"] = now.isoformat()

        # 保存用户数据
        with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(user_data, f, ensure_ascii=False, indent=2)

    except Exception as e:
        print(f"保存用户AI统计数据失败: {e}")


def load_yaml_data(group_id) -> Dict:
    # if group_id == 719518427:
    #     with open("data/yml/lanqingv1_ai.yml", "r", encoding="utf-8") as file:
    #         return yaml.safe_load(file)
    with open("data/yml/lanqingv1.yml", "r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def replace_time_in_system(yaml_data: Dict) -> None:
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    system_content = yaml_data.get("system", "")
    input_content = yaml_data.get("input", "")
    if system_content:
        yaml_data["system"] = system_content.replace("{time}", current_time)
    if input_content:
        yaml_data["input"] = input_content.replace("{time}", current_time)


def update_yaml_with_replies(yaml_data: Dict, reply_cache: ReplyCache) -> Dict:
    replies = reply_cache.get_replies()
    if replies:
        last_reply = replies[-1]
        new_history = "\n".join(replies)
        replace_placeholder(yaml_data, "{history_new}", new_history)
        replace_placeholder(yaml_data, "{history_last}", last_reply)
    return yaml_data


def replace_placeholder(data: Dict, placeholder: str, new_value: str) -> None:
    for key, value in data.items():
        if isinstance(value, str):
            if placeholder in value:
                data[key] = value.replace(placeholder, new_value)
        elif isinstance(value, dict):
            replace_placeholder(value, placeholder, new_value)


def remove_thinking_process(response: str) -> str:
    """移除回复中的思考过程和注释部分"""
    if not response:
        return response

    # 移除以 // 开头的行
    lines = response.split("\n")
    filtered_lines = [line for line in lines if not line.strip().startswith("//")]

    # 检查是否有"思考过程："这样的标记
    thinking_markers = ["思考过程：", "思考过程:", "// 思考过程", "思考：", "思考:"]
    for marker in thinking_markers:
        if marker in response:
            # 找到标记的位置
            marker_index = response.find(marker)
            # 截取标记之前的内容
            before_marker = response[:marker_index].strip()
            return before_marker

    return "\n".join(filtered_lines)


async def answer_ai(
    group_id: int,
    group_reply_caches: Dict[int, ReplyCache],
    user_id: str = None,
    trigger_type: str = "active",
) -> str:
    # 加载 YAML 数据
    yaml_data = load_yaml_data(group_id)
    replace_time_in_system(yaml_data)
    update_yaml_with_replies(yaml_data, group_reply_caches.get(group_id, ReplyCache()))

    # 注入长期记忆
    try:
        long_term_memory = await memory_manager.get_long_term_memory(group_id)
        replace_placeholder(yaml_data, "{long_term_memory}", long_term_memory)
    except Exception as e:
        _log.debug(f"[FakeAi] 获取长期记忆失败: {e}")
        replace_placeholder(yaml_data, "{long_term_memory}", "（暂无长期记忆）")

    # 注入用户印象（全局管理，根据当前群聊活跃用户获取印象）
    try:
        # 从群消息缓冲区提取活跃用户ID列表
        reply_cache = group_reply_caches.get(group_id, ReplyCache())
        active_user_ids = set()
        for reply_json in reply_cache.get_replies():
            try:
                reply_data = json.loads(reply_json)
                uid = reply_data.get("id")
                if uid and uid != "0":  # 排除蓝晴自己
                    active_user_ids.add(int(uid))
            except (json.JSONDecodeError, ValueError):
                continue

        user_impressions = await memory_manager.get_user_impressions_text(
            user_ids=list(active_user_ids) if active_user_ids else None
        )
        replace_placeholder(yaml_data, "{user_impressions}", user_impressions)
    except Exception as e:
        _log.debug(f"[FakeAi] 获取用户印象失败: {e}")
        replace_placeholder(yaml_data, "{user_impressions}", "（暂无用户印象）")

    # 注入相关知识（从知识库检索与当前话题相关的知识）
    try:
        # 从最近的消息中提取关键内容用于检索
        reply_cache = group_reply_caches.get(group_id, ReplyCache())
        recent_messages = []
        for reply_json in reply_cache.get_replies()[-10:]:  # 取最近10条消息
            try:
                reply_data = json.loads(reply_json)
                content = reply_data.get("content", "")
                if content and reply_data.get("id") != "0":  # 排除蓝晴自己的消息
                    # 从消息内容中提取纯文本
                    if isinstance(content, list):
                        # content 是消息段列表，提取文本内容
                        text_parts = []
                        for seg in content:
                            if isinstance(seg, dict) and seg.get("type") == "text":
                                text = seg.get("data", {}).get("text", "")
                                if text:
                                    text_parts.append(text)
                        text_content = " ".join(text_parts)
                    elif isinstance(content, str):
                        text_content = content
                    else:
                        text_content = str(content)

                    if text_content.strip():
                        recent_messages.append(text_content)
            except json.JSONDecodeError:
                continue

        query_text = " ".join(recent_messages)
        _log.debug(
            f"[FakeAi] 知识检索查询文本: {query_text[:200] if query_text else '空'}..."
        )

        if query_text:
            related_knowledge = await memory_manager.get_knowledge_text(
                query_text, max_length=300
            )
            if related_knowledge:
                replace_placeholder(yaml_data, "{related_knowledge}", related_knowledge)
                _log.info(f"[FakeAi] 注入相关知识: {related_knowledge[:100]}...")
            else:
                replace_placeholder(
                    yaml_data, "{related_knowledge}", "（暂无相关知识）"
                )
        else:
            replace_placeholder(yaml_data, "{related_knowledge}", "（暂无相关知识）")
    except Exception as e:
        _log.error(f"[FakeAi] 获取相关知识失败: {e}")
        replace_placeholder(yaml_data, "{related_knowledge}", "（暂无相关知识）")

    # 调用 AIUtil 的 search_deepseek 方法
    keyword = yaml_data.get("input", "")
    prompt = yaml_data.get("system", "")
    ai_response = await AiUtil.search_deepseek(keyword, prompt)

    # 处理AI响应
    if isinstance(ai_response, dict):
        response = ai_response.get("content", "")
        usage_info = ai_response.get("usage", {})
        tokens = usage_info.get("total_tokens", 0)
    else:
        response = ai_response or ""
        tokens = 0

    # 记录AI使用统计
    if user_id:
        try:
            # 直接保存AI使用统计数据到JSON文件
            record_ai_usage_to_json(str(group_id), str(user_id), tokens, trigger_type)
        except Exception as e:
            _log.error(f"记录AI使用统计失败: {e}")

    # 首先移除思考过程
    response = remove_thinking_process(response)

    # 过滤掉思考过程（以 // 开头的内容）
    if response:
        # 先尝试直接解析为 JSON
        try:
            # 检查是否是有效的 JSON 字符串
            parsed = json.loads(response)
            if isinstance(parsed, dict) and all(
                k in parsed for k in ["name", "id", "content"]
            ):
                # 已经是干净的 JSON 格式，直接返回原始响应
                return response
        except json.JSONDecodeError:
            pass

        # 使用正则表达式提取 JSON 部分
        import re

        # 匹配标准的 JSON 格式，处理内容中可能有转义引号的情况
        json_pattern = r'(\{"name":"[^"]+","id":"[^"]+","content":"(?:[^"\\]|\\.)*"\})'
        match = re.search(json_pattern, response)
        if match:
            # 只返回匹配到的 JSON 部分
            return match.group(1)

        # 如果上面的匹配失败，尝试一种更宽松的模式
        json_start = response.find('{"name":"')
        if json_start != -1:
            # 找到 JSON 开始的位置，然后尝试找最后的 }
            json_end = response.find("}", json_start)
            if json_end != -1:
                # 提取可能的 JSON 部分
                possible_json = response[json_start : json_end + 1]
                # 验证提取的内容是否是有效的 JSON
                try:
                    parsed = json.loads(possible_json)
                    if isinstance(parsed, dict) and all(
                        k in parsed for k in ["name", "id", "content"]
                    ):
                        return possible_json
                except Exception:
                    pass

    # 如果无法提取有效的 JSON，返回原始响应
    return response
