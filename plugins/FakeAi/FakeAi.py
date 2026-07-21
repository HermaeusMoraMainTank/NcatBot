import asyncio
import base64
import io
import json
import random
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Union

from PIL import Image as PILImage, ImageDraw, ImageFont
from common.utils.AiUtil import AiUtil, DEEPSEEK_CHAT_MODEL
from common.utils.async_io import load_json, load_yaml
from common.utils.CommonUtil import CommonUtil
from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.types import At, MessageArray as MessageChain, PlainText, Reply, Image
from ncatbot.plugin import NcatBotPlugin
from ncatbot.core import registrar
from ncatbot.utils import get_log

from .memory import (
    memory_manager,
    generate_summary_from_messages,
    generate_user_impression,
)
from .favorability import (
    can_view_relation_tag,
    format_favorability,
    get_reply_probability,
    get_tier,
)
from .cognition import DEFAULT_COGNITION
from .expression import (
    EXPRESSION_ENABLED,
    MAX_STICKERS_PER_REPLY,
    extract_stickers,
    sticker_catalog,
)
from .interaction import (
    DEFAULT_CONFIG,
    InteractionState,
    analyst_decide,
    state_store,
)
from .interaction.analyst import AnalystDecision
from .settings import FAVOR_SKIP_IDS, FROZEN_USERS, apply_from_plugin, is_admin

_log = get_log()
BOT_QQ = DEFAULT_CONFIG.bot_qq


def _stamp_reply_dict(payload: dict) -> str:
    """写入 ReplyCache 用 JSON，自动补 ts。"""
    if "ts" not in payload:
        payload["ts"] = time.time()
    return json.dumps(payload, ensure_ascii=False)


def _mask_qq_for_ranking_display(user_id: object) -> str:
    """排行榜展示用 QQ 脱敏：纯数字保留前三后二，中间固定为 ****。"""
    s = str(user_id).strip()
    if not s:
        return "***"
    if not s.isdigit():
        return f"{s[:2]}***" if len(s) > 4 else "***"
    n = len(s)
    if n == 1:
        return "*"
    if n <= 4:
        return s[0] + "*" * (n - 1)
    if n <= 6:
        return s[:2] + "*" * (n - 4) + s[-2:]
    return s[:3] + "****" + s[-2:]


def add_plugin_sent_reply(
    group_id: int,
    bot_id: int,
    content: str,
    plugin_source: str = "plugin",
    sub_source: str = "",
) -> None:
    """
    供其他插件调用：将插件代发的 Bot 消息加入对话缓存，
    并标记来源，以便蓝晴在上下文中知道「这不是我生成的，是插件发的」。

    Args:
        group_id: 群号
        bot_id: Bot 的 QQ 号（当前未用于过滤，保留兼容）
        content: 消息内容（纯文本或 CQ 码字符串）
        plugin_source: 插件标识（短名，如 meme、parser）
        sub_source: 子类型，如 repeat / random / speak / other
    """
    reply_cache = group_reply_caches.setdefault(group_id, ReplyCache())
    reply_json = _stamp_reply_dict(
        {
            "name": "蓝晴",
            "id": "0",
            "content": content,
            "source": plugin_source,
            "sub_source": sub_source,
        }
    )
    reply_cache.add_reply(reply_json)


# ========== 全局配置参数 ==========
trigger_interval = 10  # 群触发冷却（秒），按群独立
group_reply_caches: Dict[int, "ReplyCache"] = {}  # 存储每个群的 ReplyCache
last_trigger_times: Dict[int, datetime] = {}  # 存储每个群的上次触发时间
user_trigger_times: Dict[int, datetime] = {}  # 存储每个用户的上次触发时间
enable_group_cd = True  # 群聊冷却开关（防止同群连续触发）
enable_user_cd = False  # 用户冷却开关
enable_callback = False  # 回调功能开关
callback_timeout = 15  # 回调超时时间（秒）
# 同群生成中互斥：LLM 耗时常超过群 CD，避免并发展开第二条回复流水线
_inflight_groups: set = set()

# 被动触发：已改为状态机 + 分析员；裸 8% 废弃（见 interaction.config.fallback_random_prob）
PASSIVE_TRIGGER_BASE_PROB = 0.0  # noqa: 保留名以免外部引用炸；实际不再使用

# 允许 FakeAi 响应的群号（空集合表示不限制）— on_load / settings 会覆盖
FAKEAI_ALLOWED_GROUPS = frozenset({853963912, 719518427, 585479130, 1064163905})


def _is_fakeai_group_allowed(group_id) -> bool:
    """群白名单：FAKEAI_ALLOWED_GROUPS 非空时仅允许列表内群号。"""
    if not FAKEAI_ALLOWED_GROUPS:
        return True
    try:
        return int(group_id) in FAKEAI_ALLOWED_GROUPS
    except (TypeError, ValueError):
        return False


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

# 用于在「仅传入 event」调用时提供插件实例（兼容统一注册器未找到 plugin 的情况）
_fake_ai_plugin_instance: list = [None]
# 用于回退获取插件实例（在实例尚未设置时）
_fake_ai_loader = None


# ========== 好感度回复概率（实现在 favorability 模块） ==========


async def should_reply_by_favorability(user_id: int, skip_users: list = None) -> bool:
    """根据好感度判断是否应该回复

    Args:
        user_id: 用户ID
        skip_users: 跳过好感度检查的用户ID列表（如管理员）

    Returns:
        是否应该回复
    """
    # 冻结用户：回复概率为 0
    if int(user_id) in FROZEN_USERS:
        _log.info(f"[FakeAi] 冻结用户 {user_id}，回复概率为 0%，不回复")
        return False

    # 特定用户跳过检查（管理员等）
    if skip_users and str(user_id) in [str(u) for u in skip_users]:
        return True
    if is_admin(user_id):
        return True
    if str(user_id) in FAVOR_SKIP_IDS:
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        global _fake_ai_loader
        _fake_ai_plugin_instance[0] = self
        _fake_ai_loader = getattr(self, "_loader", None)
        # 同时写入 memory 模块，主模块被热重载后仍可通过 memory 取回实例
        try:
            from plugins.FakeAi import memory as _memory

            _memory.fake_ai_plugin_ref = self
        except Exception:
            pass

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

    # 冻结用户列表（AI功能不生效，只保留查询好感度）— 由 settings 覆盖
    frozen_users = list(FROZEN_USERS)

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
        _fake_ai_plugin_instance[0] = self
        try:
            apply_from_plugin(self)
        except Exception as e:
            _log.error("[FakeAi] 配置加载失败，使用代码默认值: %s", e)

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
            "fakeai_memory_summary",
            "10m",
            callback=self._generate_summaries,
        )
        _log.info("[FakeAi] 长期记忆总结任务已注册")

        # 添加用户印象更新任务（每15分钟检查一次）
        self.add_scheduled_task(
            "fakeai_user_impression",
            "15m",
            callback=self._update_user_impressions,
        )
        _log.info("[FakeAi] 用户印象更新任务已注册")

        if DEFAULT_COGNITION.sleep_enabled:
            self.add_scheduled_task(
                "fakeai_memory_consolidate",
                "60m",
                callback=self._consolidate_memories,
            )
            _log.info("[FakeAi] 记忆巩固任务已注册")

        # 贴纸目录：apply_from_plugin 已 reload；此处仅兜底再扫一次
        try:
            n = sticker_catalog.reload()
            _log.info("[FakeAi] 贴纸目录已加载: %s", n)
        except Exception as e:
            _log.warning("[FakeAi] 贴纸目录加载失败: %s", e)

        # 加载 meme 关键词
        try:
            meme_data = await load_json("data/json/memeKeys.json")
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
                "last_group_id": group_id,
            }

        # 更新用户名与最近活跃群
        self._user_message_buffer[user_id]["name"] = name
        self._user_message_buffer[user_id]["last_group_id"] = group_id

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
                result = await generate_summary_from_messages(
                    messages, group_id=group_id
                )

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
                last_group_id = user_data.get("last_group_id")
                impression_data = await generate_user_impression(
                    name, messages, user_id=user_id, group_id=last_group_id
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

        # MemeToRGB 命令
        if first_word.lower() == "rgb":
            return True

        # FakeAiWatermark 命令
        if first_word == "豆包水印" or first_word.lower() == "gemini水印":
            return True

        # MirageTank 命令
        if first_word in ("幻影坦克", "彩色幻影坦克"):
            return True

        # 检查是否是回复消息（用于 NetEaseCloudMusic 和 VrChatInfo）
        reply_list = input.message.filter(Reply)
        if reply_list:
            reply_id = reply_list[0].id
            # get_msg 返回的是 GroupMessageEvent 对象
            reply_msg = await self.api.qq.query.get_msg(reply_id)
            raw_message = reply_msg.raw_message
            if raw_message and (
                "请回复数字选择要播放的歌曲" in raw_message
                or "请回复数字选择要查看的玩家" in raw_message
            ):
                return True
        return False

    async def _consolidate_memories(self):
        try:
            await memory_manager.init_db()
            result = await memory_manager.consolidate_weak_memories()
            _log.info("[FakeAi] 定时巩固: %s", result)
        except Exception as e:
            _log.error("[FakeAi] 记忆巩固失败: %s", e)

    async def _process_image_descriptions(
        self, content: list, group_id: int = None, user_id: str = None
    ) -> list:
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
            from common.utils.AiStatsRecorder import SOURCE_VISION, record_from_response

            vision_result = await AiUtil.describe_image_briefly(image_url)
            description = vision_result.get("content") if vision_result else None
            if vision_result and group_id and user_id:
                record_from_response(
                    str(group_id), str(user_id), SOURCE_VISION, vision_result
                )
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
                error_msg = PlainText(text=f"查询余额失败: {balance_data['error']}")
                await self.api.qq.post_group_msg(
                    group_id=input.group_id, rtf=MessageChain([error_msg])
                )
                return

            # 解析余额信息
            is_available = balance_data.get("is_available", False)
            balance_infos = balance_data.get("balance_infos", [])

            # 构建回复消息
            message_parts = [PlainText(text="━━━━━━━━━━━━━━━━━━━━\n")]
            message_parts.append(PlainText(text="     💰 API 余额查询\n"))
            message_parts.append(PlainText(text="━━━━━━━━━━━━━━━━━━━━\n\n"))

            if is_available:
                message_parts.append(PlainText(text="📊 账户状态: ✅ 可用\n"))
            else:
                message_parts.append(PlainText(text="📊 账户状态: ❌ 不可用\n"))

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

                    message_parts.append(
                        PlainText(text=f"💵 货币类型: {currency_name}\n")
                    )
                    message_parts.append(
                        PlainText(text=f"💎 总余额: {total_balance}\n")
                    )
                    message_parts.append(
                        PlainText(text=f"🎁 赠送额度: {granted_balance}\n")
                    )
                    message_parts.append(
                        PlainText(text=f"💳 充值余额: {topped_up_balance}\n")
                    )
            else:
                message_parts.append(PlainText(text="❌ 未获取到余额信息\n"))

            # 添加模型信息和感谢语
            message_parts.append(PlainText(text="\n━━━━━━━━━━━━━━━━━━━━\n"))
            message_parts.append(
                PlainText(text=f"🤖 当前模型: {DEEPSEEK_CHAT_MODEL}\n")
            )
            message_parts.append(PlainText(text="━━━━━━━━━━━━━━━━━━━━\n"))
            message_parts.append(PlainText(text="✨ 特别鸣谢 冰鲜柠檬汁(2606440373)\n"))
            message_parts.append(PlainText(text="   提供的免费 API 支持~\n"))

            # 发送消息
            message = MessageChain(message_parts)
            await self.api.qq.post_group_msg(group_id=input.group_id, rtf=message)

        except Exception as e:
            _log.error(f"处理余额查询时发生错误: {e}")
            error_msg = PlainText(text=f"查询余额时发生错误: {str(e)}")
            await self.api.qq.post_group_msg(
                group_id=input.group_id, rtf=MessageChain([error_msg])
            )

    @staticmethod
    def _clip_impression_field(text: str, max_len: int) -> str:
        text = (text or "").strip()
        if len(text) <= max_len:
            return text
        return text[: max_len - 1] + "…"

    def _build_impression_body(
        self,
        impression_data: dict,
        query_user_id: int,
        viewer_id: int,
    ) -> str:
        """将印象字段合并为单段文本，减少 MessageChain 段数，降低发送超时风险。"""
        lines: List[str] = []

        username = impression_data.get("username", "")
        if username:
            lines.append(f"👤 名字: {username}")
        lines.append(f"🆔 ID: {query_user_id}")

        gender = impression_data.get("gender", "")
        if gender:
            gender_emoji = "👨" if gender == "男" else "👩" if gender == "女" else "🧑"
            lines.append(f"{gender_emoji} 性别: {gender}")

        impression = impression_data.get("impression", "")
        if impression:
            lines.append(f"💭 印象: {self._clip_impression_field(impression, 120)}")

        favorability = float(impression_data.get("favorability", 0))
        lines.append(f"好感: {format_favorability(favorability)}")

        if can_view_relation_tag(viewer_id):
            relation_tag = (impression_data.get("relation_tag") or "").strip()
            if relation_tag:
                lines.append(f"💞 关系: {relation_tag}")

        if query_user_id in self.frozen_users:
            reply_prob = 0.0
        else:
            reply_prob = get_reply_probability(favorability)
        prob_emoji = "🎯" if reply_prob >= 0.8 else "🎲" if reply_prob >= 0.5 else "🌙"
        lines.append(f"{prob_emoji} 回复概率: {reply_prob * 100:.0f}%")

        events = impression_data.get("events", [])
        if events:
            recent_events = events[-3:]
            lines.append(
                "📝 事件: " + self._clip_impression_field(", ".join(recent_events), 180)
            )

        new_knowledge = impression_data.get("new_knowledge", [])
        if new_knowledge:
            recent_knowledge = new_knowledge[-3:]
            lines.append(
                "📚 新知识: "
                + self._clip_impression_field(", ".join(recent_knowledge), 180)
            )

        important_events = impression_data.get("important_events", [])
        if important_events:
            recent_important = important_events[-2:]
            lines.append(
                "⭐ 重要事件: "
                + self._clip_impression_field(", ".join(recent_important), 180)
            )

        interaction_count = impression_data.get("interaction_count", 0)
        lines.append(f"🔢 互动次数: {interaction_count}")

        return "\n".join(lines)

    @staticmethod
    def _impression_user_label(impression_data: Optional[dict], user_id: int) -> str:
        if impression_data:
            name = (impression_data.get("username") or "").strip()
            if name:
                return f"{name}（{user_id}）"
        return str(user_id)

    async def _post_group_msg_retry(
        self,
        group_id: int,
        *,
        rtf: Optional[MessageChain] = None,
        text: Optional[str] = None,
        at: Optional[Union[str, int]] = None,
        retries: int = 2,
    ) -> None:
        last_err: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                if rtf is not None:
                    await self.api.qq.post_group_msg(group_id=group_id, rtf=rtf)
                else:
                    await self.api.qq.post_group_msg(
                        group_id=group_id, text=text or "", at=at
                    )
                return
            except Exception as e:
                last_err = e
                err_s = str(e)
                if attempt < retries and ("Timeout" in err_s or "[1200]" in err_s):
                    _log.warning(
                        "[FakeAi] send_group_msg 超时，重试 %s/%s",
                        attempt + 1,
                        retries,
                    )
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                raise
        if last_err:
            raise last_err

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
                _log.info(
                    "[FakeAi] 印象查询无记录: query_user_id=%s 数据库=%s",
                    query_user_id,
                    memory_manager.database_path,
                )
                if is_self:
                    text = "蓝晴还没有对你形成印象呢~\n多聊聊天吧！"
                else:
                    text = f"蓝晴还没有对 {query_user_id} 形成印象呢~"
                await self._post_group_msg_retry(
                    group_id=input.group_id,
                    at=sender_id,
                    text=text,
                )
                return

            body = self._build_impression_body(
                impression_data, query_user_id, int(sender_id)
            )
            if is_self:
                text = f"蓝晴对你的印象：\n\n{body}"
            else:
                label = self._impression_user_label(impression_data, query_user_id)
                text = f"蓝晴对 {label} 的印象：\n\n{body}"

            # 仅 @ 查询发起者；避免对目标用户 @ 导致 NapCat sendMsg 超时（如 541518108）
            await self._post_group_msg_retry(
                group_id=input.group_id,
                at=sender_id,
                text=text,
            )

        except Exception as e:
            _log.error(f"处理印象查询时发生错误: {e}")
            try:
                await self._post_group_msg_retry(
                    group_id=input.group_id,
                    at=sender_id,
                    text=f"查询印象时发生错误: {str(e)}",
                    retries=1,
                )
            except Exception as send_err:
                _log.error(f"发送印象查询错误提示失败: {send_err}")

    async def handle_knowledge_query(self, input: GroupMessage) -> None:
        """处理知识库查询命令（仅限管理员）"""
        try:
            sender_id = input.sender.user_id

            # 只允许管理员查询
            if not is_admin(sender_id):
                await self.api.qq.post_group_msg(
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
            message_parts = [PlainText(text="📚 蓝晴的知识库\n\n")]
            message_parts.append(PlainText(text=f"📊 总知识量: {total_count} 条\n\n"))

            if recent_knowledge:
                message_parts.append(PlainText(text="🆕 最近学到的知识:\n"))
                for row in recent_knowledge[:5]:
                    keyword = row["keyword"]
                    content = row["content"]
                    source = row["source_username"] or "未知"
                    if content:
                        message_parts.append(
                            PlainText(text=f"  • {keyword}: {content} (来自{source})\n")
                        )
                    else:
                        message_parts.append(
                            PlainText(text=f"  • {keyword} (来自{source})\n")
                        )

            if popular_knowledge:
                message_parts.append(PlainText(text="\n🔥 常用知识:\n"))
                for row in popular_knowledge:
                    keyword = row["keyword"]
                    content = row["content"]
                    hit_count = row["hit_count"]
                    if content:
                        message_parts.append(
                            PlainText(
                                text=f"  • {keyword}: {content} (命中{hit_count}次)\n"
                            )
                        )
                    else:
                        message_parts.append(
                            PlainText(text=f"  • {keyword} (命中{hit_count}次)\n")
                        )

            message = MessageChain(message_parts)
            await self.api.qq.post_group_msg(group_id=input.group_id, rtf=message)

        except Exception as e:
            _log.error(f"处理知识库查询时发生错误: {e}")
            await self.api.qq.post_group_msg(
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
            fav = float(user["favorability"])
            tier = get_tier(fav)

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
            if fav >= 80:
                fav_color = (255, 100, 150)  # 粉红
            elif fav >= 60:
                fav_color = (255, 150, 100)  # 橙色
            elif fav >= 40:
                fav_color = (100, 200, 100)  # 绿色
            elif fav >= 0:
                fav_color = (150, 200, 255)  # 浅蓝
            else:
                fav_color = (150, 150, 150)  # 灰色

            # 绘制排名
            draw.text((padding, y), rank_text, font=text_font, fill=rank_color)

            # 绘制用户信息
            user_text = f"{username} ({_mask_qq_for_ranking_display(user_id)})"
            draw.text(
                (padding + 60, y), user_text, font=text_font, fill=(220, 220, 220)
            )

            # 绘制好感度（右对齐）：分数 + 等级
            fav_text = f"{fav:.1f} · {tier.name}"
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

        # 绘制负好感榜标题
        bottom_title = "[ 负好感 BOTTOM 10 ]"
        draw.text((padding, y), bottom_title, font=subtitle_font, fill=(255, 100, 100))
        y += title_height

        # 绘制黑名单内容
        for i, user in enumerate(bottom_list):
            rank = i + 1
            username = user["username"] or "未知用户"
            user_id = user["user_id"]
            fav = float(user["favorability"])
            tier = get_tier(fav)

            # 排名文字
            rank_text = f" {rank}."

            # 好感度颜色（负数越低越红）
            if fav <= -35:
                fav_color = (255, 50, 50)  # 深红
            elif fav <= -25:
                fav_color = (255, 100, 80)  # 红色
            elif fav <= -15:
                fav_color = (255, 130, 90)  # 橙红
            elif fav < 0:
                fav_color = (255, 170, 100)  # 浅橙
            else:
                fav_color = (150, 150, 150)  # 灰色

            # 绘制排名
            draw.text((padding, y), rank_text, font=text_font, fill=(100, 100, 100))

            # 绘制用户信息
            user_text = f"{username} ({_mask_qq_for_ranking_display(user_id)})"
            draw.text(
                (padding + 60, y), user_text, font=text_font, fill=(180, 180, 180)
            )

            # 绘制好感度（右对齐）
            fav_text = f"{fav:.1f} · {tier.name}"
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
                await self.api.qq.post_group_msg(
                    group_id=input.group_id, text="暂无好感度数据~"
                )
                return

            # 生成排行榜图片
            img_base64 = self._generate_ranking_image(top_list, bottom_list)

            # 发送图片
            message = MessageChain([Image(file=img_base64)])
            await self.api.qq.post_group_msg(group_id=input.group_id, rtf=message)

        except Exception as e:
            _log.error(f"处理好感度排行榜时发生错误: {e}")
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text=f"生成排行榜时发生错误: {str(e)}"
            )

    async def handle_vision_test(self, input: GroupMessage) -> None:
        """处理图片测试命令 - 使用多模态模型描述图片"""
        try:
            # 从消息中提取图片
            image_urls = []
            for msg_segment in input.message:
                if isinstance(msg_segment, Image):
                    url = getattr(msg_segment, "url", "") or getattr(
                        msg_segment, "file", ""
                    )
                    if url:
                        image_urls.append(url)

            if not image_urls:
                await self.api.qq.post_group_msg(
                    group_id=input.group_id, text="请发送图片让我看看~"
                )
                return

            # 发送等待提示
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text="正在分析图片，请稍候..."
            )

            # 先下载图片转 base64（NVIDIA API 无法访问 QQ 图片服务器）
            image_base64_list = await AiUtil.download_images_as_base64(image_urls)
            if not image_base64_list:
                await self.api.qq.post_group_msg(
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
                await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text=f"📷 图片描述：\n{description}{token_info}",
                )
            else:
                await self.api.qq.post_group_msg(
                    group_id=input.group_id, text="图片分析失败，请稍后再试~"
                )

        except Exception as e:
            _log.error(f"处理图片测试时发生错误: {e}")
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text=f"图片分析出错: {str(e)}"
            )

    @registrar.qq.on_group_message()
    async def handle_fake_ai(
        self, input: Optional[GroupMessage] = None, *args, **kwargs
    ) -> None:
        # 兼容统一注册器未找到 plugin 时只传 event 的情况（此时仅有一个参数，被当作 self 传入）
        if input is None and not args:
            input = self
            self = _fake_ai_plugin_instance[0]
            if self is None and _fake_ai_loader:
                try:
                    plug = _fake_ai_loader.get_plugin("FakeAi")
                    if plug is not None:
                        _fake_ai_plugin_instance[0] = plug
                        self = plug
                except Exception:
                    pass
            # 主模块热重载后 _fake_ai_plugin_instance[0] 会变 None，从 memory 取回实例（memory 通常不重载）
            if self is None:
                try:
                    from plugins.FakeAi import memory as _memory

                    plug = getattr(_memory, "fake_ai_plugin_ref", None)
                    if plug is not None:
                        _fake_ai_plugin_instance[0] = plug
                        self = plug
                except Exception:
                    pass
            if self is None:
                return
        else:
            # 正常 (plugin, event) 调用时刷新引用，便于热重载后恢复
            _fake_ai_plugin_instance[0] = self
        # 检查消息是否来自排除的插件
        if await self._is_from_excluded_plugin(input):
            return

        group_id = input.group_id
        sender_id = input.sender.user_id
        sender_name = input.sender.nickname

        if not _is_fakeai_group_allowed(group_id):
            return

        # 将 MessageArray 转换为可序列化的格式
        content = []
        for msg_segment in input.message:
            if isinstance(msg_segment, PlainText):
                content.append({"type": "text", "data": {"text": msg_segment.text}})
            elif isinstance(msg_segment, At):
                content.append({"type": "at", "data": {"qq": msg_segment.user_id}})
            elif isinstance(msg_segment, Image):
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

        is_at_message = f"[CQ:at,qq={BOT_QQ}]" in input.raw_message
        clean_message = re.sub(r"\[CQ:[^\]]+\]", "", input.raw_message).strip()

        # 管理员回复「撤回」为管理指令，不触发 AI
        if (
            is_admin(sender_id)
            and (
                "[CQ:reply," in input.raw_message
                or any(isinstance(s, Reply) for s in input.message)
            )
            and clean_message == "撤回"
        ):
            return

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
                content = await self._process_image_descriptions(
                    content, group_id=group_id, user_id=sender_id
                )

                reply_json = _stamp_reply_dict(
                    {
                        "name": sender_name,
                        "id": sender_id,
                        "content": content,
                        "group_nickname": input.sender.card or sender_name,
                    }
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
            if is_admin(sender_id) or _is_fakeai_group_allowed(group_id):
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
            # 提取消息中被@的用户（排除机器人自己）
            target_user_id = None
            for msg_segment in input.message:
                if isinstance(msg_segment, At):
                    uid = msg_segment.user_id
                    if uid and str(uid) != str(BOT_QQ):
                        target_user_id = int(uid)
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

        # —— 统一入账后走状态机（取消裸 8% 抽卡）——
        reply_cache = group_reply_caches.setdefault(group_id, ReplyCache())
        content = await self._process_image_descriptions(
            content, group_id=group_id, user_id=sender_id
        )
        reply_json = _stamp_reply_dict(
            {
                "name": sender_name,
                "id": sender_id,
                "content": content,
                "group_nickname": input.sender.card or sender_name,
            }
        )
        reply_cache.add_reply(reply_json)
        self._add_to_message_buffer(group_id, int(sender_id), sender_name, content)

        is_at_bot = is_at_message or any(
            isinstance(s, At) and str(getattr(s, "user_id", "")) == BOT_QQ
            for s in input.message
        )
        det = state_store.determine(
            int(group_id),
            reply_cache.get_replies(),
            clean_message,
            is_at_bot,
        )
        _triggered = (
            det.state in (InteractionState.SUMMONED, InteractionState.FAMILIAR)
            or det.is_empty_summon
        )
        _state_msg = (
            "[FakeAi] group=%s state=%s reason=%s empty_summon=%s",
            group_id,
            det.state.value,
            det.reason,
            det.is_empty_summon,
        )
        if DEFAULT_CONFIG.verbose_log or _triggered:
            _log.info(*_state_msg)
        else:
            _log.debug(*_state_msg)

        if det.state in (InteractionState.NOT_PRESENT, InteractionState.OBSERVATION):
            return

        if det.is_empty_summon:
            _log.info("[FakeAi] 空召唤，不回复 group=%s", group_id)
            return

        # 召唤连打：用户 CD + 尽早占群 CD（防并发双回）
        if det.state == InteractionState.SUMMONED:
            if not is_admin(sender_id) and not check_user_cd(sender_id):
                return
            if not is_admin(sender_id) and not try_acquire_group_cd(group_id):
                return

        gid = int(group_id)
        # LLM 常 > 群 CD：生成中禁止同群再开一条完整流水线（本轮双表情根因）
        if gid in _inflight_groups:
            _log.info("[FakeAi] group=%s 上轮仍在生成，跳过", gid)
            return
        _inflight_groups.add(gid)
        try:
            decision = await analyst_decide(
                det.state,
                reply_cache.get_replies(),
                config=DEFAULT_CONFIG,
                is_empty_summon=False,
            )

            # FAMILIAR：分析员失败时过渡 fallback
            if (
                not decision.should_reply
                and det.state == InteractionState.FAMILIAR
                and decision.silence_reason in ("analyst_error", "parse_error")
                and DEFAULT_CONFIG.fallback_random_prob > 0
                and random.random() < DEFAULT_CONFIG.fallback_random_prob
            ):
                _log.warning(
                    "[FakeAi] fallback used reason=%s group=%s prob=%s",
                    decision.silence_reason,
                    group_id,
                    DEFAULT_CONFIG.fallback_random_prob,
                )
                decision = AnalystDecision(
                    should_reply=True,
                    urgency="low",
                    reply_strategy="轻轻接一句氛围，别抢戏",
                    topic="群聊热闹",
                    source="fallback",
                )

            if not decision.should_reply:
                _log.info(
                    "[FakeAi] analyst 否决 group=%s source=%s silence=%s",
                    group_id,
                    decision.source,
                    decision.silence_reason,
                )
                state_store.enter_not_present(gid)
                return

            if not await should_reply_by_favorability(
                int(sender_id), list(FAVOR_SKIP_IDS)
            ):
                state_store.enter_not_present(gid)
                return

            # FAMILIAR：通过决策后再占群 CD
            if det.state == InteractionState.FAMILIAR:
                if not is_admin(sender_id) and not try_acquire_group_cd(group_id):
                    return

            trigger_type = (
                "active" if det.state == InteractionState.SUMMONED else "passive"
            )
            answer = await answer_ai(
                group_id,
                group_reply_caches,
                str(sender_id),
                trigger_type,
                decision=decision,
            )
            _log.info(answer)
            if not answer or str(answer).strip() in ("", '""'):
                state_store.enter_not_present(gid)
                return

            await send_typing_response(self, input, answer)
            state_store.enter_observation(
                gid,
                from_familiar=(det.state == InteractionState.FAMILIAR),
            )
            try:
                await maybe_auto_remember(
                    clean_message,
                    user_id=int(sender_id) if str(sender_id).isdigit() else None,
                    group_id=gid,
                )
            except Exception as e:
                _log.debug("[FakeAi] auto_remember 失败: %s", e)

            if enable_callback:
                callback_state.add_waiting_user(sender_id, group_id)
            if not is_admin(sender_id):
                user_trigger_times[sender_id] = datetime.now()
        finally:
            _inflight_groups.discard(gid)


async def send_typing_response(self: FakeAi, input: GroupMessage, answer: str) -> None:
    try:
        # 尝试解析 JSON
        try:
            replace = json.loads(answer.replace("{{", "{").replace("}}", "}"))
            content = replace.get("content", "")
        except json.JSONDecodeError:
            # 如果不是 JSON 格式，直接使用原始内容
            content = answer

        sticker_paths = []
        if EXPRESSION_ENABLED and isinstance(content, str):
            content, sticker_paths = extract_stickers(
                content, max_count=MAX_STICKERS_PER_REPLY
            )
            if sticker_paths:
                _log.info(
                    "[FakeAi] 将另发贴纸: %s",
                    [p.stem for p in sticker_paths],
                )

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
        members_response = await self.api.qq.query.get_group_member_list(
            group_id=group_id
        )
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
                    message_elements.append(PlainText(text=text_before_at))

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
                    message_elements.append(At(user_id=user_id))
                    message_elements.append(PlainText(text=" "))

                last_match_end = match.end()

            # 处理 @ 之后的文本
            text_after_last_at = sentence[last_match_end:].strip()
            if text_after_last_at:
                message_elements.append(PlainText(text=text_after_last_at))

            # 模拟打字的延时，根据句子的字符数设置延时（可通过开关控制）
            if enable_typing_delay:
                delay = len(sentence) * typing_delay_per_char
                await asyncio.sleep(delay)

            # 发送消息
            if message_elements:  # 确保消息链不为空
                message = MessageChain(message_elements)
                await self.api.qq.post_group_msg(group_id=input.group_id, rtf=message)

        # 贴纸单独成消息：用动画表情（image.sub_type=1），勿当普通图
        for spath in sticker_paths:
            try:
                await self.api.qq.send_group_sticker(
                    input.group_id, str(spath)
                )
            except Exception as e:
                _log.error("[FakeAi] 贴纸发送失败 %s: %s", spath, e)

        # 将AI的回复加入到reply_cache中
        reply_cache = group_reply_caches.setdefault(group_id, ReplyCache())
        reply_json = _stamp_reply_dict(
            {"name": "蓝晴", "id": "0", "content": content}
        )
        reply_cache.add_reply(reply_json)

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


def try_acquire_group_cd(group_id: int) -> bool:
    """原子检查并占用群 CD。True=可触发且已记时，False=冷却中。"""
    if not enable_group_cd:
        return True
    now = datetime.now()
    last = last_trigger_times.get(group_id)
    if last:
        remaining = trigger_interval - (now - last).total_seconds()
        if remaining > 0:
            _log.info(f"群CD中: {group_id}, 剩余 {remaining:.2f}秒")
            return False
    last_trigger_times[group_id] = now
    return True


def check_cd(group_id: int) -> bool:
    """仅检查群 CD 是否已过（不占用）。"""
    if not enable_group_cd:
        return True
    last_trigger_time = last_trigger_times.get(group_id)
    if not last_trigger_time:
        return True
    remaining_time = trigger_interval - (
        datetime.now() - last_trigger_time
    ).total_seconds()
    _log.info(f"群CD检查: {group_id}, 剩余时间: {remaining_time:.2f}秒")
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
    group_id: str,
    user_id: str = None,
    tokens: int = 0,
    source: str = "active",
    trigger_type: str = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    prompt_cache_hit_tokens: int = 0,
    model: str = "deepseek-v4-flash",
):
    """保存 AI 使用统计（委托 AiStatsRecorder）。"""
    from common.utils.AiStatsRecorder import record_ai_usage

    record_ai_usage(
        group_id=group_id,
        user_id=user_id,
        tokens=tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_cache_hit_tokens=prompt_cache_hit_tokens,
        model=model,
        source=source,
        trigger_type=trigger_type,
    )


async def load_yaml_data(group_id) -> Dict:
    # if group_id == 719518427:
    #     return await load_yaml("data/yml/lanqingv1_ai.yml")
    return await load_yaml("data/yml/lanqingv1.yml")


def replace_time_in_system(yaml_data: Dict) -> None:
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    system_content = yaml_data.get("system", "")
    input_content = yaml_data.get("input", "")
    if system_content:
        yaml_data["system"] = system_content.replace("{time}", current_time)
    if input_content:
        yaml_data["input"] = input_content.replace("{time}", current_time)


def _reply_content_to_text(content) -> str:
    """从 reply 的 content 字段（可能是 str 或消息段 list）提取纯文本用于展示"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for seg in content:
            if isinstance(seg, dict) and seg.get("type") == "text":
                text = seg.get("data", {}).get("text", "")
                if text:
                    text_parts.append(text)
        return " ".join(text_parts)
    return str(content) if content else ""


def update_yaml_with_replies(yaml_data: Dict, reply_cache: ReplyCache) -> Dict:
    replies = reply_cache.get_replies()
    if replies:
        # 可读发言人格式（轻量 I2）+ 保留 JSON 行供兼容
        lines = []
        readable = []
        for reply_json in replies:
            try:
                data = json.loads(reply_json)
                source = data.get("source", "")
                sub_source = data.get("sub_source", "")
                name = data.get("group_nickname") or data.get("name", "")
                uid = str(data.get("id", ""))
                text = _reply_content_to_text(data.get("content", ""))
                ts = data.get("ts")
                if isinstance(ts, (int, float)) and ts > 0:
                    stamp = datetime.fromtimestamp(ts).strftime("%H:%M")
                else:
                    stamp = "--:--"

                if source:
                    sub_map = {
                        "repeat": "复读",
                        "speak": "主动发言",
                        "random": "随机回复",
                        "other": "其他",
                    }
                    sub_display = sub_map.get(sub_source, sub_source or "")
                    tag = source + (f"-{sub_display}" if sub_display else "")
                    data["name"] = f"{data.get('name', '')}(插件消息:{tag}，并非你生成)"
                    name = f"{name}(插件)"

                who = "蓝晴" if uid == "0" else name
                readable.append(f"[{stamp}] {who}: {text}")

                data.pop("source", None)
                data.pop("sub_source", None)
                lines.append(json.dumps(data, ensure_ascii=False))
            except (json.JSONDecodeError, TypeError):
                lines.append(reply_json)
                readable.append(reply_json)
        new_history = "\n".join(readable) if readable else "\n".join(lines)
        last_reply = readable[-1] if readable else (lines[-1] if lines else "")
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


def try_extract_remember_phrase(clean_text: str) -> Optional[str]:
    """从「记住…」类话语提取待铭记短句。"""
    if not clean_text:
        return None
    # 一次性行程/闲聊粗过滤
    ephemeral = ("现在", "马上", "下楼", "等会", "一会儿", "一会")
    m = re.search(
        r"(?:请?记住|记得一下|记一下)[，,:\s]*(.+)$",
        clean_text.strip(),
    )
    if not m:
        return None
    judgment = m.group(1).strip().strip("。.!！?？")
    if len(judgment) < 2:
        return None
    if any(w in judgment for w in ephemeral) and "以后" not in judgment and "总是" not in judgment:
        return None
    return judgment[:120]


async def maybe_auto_remember(
    clean_text: str,
    *,
    user_id: Optional[int],
    group_id: Optional[int],
) -> None:
    if not DEFAULT_COGNITION.auto_remember_on_phrase:
        return
    if not DEFAULT_COGNITION.memory_record_enabled:
        return
    judgment = try_extract_remember_phrase(clean_text)
    if not judgment:
        return
    scope = f"user:{user_id}" if user_id else "public"
    result = await memory_manager.remember(
        judgment,
        reasoning="用户明确要求记住",
        tags=["用户嘱托"],
        strength=70,
        memory_type="preference",
        scope=scope,
        source_user_id=user_id,
    )
    _log.info("[FakeAi] auto_remember %s scope=%s → %s", judgment, scope, result)


async def answer_ai(
    group_id: int,
    group_reply_caches: Dict[int, ReplyCache],
    user_id: str = None,
    trigger_type: str = "active",
    decision: Optional[AnalystDecision] = None,
) -> str:
    # 确保数据库已初始化
    await memory_manager.init_db()

    # 加载 YAML 数据
    yaml_data = await load_yaml_data(group_id)
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

    # 注入相关主动记忆
    mem_text = ""
    try:
        if (
            DEFAULT_COGNITION.memory_record_enabled
            and DEFAULT_COGNITION.auto_recall_enabled
        ):
            reply_cache_m = group_reply_caches.get(group_id, ReplyCache())
            recent_bits = []
            for reply_json in reply_cache_m.get_replies()[-8:]:
                try:
                    rd = json.loads(reply_json)
                    if str(rd.get("id", "")) == "0":
                        continue
                    t = _reply_content_to_text(rd.get("content", ""))
                    if t:
                        recent_bits.append(t)
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
            topic_q = ""
            if decision is not None and decision.topic:
                topic_q = decision.topic
            if not topic_q:
                topic_q = " ".join(recent_bits)[-120:]
            uid_i = int(user_id) if user_id and str(user_id).isdigit() else None
            mem_text = await memory_manager.get_memories_text(
                topic_q,
                user_id=uid_i,
                group_id=int(group_id) if group_id else None,
                limit=DEFAULT_COGNITION.auto_recall_limit,
                max_length=300,
            )
            if mem_text:
                replace_placeholder(yaml_data, "{related_memories}", mem_text)
                _log.info("[FakeAi] 注入主动记忆: %s", mem_text[:100])
            else:
                replace_placeholder(yaml_data, "{related_memories}", "（暂无相关记忆）")
                mem_text = ""
        else:
            replace_placeholder(yaml_data, "{related_memories}", "（暂无相关记忆）")
            mem_text = ""
    except Exception as e:
        _log.debug(f"[FakeAi] 获取主动记忆失败: {e}")
        replace_placeholder(yaml_data, "{related_memories}", "（暂无相关记忆）")
        mem_text = ""

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
                query_text, max_length=DEFAULT_COGNITION.knowledge_inject_max_chars
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
    if decision is not None and decision.should_reply:
        inject = decision.inject_block()
        if inject:
            prompt = (prompt or "") + inject
    # YAML 若无 {related_memories} 占位，追加一块，避免静默丢失
    if mem_text and "{related_memories}" not in (yaml_data.get("system") or ""):
        prompt = (prompt or "") + f"\n\n【相关记忆】\n{mem_text}"
    if EXPRESSION_ENABLED:
        sticker_block = sticker_catalog.prompt_block()
        if sticker_block:
            prompt = (prompt or "") + sticker_block
            _log.info(
                "[FakeAi] 已注入贴纸提示 names=%s",
                len(sticker_catalog.names()),
            )
        else:
            _log.warning("[FakeAi] 贴纸目录为空，跳过表情注入")
    ai_response = await AiUtil.search_deepseek(keyword, prompt)

    # 处理AI响应
    if isinstance(ai_response, dict):
        response = ai_response.get("content", "")
        usage_info = ai_response.get("usage", {})
        usage_info.get("total_tokens", 0)
        usage_info.get("prompt_tokens", 0)
        usage_info.get("completion_tokens", 0)
        usage_info.get("prompt_cache_hit_tokens", 0)
        ai_response.get("model", "deepseek-v4-flash")
    else:
        response = ai_response or ""

    # 记录AI使用统计
    if group_id:
        try:
            from common.utils.AiStatsRecorder import record_from_response

            record_from_response(
                str(group_id),
                str(user_id) if user_id else None,
                trigger_type,
                ai_response if isinstance(ai_response, dict) else None,
            )
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
