import asyncio
import base64
import io
import json
import random
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Union

from PIL import Image as PILImage, ImageDraw, ImageFont
from common.utils.AiUtil import AiUtil, DEEPSEEK_CHAT_MODEL
from common.utils.async_io import load_json, load_yaml
from common.utils.CommonUtil import CommonUtil
from ncatbot.event.qq import GroupMessageEvent as GroupMessage, NoticeEvent
from ncatbot.types import At, MessageArray as MessageChain, PlainText, Reply, Image
from ncatbot.types.qq import Forward
from ncatbot.plugin import NcatBotPlugin
from ncatbot.core import registrar
from ncatbot.utils import get_log

from .memory import (
    memory_manager,
    generate_summary_from_messages,
    generate_user_impression,
)
from .agent import AgentAction, AgentResult, run_agent
from .favorability import (
    can_view_relation_tag,
    format_favorability,
    get_reply_probability,
    get_tier,
)
from .cognition import DEFAULT_COGNITION
from . import expression as expr_pkg
from .expression import extract_stickers, sticker_catalog, whale_catalog
from .interaction import (
    DEFAULT_CONFIG,
    InteractionState,
    analyst_decide,
    state_store,
)
from .interaction.analyst import AnalystDecision
from .settings import (
    FAVOR_SKIP_IDS,
    FROZEN_USERS,
    apply_from_plugin,
    is_admin,
    set_group_enabled,
)

_log = get_log()
BOT_QQ = DEFAULT_CONFIG.bot_qq


def _stamp_reply_dict(payload: dict) -> str:
    """写入 ReplyCache 用 JSON，自动补 ts。"""
    if "ts" not in payload:
        payload["ts"] = time.time()
    return json.dumps(payload, ensure_ascii=False)


def _collect_image_urls(content: list) -> list:
    """收集当前消息里的图片 URL，供 DeepSeek 原生视觉使用。"""
    urls = []
    for item in content or []:
        if isinstance(item, dict) and item.get("type") == "image":
            data = item.get("data") or {}
            url = data.get("url") or ""
            if url:
                urls.append(str(url))
    return urls


def _mark_images_without_vision(content: list) -> list:
    """agent 模式不再预描述图片，历史里只留 [图片] 占位。"""
    for item in content or []:
        if isinstance(item, dict) and item.get("type") == "image":
            item.clear()
            item.update({"type": "text", "data": {"text": "[图片]"}})
    return content


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

# 主动机会：群里安静足够久时，低概率自己起话题；可配置关闭
PROACTIVE_ENABLED = True
PROACTIVE_QUIET_SEC = 45 * 60
PROACTIVE_INTERVAL_SEC = 3 * 3600
PROACTIVE_PROBABILITY = 0.2
_proactive_last: Dict[int, float] = {}

# 最近消息持久化：重启后仍能接上刚才的聊天，只保留 6 小时内
RECENT_CACHE_PATH = Path("data/FakeAi/recent_cache.json")
RECENT_CACHE_MAX_AGE = 6 * 3600
# 同群生成中互斥：LLM 耗时常超过群 CD，避免并发展开第二条回复流水线
_inflight_groups: set = set()

# 被动触发：已改为状态机 + 分析员；裸 8% 废弃（见 interaction.config.fallback_random_prob）
PASSIVE_TRIGGER_BASE_PROB = 0.0  # noqa: F841  # 保留名以免外部引用炸；实际不再使用

# 允许 FakeAi 响应的群号（空集合表示不限制）— on_load / settings 会覆盖
FAKEAI_GROUP_MODE = "allowlist"
FAKEAI_ALLOWED_GROUPS = frozenset({853963912, 719518427, 585479130, 1064163905})
FAKEAI_DISABLED_GROUPS = frozenset()
FAKEAI_GROUP_ADMIN_ID = "273421673"


def _is_fakeai_group_allowed(group_id) -> bool:
    """按配置判断群是否启用 FakeAi。"""
    try:
        group = int(group_id)
    except (TypeError, ValueError):
        return False
    if group in FAKEAI_DISABLED_GROUPS:
        return False
    if FAKEAI_GROUP_MODE == "all":
        return True
    return group in FAKEAI_ALLOWED_GROUPS


# ---- 事件感知 ----
# 进退群 / 上传文件 / 戳一戳这类事件也进模型视野，但发言按群限流，避免连击烧钱
EVENT_SPEAK_CD = 25  # 秒
_event_last_spoken: Dict[int, float] = {}
_nickname_cache: Dict[tuple, tuple] = {}  # (group_id, user_id) -> (name, ts)
NICKNAME_CACHE_TTL = 600


def _event_can_speak(group_id: int) -> bool:
    """事件发言的按群冷却：True=可以尝试发言并已占位。"""
    now = time.time()
    if now - _event_last_spoken.get(group_id, 0) < EVENT_SPEAK_CD:
        return False
    _event_last_spoken[group_id] = now
    return True


# 模拟打字延迟开关（默认关闭）
enable_typing_delay = False
# 每个字符的延迟时间（秒）
typing_delay_per_char = 0.1

# 工具型 agent：模型自己决定发消息/表情/拍一拍/搜索/沉默
USE_DS_AGENT = True
# 当前人设（可在 plugin_configs.FakeAi.persona 改）
PERSONA = "lanqing"
PERSONA_FILES = {
    "lanqing": "plugins/FakeAi/prompts/lanqing_agent.yml",
    "default": "plugins/FakeAi/prompts/default_agent.yml",
    "blue_bigfish": "plugins/FakeAi/prompts/blue_bigfish_agent.yml",
    "deepsleep_gamer": "plugins/FakeAi/prompts/deepsleep_gamer_agent.yml",
    "xiaojingyu_polite": "plugins/FakeAi/prompts/xiaojingyu_polite_agent.yml",
}
AGENT_YAML_PATH = PERSONA_FILES["lanqing"]


def persona_path(name: str) -> str:
    """人设 key -> yml 路径；未知 key 返回空串。"""
    return PERSONA_FILES.get(str(name or "").strip(), "")
# 是否把插件能力目录（SkillHub）接进 agent 工具循环
SKILLS_ENABLED = True
# 单轮对话的 token 上限，防止工具循环失控（可在 plugin_configs.FakeAi.agent_max_tokens 改）
AGENT_MAX_TOKENS = 60000
# 单轮工具循环的最大轮数（可在 plugin_configs.FakeAi.agent_max_rounds 改）
AGENT_MAX_ROUNDS = 12


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

    # 后台配置页的展示元数据（中文名 / 下拉选项）
    config_labels = {
        "persona": "人设",
        "agent_max_rounds": "单轮最多工具轮数",
        "agent_max_tokens": "单轮 Token 上限",
        "skills_enabled": "允许调用插件技能",
        "agent_enabled": "启用 Agent 模式",
    }
    config_choices = {
        "sticker_library": [
            {"value": "both", "label": "本地 + 梗鲸"},
            {"value": "darkdog", "label": "仅本地（darkdog）"},
            {"value": "gengjing", "label": "仅梗鲸（网络梗图）"},
        ],
        "persona": [
            {"value": "lanqing", "label": "蓝晴（默认）"},
            {"value": "default", "label": "小鲸鱼（QQ-Agent 默认）"},
            {"value": "blue_bigfish", "label": "蓝色大肥鱼"},
            {"value": "deepsleep_gamer", "label": "大肥鱼 Deepsleep（游戏群友版）"},
            {"value": "xiaojingyu_polite", "label": "小鲸鱼（乖巧不说脏话）"},
        ],
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        global _fake_ai_loader
        _fake_ai_plugin_instance[0] = self
        _fake_ai_loader = getattr(self, "_loader", None)
        # 同时写入 memory 模块，主模块被热重载后仍可通过 memory 取回实例
        try:
            from . import memory as _memory

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

    def apply_config(self) -> None:
        """后台保存配置后立即生效，不必手动重载插件。"""
        try:
            apply_from_plugin(self)
        except Exception as e:
            _log.error("[FakeAi] 应用配置失败: %s", e)

    async def on_load(self):
        """加载插件"""
        _fake_ai_plugin_instance[0] = self
        try:
            apply_from_plugin(self)
        except Exception as e:
            _log.error("[FakeAi] 配置加载失败，使用代码默认值: %s", e)

        # 初始化长期记忆数据库
        await memory_manager.init_db()
        self._load_recent_caches()
        self.add_scheduled_task(
            "fakeai_recent_persist",
            "5m",
            callback=self._persist_recent_caches,
        )
        CommonUtil.cleanup_backup_files(["data/db"], keep_seconds=86400)
        self.add_scheduled_task(
            "fakeai_db_backup_cleanup",
            "24h",
            callback=lambda: CommonUtil.cleanup_backup_files(
                ["data/db"], keep_seconds=86400
            ),
        )
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

        if PROACTIVE_ENABLED and USE_DS_AGENT:
            self.add_scheduled_task(
                "fakeai_proactive",
                "30m",
                callback=self._proactive_tick,
            )
            _log.info("[FakeAi] 主动机会任务已注册")

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

    async def on_close(self) -> None:
        """正常退出时落盘最近消息并关闭记忆库连接。"""
        self._persist_recent_caches()
        try:
            await memory_manager.close()
        except Exception as e:
            _log.debug("[FakeAi] 关闭记忆数据库失败: %s", e)

    def _load_recent_caches(self) -> None:
        """启动时恢复最近消息缓存（只保留 6 小时内）。"""
        try:
            if not RECENT_CACHE_PATH.is_file():
                return
            data = json.loads(RECENT_CACHE_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            now = time.time()
            restored = 0
            for raw_gid, entries in data.items():
                try:
                    gid = int(raw_gid)
                except (TypeError, ValueError):
                    continue
                cache = ReplyCache()
                for raw in entries or []:
                    try:
                        ts = float(json.loads(raw).get("ts") or 0)
                    except (json.JSONDecodeError, TypeError, ValueError):
                        continue
                    if ts and now - ts <= RECENT_CACHE_MAX_AGE:
                        cache.add_reply(raw)
                if cache.get_replies():
                    group_reply_caches[gid] = cache
                    restored += 1
            if restored:
                _log.info("[FakeAi] 已恢复最近消息缓存 groups=%s", restored)
        except Exception as e:
            _log.warning("[FakeAi] 恢复最近消息失败: %s", e)

    def _persist_recent_caches(self) -> None:
        """把最近消息缓存写盘，供重启后接上上下文。"""
        try:
            RECENT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            now = time.time()
            data = {}
            for gid, cache in group_reply_caches.items():
                kept = []
                for raw in cache.get_replies():
                    try:
                        ts = float(json.loads(raw).get("ts") or 0)
                    except (json.JSONDecodeError, TypeError, ValueError):
                        continue
                    if ts and now - ts <= RECENT_CACHE_MAX_AGE:
                        kept.append(raw)
                if kept:
                    data[str(gid)] = kept[-20:]
            tmp = RECENT_CACHE_PATH.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
            tmp.replace(RECENT_CACHE_PATH)
        except Exception as e:
            _log.warning("[FakeAi] 保存最近消息失败: %s", e)

    async def _proactive_tick(self) -> None:
        """群里安静足够久时，低概率主动起一个话题。"""
        if not PROACTIVE_ENABLED or not USE_DS_AGENT:
            return
        now = time.time()
        groups = set(group_reply_caches.keys())
        if FAKEAI_GROUP_MODE != "all":
            groups &= set(FAKEAI_ALLOWED_GROUPS)
        for gid in list(groups):
            if gid in _inflight_groups:
                continue
            cache = group_reply_caches.get(gid)
            if not cache:
                continue
            last_ts = 0.0
            for raw in reversed(cache.get_replies()):
                try:
                    ts = float(json.loads(raw).get("ts") or 0)
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
                if ts:
                    last_ts = ts
                    break
            if not last_ts or now - last_ts < PROACTIVE_QUIET_SEC:
                continue
            if now - _proactive_last.get(gid, 0) < PROACTIVE_INTERVAL_SEC:
                continue
            if random.random() > PROACTIVE_PROBABILITY:
                continue
            _proactive_last[gid] = now
            try:
                result = await answer_ai(
                    gid, group_reply_caches, None, "proactive"
                )
                if isinstance(result, AgentResult) and result.actions:
                    await send_agent_result(self, None, result, group_id=gid)
                    _log.info("[FakeAi] 主动机会发言 group=%s", gid)
                else:
                    _log.info("[FakeAi] 主动机会选择沉默 group=%s", gid)
            except Exception as e:
                _log.error("[FakeAi] 主动机会失败 group=%s: %s", gid, e)
            return

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

    @staticmethod
    def _text_content(text: str) -> dict:
        return {"type": "text", "data": {"text": text}}

    @staticmethod
    def _message_field(message: object, field: str, default=None):
        if isinstance(message, dict):
            return message.get(field, default)
        return getattr(message, field, default)

    @classmethod
    def _sender_label(cls, message: object) -> str:
        sender = cls._message_field(message, "sender")
        if sender is None:
            sender = message
        nickname = cls._message_field(sender, "nickname") or cls._message_field(
            sender, "card"
        )
        user_id = cls._message_field(sender, "user_id", "")
        return str(nickname or user_id or "未知用户")

    @staticmethod
    def _raw_message_chain(raw_message: object) -> Optional[MessageChain]:
        if isinstance(raw_message, MessageChain):
            return raw_message
        if not isinstance(raw_message, (list, str)):
            return None
        try:
            return MessageChain.from_any(raw_message)
        except (TypeError, ValueError, KeyError):
            return None

    async def _content_from_message_data(
        self, message_data: object, depth: int
    ) -> list:
        raw_segments = self._message_field(message_data, "message")
        if raw_segments is None:
            raw_segments = self._message_field(message_data, "content")
        message_chain = self._raw_message_chain(raw_segments)
        if message_chain is not None:
            return await self._build_message_content(message_chain, depth=depth)

        raw_message = self._message_field(message_data, "raw_message", "")
        if raw_message:
            return [self._text_content(str(raw_message))]
        return []

    async def _read_reply_content(self, segment: Reply, depth: int) -> list:
        if depth >= 2:
            return [self._text_content("[引用消息内容层级过深]")]
        try:
            message_data = await self.api.qq.query.get_msg(segment.id)
            content = await self._content_from_message_data(message_data, depth + 1)
            if not content:
                content = [self._text_content("[空消息]")]
            return [
                self._text_content(f"[引用消息] {self._sender_label(message_data)}：")
            ] + content + [self._text_content("\n")]
        except Exception as e:
            _log.debug("[FakeAi] 读取引用消息失败 message_id=%s: %s", segment.id, e)
            return [self._text_content(f"[引用消息 {segment.id}]")]

    async def _read_forward_content(self, segment: Forward, depth: int) -> list:
        if depth >= 2:
            return [self._text_content("[合并转发内容层级过深]")]

        nodes = list(segment.content or [])
        if not nodes and segment.id:
            try:
                forward_data = await self.api.qq.query.get_forward_msg(segment.id)
                nodes = list(self._message_field(forward_data, "messages", []) or [])
            except Exception as e:
                _log.debug(
                    "[FakeAi] 读取合并转发失败 message_id=%s: %s", segment.id, e
                )
                return [self._text_content(f"[合并转发消息 {segment.id}]")]

        if not nodes:
            return [self._text_content("[空合并转发]")]

        content = [self._text_content("[合并转发]\n")]
        for node in nodes:
            node_content = await self._content_from_message_data(node, depth + 1)
            if not node_content:
                node_content = [self._text_content("[空消息]")]
            content.append(self._text_content(f"{self._sender_label(node)}："))
            content.extend(node_content)
            content.append(self._text_content("\n"))
        return content

    async def _build_message_content(
        self, message: MessageChain, *, depth: int = 0
    ) -> list:
        """将消息段转换为 AI 上下文，并展开引用消息和合并转发。"""
        content = []
        for msg_segment in message:
            if isinstance(msg_segment, Reply):
                content.extend(await self._read_reply_content(msg_segment, depth))
            elif isinstance(msg_segment, Forward):
                content.extend(await self._read_forward_content(msg_segment, depth))
            elif isinstance(msg_segment, PlainText):
                content.append(self._text_content(msg_segment.text))
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
            elif hasattr(msg_segment, "data") and "image" in str(
                msg_segment.data
            ).lower():
                content.append({"type": "image", "data": {"raw": str(msg_segment)}})
        return content

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

        # GalgameBox 命令
        if first_word.lower() == "gal":
            return True

        # TodayWaifu 命令
        if first_word in (
            "今日老婆",
            "jrlp",
            "抽老婆",
            "我的老婆",
            "wdlp",
            "抽取历史",
            "关系图",
            "羁绊图谱",
            "gxt",
            "rbq排行",
            "rbqph",
            "抽老婆帮助",
            "老婆插件帮助",
            "clpbz",
        ):
            return True
        if first_word in ("强娶", "qiangqu", "求婚", "qh"):
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

            # 先下载图片转 base64（DeepSeek 无法直接访问 QQ 图床）
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
                    from . import memory as _memory

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
        # 模型代发的原插件命令：只让命令插件处理，AI 不插嘴
        if getattr(getattr(input, "_data", None), "synthetic", False):
            return
        # 检查消息是否来自排除的插件
        if await self._is_from_excluded_plugin(input):
            return

        group_id = input.group_id
        sender_id = input.sender.user_id
        sender_name = input.sender.nickname
        clean_message = re.sub(r"\[CQ:[^\]]+\]", "", input.raw_message).strip()

        if clean_message in {"蓝晴启用", "蓝晴关闭"}:
            if str(sender_id) != FAKEAI_GROUP_ADMIN_ID:
                await self.api.qq.post_group_msg(
                    group_id=group_id, text="只有管理员 273421673 可以修改蓝晴群组状态。"
                )
                return
            enabled = clean_message == "蓝晴启用"
            try:
                set_group_enabled(self, group_id, enabled)
            except (TypeError, ValueError):
                await self.api.qq.post_group_msg(
                    group_id=group_id, text="无法识别当前群号，蓝晴群组状态未修改。"
                )
                return
            await self.api.qq.post_group_msg(
                group_id=group_id,
                text=f"本群蓝晴已{'启用' if enabled else '关闭'}。",
            )
            return

        if not _is_fakeai_group_allowed(group_id):
            return

        # 将 MessageArray 转换为可序列化的格式，并读取引用/合并转发正文
        content = await self._build_message_content(input.message)

        is_at_message = f"[CQ:at,qq={BOT_QQ}]" in input.raw_message
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

                # agent 模式把原图直接交给 DeepSeek；旧链路继续用文字描述
                image_urls = []
                if USE_DS_AGENT:
                    image_urls = _collect_image_urls(content)
                    content = _mark_images_without_vision(content)
                else:
                    content = await self._process_image_descriptions(
                        content, group_id=group_id, user_id=sender_id
                    )

                reply_json = _stamp_reply_dict(
                    {
                        "name": sender_name,
                        "id": sender_id,
                        "content": content,
                        "group_nickname": input.sender.card or sender_name,
                        "image_urls": image_urls,
                        "message_id": input.message_id,
                    }
                )
                reply_cache.add_reply(reply_json)
                # 添加到长期记忆缓冲区
                self._add_to_message_buffer(
                    group_id, int(sender_id), sender_name, content
                )
                answer = await answer_ai(
                    group_id,
                    group_reply_caches,
                    str(sender_id),
                    "callback",
                    sender_name=sender_name,
                )
                _log.info(answer)
                await send_typing_response(self, input, answer)
                return

        # 判断是否是功能测试的指令
        if input.raw_message == "蓝晴说话":
            if is_admin(sender_id) or _is_fakeai_group_allowed(group_id):
                answer = await answer_ai(
                    group_id,
                    group_reply_caches,
                    str(sender_id),
                    "test",
                    sender_name=sender_name,
                )
                _log.info(answer)
                await send_typing_response(self, input, answer)
                return

        # MiMo TTS：普通 API key 专用，Token Plan key 不回退使用。
        tts_match = re.match(r"^说(?:\s+|　+)(.+)$", input.raw_message.strip(), re.S)
        if tts_match:
            audio_bytes = await AiUtil.synthesize_mimo_tts(tts_match.group(1))
            if not audio_bytes:
                await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    rtf=MessageChain([PlainText(text="TTS 暂不可用，请检查普通 MiMo API key 配置")]),
                )
                return

            audio_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as audio_file:
                    audio_file.write(audio_bytes)
                    audio_path = audio_file.name
                await self.api.qq.send_group_record(input.group_id, audio_path)
            except Exception as e:
                _log.error(f"[TTS] 发送群语音失败: {e}")
            finally:
                if audio_path:
                    try:
                        Path(audio_path).unlink(missing_ok=True)
                    except OSError:
                        pass
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
        image_urls = []
        if USE_DS_AGENT:
            image_urls = _collect_image_urls(content)
            content = _mark_images_without_vision(content)
        else:
            content = await self._process_image_descriptions(
                content, group_id=group_id, user_id=sender_id
            )
        reply_json = _stamp_reply_dict(
            {
                "name": sender_name,
                "id": sender_id,
                "content": content,
                "group_nickname": input.sender.card or sender_name,
                "image_urls": image_urls,
                "message_id": input.message_id,
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
                sender_name=sender_name,
            )
            _log.info(answer)
            if isinstance(answer, AgentResult):
                if not answer.actions:
                    if det.state == InteractionState.SUMMONED:
                        answer = AgentResult(
                            actions=[
                                AgentAction(
                                    kind="text",
                                    messages=[random.choice(["？", "在", "咋了"])],
                                )
                            ],
                            usage=answer.usage,
                            model=answer.model,
                        )
                    else:
                        state_store.enter_not_present(gid)
                        return
            elif not answer or str(answer).strip() in ("", '""'):
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

    # ---------- 事件感知：notice / request 也进上下文 ----------

    async def _event_nickname(self, group_id: str, user_id: str) -> str:
        """带缓存的群昵称查询，失败就退回 QQ 号，不阻塞事件处理。"""
        if not user_id:
            return "某人"
        key = (str(group_id or ""), str(user_id))
        now = time.time()
        cached = _nickname_cache.get(key)
        if cached and now - cached[1] < NICKNAME_CACHE_TTL:
            return cached[0]
        name = ""
        if group_id:
            try:
                info = await self.api.qq.query.get_group_member_info(
                    group_id=int(group_id), user_id=int(user_id)
                )
                if info is not None:
                    name = str(
                        getattr(info, "card", "")
                        or getattr(info, "nickname", "")
                        or ""
                    ).strip()
            except Exception as e:
                _log.debug("[FakeAi] 查询昵称失败 %s: %s", user_id, e)
        if not name:
            name = f"用户{user_id}"
        _nickname_cache[key] = (name, now)
        return name

    async def _describe_notice(self, input: NoticeEvent) -> Optional[tuple]:
        """把 notice 事件翻译成一行群聊里看得懂的事件描述。

        返回 ``(描述, 是否值得开口)``；返回 None 表示这类事件直接忽略。
        """
        notice_type = str(getattr(input.notice_type, "value", input.notice_type) or "")
        raw_sub = getattr(input, "sub_type", "")
        sub_type = str(getattr(raw_sub, "value", raw_sub) or "")
        group_id = str(getattr(input, "group_id", "") or "")
        user_id = str(getattr(input, "user_id", "") or "")
        operator_id = str(getattr(input, "operator_id", "") or "")

        if notice_type == "group_increase":
            who = await self._event_nickname(group_id, user_id)
            if sub_type == "invite" and operator_id:
                inviter = await self._event_nickname(group_id, operator_id)
                return f"{who}（{user_id}）被 {inviter} 拉进了群", True
            return f"{who}（{user_id}）加入了群聊", True

        if notice_type == "group_decrease":
            who = await self._event_nickname(group_id, user_id)
            if sub_type == "kick" and operator_id:
                operator = await self._event_nickname(group_id, operator_id)
                return f"{who}（{user_id}）被 {operator} 踢出了群", True
            if sub_type == "kick_me":
                return f"你被踢出了群 {group_id}", False
            return f"{who}（{user_id}）退群了", True

        if notice_type == "group_upload":
            who = await self._event_nickname(group_id, user_id)
            file = getattr(input, "file", None)
            name = str(
                getattr(file, "name", "") or getattr(file, "file_name", "") or "文件"
            )
            size = 0
            try:
                size = int(getattr(file, "size", 0) or 0)
            except (TypeError, ValueError):
                size = 0
            if size >= 1024 * 1024:
                human = f"{size / 1024 / 1024:.1f} MB"
            elif size >= 1024:
                human = f"{size / 1024:.0f} KB"
            else:
                human = f"{size} B"
            return f"{who}（{user_id}）上传了文件「{name}」（{human}）", True

        if notice_type == "group_admin":
            who = await self._event_nickname(group_id, user_id)
            if sub_type == "set":
                return f"{who}（{user_id}）被设成了管理员", True
            return f"{who}（{user_id}）被撤了管理员", True

        if notice_type == "group_ban":
            who = await self._event_nickname(group_id, user_id)
            operator = ""
            if operator_id:
                operator = await self._event_nickname(group_id, operator_id)
            duration = int(getattr(input, "duration", 0) or 0)
            if duration > 0:
                minutes = max(1, duration // 60)
                by = f"被 {operator} " if operator else "被"
                return f"{who}（{user_id}）{by}禁言 {minutes} 分钟", True
            by = f"被 {operator} " if operator else "被"
            return f"{who}（{user_id}）{by}解除禁言", True

        if notice_type == "group_recall":
            who = await self._event_nickname(group_id, user_id or operator_id)
            return f"{who}（{user_id or operator_id}）撤回了一条消息", False

        if notice_type == "group_msg_emoji_like":
            # 表情回应太频繁，只当作噪音，不写进上下文
            return None

        if notice_type == "notify":
            if sub_type == "poke":
                who = await self._event_nickname(group_id or "", user_id)
                target = str(getattr(input, "target_id", "") or "")
                self_id = str(getattr(input, "self_id", "") or "")
                if target and target in {self_id, str(BOT_QQ)}:
                    return f"{who}（{user_id}）戳了戳你", True
                target_name = await self._event_nickname(group_id or "", target)
                return f"{who}（{user_id}）戳了戳 {target_name}（{target}）", False
            if sub_type == "lucky_king":
                lucky = str(getattr(input, "target_id", "") or user_id)
                who = await self._event_nickname(group_id, lucky)
                return f"{who}（{lucky}）成了运气王", True
            if sub_type == "honor":
                who = await self._event_nickname(group_id, user_id)
                honor = str(getattr(input, "honor_type", "") or "")
                label = {
                    "talkative": "龙王",
                    "performer": "群聊之火",
                    "emotion": "快乐源泉",
                }.get(honor, honor or "群荣誉")
                return f"{who}（{user_id}）拿到了「{label}」", True
            return None

        return None

    @registrar.on_notice()
    async def handle_fake_ai_notice(self, input: NoticeEvent) -> None:
        """进退群、上传文件、戳一戳这些事件也进模型视野，由它决定接不接话。"""
        try:
            if getattr(getattr(input, "_data", None), "synthetic", False):
                return
            group_id = str(getattr(input, "group_id", "") or "")
            if not group_id or not _is_fakeai_group_allowed(group_id):
                return
            described = await self._describe_notice(input)
            if not described:
                return
            desc, speak = described
            gid = int(group_id)
            cache = group_reply_caches.setdefault(gid, ReplyCache())
            cache.add_reply(
                _stamp_reply_dict(
                    {
                        "name": "系统",
                        "id": "event",
                        "kind": "event",
                        "content": desc,
                    }
                )
            )
            _log.info("[FakeAi] 感知事件: %s", desc)
            if not speak:
                return
            if not _event_can_speak(gid):
                _log.debug("[FakeAi] 事件发言冷却中，仅记录：%s", desc)
                return
            if gid in _inflight_groups:
                return
            _inflight_groups.add(gid)
            try:
                answer = await answer_ai(gid, group_reply_caches, None, "event")
                if isinstance(answer, AgentResult) and answer.actions:
                    await send_agent_result(self, None, answer, group_id=gid)
                else:
                    _log.info("[FakeAi] 事件后选择沉默: %s", desc)
            finally:
                _inflight_groups.discard(gid)
        except Exception as e:
            _log.error("[FakeAi] 处理事件通知失败: %s", e)

    @registrar.on_request()
    async def handle_fake_ai_request(self, input) -> None:
        """好友/加群请求只记录进上下文，让模型知道有人来过。"""
        try:
            raw_type = getattr(input, "request_type", "")
            request_type = str(getattr(raw_type, "value", raw_type) or "")
            user_id = str(getattr(input, "user_id", "") or "")
            group_id = str(getattr(input, "group_id", "") or "")
            comment = str(getattr(input, "comment", "") or "").strip()
            if request_type == "group" and group_id:
                if not _is_fakeai_group_allowed(group_id):
                    return
                who = await self._event_nickname(group_id, user_id)
                desc = f"{who}（{user_id}）请求加群"
            elif request_type == "friend":
                who = await self._event_nickname("", user_id)
                desc = f"{who}（{user_id}）请求加好友"
            else:
                return
            if comment:
                desc += f"，附言：{comment[:80]}"
            gid = int(group_id) if group_id else 0
            if not gid:
                return
            group_reply_caches.setdefault(gid, ReplyCache()).add_reply(
                _stamp_reply_dict(
                    {"name": "系统", "id": "event", "kind": "event", "content": desc}
                )
            )
            _log.info("[FakeAi] 感知请求: %s", desc)
        except Exception as e:
            _log.error("[FakeAi] 处理请求事件失败: %s", e)


async def _send_sticker(plugin: "FakeAi", group_id: int, path: str) -> None:
    """优先按「动画表情」发，失败就退回普通图片。

    QQ 的表情接口只认 gif/png 一类，webp/jpg 容易被 retcode 100 拒掉；
    梗鲸图库下载的就是 webp，所以这里必须有回退。
    """
    try:
        await plugin.api.qq.send_group_sticker(group_id, path)
        return
    except Exception as e:
        _log.warning("[FakeAi] 表情接口失败，改用普通图片发送：%s", e)
    await plugin.api.qq.post_group_msg(group_id=group_id, image=Image(file=path))


async def send_agent_result(
    self: FakeAi,
    input: Optional[GroupMessage],
    result: AgentResult,
    *,
    group_id: Optional[int] = None,
) -> None:
    """执行 agent 产出的动作：文字、表情、拍一拍，并写回 ReplyCache。"""
    group_id = group_id or (input.group_id if input else None)
    if group_id is None:
        return
    message_id = getattr(input, "message_id", None) if input else None
    sent_texts: List[str] = []
    for action in result.actions:
        try:
            if action.kind == "text":
                for i, text in enumerate(action.messages):
                    elements = []
                    reply_id = action.reply_to or (
                        str(message_id) if action.quote and message_id else None
                    )
                    if i == 0 and reply_id:
                        elements.append(Reply(id=str(reply_id)))
                    if i == 0 and action.at_user:
                        elements.append(At(user_id=str(action.at_user)))
                        elements.append(PlainText(text=" "))
                    elements.append(PlainText(text=str(text)))
                    await self.api.qq.post_group_msg(
                        group_id=group_id, rtf=MessageChain(elements)
                    )
                    sent_texts.append(str(text))
                    await asyncio.sleep(random.uniform(0.7, 1.6))
            elif action.kind == "sticker":
                if action.sticker:
                    await _send_sticker(self, group_id, str(action.sticker))
                    await asyncio.sleep(random.uniform(0.6, 1.2))
            elif action.kind == "poke":
                if action.user_id:
                    await self.api.qq.send_poke(group_id, str(action.user_id))
                    await asyncio.sleep(random.uniform(0.4, 0.8))
            elif action.kind == "react":
                mid = action.message_id or message_id
                if mid:
                    await self.api.qq.set_msg_emoji_like(
                        message_id=mid,
                        emoji_id=action.emoji_id or "289",
                        set=True,
                    )
                    await asyncio.sleep(random.uniform(0.2, 0.5))
        except Exception as e:
            _log.error("[FakeAi] agent 动作发送失败 kind=%s: %s", action.kind, e)

    if sent_texts:
        reply_cache = group_reply_caches.setdefault(group_id, ReplyCache())
        reply_cache.add_reply(
            _stamp_reply_dict(
                {"name": "蓝晴", "id": "0", "content": " ".join(sent_texts)}
            )
        )
    _log.info(
        "[FakeAi] agent 动作执行完成 text=%s sticker=%s poke=%s",
        len(sent_texts),
        sum(1 for a in result.actions if a.kind == "sticker"),
        sum(1 for a in result.actions if a.kind == "poke"),
    )


async def send_typing_response(
    self: FakeAi, input: GroupMessage, answer: Union[str, AgentResult]
) -> None:
    if isinstance(answer, AgentResult):
        await send_agent_result(self, input, answer)
        return
    try:
        # 尝试解析 JSON
        try:
            replace = json.loads(answer.replace("{{", "{").replace("}}", "}"))
            content = replace.get("content", "")
        except json.JSONDecodeError:
            # 如果不是 JSON 格式，直接使用原始内容
            content = answer

        sticker_paths = []
        if expr_pkg.EXPRESSION_ENABLED and isinstance(content, str):
            content, sticker_paths = extract_stickers(
                content, max_count=expr_pkg.MAX_STICKERS_PER_REPLY
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
        for idx, sentence in enumerate(sentences):
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

            # 分条发送时给一点真人式间隔；开启 typing delay 则按字数计算
            if enable_typing_delay:
                delay = len(sentence) * typing_delay_per_char
            elif idx > 0:
                delay = random.uniform(0.7, 1.6)
            else:
                delay = 0.0
            if delay > 0:
                await asyncio.sleep(delay)

            # 发送消息
            if message_elements:  # 确保消息链不为空
                message = MessageChain(message_elements)
                await self.api.qq.post_group_msg(group_id=input.group_id, rtf=message)

        # 贴纸单独成消息：用动画表情（image.sub_type=1），勿当普通图
        for spath in sticker_paths:
            try:
                await self.api.qq.send_group_sticker(input.group_id, str(spath))
            except Exception as e:
                _log.error("[FakeAi] 贴纸发送失败 %s: %s", spath, e)

        # 将AI的回复加入到reply_cache中
        reply_cache = group_reply_caches.setdefault(group_id, ReplyCache())
        reply_json = _stamp_reply_dict({"name": "蓝晴", "id": "0", "content": content})
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
    remaining_time = (
        trigger_interval - (datetime.now() - last_trigger_time).total_seconds()
    )
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
    model: str = "deepseek-flash",
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
    if USE_DS_AGENT and Path(AGENT_YAML_PATH).is_file():
        return await load_yaml(AGENT_YAML_PATH)
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


def update_yaml_with_replies(
    yaml_data: Dict, reply_cache: ReplyCache, limit: int | None = None
) -> Dict:
    replies = reply_cache.get_replies()
    if limit:
        replies = replies[-limit:]
    if not replies:
        replace_placeholder(yaml_data, "{history_new}", "（暂无）")
        replace_placeholder(yaml_data, "{history_last}", "（暂无）")
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

                if str(data.get("kind") or "") == "event":
                    readable.append(f"[{stamp}] （系统事件）{text}")
                    lines.append(json.dumps(data, ensure_ascii=False))
                    continue

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
    if (
        any(w in judgment for w in ephemeral)
        and "以后" not in judgment
        and "总是" not in judgment
    ):
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


WEB_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "联网搜索实时信息、新闻、新梗或不确定的事实时使用。返回标题、URL、摘要。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索词"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "抓取网页正文（Markdown），用于阅读搜索结果或群友发来的链接。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "http(s) URL"}
                },
                "required": ["url"],
            },
        },
    },
]

WEB_TOOL_PROMPT = (
    "\n\n【联网工具】遇到实时信息、新闻、新梗或你自己不确定的事实，"
    "先调用 web_search；需要读正文时调用 web_fetch。普通闲聊不要搜，"
    "搜索完直接回答，不要汇报搜索过程。"
)


async def _chat_with_web_tools(messages: list) -> dict | None:
    """带 web_search / web_fetch 的小工具循环（最多 4 轮）。"""
    usage_total = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 0,
    }
    last = None
    for _ in range(4):
        res = await AiUtil.chat_completion(
            messages,
            tools=WEB_TOOLS,
            capability="chat",
            max_tokens=800,
            temperature=0.9,
        )
        if not res:
            return None
        usage = res.get("usage") or {}
        for key in usage_total:
            usage_total[key] += int(usage.get(key, 0) or 0)
        last = res
        calls = res.get("tool_calls") or []
        if not calls:
            res["usage"] = usage_total
            return res

        messages.append(
            {
                "role": "assistant",
                "content": res.get("content") or "",
                "tool_calls": calls,
            }
        )
        for call in calls:
            name = call.get("function", {}).get("name", "")
            try:
                args = json.loads(call.get("function", {}).get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            try:
                if name == "web_search":
                    data = await AiUtil.search_web(
                        str(args.get("query", "")), count=5
                    )
                    items = [
                        {
                            "title": item.get("title"),
                            "url": item.get("url"),
                            "summary": item.get("summary"),
                            "date": item.get("date"),
                        }
                        for item in (data.get("results") or [])
                    ]
                    text = json.dumps(items, ensure_ascii=False)
                elif name == "web_fetch":
                    data = await AiUtil.fetch_web(str(args.get("url", "")))
                    text = (
                        f"{data.get('title', '')}\n\n"
                        f"{(data.get('content') or '')[:6000]}"
                    )
                else:
                    text = f"unknown tool: {name}"
            except Exception as e:
                text = f"tool error: {type(e).__name__}: {e}"
            messages.append(
                {"role": "tool", "tool_call_id": call.get("id"), "content": text}
            )
    if last:
        last["usage"] = usage_total
    return last


SKILL_HINT = (
    "\n\n【动手能力（硬性规则）】群友让你做具体功能时（占卜、抽卡、点歌、查攻略/物价、"
    "看群友印象、发图库、算数、签到、生成图片/语音、写文件、拉起子智能体），"
    "必须先用 list_skills 找到对应插件或技能，再 describe_skills 确认参数，"
    "最后 invoke_skill 执行。不要只用 send_message 口头回应，也不要假装已经做过。"
    "例：有人说「帮我占卜」→ 在 list_skills 里找 tarot.divine 或 plugin.Tarot.run "
    "→ describe_skills → invoke_skill；拿到牌面后再发消息/图片。"
    "常见对应：占卜→tarot.divine / plugin.Tarot.run；算数→plugin.ChatCalc.run；"
    "图库→plugin.ImageSender.run；FF14 查询→plugin.Tataru.run；"
    "点歌/解析链接→plugin.MediaParser.run；签到→plugin.GroupSign.run。"
    "\nplugin.*.run 类调用成功后，插件已经自己把结果发到群里了，"
    "不要再复述、不要发“查了/看上面”这类补充消息，直接 finish 收尾；"
    "只有插件没有发出任何内容时，你才需要自己补一条。"
    "\nskill 返回的图片会自动发到群里，看到结果里的「已自动发出」就不要再调用 message.send_image。"
    "\nskill 明确报错（未开放 / 没权限 / 不匹配）时，用一句自然的话把原因告诉群友，"
    "例如「这功能本群没开」，不要沉默，也不要假装已经做过。"
)


SKILL_FOCUS_RULES = (
    (("占卜", "塔罗", "抽张牌", "抽一张牌"), "tarot.divine"),
    (("人品",), "plugin.Jrrp.run"),
    (("签到",), "plugin.GroupSign.run"),
    (("图库", "来几张", "发几张", "发三张", "发两张"), "image.package.send"),
    (("算数", "算一下", "计算一下"), "plugin.ChatCalc.run"),
)


def _last_human_message(reply_cache: ReplyCache) -> str:
    """取最近一条群友发言（跳过蓝晴自己、插件消息和系统事件）。"""
    for raw in reversed(reply_cache.get_replies()):
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if str(data.get("id", "")) == "0" or str(data.get("kind") or "") == "event":
            continue
        if data.get("source"):
            continue
        text = _reply_content_to_text(data.get("content", "")).strip()
        if text:
            return text
    return ""


def _focus_skill_hint(keyword: str) -> str:
    """消息里点名了功能时给模型硬性指令；一句话里有多个请求就全部列出来。"""
    text = str(keyword or "")
    if not text:
        return ""
    hits: list[str] = []
    for words, skill in SKILL_FOCUS_RULES:
        if any(word in text for word in words) and skill not in hits:
            hits.append(skill)
    if not hits:
        return ""
    if len(hits) == 1:
        return (
            f"\n\n【本次必须动手】这条消息要求「{hits[0]}」。"
            f"先 describe_skills(['{hits[0]}']) 再 invoke_skill('{hits[0]}')，"
            "按返回结果说话，禁止自己编造牌面/数字/图片；"
            "skill 返回的图片会自动发出，不需要你再发一次。"
        )
    return (
        f"\n\n【本次必须动手】这条消息里有 {len(hits)} 个独立请求，"
        "每个都要真的调用一次 skill，不能只做其中一两个，也不能用嘴代替："
        + "、".join(hits)
        + "。逐个 describe_skills 后 invoke_skill，按返回结果说话；"
        "禁止编造结果，skill 返回的图片会自动发出。"
    )


async def _auto_pick_sticker(result: AgentResult, topic: str) -> None:
    """模型忘了配图时按配置概率补一张，避免聊天干巴巴。"""
    if not expr_pkg.EXPRESSION_ENABLED or expr_pkg.STICKER_AUTO_RATE <= 0:
        return
    if any(action.kind == "sticker" for action in result.actions):
        return
    if not any(action.kind == "text" for action in result.actions):
        return
    if random.random() >= expr_pkg.STICKER_AUTO_RATE:
        return
    lib = str(expr_pkg.STICKER_LIBRARY or "both")
    path = None
    if lib in ("gengjing", "both") and whale_catalog.count():
        if lib == "gengjing" or random.random() < 0.5:
            try:
                path = await whale_catalog.fetch(topic)
            except Exception as e:
                _log.debug("[FakeAi] 梗鲸补图失败: %s", e)
    if path is None and lib in ("darkdog", "both"):
        path = sticker_catalog.pick_any()
    if path is not None:
        result.actions.append(AgentAction(kind="sticker", sticker=str(path)))
        _log.info("[FakeAi] 自动补了一张表情：%s", Path(path).name)


def _build_skill_bridge(
    group_id: int, user_id: Optional[str], sender_name: Optional[str] = None
):
    """给 agent 循环接上 SkillHub；返回 (bridge, 提示词片段)。"""
    if not SKILLS_ENABLED:
        return None, ""
    plugin = _fake_ai_plugin_instance[0]
    registry = getattr(plugin, "skills", None) if plugin else None
    if registry is None:
        return None, ""
    try:
        from ncatbot.skill import SkillBridge, SkillContext

        runtime = getattr(plugin, "agents", None)
        workspace = (
            runtime.workspaces.get(f"group:{group_id}")
            if runtime is not None
            else None
        )
        skill_ctx = SkillContext(
            session_key=f"group:{group_id}",
            user_id=str(user_id or ""),
            group_id=str(group_id or ""),
            roles={"user"},
            api=getattr(plugin, "api", None),
            runtime=runtime,
            workspace=workspace,
            sandbox=runtime.runner if runtime is not None else None,
            extras={"nickname": str(sender_name or "").strip()},
        )

        async def _deliver_artifacts(artifacts: List[dict]) -> int:
            """skill 产出的图片直接发到群里，不依赖模型再调一次 send_image。"""
            api = getattr(plugin, "api", None)
            if api is None or not group_id:
                return 0
            sent = 0
            for item in artifacts:
                if str(item.get("kind") or "") != "image":
                    continue
                raw = item.get("abs_path") or item.get("path")
                if not raw:
                    continue
                try:
                    await api.qq.post_group_msg(
                        group_id=group_id, image=Image(file=str(raw))
                    )
                    sent += 1
                except Exception as e:
                    _log.error("[FakeAi] skill 产物发送失败 path=%s: %s", raw, e)
            if sent:
                _log.info("[FakeAi] 已自动发送 skill 图片产物 %s 个", sent)
            return sent

        bridge = SkillBridge(
            registry,
            skill_ctx,
            deny={"message.send"},
            max_calls=12,
            artifact_sink=_deliver_artifacts,
        )
        return bridge, SKILL_HINT
    except Exception as e:
        _log.debug("[FakeAi] skill bridge 初始化失败: %s", e)
        return None, ""


async def answer_ai(
    group_id: int,
    group_reply_caches: Dict[int, ReplyCache],
    user_id: str = None,
    trigger_type: str = "active",
    decision: Optional[AnalystDecision] = None,
    sender_name: Optional[str] = None,
) -> str:
    # 确保数据库已初始化
    await memory_manager.init_db()

    # 加载 YAML 数据
    yaml_data = await load_yaml_data(group_id)
    replace_time_in_system(yaml_data)
    if trigger_type in ("passive", "proactive"):
        context_limit = 8
    elif trigger_type == "event":
        context_limit = 10
    else:
        context_limit = 14
    update_yaml_with_replies(
        yaml_data,
        group_reply_caches.get(group_id, ReplyCache()),
        limit=context_limit if USE_DS_AGENT else None,
    )

    # 注入长期记忆
    try:
        long_term_memory = await memory_manager.get_long_term_memory(
            group_id, max_length=300 if USE_DS_AGENT else 500
        )
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

        if USE_DS_AGENT and user_id and str(user_id).isdigit():
            impression_ids = [int(user_id)]
        else:
            impression_ids = list(active_user_ids) if active_user_ids else None
        user_impressions = await memory_manager.get_user_impressions_text(
            user_ids=impression_ids,
            max_length=400 if USE_DS_AGENT else 800,
        )
        replace_placeholder(yaml_data, "{user_impressions}", user_impressions)
    except Exception as e:
        _log.debug(f"[FakeAi] 获取用户印象失败: {e}")
        replace_placeholder(yaml_data, "{user_impressions}", "（暂无用户印象）")

    # 注入相关主动记忆
    mem_text = ""
    skip_passive = USE_DS_AGENT and trigger_type in ("passive", "proactive")
    try:
        if skip_passive:
            replace_placeholder(
                yaml_data,
                "{related_memories}",
                "（闲聊模式未加载，需要时用 memory_query）",
            )
            mem_text = ""
        elif (
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

        if skip_passive:
            replace_placeholder(
                yaml_data, "{related_knowledge}", "（需要时用 web_search）"
            )
        elif query_text:
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

    # 系统提示保持静态（利于前缀缓存），动态内容全部放进 user 消息
    keyword = yaml_data.get("input", "")
    prompt = yaml_data.get("system", "")
    agent_dynamic = []

    try:
        recent_entries = []
        for raw in group_reply_caches.get(group_id, ReplyCache()).get_replies()[-10:]:
            try:
                recent_entries.append(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                continue
        human_lengths = []
        human_texts = []
        self_texts = []
        for entry in recent_entries:
            text = _reply_content_to_text(entry.get("content", "")).strip()
            if not text:
                continue
            if str(entry.get("id")) == "0":
                self_texts.append(text[:40])
            elif text not in ("[图片]", "[表情]"):
                human_lengths.append(len(text))
                human_texts.append(text)
        if human_lengths:
            avg_len = max(4, min(60, int(sum(human_lengths) / len(human_lengths))))
            agent_dynamic.append(
                f"【群聊语感】最近大家发言平均约 {avg_len} 字，你的回复别明显更长。"
            )
        stop_words = {
            "什么", "怎么", "可以", "没有", "我们", "你们", "他们",
            "今天", "现在", "感觉", "真的", "不是", "还是", "就是",
            "这个", "那个", "一个", "一下", "知道", "觉得",
        }
        phrase_counts = {}
        for text in human_texts:
            for phrase in re.findall(r"[\u4e00-\u9fff]{2,6}", text):
                if phrase in stop_words:
                    continue
                phrase_counts[phrase] = phrase_counts.get(phrase, 0) + 1
        top_phrases = [
            p
            for p, c in sorted(
                phrase_counts.items(), key=lambda x: -x[1]
            )[:5]
            if c >= 2
        ]
        if top_phrases:
            agent_dynamic.append("【群聊常用】" + "、".join(top_phrases))
        if self_texts:
            agent_dynamic.append(
                "【你最近说过】"
                + "；".join(self_texts[-3:])
                + "。不要重复同样的说法或口头禅。"
            )
    except Exception as e:
        _log.debug("[FakeAi] 群聊语感计算失败: %s", e)

    if trigger_type == "proactive":
        agent_dynamic.append(
            "（主动机会）群里安静很久了。可以主动抛一个自然的话题，"
            "也可以 finish 安静结束，不要硬找话。"
        )
    elif trigger_type == "event":
        agent_dynamic.append(
            "（系统事件）最近一条是群里的系统事件，不是有人在向你提问。"
            "想接就自然接一句（新人来了可以招呼、有人退了可以吐槽、"
            "被戳了可以回一句或戳回去），不想理就 finish，别把事件当成问题来回答。"
            "被戳、新人进群、有人退群这类事，能随口说一句就说，真没话说再 finish。"
            "事件不是功能请求：不要调用 list_skills / invoke_skill，"
            "也不要发和事件无关的图片。"
        )

    if decision is not None and decision.should_reply:
        inject = decision.inject_block()
        if inject:
            if USE_DS_AGENT:
                agent_dynamic.append(inject.strip())
            else:
                prompt = (prompt or "") + inject
    # YAML 若无 {related_memories} 占位，追加一块，避免静默丢失
    if (
        mem_text
        and not USE_DS_AGENT
        and "{related_memories}" not in (yaml_data.get("system") or "")
    ):
        prompt = (prompt or "") + f"\n\n【相关记忆】\n{mem_text}"
    if USE_DS_AGENT:
        names = sticker_catalog.names()
        libs = sticker_catalog.libraries()
        lib_text = "、".join(f"{k}({v})" for k, v in libs.items()) or "无"
        whale_count = whale_catalog.count()
        blocks = []
        if names:
            blocks.append(
                "\n\n【表情包·本地】图库：" + lib_text + "。"
                "想配表情就调用 send_sticker(name)，可用名："
                + "、".join(names[:60]) + "。"
            )
        if whale_count:
            blocks.append(
                "\n【表情包·梗鲸】另有 " + str(whale_count) + " 张网络梗图"
                "（蓝色大肥鱼 / AI 娘梗图，带梗名与出处），"
                "想发这类图用 send_meme(topic)，topic 填此刻的情绪或话题关键词。"
            )
        if expr_pkg.EXPRESSION_ENABLED and blocks:
            blocks.append(
                "\n【表情包·节奏】别憋着：普通闲聊大约每 2~3 轮就该配一张，"
                "被戳中笑点、接梗、自嘲、无语、赢了输了、晚安时更要发；"
                "同一张不要连续用，也别每句都发。"
            )
        if blocks:
            prompt = (prompt or "") + "".join(blocks)
    elif expr_pkg.EXPRESSION_ENABLED:
        sticker_block = sticker_catalog.prompt_block()
        if sticker_block:
            prompt = (prompt or "") + sticker_block
            _log.info(
                "[FakeAi] 已注入贴纸提示 names=%s",
                len(sticker_catalog.names()),
            )
        else:
            _log.warning("[FakeAi] 贴纸目录为空，跳过表情注入")

    if USE_DS_AGENT and Path(AGENT_YAML_PATH).is_file():
        focus_hint = _focus_skill_hint(
            _last_human_message(group_reply_caches.get(group_id, ReplyCache()))
        )
        if agent_dynamic:
            keyword = (keyword or "") + "\n\n" + "\n\n".join(agent_dynamic)
        image_base64_list = []
        history_entries = []
        try:
            replies = group_reply_caches.get(group_id, ReplyCache()).get_replies()
            for raw in replies:
                try:
                    history_entries.append(json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    continue
            if history_entries and trigger_type != "proactive":
                image_urls = history_entries[-1].get("image_urls") or []
                if image_urls:
                    image_base64_list = await AiUtil.download_images_as_base64(
                        image_urls[:3]
                    )
        except Exception as e:
            _log.debug("[FakeAi] agent 图片加载失败: %s", e)

        skill_bridge, skill_hint = _build_skill_bridge(
            group_id, user_id, sender_name
        )
        if trigger_type == "event":
            # 事件不需要动手能力：进退群、文件、戳一戳都只该接一句话
            skill_bridge, skill_hint = None, ""
        if skill_hint:
            prompt = (prompt or "") + skill_hint + focus_hint
        agent_result = await run_agent(
            prompt or "",
            keyword,
            group_id=group_id,
            user_id=user_id,
            sticker_catalog=sticker_catalog,
            image_base64_list=image_base64_list,
            history_entries=history_entries,
            max_total_tokens=AGENT_MAX_TOKENS,
            max_rounds=AGENT_MAX_ROUNDS,
            skill_bridge=skill_bridge,
        )
        if agent_result is not None and agent_result.actions:
            await _auto_pick_sticker(agent_result, keyword)
        if agent_result is not None and (
            agent_result.actions or agent_result.usage.get("total_tokens")
        ):
            if group_id:
                try:
                    from common.utils.AiStatsRecorder import record_from_response

                    record_from_response(
                        str(group_id),
                        str(user_id) if user_id else None,
                        trigger_type,
                        {
                            "usage": agent_result.usage,
                            "model": agent_result.model,
                        },
                    )
                except Exception as e:
                    _log.error("[FakeAi] 记录 agent 用量失败: %s", e)
            return agent_result

    prompt = (prompt or "") + WEB_TOOL_PROMPT
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": keyword},
    ]
    ai_response = await _chat_with_web_tools(messages)

    # 处理AI响应
    if isinstance(ai_response, dict):
        response = ai_response.get("content", "")
        usage_info = ai_response.get("usage", {})
        usage_info.get("total_tokens", 0)
        usage_info.get("prompt_tokens", 0)
        usage_info.get("completion_tokens", 0)
        usage_info.get("prompt_cache_hit_tokens", 0)
        ai_response.get("model", "deepseek-flash")
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
