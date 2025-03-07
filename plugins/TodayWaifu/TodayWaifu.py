import random
from datetime import date
from typing import Dict, Set

from common.constants.HMMT import HMMT
from common.entity.GroupMember import GroupMember
from common.utils.CommonUtil import CommonUtil
from ncatbot.core.element import At, Image as ImageElement, MessageChain, Text
from ncatbot.core.message import GroupMessage
from ncatbot.plugin.compatible import CompatibleEnrollment
from ncatbot.plugin.base_plugin import BasePlugin

from ncatbot.utils.logger import get_log

bot = CompatibleEnrollment

_log = get_log()


class TodayWaifu(BasePlugin):
    name = "TodayWaifu"  # 插件名称
    version = "1.0"  # 插件版本

    allocated_wives_by_group: Dict[int, Set[int]] = {}
    user_to_wife_map_by_group: Dict[int, Dict[int, int]] = {}
    last_reset_date = date.today()  # 记录上一次重置的日期

    def reset_all_allocated_wives(self):
        """重置所有已分配的老婆"""
        self.allocated_wives_by_group.clear()
        self.user_to_wife_map_by_group.clear()

    async def get_random_wife(self, input: GroupMessage, group_id: int):
        """随机获取一个老婆"""
        allocated_wives = self.allocated_wives_by_group.setdefault(group_id, set())

        members_response = await self.api.get_group_member_list(group_id=group_id)

        if (
            members_response.get("status") != "ok"
            or members_response.get("retcode") != 0
        ):
            return await self.api.get_group_member_info(
                group_id=group_id, user_id=HMMT.BOT_ID,no_cache=True
            )

        members = [GroupMember(member) for member in members_response.get("data", [])]

        filtered_members = [
            member
            for member in members
            if member.user_id not in allocated_wives and member.user_id != input.user_id
        ]

        if not filtered_members:
            bot_member_info = await self.api.get_group_member_info(
                group_id=group_id, user_id=HMMT.BOT_ID,no_cache=True
            )
            if isinstance(bot_member_info, dict):  # 如果返回的是字典
                return GroupMember(bot_member_info)  # 转换为 GroupMember 对象
            return bot_member_info  # 如果已经是 GroupMember 对象，直接返回

        selected_wife = random.choice(filtered_members)
        allocated_wives.add(selected_wife.user_id)

        return selected_wife

    @bot.group_event()
    async def handle_message(self, input: GroupMessage):
        if not input.message:
            return
        """处理消息"""
        message = input.message[0].get("data", {}).get("text", "")
        user_id = input.sender.user_id
        group_id = input.group_id

        # 检查日期是否已经跨天，如果是，则执行重置操作
        current_date = date.today()
        if current_date != self.last_reset_date:
            self.reset_all_allocated_wives()
            self.last_reset_date = current_date

        if message == "今日老婆":
            random_value = random.random()
            if 0.05 <= random_value <= 0.15:
                return await self.api.post_group_msg(
                    group_id=input.group_id, text="今*老婆"
                )
            if random_value < 0.05:
                return await self.api.post_group_msg(
                    group_id=input.group_id, text="你快醒醒 你没有老婆"
                )

            user_to_wife_map = self.user_to_wife_map_by_group.setdefault(group_id, {})
            allocated_wives = self.allocated_wives_by_group.setdefault(group_id, set())

            # 检查当前用户是否已经有老婆
            if user_id in user_to_wife_map:
                wife_id = user_to_wife_map[user_id]
                wife_info = await self.api.get_group_member_info(
                    group_id=group_id, user_id=wife_id,no_cache=True
                )

                if isinstance(wife_info, dict) and wife_info.get("status") == "ok":
                    wife_data = wife_info.get("data", {})
                    if wife_data:  # 确保 data 字段存在且不为空
                        wife_info = GroupMember(
                            wife_data
                        )  # 将 data 字段传递给 GroupMember

                        avatar_url = CommonUtil.get_avatar(wife_info.user_id)

                        return await self.api.post_group_msg(
                            group_id=input.group_id,
                            rtf=MessageChain(
                                [
                                    At(user_id),
                                    Text("你今天的群友老婆是："),
                                    ImageElement(avatar_url),
                                    Text(f" {wife_info.nickname}({wife_info.user_id})"),
                                ]
                            ),
                        )
                else:
                    return await self.api.post_group_msg(
                        group_id=input.group_id, text="获取老婆信息失败，请稍后再试。"
                    )

            new_wife = await self.get_random_wife(input, group_id)

            user_to_wife_map[user_id] = new_wife.user_id
            allocated_wives.add(new_wife.user_id)

            avatar_url = CommonUtil.get_avatar(new_wife.user_id)

            # 发送消息
            return await self.api.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain(
                    [
                        At(user_id),
                        Text("你今天的群友老婆是："),
                        ImageElement(avatar_url),
                        Text(f" {new_wife.nickname}({new_wife.user_id})"),
                    ]
                ),
            )

        if (
            input.raw_message.startswith("换")
            and input.raw_message.endswith("的老婆")
            and user_id == HMMT.HMMT_ID
        ):
            target_user_id = 0

            for isAt in input.message:
                if isAt.get("type") == "at":
                    target_user_id = int(isAt.get("data").get("qq"))

            target = await self.api.get_group_member_info(
                group_id=group_id, user_id=target_user_id,no_cache=True
            )
            target_username = target.get("data").get("nickname")

            user_to_wife_map = self.user_to_wife_map_by_group.setdefault(group_id, {})
            if target_user_id not in user_to_wife_map:
                return await self.api.post_group_msg(
                    group_id=input.group_id,
                    text=f"{target_username} 没有老婆，无法更换。",
                )

            target_wife_id = user_to_wife_map[target_user_id]

            allocated_wives = self.allocated_wives_by_group.setdefault(group_id, set())

            allocated_wives.remove(target_wife_id)
            user_to_wife_map.pop(target_user_id)
            new_wife = await self.get_random_wife(input, group_id)
            user_to_wife_map[target_user_id] = new_wife.user_id

            await self.api.post_group_msg(
                group_id=input.group_id,
                text=f"@{input.sender.nickname} 成功更换了 {target_username} 的老婆。",
            )
            return await self.api.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain(
                    [
                        At(target_user_id),
                        Text(f"你的老婆被 {input.sender.nickname} 更换了。"),
                    ]
                ),
            )

        if message == "换一个老婆" and user_id == HMMT.HMMT_ID:
            user_to_wife_map = self.user_to_wife_map_by_group.setdefault(group_id, {})
            allocated_wives = self.allocated_wives_by_group.setdefault(group_id, set())

            if user_id not in user_to_wife_map:
                return await self.api.post_group_msg(
                    group_id=input.group_id, text="你还没有老婆，无法换一个老婆。"
                )

            current_wife_id = user_to_wife_map[user_id]

            if current_wife_id in allocated_wives:
                allocated_wives.remove(current_wife_id)

            user_to_wife_map.pop(user_id)

            new_wife = await self.get_random_wife(input, group_id)
            if new_wife is None:
                return await self.api.post_group_msg(
                    group_id=input.group_id, text="无法获取新的老婆，请稍后再试。"
                )

            user_to_wife_map[user_id] = new_wife.user_id
            allocated_wives.add(new_wife.user_id)

            return await self.api.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain(
                    [
                        At(user_id),
                        Text(
                            f"@{input.sender.nickname} 成功更换了老婆，你的新老婆是：{new_wife.nickname}"
                        ),
                    ]
                ),
            )
