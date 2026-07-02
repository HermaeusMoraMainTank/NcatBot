import base64
import io
import random
from datetime import date
from typing import Dict, List, Set, Tuple

from PIL import Image as PILImage, ImageDraw, ImageFont

from common.constants.HMMT import HMMT
from common.entity.GroupMember import GroupMember
from common.utils.CommonUtil import CommonUtil
from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.types import At, MessageArray
from ncatbot.plugin import NcatBotPlugin
from ncatbot.core import registrar

from ncatbot.utils import get_log

_log = get_log()


class TodayWaifu(NcatBotPlugin):
    name = "TodayWaifu"  # 插件名称
    version = "1.0"  # 插件版本

    allocated_wives_by_group: Dict[str, Set[str]] = {}
    user_to_wife_map_by_group: Dict[str, Dict[str, str]] = {}
    last_reset_date = date.today()  # 记录上一次重置的日期

    def reset_all_allocated_wives(self):
        """重置所有已分配的老婆"""
        self.allocated_wives_by_group.clear()
        self.user_to_wife_map_by_group.clear()

    async def get_random_wife(self, input: GroupMessage, group_id: str):
        """随机获取一个老婆"""
        allocated_wives = self.allocated_wives_by_group.setdefault(group_id, set())

        members_response = await self.api.qq.query.get_group_member_list(
            group_id=group_id
        )

        # NcatBot 5：get_group_member_list 返回 list[GroupMemberInfo]，无 .members
        if members_response is None:
            empty = True
        elif isinstance(members_response, list):
            empty = len(members_response) == 0
        elif hasattr(members_response, "members"):
            empty = not members_response.members
        else:
            empty = True

        if empty:
            return await self.api.qq.query.get_group_member_info(
                group_id=group_id, user_id=HMMT.BOT_ID
            )

        # 使用 CommonUtil 解析群成员列表
        members = CommonUtil.parse_group_member_list(members_response)

        # 过滤已分配的和自己
        bot_id = str(HMMT.BOT_ID)
        filtered_members = [
            member
            for member in members
            if str(member.user_id) not in allocated_wives
            and str(member.user_id) != str(input.user_id)
            and str(member.user_id) != bot_id
            and not (getattr(member, "is_robot", None) or False)
        ]

        if not filtered_members:
            bot_member_info = await self.api.qq.query.get_group_member_info(
                group_id=group_id, user_id=HMMT.BOT_ID
            )
            if isinstance(bot_member_info, dict):  # 如果返回的是字典
                return GroupMember(bot_member_info)  # 转换为 GroupMember 对象
            return bot_member_info  # 如果已经是 GroupMember 对象，直接返回

        selected_wife = random.choice(filtered_members)
        allocated_wives.add(str(selected_wife.user_id))

        return selected_wife

    @staticmethod
    def _display_name(member: GroupMember | None, fallback_id: str) -> str:
        if member is None:
            return fallback_id
        return member.card or member.nickname or str(member.user_id)

    def _load_font(self, size: int):
        for path in ("data/font/sakura.ttf", "simhei.ttf"):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        return ImageFont.load_default()

    @staticmethod
    def _truncate_line(
        draw: ImageDraw.ImageDraw, text: str, font, max_width: int
    ) -> str:
        if not text:
            return text
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= max_width:
            return text
        ellipsis = "…"
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            probe = text[:mid] + ellipsis
            probe_bbox = draw.textbbox((0, 0), probe, font=font)
            if probe_bbox[2] - probe_bbox[0] <= max_width:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo] + ellipsis if lo > 0 else ellipsis

    def _generate_wife_list_image(self, pairs: List[Tuple[str, str, str, str]]) -> str:
        width = 920
        padding = 28
        row_height = 42
        title_height = 56
        height = padding * 2 + title_height + len(pairs) * row_height + 16

        img = PILImage.new("RGB", (width, height), color=(248, 250, 255))
        draw = ImageDraw.Draw(img)

        title_font = self._load_font(28)
        text_font = self._load_font(20)
        meta_font = self._load_font(15)

        y = padding
        title = "今日群老婆列表"
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_width = title_bbox[2] - title_bbox[0]
        draw.text(
            ((width - title_width) // 2, y),
            title,
            font=title_font,
            fill=(255, 120, 160),
        )
        y += title_height - 8
        draw.line(
            [(padding, y), (width - padding, y)],
            fill=(220, 225, 235),
            width=2,
        )
        y += 16

        inner_width = width - padding * 2
        for user_name, user_id, wife_name, wife_id in pairs:
            left = f"{user_name}（{user_id}）"
            arrow = " →→→ "
            right = f"{wife_name}（{wife_id}）"
            line = self._truncate_line(
                draw, f"{left}{arrow}{right}", text_font, inner_width
            )

            draw.text((padding, y), line, font=text_font, fill=(51, 58, 72))
            y += row_height

        footer = f"共 {len(pairs)} 对"
        footer_bbox = draw.textbbox((0, 0), footer, font=meta_font)
        footer_width = footer_bbox[2] - footer_bbox[0]
        draw.text(
            (width - padding - footer_width, y - 6),
            footer,
            font=meta_font,
            fill=(126, 136, 156),
        )

        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format="PNG")
        img_byte_arr.seek(0)
        img_base64 = base64.b64encode(img_byte_arr.getvalue()).decode()
        return f"base64://{img_base64}"

    async def _build_wife_list_pairs(
        self, group_id: str
    ) -> List[Tuple[str, str, str, str]]:
        user_to_wife_map = self.user_to_wife_map_by_group.get(group_id, {})
        if not user_to_wife_map:
            return []

        members_response = await self.api.qq.query.get_group_member_list(
            group_id=group_id
        )
        members = CommonUtil.parse_group_member_list(members_response)
        member_by_id = {str(m.user_id): m for m in members}

        pairs: List[Tuple[str, str, str, str]] = []
        for user_id, wife_id in user_to_wife_map.items():
            user_member = member_by_id.get(user_id)
            wife_member = member_by_id.get(wife_id)
            pairs.append(
                (
                    self._display_name(user_member, user_id),
                    user_id,
                    self._display_name(wife_member, wife_id),
                    wife_id,
                )
            )

        pairs.sort(key=lambda item: int(item[1]))
        return pairs

    @registrar.qq.on_group_message()
    async def handle_message(self, input: GroupMessage):
        """处理消息"""
        if not input.message:
            return

        # 获取消息文本
        message_text = ""
        for element in input.message:
            if hasattr(element, "text"):
                message_text += element.text

        user_id = str(input.sender.user_id)
        group_id = str(input.group_id)

        # 检查日期是否已经跨天，如果是，则执行重置操作
        current_date = date.today()
        if current_date != self.last_reset_date:
            self.reset_all_allocated_wives()
            self.last_reset_date = current_date

        if message_text == "今日老婆":
            random_value = random.random()
            if 0.05 <= random_value <= 0.15:
                return await self.api.qq.post_group_msg(
                    group_id=input.group_id, text="今*老婆"
                )
            if random_value < 0.05:
                return await self.api.qq.post_group_msg(
                    group_id=input.group_id, text="你快醒醒 你没有老婆"
                )

            user_to_wife_map = self.user_to_wife_map_by_group.setdefault(group_id, {})
            allocated_wives = self.allocated_wives_by_group.setdefault(group_id, set())

            # 检查当前用户是否已经有老婆
            if user_id in user_to_wife_map:
                wife_id = user_to_wife_map[user_id]
                wife_info = await self.api.qq.query.get_group_member_info(
                    group_id=group_id, user_id=wife_id
                )

                # 检查响应类型并解析
                if hasattr(wife_info, "user_id"):  # 如果是 GroupMemberInfo 对象
                    # 将 GroupMemberInfo 对象转换为字典
                    wife_data = {
                        "group_id": wife_info.group_id,
                        "user_id": wife_info.user_id,
                        "nickname": wife_info.nickname,
                        "card": wife_info.card,
                        "role": wife_info.role,
                        "title": wife_info.title,
                        "level": wife_info.level,
                        "sex": wife_info.sex,
                        "age": wife_info.age,
                        "area": wife_info.area,
                        "qq_level": wife_info.qq_level,
                        "join_time": wife_info.join_time,
                        "last_sent_time": wife_info.last_sent_time,
                        "title_expire_time": wife_info.title_expire_time,
                        "unfriendly": wife_info.unfriendly,
                        "card_changeable": wife_info.card_changeable,
                        "is_robot": wife_info.is_robot,
                        "shut_up_timestamp": wife_info.shut_up_timestamp,
                    }
                    wife_member = GroupMember(wife_data)

                    avatar_url = await CommonUtil.get_avatar_async(wife_member.user_id)

                    message = MessageArray()
                    message.add_at(user_id)
                    message.add_text("你今天的群友老婆是：")
                    message.add_image(avatar_url)
                    message.add_text(f" {wife_member.nickname}({wife_member.user_id})")
                    return await self.api.qq.post_group_msg(
                        group_id=input.group_id, rtf=message
                    )
                elif isinstance(wife_info, dict) and wife_info.get("status") == "ok":
                    # 兼容旧的字典格式
                    wife_data = wife_info.get("data", {})
                    if wife_data:  # 确保 data 字段存在且不为空
                        wife_member = GroupMember(wife_data)

                        avatar_url = await CommonUtil.get_avatar_async(wife_member.user_id)

                        message = MessageArray()
                        message.add_at(user_id)
                        message.add_text("你今天的群友老婆是：")
                        message.add_image(avatar_url)
                        message.add_text(
                            f" {wife_member.nickname}({wife_member.user_id})"
                        )
                        return await self.api.qq.post_group_msg(
                            group_id=input.group_id, rtf=message
                        )
                    else:
                        _log.error(f"老婆数据为空: wife_data={wife_data}")
                        return await self.api.qq.post_group_msg(
                            group_id=input.group_id, text="老婆数据为空，请稍后再试。"
                        )
                else:
                    _log.error(f"获取老婆信息失败: wife_info={wife_info}")
                    return await self.api.qq.post_group_msg(
                        group_id=input.group_id,
                        text=f"获取老婆信息失败，请稍后再试。错误信息: {wife_info}",
                    )

            new_wife = await self.get_random_wife(input, group_id)

            user_to_wife_map[user_id] = new_wife.user_id
            allocated_wives.add(new_wife.user_id)

            avatar_url = await CommonUtil.get_avatar_async(new_wife.user_id)

            # 发送消息
            message = MessageArray()
            message.add_at(user_id)
            message.add_text(" 你今天的群友老婆是：")
            message.add_image(avatar_url)
            message.add_text(f" {new_wife.nickname}({new_wife.user_id})")
            return await self.api.qq.post_group_msg(
                group_id=input.group_id, rtf=message
            )

        if message_text.strip() == "群老婆列表":
            pairs = await self._build_wife_list_pairs(group_id)
            if not pairs:
                return await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text="今日还没有人抽到老婆哦~",
                )

            img_base64 = self._generate_wife_list_image(pairs)
            message = MessageArray()
            message.add_image(img_base64)
            return await self.api.qq.post_group_msg(
                group_id=input.group_id, rtf=message
            )

        if (
            input.raw_message.startswith("换")
            and "的老婆" in input.raw_message
            and user_id == str(HMMT.HMMT_ID)
        ):
            target_user_id = None
            new_wife_id = None
            at_count = 0

            for element in input.message:
                if isinstance(element, At):
                    if at_count == 0:
                        target_user_id = str(element.user_id)
                    elif at_count == 1:
                        new_wife_id = str(element.user_id)
                    at_count += 1

            if not target_user_id:
                return await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text="请艾特要更换老婆的用户。",
                )

            # 获取目标用户信息
            target_info = await self.api.qq.query.get_group_member_info(
                group_id=group_id, user_id=target_user_id
            )

            # 检查响应类型并解析
            if hasattr(target_info, "user_id"):  # 如果是 GroupMemberInfo 对象
                # 将 GroupMemberInfo 对象转换为字典
                target_data = {
                    "group_id": target_info.group_id,
                    "user_id": target_info.user_id,
                    "nickname": target_info.nickname,
                    "card": target_info.card,
                    "role": target_info.role,
                    "title": target_info.title,
                    "level": target_info.level,
                    "sex": target_info.sex,
                    "age": target_info.age,
                    "area": target_info.area,
                    "qq_level": target_info.qq_level,
                    "join_time": target_info.join_time,
                    "last_sent_time": target_info.last_sent_time,
                    "title_expire_time": target_info.title_expire_time,
                    "unfriendly": target_info.unfriendly,
                    "card_changeable": target_info.card_changeable,
                    "is_robot": target_info.is_robot,
                    "shut_up_timestamp": target_info.shut_up_timestamp,
                }
                target_member = GroupMember(target_data)
            elif isinstance(target_info, dict) and target_info.get("status") == "ok":
                target_data = target_info.get("data", {})
                if not target_data:
                    return await self.api.qq.post_group_msg(
                        group_id=input.group_id,
                        text="获取目标用户信息失败，请稍后再试。",
                    )
                target_member = GroupMember(target_data)
            else:
                return await self.api.qq.post_group_msg(
                    group_id=input.group_id, text="获取目标用户信息失败，请稍后再试。"
                )

            user_to_wife_map = self.user_to_wife_map_by_group.setdefault(group_id, {})
            allocated_wives = self.allocated_wives_by_group.setdefault(group_id, set())

            # 如果指定了新老婆
            if new_wife_id:
                # 获取新老婆信息
                new_wife_info = await self.api.qq.query.get_group_member_info(
                    group_id=group_id, user_id=new_wife_id
                )

                # 检查响应类型并解析
                if hasattr(new_wife_info, "user_id"):  # 如果是 GroupMemberInfo 对象
                    # 将 GroupMemberInfo 对象转换为字典
                    new_wife_data = {
                        "group_id": new_wife_info.group_id,
                        "user_id": new_wife_info.user_id,
                        "nickname": new_wife_info.nickname,
                        "card": new_wife_info.card,
                        "role": new_wife_info.role,
                        "title": new_wife_info.title,
                        "level": new_wife_info.level,
                        "sex": new_wife_info.sex,
                        "age": new_wife_info.age,
                        "area": new_wife_info.area,
                        "qq_level": new_wife_info.qq_level,
                        "join_time": new_wife_info.join_time,
                        "last_sent_time": new_wife_info.last_sent_time,
                        "title_expire_time": new_wife_info.title_expire_time,
                        "unfriendly": new_wife_info.unfriendly,
                        "card_changeable": new_wife_info.card_changeable,
                        "is_robot": new_wife_info.is_robot,
                        "shut_up_timestamp": new_wife_info.shut_up_timestamp,
                    }
                    new_wife_member = GroupMember(new_wife_data)
                elif (
                    isinstance(new_wife_info, dict)
                    and new_wife_info.get("status") == "ok"
                ):
                    new_wife_data = new_wife_info.get("data", {})
                    if not new_wife_data:
                        return await self.api.qq.post_group_msg(
                            group_id=input.group_id,
                            text="获取新老婆信息失败，请稍后再试。",
                        )
                    new_wife_member = GroupMember(new_wife_data)
                else:
                    return await self.api.qq.post_group_msg(
                        group_id=input.group_id, text="获取新老婆信息失败，请稍后再试。"
                    )

                # 如果目标用户已经有老婆，从已分配集合中移除
                if target_user_id in user_to_wife_map:
                    old_wife_id = user_to_wife_map[target_user_id]
                    allocated_wives.discard(old_wife_id)

                # 如果新老婆已经被分配给其他人，从原分配中移除
                for uid, wife_id in user_to_wife_map.items():
                    if wife_id == new_wife_id:
                        allocated_wives.discard(wife_id)
                        user_to_wife_map.pop(uid)
                        break

                # 设置新的老婆
                user_to_wife_map[target_user_id] = new_wife_id
                allocated_wives.add(new_wife_id)

                message = MessageArray()
                message.add_at(user_id)
                message.add_text(
                    f" 成功将 {target_member.nickname} 的老婆更换为 {new_wife_member.nickname}"
                )
                return await self.api.qq.post_group_msg(
                    group_id=input.group_id, rtf=message
                )

            # 如果没有指定新老婆，使用随机分配
            if target_user_id not in user_to_wife_map:
                return await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text=f"{target_member.nickname} 没有老婆，无法更换。",
                )

            target_wife_id = user_to_wife_map[target_user_id]
            allocated_wives.remove(target_wife_id)
            user_to_wife_map.pop(target_user_id)
            new_wife = await self.get_random_wife(input, group_id)
            user_to_wife_map[target_user_id] = new_wife.user_id

            message1 = MessageArray()
            message1.add_at(input.sender.user_id)
            message1.add_text(f" 成功更换了 {target_member.nickname} 的老婆。")
            await self.api.qq.post_group_msg(group_id=input.group_id, rtf=message1)

            message2 = MessageArray()
            message2.add_at(target_user_id)
            message2.add_text(f" 你的老婆被 {input.sender.nickname} 更换了。")
            return await self.api.qq.post_group_msg(
                group_id=input.group_id, rtf=message2
            )

        if message_text.strip() == "换一个老婆" and user_id == str(HMMT.HMMT_ID):
            user_to_wife_map = self.user_to_wife_map_by_group.setdefault(group_id, {})
            allocated_wives = self.allocated_wives_by_group.setdefault(group_id, set())

            # 检查是否有艾特消息
            target_user_id = None
            for element in input.message:
                if isinstance(element, At):
                    target_user_id = str(element.user_id)
                    break

            if target_user_id:
                # 如果被艾特的人是发送者自己，返回错误信息
                if target_user_id == user_id:
                    return await self.api.qq.post_group_msg(
                        group_id=input.group_id, text="不能选择自己作为老婆！"
                    )

                # 获取被艾特用户的信息
                target_info = await self.api.qq.query.get_group_member_info(
                    group_id=group_id, user_id=target_user_id
                )

                # 检查响应类型并解析
                if hasattr(target_info, "user_id"):  # 如果是 GroupMemberInfo 对象
                    # 将 GroupMemberInfo 对象转换为字典
                    target_data = {
                        "group_id": target_info.group_id,
                        "user_id": target_info.user_id,
                        "nickname": target_info.nickname,
                        "card": target_info.card,
                        "role": target_info.role,
                        "title": target_info.title,
                        "level": target_info.level,
                        "sex": target_info.sex,
                        "age": target_info.age,
                        "area": target_info.area,
                        "qq_level": target_info.qq_level,
                        "join_time": target_info.join_time,
                        "last_sent_time": target_info.last_sent_time,
                        "title_expire_time": target_info.title_expire_time,
                        "unfriendly": target_info.unfriendly,
                        "card_changeable": target_info.card_changeable,
                        "is_robot": target_info.is_robot,
                        "shut_up_timestamp": target_info.shut_up_timestamp,
                    }
                    target_member = GroupMember(target_data)
                elif (
                    isinstance(target_info, dict) and target_info.get("status") == "ok"
                ):
                    target_data = target_info.get("data", {})
                    if target_data:
                        target_member = GroupMember(target_data)
                    else:
                        return await self.api.qq.post_group_msg(
                            group_id=input.group_id,
                            text="获取目标用户信息失败，请稍后再试。",
                        )
                else:
                    return await self.api.qq.post_group_msg(
                        group_id=input.group_id,
                        text="获取目标用户信息失败，请稍后再试。",
                    )

                # 如果目标用户已经在其他用户的分配中，从原分配中移除
                for uid, wife_id in user_to_wife_map.items():
                    if wife_id == target_user_id:
                        allocated_wives.discard(wife_id)
                        user_to_wife_map.pop(uid)
                        break

                # 如果当前用户已有老婆，从已分配集合中移除
                if user_id in user_to_wife_map:
                    allocated_wives.discard(user_to_wife_map[user_id])

                # 设置新的老婆
                user_to_wife_map[user_id] = target_user_id
                allocated_wives.add(target_user_id)

                message = MessageArray()
                message.add_at(user_id)
                message.add_text(
                    f" 成功更换了老婆，你的新老婆是：{target_member.nickname}"
                )
                return await self.api.qq.post_group_msg(
                    group_id=input.group_id, rtf=message
                )

            # 原有的随机抽取逻辑
            if user_id not in user_to_wife_map:
                return await self.api.qq.post_group_msg(
                    group_id=input.group_id, text="你还没有老婆，无法换一个老婆。"
                )

            current_wife_id = user_to_wife_map[user_id]

            if current_wife_id in allocated_wives:
                allocated_wives.remove(current_wife_id)

            user_to_wife_map.pop(user_id)

            new_wife = await self.get_random_wife(input, group_id)
            if new_wife is None:
                return await self.api.qq.post_group_msg(
                    group_id=input.group_id, text="无法获取新的老婆，请稍后再试。"
                )

            user_to_wife_map[user_id] = new_wife.user_id
            allocated_wives.add(new_wife.user_id)

            message = MessageArray()
            message.add_at(user_id)
            message.add_text(f" 成功更换了老婆，你的新老婆是：{new_wife.nickname}")
            return await self.api.qq.post_group_msg(
                group_id=input.group_id, rtf=message
            )
