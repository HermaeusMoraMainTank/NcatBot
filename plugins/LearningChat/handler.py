"""
LearningChat 核心处理模块
"""

import asyncio
import datetime
import random
import re
import time
from functools import cmp_to_key
from typing import List, Union, Optional, Tuple, Dict
from enum import IntEnum, auto
import logging

from .models import (
    ChatBlackList,
    ChatContext,
    ChatAnswer,
    ChatMessage,
    ImageCache,
    db_manager,
)
from .config import (
    config_manager,
    SUPERUSERS,
    NICKNAME,
    log_info,
    log_debug,
)

# 群级别的复读锁，防止同一群同时触发多次复读
_repeat_locks: Dict[int, asyncio.Lock] = {}

# 最近复读记录，防止重复复读 {group_id: (message_hash, timestamp)}
_recent_repeats: Dict[int, Tuple[str, float]] = {}

log = logging.getLogger(__name__)

chat_config = config_manager.config

# 预定义回复语句
NO_PERMISSION_WORDS = [f"{NICKNAME}就喜欢说这个，哼！", f"你管得着{NICKNAME}吗！"]
ENABLE_WORDS = [
    f"{NICKNAME}会尝试学你们说怪话！",
    f"好的呢，让{NICKNAME}学学你们的说话方式~",
]
DISABLE_WORDS = [
    f"好好好，{NICKNAME}不学说话就是了！",
    f"果面呐噻，{NICKNAME}以后不学了...",
]
SORRY_WORDS = [
    f"{NICKNAME}知道错了...达咩!",
    f"{NICKNAME}不会再这么说了...",
    f"果面呐噻,{NICKNAME}说错话了...",
]
DOUBT_WORDS = [f"{NICKNAME}有说什么奇怪的话吗？"]
BREAK_REPEAT_WORDS = ["打断复读", "打断！"]
ALL_WORDS = (
    NO_PERMISSION_WORDS
    + SORRY_WORDS
    + DOUBT_WORDS
    + ENABLE_WORDS
    + DISABLE_WORDS
    + BREAK_REPEAT_WORDS
)


class Result(IntEnum):
    """处理结果枚举"""

    Learn = auto()
    Pass = auto()
    Repeat = auto()
    Ban = auto()
    SetEnable = auto()
    SetDisable = auto()


class LearningChatHandler:
    """群聊学习处理器"""

    def __init__(
        self,
        group_id: int,
        user_id: int,
        message_id: int,
        message: str,
        raw_message: str,
        plain_text: str,
        time: int,
        bot_id: int,
        to_me: bool = False,
        role: str = "member",
        reply_message_id: int = None,
    ):
        # 清理消息中的 at 和 reply 信息
        if reply_message_id:
            cleaned_message = re.sub(
                r"(\[CQ:at,qq=.+\])|(\[CQ:reply,id=.+\])",
                "",
                re.sub(r"(,subType=\d+,url=.+\])", r"]", raw_message),
            ).strip()
        else:
            cleaned_message = re.sub(
                r"(\[CQ:at,qq=.+\])",
                "",
                re.sub(r"(,subType=\d+,url=.+\])", r"]", raw_message),
            ).strip()

        self.data = ChatMessage(
            group_id=group_id,
            user_id=user_id,
            message_id=message_id,
            message=cleaned_message,
            raw_message=raw_message,
            plain_text=plain_text,
            time=time,
        )
        self.reply_message_id = reply_message_id
        self.bot_id = bot_id
        self.to_me = to_me or NICKNAME in self.data.message
        self.role = "superuser" if user_id in SUPERUSERS else role
        self.config = config_manager.get_group_config(group_id)
        self.ban_users = set(chat_config.ban_users + self.config.ban_users)
        self.ban_words = set(chat_config.ban_words + self.config.ban_words)

    async def _learn(self) -> Result:
        """学习逻辑"""
        # 检查是否要开启/关闭学习
        if self.to_me and any(
            w in self.data.message for w in {"学说话", "快学", "开启学习"}
        ):
            return Result.SetEnable
        elif self.to_me and any(
            w in self.data.message for w in {"闭嘴", "别学", "关闭学习"}
        ):
            return Result.SetDisable
        elif not chat_config.total_enable or not self.config.enable:
            log_debug("群聊学习", f"➤该群{self.data.group_id}未开启群聊学习，跳过")
            return Result.Pass
        elif self.data.user_id in self.ban_users:
            log_debug("群聊学习", f"➤发言人{self.data.user_id}在屏蔽列表中，跳过")
            return Result.Pass
        elif self.to_me and any(
            w in self.data.message for w in {"不可以", "达咩", "不能说这"}
        ):
            return Result.Ban
        elif not await self._check_allow(self.data):
            log_debug("群聊学习", "➤消息未通过校验，跳过")
            return Result.Pass
        elif self.reply_message_id:
            # 如果是回复消息
            messages = await ChatMessage.filter(message_id=self.reply_message_id)
            if not messages:
                log_debug("群聊学习", "➤回复的消息不在数据库中，跳过")
                return Result.Pass
            message = messages[0]
            if message.user_id in self.ban_users:
                log_debug("群聊学习", "➤回复的人在屏蔽列表中，跳过")
                return Result.Pass
            if not await self._check_allow(message):
                log_debug("群聊学习", "➤回复的消息未通过校验，跳过")
                return Result.Pass
            await self._set_answer(message)
            return Result.Learn
        else:
            # 获取本群一个小时内的最后6条消息（当前消息已保存，所以多取一条）
            all_messages = await ChatMessage.filter(
                group_id=self.data.group_id, time__gte=self.data.time - 3600
            )
            # 排除当前消息
            messages = [
                msg
                for msg in all_messages
                if msg.message_id != self.data.message_id and msg.id != self.data.id
            ][:5]

            if messages:
                # 使用规范化比较，支持图片消息的复读检测
                is_repeat = ChatMessage.messages_equal(
                    messages[0].message, self.data.message
                )
                if is_repeat:
                    log_info("群聊学习", "➤复读中，进入复读处理")
                    return Result.Repeat

                for message in messages:
                    if (
                        message.user_id not in self.ban_users
                        and set(self.data.keyword_list) & set(message.keyword_list)
                        and self.data.keyword_list != message.keyword_list
                        and await self._check_allow(message)
                    ):
                        await self._set_answer(message)
                        return Result.Learn

                if messages[0].user_id in self.ban_users or not await self._check_allow(
                    messages[0]
                ):
                    log_debug("群聊学习", "➤最后一条消息未通过校验，跳过")
                    return Result.Pass

                await self._set_answer(messages[0])
                return Result.Learn

            return Result.Pass

    async def answer(self) -> Optional[List[str]]:
        """获取这句话的回复"""
        # 先保存当前消息，确保后续查询能看到它
        await self.data.save()
        result = await self._learn()

        if result == Result.Ban:
            if self.role not in {"superuser", "admin", "owner"}:
                return [random.choice(NO_PERMISSION_WORDS)]
            if self.reply_message_id:
                ban_result = await self._ban(message_id=self.reply_message_id)
            else:
                ban_result = await self._ban()
            if ban_result:
                return [random.choice(SORRY_WORDS)]
            else:
                return [random.choice(DOUBT_WORDS)]

        elif result in [Result.SetEnable, Result.SetDisable]:
            if self.role not in {"superuser", "admin", "owner"}:
                return [random.choice(NO_PERMISSION_WORDS)]
            self.config.update(enable=(result == Result.SetEnable))
            config_manager.config.group_config[self.data.group_id] = self.config
            config_manager.save()
            log_info(
                "群聊学习",
                f"群{self.data.group_id}{'开启' if result == Result.SetEnable else '关闭'}学习功能",
            )
            return [
                random.choice(
                    ENABLE_WORDS if result == Result.SetEnable else DISABLE_WORDS
                )
            ]

        elif result == Result.Pass:
            return None

        elif result == Result.Repeat:
            # 获取或创建该群的复读锁
            group_id = self.data.group_id
            if group_id not in _repeat_locks:
                _repeat_locks[group_id] = asyncio.Lock()

            # 使用锁确保同一群的复读检测串行执行
            async with _repeat_locks[group_id]:
                # 检查最近是否已经复读过相同的消息（10秒内）
                msg_normalized = self.data.message[:100]  # 取前100字符作为标识
                current_time = time.time()
                if group_id in _recent_repeats:
                    last_msg, last_time = _recent_repeats[group_id]
                    if last_msg == msg_normalized and current_time - last_time < 10:
                        log_info("群聊学习", "➤➤10秒内已复读过相同消息，跳过")
                        return None

                # 检查是否已复读过（数据库检查，2分钟内不重复复读）
                bot_messages = await ChatMessage.filter(
                    group_id=self.data.group_id,
                    user_id=self.bot_id,
                    time__gte=self.data.time - 120,  # 2分钟
                )
                for msg in bot_messages[: self.config.repeat_threshold + 5]:
                    # 使用规范化比较，支持图片消息
                    if ChatMessage.messages_equal(msg.message, self.data.message):
                        log_info("群聊学习", "➤➤已经复读过了，跳过")
                        return None

                # 获取数据库中已保存的消息
                # 注意：当前消息已保存到数据库，需要排除当前消息
                all_messages = await ChatMessage.filter(
                    group_id=self.data.group_id,
                    time__gte=self.data.time - 3600,
                )
                # 排除当前消息（通过 message_id 或 id）
                messages = [
                    msg
                    for msg in all_messages
                    if msg.message_id != self.data.message_id and msg.id != self.data.id
                ]

                # 需要 threshold - 1 条历史消息（加上当前消息就是 threshold 条）
                required_count = self.config.repeat_threshold - 1
                messages = messages[:required_count]

                log_info(
                    "群聊学习",
                    f"➤➤复读检测: 需要{required_count}条, 获取到{len(messages)}条",
                )

                if len(messages) < required_count:
                    return None

                # 检查是否达到复读阈值
                # 数据库中需要有 threshold - 1 条相同消息
                # （当前消息也算一条，所以总共是 threshold 条）
                all_same = all(
                    ChatMessage.messages_equal(msg.message, self.data.message)
                    for msg in messages
                )

                log_info(
                    "群聊学习",
                    f"➤➤all_same={all_same}",
                )

                if all_same:
                    # 记录这次复读，防止重复
                    _recent_repeats[group_id] = (msg_normalized, current_time)

                    if random.random() < self.config.break_probability:
                        log_info("群聊学习", "➤➤达到复读阈值，打断复读！")
                        return [random.choice(BREAK_REPEAT_WORDS)]
                    else:
                        log_info("群聊学习", "➤➤达到复读阈值，复读")
                        # 如果是图片消息，转换为可发送的格式
                        reply_msg = self.data.message
                        if self.data.is_image_message:
                            reply_msg = ImageCache.convert_message_for_send(reply_msg)
                            if reply_msg is None:
                                # 图片缓存不存在，跳过复读
                                log_debug("群聊学习", "➤➤图片缓存不存在，跳过复读")
                                return None
                        return [reply_msg]
                return None

        else:
            # 回复逻辑
            # 纯文本消息长度检查，但图片消息不受此限制
            if self.data.is_plain_text and len(self.data.plain_text) <= 1:
                log_debug("群聊学习", "➤➤消息过短，不回复")
                return None

            # 纯图片消息也可以触发回复
            contexts = await ChatContext.filter(keywords=self.data.keywords)
            if not contexts:
                log_debug("群聊学习", "➤➤尚未有已学习的回复，不回复")
                return None
            context = contexts[0]

            # 获取回复阈值
            if not self.to_me:
                answer_choices = list(
                    range(
                        self.config.answer_threshold
                        - len(self.config.answer_threshold_weights)
                        + 1,
                        self.config.answer_threshold + 1,
                    )
                )
                answer_count_threshold = random.choices(
                    answer_choices, weights=self.config.answer_threshold_weights
                )[0]

                if len(self.data.keyword_list) == chat_config.keywords_size:
                    answer_count_threshold -= 1
                cross_group_threshold = chat_config.cross_group_threshold
            else:
                answer_count_threshold = 1
                cross_group_threshold = 1

            log_debug(
                "群聊学习",
                f"➤➤本次回复阈值为{answer_count_threshold}，跨群阈值为{cross_group_threshold}",
            )

            # 获取跨群关键词
            cross_keywords = await ChatAnswer.get_cross_group_keywords(
                cross_group_threshold
            )

            # 获取满足跨群条件的回复
            answers_cross = await ChatAnswer.filter(
                context=context,
                count__gte=answer_count_threshold,
                keywords__in=cross_keywords,
            )

            # 获取本群回复
            answer_same_group = await ChatAnswer.filter(
                context=context,
                count__gte=answer_count_threshold,
                group_id=self.data.group_id,
            )

            candidate_answers: List[ChatAnswer] = []
            for answer in set(answers_cross) | set(answer_same_group):
                if not await self._check_allow(answer):
                    continue
                candidate_answers.append(answer)

            if not candidate_answers:
                log_debug("群聊学习", "➤➤没有符合条件的候选回复")
                return None

            # 从候选回复中进行选择
            sum_count = sum(answer.count for answer in candidate_answers)
            per_list = [
                answer.count / sum_count * (1 - 1 / answer.count)
                for answer in candidate_answers
            ]
            per_list.append(1 - sum(per_list))

            result = random.choices(candidate_answers + [None], weights=per_list)[0]
            if result is None:
                log_debug("群聊学习", "➤➤但不进行回复")
                return None

            result_message = random.choice(result.messages)
            # 如果回复中包含图片，转换为可发送的格式
            if "[CQ:image" in result_message:
                result_message = ImageCache.convert_message_for_send(result_message)
                if result_message is None:
                    # 图片缓存不存在，跳过回复
                    log_debug("群聊学习", "➤➤图片缓存不存在，跳过回复")
                    return None
            log_debug("群聊学习", f"➤➤将回复{result_message}")
            await asyncio.sleep(random.random() + 0.5)
            return [result_message]

    async def _ban(self, message_id: int = None) -> bool:
        """屏蔽消息"""
        if message_id:
            messages = await ChatMessage.filter(message_id=message_id)
            if not messages or messages[0].message in ALL_WORDS:
                return False
            keywords = messages[0].keywords
        else:
            # 获取bot最后一条回复
            bot_messages = await ChatMessage.filter(
                group_id=self.data.group_id,
                user_id=self.bot_id,
            )
            if bot_messages and bot_messages[0].message not in ALL_WORDS:
                keywords = bot_messages[0].keywords
            else:
                return False

        ban_words = await ChatBlackList.filter(keywords=keywords)
        if ban_words:
            ban_word = ban_words[0]
            if self.data.group_id not in ban_word.ban_group_id:
                ban_word.ban_group_id.append(self.data.group_id)
            if len(ban_word.ban_group_id) >= 2:
                ban_word.global_ban = True
                log_info("群聊学习", f"学习词{keywords}将被全局禁用")
                await ChatAnswer.delete_by_keywords(keywords)
            else:
                log_info("群聊学习", f"群{self.data.group_id}禁用了学习词{keywords}")
                await ChatAnswer.delete_by_keywords(keywords, self.data.group_id)
        else:
            log_info("群聊学习", f"群{self.data.group_id}禁用了学习词{keywords}")
            ban_word = ChatBlackList(
                keywords=keywords, ban_group_id=[self.data.group_id]
            )
            await ChatAnswer.delete_by_keywords(keywords, self.data.group_id)

        # 删除上下文
        contexts = await ChatContext.filter(keywords=keywords)
        for ctx in contexts:
            db = db_manager.db
            await db.execute("DELETE FROM context WHERE id = ?", (ctx.id,))
            await db.commit()

        await ban_word.save()
        return True

    async def _set_answer(self, message: ChatMessage):
        """设置回答"""
        contexts = await ChatContext.filter(keywords=message.keywords)

        if contexts:
            context = contexts[0]
            if context.count < chat_config.learn_max_count:
                context.count += 1
            context.time = self.data.time

            answers = await ChatAnswer.filter(
                keywords=self.data.keywords,
                group_id=self.data.group_id,
                context=context,
            )

            if answers:
                answer = answers[0]
                if answer.count < chat_config.learn_max_count:
                    answer.count += 1
                answer.time = self.data.time
                if self.data.message not in answer.messages:
                    answer.messages.append(self.data.message)
            else:
                answer = ChatAnswer(
                    keywords=self.data.keywords,
                    group_id=self.data.group_id,
                    time=self.data.time,
                    context_id=context.id,
                    messages=[self.data.message],
                )
            await answer.save()
            await context.save()
        else:
            context = await ChatContext.create(
                keywords=message.keywords, time=self.data.time
            )
            answer = await ChatAnswer.create(
                keywords=self.data.keywords,
                group_id=self.data.group_id,
                time=self.data.time,
                context=context,
                messages=[self.data.message],
            )

        log_debug(
            "群聊学习", f"➤将被学习为{message.message}的回答，已学次数为{answer.count}"
        )

    # 插件命令排除列表 - 精确匹配
    EXCLUDED_COMMANDS = {
        # Help
        "help",
        "帮助",
        "功能",
        "菜单",
        # FakeAi
        "蓝晴说话",
        "查询余额",
        "蓝晴印象",
        "蓝晴知识库",
        "蓝晴好感度排行榜",
        "蓝晴好感度排行",
        # Meme
        "meme",
        # Moyu
        "摸鱼",
        "moyu",
        # Jrrp
        "jrrp",
        "今日人品",
        # CrazyThursday
        "疯狂星期四",
        "KFC",
        "星期四",
        # Kalabichu
        "卡拉彼丘",
        "卡丘",
        "kalabiqiu",
        "喵",
        # Fortune
        "运势",
        "今日doro",
        "今日运势",
        # Tarot
        "占卜",
        # Status
        "状态",
        # SendLike
        "赞我",
        # TodayWaifu
        "今日老婆",
        # TodayAnimeWaifu
        "今日二次元老婆",
        "今日二刺猿老婆",
        "今日二刺螈老婆",
        "今日2次元老婆",
        "今日二次元",
        "今日二刺猿",
        "今日二刺螈",
        # MoehuImageSender
        "随机表情包",
        # Lottery
        "开始大乐透",
        # RussianRoulette
        "午时已到",
        # Daily3Min
        "每天3分钟",
        "每天三分钟",
        # RollPig
        "今日小猪",
    }

    # 插件命令排除列表 - 前缀匹配
    EXCLUDED_PREFIXES = [
        # AnimeTrace
        "查询人物",
        "识别人物",
        "角色",
        "人物",
        # NetEaseCloudMusic
        "点歌",
        # QQMusicCardSender
        "qq点歌",
        # BilibiliAnalysis
        "搜视频",
        "查询视频",
        "搜索视频",
        # EmojiStats
        "表情包统计",
        # MessageStats
        "发言统计",
        # AiStats
        "ai统计",
        # Lottery
        "大乐透",
        # RussianRoulette
        "轮盘赌",
        # Nbnhhsh
        "翻译 ",
        # MuteToWorkEnd
        "禁言",
        "解禁",
        "取消禁言",
        "禁言到下班",
        # GroupRecall
        "查询撤回",
        # Uptime
        "灭云",
        # ImageSender
        "上传",
        "删除 ",
        # VrChatInfo
        "搜索vrc玩家",
        # Universalis
        "搜索物品",
        # TodayWaifu / TodayAnimeWaifu
        "换一个老婆",
        "换一个二次元老婆",
        # Fortune
        "重置",
        # EatWhat - 吃/喝相关
        "吃什么",
        "喝什么",
        "今天吃",
        "今天喝",
        "明天吃",
        "明天喝",
        "早餐吃",
        "午餐吃",
        "晚餐吃",
        "夜宵吃",
        "早上吃",
        "中午吃",
        "晚上吃",
        "查看菜单",
        "查看饮料",
        "查看全部菜单",
        "查看全部饮料",
        "添加菜品",
        "添加饮品",
        "删除菜品",
        "删除饮品",
        # RollPig
        "今日小猪",
    ]

    async def _check_allow(self, message: Union[ChatMessage, ChatAnswer]) -> bool:
        """检查消息是否允许"""
        raw_message = (
            message.message if isinstance(message, ChatMessage) else message.messages[0]
        )

        # 移除 CQ 码后的纯文本
        clean_message = re.sub(r"\[CQ:[^\]]+\]", "", raw_message).strip()

        # 检查是否是插件命令 - 精确匹配
        if clean_message in self.EXCLUDED_COMMANDS:
            return False

        # 检查是否是插件命令 - 前缀匹配
        for prefix in self.EXCLUDED_PREFIXES:
            if clean_message.startswith(prefix):
                return False

        # 检查特殊消息类型
        if any(
            i in raw_message
            for i in {
                "[CQ:xml",
                "[CQ:json",
                "[CQ:at",
                "[CQ:video",
                "[CQ:record",
                "[CQ:share",
            }
        ):
            return False

        # 检查屏蔽词
        if any(i in raw_message for i in self.ban_words):
            return False

        # 检查特殊格式
        if raw_message.startswith("&#91;") and raw_message.endswith("&#93;"):
            return False

        # 检查黑名单
        keywords = (
            message.keywords if isinstance(message, ChatMessage) else message.keywords
        )
        ban_words = await ChatBlackList.filter(keywords=keywords)
        if ban_words:
            ban_word = ban_words[0]
            group_id = message.group_id
            if ban_word.global_ban or group_id in ban_word.ban_group_id:
                return False

        return True

    @staticmethod
    async def speak(bot_id: int) -> Optional[Tuple[int, List[str]]]:
        """主动发言"""
        cur_time = int(time.time())
        # 使用 time.mktime() 获取今日零点的时间戳，跨平台兼容
        today_time = int(time.mktime(datetime.date.today().timetuple()))

        # 获取今日内消息超过10条的群列表
        db = db_manager.db
        cursor = await db.execute(
            """SELECT group_id, COUNT(*) as count 
               FROM message 
               WHERE time >= ? 
               GROUP BY group_id 
               HAVING count >= 10""",
            (today_time,),
        )
        groups = [row[0] for row in await cursor.fetchall()]

        if not groups:
            return None

        total_messages = {}
        for group_id in groups:
            messages = await ChatMessage.filter(
                group_id=group_id,
                time__gte=today_time,
            )
            if messages:
                total_messages[group_id] = messages

        if not total_messages:
            return None

        # 根据消息平均间隔来对群进行排序
        def group_popularity_cmp(left_group, right_group):
            def cmp(a, b):
                return (a > b) - (a < b)

            left_group_id, left_messages = left_group
            right_group_id, right_messages = right_group
            if len(left_messages) <= 1 or len(right_messages) <= 1:
                return cmp(len(left_messages), len(right_messages))
            left_duration = left_messages[0].time - left_messages[-1].time
            right_duration = right_messages[0].time - right_messages[-1].time
            if left_duration == 0 or right_duration == 0:
                return cmp(len(left_messages), len(right_messages))
            return cmp(
                len(left_messages) / left_duration, len(right_messages) / right_duration
            )

        popularity = sorted(
            total_messages.items(), key=cmp_to_key(group_popularity_cmp), reverse=True
        )

        log_debug(
            "群聊学习",
            f"主动发言：群热度排行{'>>'.join([str(g[0]) for g in popularity])}",
        )

        for group_id, messages in popularity:
            if len(messages) < 30:
                log_debug("群聊学习", f"主动发言：群{group_id}消息小于30条，不发言")
                continue

            config = config_manager.get_group_config(group_id)
            ban_words = set(
                chat_config.ban_words
                + config.ban_words
                + [
                    "[CQ:xml",
                    "[CQ:json",
                    "[CQ:at",
                    "[CQ:video",
                    "[CQ:record",
                    "[CQ:share",
                ]
            )

            if not config.speak_enable or not config.enable:
                log_debug("群聊学习", f"主动发言：群{group_id}未开启，不发言")
                continue

            # 检查最后一条消息是否是自己发的
            bot_messages = await ChatMessage.filter(
                group_id=group_id,
                user_id=bot_id,
            )
            if bot_messages:
                last_reply = bot_messages[0]
                if last_reply.time >= messages[0].time:
                    log_debug(
                        "群聊学习",
                        f"主动发言：群{group_id}最后一条消息是{NICKNAME}发的，不发言",
                    )
                    continue
                elif cur_time - last_reply.time < config.speak_min_interval:
                    log_debug(
                        "群聊学习",
                        f"主动发言：群{group_id}上次主动发言时间小于主动发言最小间隔，不发言",
                    )
                    continue

            # 计算平均发言间隔
            if len(messages) > 1:
                avg_interval = (messages[0].time - messages[-1].time) / len(messages)
            else:
                avg_interval = 60

            silent_time = cur_time - messages[0].time
            threshold = avg_interval * config.speak_threshold

            if silent_time < threshold:
                log_debug(
                    "群聊学习",
                    f"主动发言：群{group_id}已沉默时间({silent_time})小于阈值({int(threshold)})，不发言",
                )
                continue

            # 获取可用的上下文
            contexts = await ChatContext.filter(count__gte=config.answer_threshold)
            if contexts:
                speak_list = []
                random.shuffle(contexts)

                for context in contexts:
                    if (
                        not speak_list
                        or random.random() < config.speak_continuously_probability
                    ) and len(speak_list) < config.speak_continuously_max_len:
                        answers = await ChatAnswer.filter(
                            context=context,
                            group_id=group_id,
                            count__gte=config.answer_threshold,
                        )
                        if answers:
                            weights = [
                                a.count + 1 if a.time >= today_time else a.count
                                for a in answers
                            ]
                            answer = random.choices(answers, weights=weights)[0]
                            message = random.choice(answer.messages)

                            if len(message) < 2:
                                continue
                            if message.startswith("&#91;") and message.endswith(
                                "&#93;"
                            ):
                                continue
                            if any(word in message for word in ban_words):
                                continue

                            speak_list.append(message)
                    else:
                        break

                if speak_list:
                    # 转换图片消息为可发送格式
                    converted_list = []
                    for msg in speak_list:
                        if "[CQ:image" in msg:
                            converted_msg = ImageCache.convert_message_for_send(msg)
                            if converted_msg is None:
                                # 图片缓存不存在，跳过这条消息
                                continue
                            msg = converted_msg
                        converted_list.append(msg)
                    if converted_list:
                        return group_id, converted_list
                else:
                    log_debug(
                        "群聊学习",
                        f"主动发言：群{group_id}没有找到符合条件的发言，不发言",
                    )

            log_debug("群聊学习", "主动发言：没有符合条件的群，不主动发言")
            return None

        return None
