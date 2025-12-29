"""
LearningChat 插件主模块
让Bot学习群友们的发言和表情包
"""

import asyncio
import random
import time
import re
import logging

from ncatbot.core import GroupMessage, Reply
from ncatbot.plugin_system import NcatBotPlugin, on_message

from .handler import LearningChatHandler
from .models import ChatMessage, db_manager
from .config import config_manager, NICKNAME

log = logging.getLogger(__name__)


# ========== 公共记录接口 - 供其他插件调用 ==========
async def record_bot_message(
    group_id: int,
    bot_id: int,
    message: str,
    message_id: int = 0,
):
    """
    记录 Bot 发送的消息到 LearningChat 数据库

    其他插件（如 FakeAi）可以在发送消息后调用此方法，
    让 LearningChat 学习 Bot 的回复。

    使用方法:
        from plugins.LearningChat import record_bot_message
        await record_bot_message(group_id, bot_id, "Bot的回复内容", message_id)

    Args:
        group_id: 群号
        bot_id: Bot 的 QQ 号
        message: 消息内容
        message_id: 消息 ID（可选，默认为 0）
    """
    try:
        bot_message = ChatMessage(
            group_id=group_id,
            user_id=bot_id,
            message_id=message_id,
            message=message,
            raw_message=message,
            plain_text=message,
            time=int(time.time()),
        )
        await bot_message.save()
        log.debug(
            f"[LearningChat] 记录 Bot 消息: group={group_id}, msg={message[:50]}..."
        )
    except Exception as e:
        log.error(f"[LearningChat] 记录 Bot 消息失败: {e}")


# ========== 公共记录接口 - 结束 ==========


class LearningChat(NcatBotPlugin):
    """群聊学习插件"""

    name = "LearningChat"
    version = "1.0"
    description = "学习群友们的发言、复读以及主动发言"

    async def on_load(self):
        """插件加载时初始化"""
        log.info(f"开始加载 {self.name} 插件 v{self.version}")

        # 初始化数据库
        await db_manager.init_db()
        log.info(f"[{self.name}] 数据库初始化完成")

        # 添加主动发言定时任务（每3分钟检查一次）
        self.add_scheduled_task(
            self._speak_up,
            "learning_chat_speak",
            "3m",  # 每3分钟执行一次
        )
        log.info(f"[{self.name}] 主动发言定时任务已注册")

        log.info(f"{self.name} 插件加载完成")

    async def on_unload(self):
        """插件卸载时清理"""
        await db_manager.close()
        log.info(f"{self.name} 插件已卸载")

    @on_message
    async def handle_message(self, input: GroupMessage):
        """处理群消息"""

        # 检查总开关
        if not config_manager.config.total_enable:
            return

        # 获取消息内容
        raw_message = input.raw_message
        plain_text = self._extract_plain_text(raw_message)

        # 检查是否有回复
        reply_message_id = None
        reply_list = input.message.filter(Reply)
        if reply_list:
            reply_message_id = reply_list[0].id
        else:
            # 兼容 CQ 码格式
            match = re.search(r"\[CQ:reply,id=(\d+)\]", raw_message)
            if match:
                try:
                    reply_message_id = int(match.group(1))
                except ValueError:
                    pass

        # 判断是否 @机器人
        to_me = self._check_to_me(input)

        # 检查是否是 @机器人的消息，如果是则让 FakeAi 处理，不进行回复
        # 但仍然学习消息内容
        skip_reply = self._should_skip_for_ai(input)

        # 获取发送者角色
        role = getattr(input.sender, "role", "member")

        # 创建处理器
        handler = LearningChatHandler(
            group_id=input.group_id,
            user_id=input.sender.user_id,
            message_id=input.message_id,
            message=raw_message,
            raw_message=raw_message,
            plain_text=plain_text,
            time=int(time.time()),
            bot_id=self._get_bot_id(),
            to_me=to_me,
            role=role,
            reply_message_id=reply_message_id,
        )

        # 获取回复（如果需要跳过回复，则只学习不回复）
        answers = await handler.answer()

        # 如果应该跳过回复（让 AI 插件处理），则不发送
        if skip_reply:
            log.debug(f"[{self.name}] 检测到 @机器人，跳过回复，由 AI 插件处理")
            return

        if answers:
            for answer in answers:
                try:
                    log.info(f'[{self.name}] 将向群{input.group_id}回复"{answer}"')

                    # 发送回复
                    response = await self.api.post_group_msg(
                        group_id=input.group_id,
                        text=answer,
                    )

                    # 记录bot发送的消息
                    bot_message = ChatMessage(
                        group_id=input.group_id,
                        user_id=self._get_bot_id(),
                        message_id=response if isinstance(response, int) else 0,
                        message=answer,
                        raw_message=answer,
                        plain_text=self._extract_plain_text(answer),
                        time=int(time.time()),
                    )
                    await bot_message.save()

                    # 随机延迟，模拟人类行为
                    await asyncio.sleep(random.random() + 0.5)

                except Exception as e:
                    log.error(
                        f'[{self.name}] 向群{input.group_id}的回复"{answer}"发送失败: {e}'
                    )

    async def _speak_up(self):
        """主动发言定时任务"""
        if not config_manager.config.total_enable:
            return

        try:
            bot_id = self._get_bot_id()
            speak = await LearningChatHandler.speak(bot_id)

            if not speak:
                return

            group_id, messages = speak

            # ========== 【群白名单限制】 - 开始 ==========
            # TODO: 以后要开放全部群时，删除这段检查代码
            if group_id not in self.ALLOWED_GROUPS:
                log.debug(f"[{self.name}] 群 {group_id} 不在白名单中，跳过主动发言")
                return
            # ========== 【群白名单限制】 - 结束 ==========

            for msg in messages:
                try:
                    log.info(f'[{self.name}] 向群{group_id}主动发言"{msg}"')

                    response = await self.api.post_group_msg(
                        group_id=group_id,
                        text=msg,
                    )

                    # 记录bot发送的消息
                    bot_message = ChatMessage(
                        group_id=group_id,
                        user_id=bot_id,
                        message_id=response if isinstance(response, int) else 0,
                        message=msg,
                        raw_message=msg,
                        plain_text=self._extract_plain_text(msg),
                        time=int(time.time()),
                    )
                    await bot_message.save()

                    await asyncio.sleep(random.randint(2, 4))

                except Exception as e:
                    log.error(
                        f'[{self.name}] 向群{group_id}主动发言"{msg}"发送失败: {e}'
                    )

        except Exception as e:
            log.error(f"[{self.name}] 主动发言任务执行失败: {e}")

    def _get_bot_id(self) -> int:
        """获取机器人ID"""
        # 尝试从 api 获取
        if hasattr(self, "api") and hasattr(self.api, "self_id"):
            return int(self.api.self_id)
        # 默认返回 0
        return 0

    def _check_to_me(self, input: GroupMessage) -> bool:
        """检查是否 @机器人"""
        # 检查消息中是否有 @ 机器人
        raw_message = input.raw_message
        bot_id = self._get_bot_id()

        # 检查 CQ 码格式的 @
        if f"[CQ:at,qq={bot_id}]" in raw_message:
            return True

        # 检查是否包含机器人昵称
        if NICKNAME and NICKNAME in raw_message:
            return True

        return False

    def _should_skip_for_ai(self, input: GroupMessage) -> bool:
        """
        检查是否应该跳过回复，让 AI 插件处理
        当消息中包含 @机器人 时，跳过 LearningChat 的回复
        """
        raw_message = input.raw_message
        bot_id = self._get_bot_id()

        # 检查 CQ 码格式的 @ 机器人
        if f"[CQ:at,qq={bot_id}]" in raw_message:
            return True

        # 也可以检查其他需要跳过的情况
        # 例如：特定的命令前缀
        skip_prefixes = [
            "蓝晴说话",  # FakeAi 测试命令
            "查询余额",  # FakeAi 余额查询
        ]
        clean_message = re.sub(r"\[CQ:[^\]]+\]", "", raw_message).strip()
        for prefix in skip_prefixes:
            if clean_message.startswith(prefix):
                return True

        return False

    @staticmethod
    def _extract_plain_text(message: str) -> str:
        """提取纯文本内容"""
        # 移除所有 CQ 码
        text = re.sub(r"\[CQ:[^\]]+\]", "", message)
        return text.strip()
