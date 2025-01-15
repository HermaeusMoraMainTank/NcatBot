import random
from datetime import date
from typing import Dict, Set, List

from .. import logging, WsApi
from ..common.constants.HMMT import HMMT
from ..common.entity.GroupMember import GroupMember
from ..common.utils.CommonUtil import CommonUtil
from ..message import GroupMessage

_log = logging.get_logger()
common_util = CommonUtil()


class TodayWaifu:
    def __init__(self):
        self.WsApi = WsApi()
        self.allocated_wives_by_group: Dict[int, Set[int]] = {}
        self.user_to_wife_map_by_group: Dict[int, Dict[int, int]] = {}
        self.last_reset_date = date.today()  # 记录上一次重置的日期

    def reset_all_allocated_wives(self):
        """重置所有已分配的老婆"""
        self.allocated_wives_by_group.clear()
        self.user_to_wife_map_by_group.clear()

    async def get_random_wife(self, input: GroupMessage, group_id: int):
        """随机获取一个老婆"""
        allocated_wives = self.allocated_wives_by_group.setdefault(group_id, set())

        members_response = await input.get_group_member_list(group_id=group_id)

        if members_response.get("status") != "ok" or members_response.get("retcode") != 0:
            return await input.get_group_member_info(group_id=group_id, user_id=HMMT.BOT_ID)

        members = [GroupMember(member) for member in members_response.get("data", [])]

        filtered_members = [
            member for member in members
            if member.user_id not in allocated_wives
               and member.user_id != input.user_id
        ]

        if not filtered_members:
            bot_member_info = await input.get_group_member_info(group_id=group_id, user_id=HMMT.BOT_ID)
            if isinstance(bot_member_info, dict):  # 如果返回的是字典
                return GroupMember(bot_member_info)  # 转换为 GroupMember 对象
            return bot_member_info  # 如果已经是 GroupMember 对象，直接返回

        selected_wife = random.choice(filtered_members)
        allocated_wives.add(selected_wife.user_id)

        return selected_wife

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
                await input.add_text("今*老婆").reply()
                return
            if random_value < 0.05:
                await input.add_text("你快醒醒 你没有老婆").reply()
                return

            user_to_wife_map = self.user_to_wife_map_by_group.setdefault(group_id, {})
            allocated_wives = self.allocated_wives_by_group.setdefault(group_id, set())

            # 检查当前用户是否已经有老婆
            if user_id in user_to_wife_map:
                wife_id = user_to_wife_map[user_id]
                wife_info = await input.get_group_member_info(group_id=group_id, user_id=wife_id)

                if isinstance(wife_info, dict) and wife_info.get("status") == "ok":
                    wife_data = wife_info.get("data", {})
                    if wife_data:  # 确保 data 字段存在且不为空
                        wife_info = GroupMember(wife_data)  # 将 data 字段传递给 GroupMember

                        avatar_url = common_util.get_avatar(wife_info.user_id)

                        await (input.add_at(user_id).add_text(f"你今天的群友老婆是：").add_image(avatar_url)
                               .add_text(f" {wife_info.nickname}({wife_info.user_id})")).reply()
                        return
                else:
                    await input.add_text("获取老婆信息失败，请稍后再试。").reply()
                    return

            new_wife = await self.get_random_wife(input, group_id)

            user_to_wife_map[user_id] = new_wife.user_id
            allocated_wives.add(new_wife.user_id)

            avatar_url = common_util.get_avatar(new_wife.user_id)

            # 发送消息
            await (input.add_at(user_id).add_text(f"你今天的群友老婆是：").add_image(avatar_url)
                   .add_text(f" {new_wife.nickname}({new_wife.user_id})")).reply()

        if input.raw_message.startswith("换") and input.raw_message.endswith("的老婆") and user_id == HMMT.HMMT_ID:
            target_user_id = 0

            for isAt in input.message:
                if isAt.get("type") == "at":
                    target_user_id = isAt.get("data").get("qq")

            target = await input.get_group_member_info(group_id=group_id, user_id=target_user_id)
            target_username = target.get("data").get("nickname")

            user_to_wife_map = self.user_to_wife_map_by_group.setdefault(group_id, {})
            if target_user_id not in user_to_wife_map:
                await input.add_text(f"{target_username} 没有老婆，无法更换。").reply()
                return

            target_wife_id = user_to_wife_map[target_user_id]

            allocated_wives = self.allocated_wives_by_group.setdefault(group_id, set())

            allocated_wives.remove(target_wife_id)
            user_to_wife_map.pop(target_user_id)
            new_wife = await self.get_random_wife(input, group_id)
            user_to_wife_map[target_user_id] = new_wife.user_id

            await input.add_text(f"@{input.sender.nickname} 成功更换了 {target_username} 的老婆。").reply()
            await input.add_at(target_user_id).add_text(f"你的老婆被 {input.sender.nickname} 更换了。").reply()

        if message == "换一个老婆" and user_id == HMMT.HMMT_ID:
            user_to_wife_map = self.user_to_wife_map_by_group.setdefault(group_id, {})
            allocated_wives = self.allocated_wives_by_group.setdefault(group_id, set())

            if user_id not in user_to_wife_map:
                await input.add_text("你还没有老婆，无法换一个老婆。").reply()
                return

            current_wife_id = user_to_wife_map[user_id]

            if current_wife_id in allocated_wives:
                allocated_wives.remove(current_wife_id)

            user_to_wife_map.pop(user_id)

            new_wife = await self.get_random_wife(input, group_id)
            if new_wife is None:
                await input.add_text("无法获取新的老婆，请稍后再试。").reply()
                return

            user_to_wife_map[user_id] = new_wife.user_id
            allocated_wives.add(new_wife.user_id)

            await input.add_text(f"@{input.sender.nickname} 成功更换了老婆，你的新老婆是：{new_wife.nickname}").reply()
