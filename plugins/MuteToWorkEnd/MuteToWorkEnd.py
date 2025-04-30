import logging
from datetime import datetime, timedelta
from ncatbot.core.message import GroupMessage
from ncatbot.plugin import CompatibleEnrollment, BasePlugin
from common.entity.GroupMember import GroupMember

bot = CompatibleEnrollment
log = logging.getLogger(__name__)


class MuteToWorkEnd(BasePlugin):
    name = "MuteToWorkEnd"
    version = "1.0"
    WORK_END_HOUR = 19
    WORK_END_MINUTE = 30
    ADMIN_QQ = 273421673  # 管理员QQ号

    @bot.group_event()
    async def handle_mute_to_work_end(self, input: GroupMessage):
        """处理禁言到下班时间的指令"""
        if input.raw_message.strip().startswith("禁言到下班"):
            # 检查权限
            if input.user_id != self.ADMIN_QQ:
                await self.api.post_group_msg(
                    group_id=input.group_id,
                    text="只有管理员才能使用此命令",
                    reply=input.message_id,
                )
                return
            await self.mute_to_work_end(input)

    async def mute_to_work_end(self, input: GroupMessage):
        """禁言到下班时间"""
        # 检查是否有@消息
        target_id = None
        for message in input.message:
            if message["type"] == "at":
                target_id = message["data"]["qq"]
                break

        if not target_id:
            await self.api.post_group_msg(
                group_id=input.group_id, text="请@要禁言的人", reply=input.message_id
            )
            return

        # 获取被@用户的信息
        try:
            member_info = await self.api.get_group_member_info(
                group_id=input.group_id, user_id=target_id, no_cache=True
            )
            if isinstance(member_info, dict) and member_info.get("status") == "ok":
                member_data = member_info.get("data", {})
                if member_data:
                    target_member = GroupMember(member_data)
                    target_nickname = target_member.nickname
                else:
                    target_nickname = f"用户{target_id}"
            else:
                target_nickname = f"用户{target_id}"
        except Exception as e:
            log.error(f"获取用户信息失败: {e}")
            target_nickname = f"用户{target_id}"

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
