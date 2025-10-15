import logging
import re
from datetime import datetime, timedelta
from ncatbot.core.message import GroupMessage
from ncatbot.plugin_system import NcatBotPlugin
from ncatbot.plugin_system.builtin_plugin.unified_registry.filter_system.decorators import (
    group_only,
)

log = logging.getLogger(__name__)


class MuteManager(NcatBotPlugin):
    name = "MuteManager"
    version = "2.0"
    WORK_END_HOUR = 19
    WORK_END_MINUTE = 30

    def parse_time_string(self, time_str: str) -> int:
        """
        解析时间字符串，返回禁言秒数
        支持格式：10分钟、1小时1分钟、61分钟、2天2小时3分钟等
        """
        time_str = time_str.strip()
        total_seconds = 0

        # 定义时间单位映射
        time_units = {"秒": 1, "分钟": 60, "小时": 3600, "天": 86400, "日": 86400}

        # 正则表达式匹配数字+单位
        pattern = r"(\d+)\s*(秒|分钟|小时|天|日)"
        matches = re.findall(pattern, time_str)

        if not matches:
            return 0

        for number, unit in matches:
            try:
                num = int(number)
                seconds = num * time_units.get(unit, 0)
                total_seconds += seconds
            except ValueError:
                continue

        return total_seconds

    async def is_admin_or_owner(self, group_id: int, user_id: int) -> bool:
        """检查用户是否为群主或管理员"""
        try:
            member_info = await self.api.get_group_member_info(
                group_id=group_id, user_id=user_id
            )
            # 新API直接返回GroupMemberInfo对象
            if member_info and hasattr(member_info, "role"):
                # onebot协议中role字段的值：owner(群主), admin(管理员), member(普通成员)
                return member_info.role in ["owner", "admin"]
            return False
        except Exception as e:
            log.error(f"获取用户权限信息失败: {e}")
            return False

    @group_only
    async def handle_mute_commands(self, input: GroupMessage):
        """处理禁言相关指令"""
        message = input.raw_message.strip()

        # 只处理包含禁言相关关键词的消息
        mute_keywords = ["禁言", "解禁", "取消禁言"]
        if not any(keyword in message for keyword in mute_keywords):
            return

        # 检查权限
        if not await self.is_admin_or_owner(input.group_id, input.sender.user_id):
            await self.api.post_group_msg(
                group_id=input.group_id,
                text="只有群主或管理员才能使用此命令",
                reply=input.message_id,
            )
            return

        # 解禁命令
        if message.startswith("解禁") or message.startswith("取消禁言"):
            await self.unmute_user(input)
            return

        # 禁言到下班时间
        if message.startswith("禁言到下班"):
            await self.mute_to_work_end(input)
            return

        # 解析禁言时间命令
        mute_patterns = [
            r"禁言\s*(\d+[天日小时分钟秒]+(?:\s*\d+[天日小时分钟秒]+)*)",
            r"禁言\s*(\d+)\s*分钟",
            r"禁言\s*(\d+)\s*小时",
            r"禁言\s*(\d+)\s*天",
            r"禁言\s*(\d+)\s*日",
        ]

        for pattern in mute_patterns:
            match = re.search(pattern, message)
            if match:
                time_str = match.group(1)
                mute_seconds = self.parse_time_string(time_str)
                if mute_seconds > 0:
                    await self.mute_user(input, mute_seconds)
                    return

        # 如果没有匹配到任何命令，显示帮助信息
        await self.show_help(input)

    def get_target_user_id(self, input: GroupMessage) -> str:
        """获取被@的用户ID"""
        for message in input.message:
            if hasattr(message, "msg_seg_type") and message.msg_seg_type == "at":
                return message.qq
        return None

    async def get_user_nickname(self, group_id: int, user_id: str) -> str:
        """获取用户昵称"""
        try:
            member_info = await self.api.get_group_member_info(
                group_id=group_id, user_id=user_id
            )
            if member_info and hasattr(member_info, "nickname"):
                return member_info.nickname
        except Exception as e:
            log.error(f"获取用户信息失败: {e}")
        return f"用户{user_id}"

    async def unmute_user(self, input: GroupMessage):
        """解禁用户"""
        target_id = self.get_target_user_id(input)
        if not target_id:
            await self.api.post_group_msg(
                group_id=input.group_id, text="请@要解禁的人", reply=input.message_id
            )
            return

        target_nickname = await self.get_user_nickname(input.group_id, target_id)

        try:
            await self.api.set_group_ban(
                group_id=input.group_id, user_id=target_id, duration=0
            )
            await self.api.post_group_msg(
                group_id=input.group_id,
                text=f"已解禁 {target_nickname}",
                reply=input.message_id,
            )
        except Exception as e:
            log.error(f"解禁失败: {e}")
            await self.api.post_group_msg(
                group_id=input.group_id,
                text="解禁失败，请稍后重试",
                reply=input.message_id,
            )

    async def mute_user(self, input: GroupMessage, mute_seconds: int):
        """禁言用户指定时间"""
        target_id = self.get_target_user_id(input)
        if not target_id:
            await self.api.post_group_msg(
                group_id=input.group_id, text="请@要禁言的人", reply=input.message_id
            )
            return

        target_nickname = await self.get_user_nickname(input.group_id, target_id)

        try:
            await self.api.set_group_ban(
                group_id=input.group_id, user_id=target_id, duration=mute_seconds
            )

            # 格式化时间显示
            time_display = self.format_duration(mute_seconds)
            await self.api.post_group_msg(
                group_id=input.group_id,
                text=f"已将 {target_nickname} 禁言 {time_display}",
                reply=input.message_id,
            )
        except Exception as e:
            log.error(f"禁言失败: {e}")
            await self.api.post_group_msg(
                group_id=input.group_id,
                text="禁言失败，请稍后重试",
                reply=input.message_id,
            )

    def format_duration(self, seconds: int) -> str:
        """格式化时间显示"""
        if seconds == 0:
            return "0秒"

        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        parts = []
        if days > 0:
            parts.append(f"{days}天")
        if hours > 0:
            parts.append(f"{hours}小时")
        if minutes > 0:
            parts.append(f"{minutes}分钟")
        if secs > 0:
            parts.append(f"{secs}秒")

        return "".join(parts)

    async def show_help(self, input: GroupMessage):
        """显示帮助信息"""
        help_text = """禁言管理插件使用说明：

禁言命令：
• 禁言10分钟
• 禁言1小时1分钟  
• 禁言2天2小时3分钟
• 禁言到下班

解禁命令：
• 解禁 @用户
• 取消禁言 @用户

注意：只有群主和管理员可以使用"""

        await self.api.post_group_msg(
            group_id=input.group_id,
            text=help_text,
            reply=input.message_id,
        )

    async def mute_to_work_end(self, input: GroupMessage):
        """禁言到下班时间"""
        target_id = self.get_target_user_id(input)
        if not target_id:
            await self.api.post_group_msg(
                group_id=input.group_id, text="请@要禁言的人", reply=input.message_id
            )
            return

        target_nickname = await self.get_user_nickname(input.group_id, target_id)

        now = datetime.now()
        work_end = now.replace(
            hour=self.WORK_END_HOUR,
            minute=self.WORK_END_MINUTE,
            second=0,
            microsecond=0,
        )

        # 如果当前时间已经过了下班时间，就禁言到明天
        if now >= work_end:
            work_end = work_end + timedelta(days=1)

        # 计算需要禁言的秒数
        mute_seconds = int((work_end - now).total_seconds())

        try:
            await self.api.set_group_ban(
                group_id=input.group_id, user_id=target_id, duration=mute_seconds
            )
            await self.api.post_group_msg(
                group_id=input.group_id,
                text=f"已将 {target_nickname} 禁言到 {work_end.strftime('%H:%M')}",
                reply=input.message_id,
            )
        except Exception as e:
            log.error(f"禁言失败: {e}")
            await self.api.post_group_msg(
                group_id=input.group_id,
                text="禁言失败，请稍后重试",
                reply=input.message_id,
            )
