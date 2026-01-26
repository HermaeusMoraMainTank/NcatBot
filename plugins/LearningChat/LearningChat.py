"""
LearningChat 插件主模块
让Bot学习群友们的发言和表情包
"""

import asyncio
import random
import time
import re
import logging

from ncatbot.core import GroupMessage, Reply, Image
from ncatbot.plugin_system import NcatBotPlugin, on_message

from .handler import LearningChatHandler
from .models import ChatMessage, ImageCache, db_manager
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

    # ========== 配置参数 ==========
    # 允许主动发言的群白名单（TODO: 以后开放全部群时删除此限制）
    ALLOWED_GROUPS = [719518427, 853963912]

    # 模拟打字延迟开关（默认关闭）
    ENABLE_TYPING_DELAY = False
    # 每个字符的延迟时间（秒）
    TYPING_DELAY_PER_CHAR = 0.1

    # 冻结用户列表（LearningChat 功能不生效）
    FROZEN_USERS = [794383252]

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

        # 添加图片缓存清理定时任务（每小时执行一次）
        self.add_scheduled_task(
            self._cleanup_image_cache,
            "learning_chat_image_cleanup",
            "1h",  # 每小时执行一次
        )
        log.info(f"[{self.name}] 图片缓存清理定时任务已注册")

        # 启动时执行一次图片缓存清理
        try:
            deleted, size_mb = await ImageCache.cleanup_unused_images()
            if deleted > 0:
                log.info(
                    f"[{self.name}] 启动清理: 删除 {deleted} 张图片，释放 {size_mb:.2f} MB"
                )
            # 输出缓存统计
            stats = ImageCache.get_cache_stats()
            log.info(
                f"[{self.name}] 图片缓存状态: "
                f"{stats['total_files']} 个文件, {stats['total_size_mb']:.2f} MB"
            )
        except Exception as e:
            log.warning(f"[{self.name}] 启动清理失败: {e}")

        log.info(f"{self.name} 插件加载完成")

    async def on_unload(self):
        """插件卸载时清理"""
        await db_manager.close()
        log.info(f"{self.name} 插件已卸载")

    async def _cleanup_image_cache(self):
        """定时清理图片缓存"""
        try:
            deleted, size_mb = await ImageCache.cleanup_unused_images()
            if deleted > 0:
                log.info(
                    f"[{self.name}] 定时清理: 删除 {deleted} 张图片，释放 {size_mb:.2f} MB"
                )
        except Exception as e:
            log.error(f"[{self.name}] 图片缓存清理失败: {e}")

    @on_message
    async def handle_message(self, input: GroupMessage):
        """处理群消息"""

        # 检查总开关
        if not config_manager.config.total_enable:
            return

        # 冻结用户：LearningChat 功能不生效
        if int(input.sender.user_id) in self.FROZEN_USERS:
            log.debug(f"[{self.name}] 冻结用户 {input.sender.user_id}，跳过学习和回复")
            return

        # 获取消息内容
        raw_message = input.raw_message
        plain_text = self._extract_plain_text(raw_message)

        # 如果消息包含图片，尝试缓存图片
        if "[CQ:image" in raw_message:
            try:
                # 使用 ncatbot 的 Image 对象下载（推荐方式）
                images = input.message.filter(Image)
                log.debug(
                    f"[{self.name}] 检测到图片消息，Image对象数量: {len(images) if images else 0}"
                )
                if images:
                    cached = await ImageCache.cache_from_images(images)
                    log.debug(f"[{self.name}] 图片缓存结果: {len(cached)} 张成功")
                else:
                    # 备用方式：从 raw_message 解析 URL 下载
                    log.debug(f"[{self.name}] 没有Image对象，使用备用方式下载")
                    ImageCache.cache_from_message(raw_message)
            except Exception as e:
                log.warning(f"[{self.name}] 缓存图片失败: {e}")

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
                    # 检查并处理图片消息
                    send_message = answer
                    if "[CQ:image" in answer:
                        # 转换图片为可发送格式，如果图片不存在会被移除
                        send_message = ImageCache.convert_message_for_send(answer)
                        if send_message is None:
                            # 所有图片都不存在，跳过这条回复
                            log.debug(
                                f"[{self.name}] 回复中的图片已被清理，跳过: {answer[:50]}..."
                            )
                            continue

                    log.info(
                        f'[{self.name}] 将向群{input.group_id}回复"{send_message}"'
                    )

                    # 发送回复（可选模拟打字延迟）
                    response = await self._send_with_typing_delay(
                        group_id=input.group_id,
                        message=send_message,
                    )

                    # 记录bot发送的消息
                    bot_message = ChatMessage(
                        group_id=input.group_id,
                        user_id=self._get_bot_id(),
                        message_id=response if isinstance(response, int) else 0,
                        message=send_message,
                        raw_message=send_message,
                        plain_text=self._extract_plain_text(send_message),
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
                    # 检查并处理图片消息
                    send_message = msg
                    if "[CQ:image" in msg:
                        # 转换图片为可发送格式，如果图片不存在会被移除
                        send_message = ImageCache.convert_message_for_send(msg)
                        if send_message is None:
                            # 所有图片都不存在，跳过这条消息
                            log.debug(
                                f"[{self.name}] 主动发言中的图片已被清理，跳过: {msg[:50]}..."
                            )
                            continue

                    log.info(f'[{self.name}] 向群{group_id}主动发言"{send_message}"')

                    # 发送消息（可选模拟打字延迟）
                    response = await self._send_with_typing_delay(
                        group_id=group_id,
                        message=send_message,
                    )

                    # 记录bot发送的消息
                    bot_message = ChatMessage(
                        group_id=group_id,
                        user_id=bot_id,
                        message_id=response if isinstance(response, int) else 0,
                        message=send_message,
                        raw_message=send_message,
                        plain_text=self._extract_plain_text(send_message),
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

    async def _send_with_typing_delay(self, group_id: int, message: str):
        """发送消息，可选模拟打字延迟

        Args:
            group_id: 群号
            message: 消息内容

        Returns:
            发送结果
        """
        # 如果开启了打字延迟，根据消息长度延迟
        if self.ENABLE_TYPING_DELAY:
            # 计算纯文本长度（去除CQ码）
            plain_text = self._extract_plain_text(message)
            delay = len(plain_text) * self.TYPING_DELAY_PER_CHAR
            # 限制最大延迟为5秒
            delay = min(delay, 5.0)
            if delay > 0:
                await asyncio.sleep(delay)

        # 检查消息是否包含本地图片路径
        if "[CQ:image,file=file:///" in message:
            # 使用 rtf 发送包含图片的消息
            log.debug(f"[{self.name}] 发送图片消息到群{group_id}")
            result = await self._send_image_message(group_id, message)
            log.info(f"[{self.name}] 图片消息发送完成，结果: {result}")
            return result
        else:
            log.debug(f"[{self.name}] 发送文本消息到群{group_id}: {message[:50]}...")
            result = await self.api.post_group_msg(group_id=group_id, text=message)
            log.info(f"[{self.name}] 文本消息发送完成，结果: {result}")
            return result

    async def _send_image_message(self, group_id: int, message: str):
        """
        发送包含图片的消息

        Args:
            group_id: 群号
            message: 包含图片CQ码的消息

        Returns:
            发送结果
        """
        from ncatbot.core import MessageArray
        import re

        # 解析消息，将其转换为 MessageArray
        msg_array = MessageArray()

        # 正则表达式匹配图片CQ码
        image_pattern = re.compile(r"\[CQ:image,file=file:///([^\]]+)\]")

        last_end = 0
        for match in image_pattern.finditer(message):
            # 添加图片前的文本
            if match.start() > last_end:
                text_part = message[last_end : match.start()]
                if text_part:
                    msg_array.add_text(text_part)

            # 添加图片
            image_path = match.group(1)
            msg_array.add_image(image_path)

            last_end = match.end()

        # 添加最后的文本部分
        if last_end < len(message):
            text_part = message[last_end:]
            if text_part:
                msg_array.add_text(text_part)

        # 如果消息数组为空（不应该发生），发送原始文本
        if len(msg_array.elements) == 0:
            return await self.api.post_group_msg(group_id=group_id, text=message)

        return await self.api.post_group_msg(group_id=group_id, rtf=msg_array)

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

    @on_message
    async def handle_test_image(self, input: GroupMessage):
        """测试图片发送功能"""
        message = input.raw_message.strip()

        # 测试命令：测试图片缓存
        if message == "测试图片缓存":
            from pathlib import Path
            from ncatbot.core import MessageChain, Text, Image as BotImage

            cache_dir = Path("data") / "LearningChat" / "image_cache"
            if not cache_dir.exists():
                await self.api.post_group_msg(
                    group_id=input.group_id, text="图片缓存目录不存在"
                )
                return

            # 获取最新缓存的图片
            image_files = sorted(
                cache_dir.glob("*.*"), key=lambda f: f.stat().st_mtime, reverse=True
            )

            if not image_files:
                await self.api.post_group_msg(
                    group_id=input.group_id, text="没有缓存的图片"
                )
                return

            # 获取最新的3张图片
            recent_images = image_files[:3]

            # 发送统计信息
            stats = ImageCache.get_cache_stats()
            info_msg = "图片缓存统计：\n"
            info_msg += f"- 总文件数: {stats['total_files']}\n"
            info_msg += f"- 总大小: {stats['total_size_mb']:.2f} MB\n"
            info_msg += "- 最新缓存的图片："

            await self.api.post_group_msg(group_id=input.group_id, text=info_msg)

            # 尝试发送最新的图片
            for img_path in recent_images:
                try:
                    log.info(f"[{self.name}] 测试发送图片: {img_path}")

                    # 方式1：使用 MessageChain
                    chain = MessageChain(
                        [
                            Text(f"文件: {img_path.name}\n"),
                            BotImage(str(img_path.absolute())),
                        ]
                    )
                    await self.api.post_group_msg(group_id=input.group_id, rtf=chain)

                    log.info(f"[{self.name}] 图片发送成功: {img_path.name}")
                except Exception as e:
                    log.error(f"[{self.name}] 图片发送失败: {e}")
                    await self.api.post_group_msg(
                        group_id=input.group_id, text=f"发送失败 {img_path.name}: {e}"
                    )

        # 测试命令：测试CQ码图片
        elif message == "测试CQ图片":
            from pathlib import Path

            cache_dir = Path("data") / "LearningChat" / "image_cache"
            image_files = sorted(
                cache_dir.glob("*.*"), key=lambda f: f.stat().st_mtime, reverse=True
            )

            if not image_files:
                await self.api.post_group_msg(
                    group_id=input.group_id, text="没有缓存的图片"
                )
                return

            # 获取最新的图片
            img_path = image_files[0]
            file_id = img_path.name

            # 使用 ImageCache 的方法转换
            cq_code = ImageCache.get_image_cq_code(file_id)
            if cq_code:
                log.info(f"[{self.name}] 测试CQ码: {cq_code}")

                # 使用 _send_image_message 方法发送
                try:
                    await self._send_image_message(input.group_id, cq_code)
                    log.info(f"[{self.name}] CQ码图片发送成功")
                except Exception as e:
                    log.error(f"[{self.name}] CQ码图片发送失败: {e}")
                    await self.api.post_group_msg(
                        group_id=input.group_id, text=f"CQ码发送失败: {e}"
                    )
            else:
                await self.api.post_group_msg(
                    group_id=input.group_id, text=f"无法生成CQ码: {file_id}"
                )
