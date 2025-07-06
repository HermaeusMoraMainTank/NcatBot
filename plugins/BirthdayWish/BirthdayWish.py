import json
import random
from datetime import date, datetime
from typing import Dict, List, Optional
import os

from common.constants.HMMT import HMMT
from common.entity.GroupMember import GroupMember
from common.utils.CommonUtil import CommonUtil
from ncatbot.core.element import At, Image as ImageElement, MessageChain, Text
from ncatbot.core.message import GroupMessage
from ncatbot.plugin import CompatibleEnrollment, BasePlugin

from ncatbot.utils.logger import get_log

bot = CompatibleEnrollment

_log = get_log()

# 默认生日祝福语列表
DEFAULT_BIRTHDAY_BLESSINGS = [
    "生日快乐！愿你所有的梦想都能实现！",
    "祝你生日快乐！愿你的每一天都充满阳光和快乐！",
    "生日快乐！愿你的生活像彩虹一样绚丽多彩！",
    "祝你生日快乐！愿你的未来充满无限可能！",
    "生日快乐！愿你的笑容永远灿烂如花！",
    "祝你生日快乐！愿你的生活甜甜蜜蜜，幸福美满！",
    "生日快乐！愿你的每一天都值得庆祝！",
    "祝你生日快乐！愿你的生活充满惊喜和美好！",
    "生日快乐！愿你的梦想都能成真！",
    "祝你生日快乐！愿你的生活精彩纷呈！",
]


class BirthdayWish(BasePlugin):
    name = "BirthdayWish"  # 插件名称
    version = "1.0"  # 插件版本

    def __init__(self):
        super().__init__()
        self.config_file = "data/BirthdayWish/BirthdayWish.json"
        self.birthday_data = {}  # 存储生日数据
        self.settings = {}  # 存储设置
        self.last_check_date = date.today()  # 记录上次检查日期
        self.load_config()

    def load_config(self):
        """加载配置文件"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    self.birthday_data = config.get("birthday_data", {})
                    self.settings = config.get("settings", {})
                    _log.info("生日祝福插件配置加载成功")
            else:
                # 如果配置文件不存在，创建默认配置
                self.settings = {
                    "auto_check": True,
                    "check_time": "09:00",
                    "blessing_messages": DEFAULT_BIRTHDAY_BLESSINGS,
                }
                self.save_config()
                _log.info("创建默认生日祝福插件配置")
        except Exception as e:
            _log.error(f"加载配置文件失败: {e}")
            self.settings = {
                "auto_check": True,
                "check_time": "09:00",
                "blessing_messages": DEFAULT_BIRTHDAY_BLESSINGS,
            }

    def save_config(self):
        """保存配置文件"""
        try:
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            config = {"birthday_data": self.birthday_data, "settings": self.settings}
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            _log.info("生日祝福插件配置保存成功")
        except Exception as e:
            _log.error(f"保存配置文件失败: {e}")

    async def get_all_group_members(self, group_id: int) -> List[GroupMember]:
        """获取群内所有成员信息"""
        try:
            members_response = await self.api.get_group_member_list(group_id=group_id)

            if (
                members_response.get("status") != "ok"
                or members_response.get("retcode") != 0
            ):
                _log.error(f"获取群成员列表失败: {members_response}")
                return []

            members_data = members_response.get("data", [])
            members = [GroupMember(member) for member in members_data]

            # 输出到控制台，方便你查看群友信息
            _log.info(f"群 {group_id} 共有 {len(members)} 个成员")
            for member in members[:5]:  # 只显示前5个成员信息作为示例
                _log.info(
                    f"成员: {member.nickname}({member.user_id}) - 等级: {member.level}"
                )

            return members

        except Exception as e:
            _log.error(f"获取群成员信息时发生错误: {e}")
            return []

    async def get_member_info(
        self, group_id: int, user_id: int
    ) -> Optional[GroupMember]:
        """获取指定成员信息"""
        try:
            member_info = await self.api.get_group_member_info(
                group_id=group_id, user_id=user_id, no_cache=True
            )

            if isinstance(member_info, dict) and member_info.get("status") == "ok":
                member_data = member_info.get("data", {})
                if member_data:
                    member = GroupMember(member_data)
                    _log.info(f"获取成员信息成功: {member.nickname}({member.user_id})")
                    return member

            _log.error(f"获取成员信息失败: {member_info}")
            return None

        except Exception as e:
            _log.error(f"获取成员信息时发生错误: {e}")
            return None

    async def check_birthday_members(self, group_id: int):
        """检查今天过生日的成员"""
        current_date = date.today()

        # 如果日期没有变化，跳过检查
        if current_date == self.last_check_date:
            return

        self.last_check_date = current_date

        # 获取所有群成员
        members = await self.get_all_group_members(group_id)
        if not members:
            return

        # 这里可以添加生日检查逻辑
        # 目前只是输出成员信息到控制台
        _log.info(f"=== 群 {group_id} 成员信息检查 ===")
        _log.info(f"当前日期: {current_date}")
        _log.info(f"群成员总数: {len(members)}")

        # 输出一些成员的基本信息
        for i, member in enumerate(members[:10]):  # 只显示前10个
            _log.info(
                f"成员{i + 1}: {member.nickname}({member.user_id}) - 等级:{member.level} - 角色:{member.role}"
            )

    async def set_birthday(self, user_id: int, birthday: str, group_id: int):
        """设置用户生日"""
        try:
            # 验证生日格式 (MM-DD)
            if len(birthday) != 5 or birthday[2] != "-":
                return False, "生日格式错误，请使用 MM-DD 格式，例如：12-25"

            month, day = birthday.split("-")
            if not (1 <= int(month) <= 12 and 1 <= int(day) <= 31):
                return False, "生日日期无效"

            # 保存生日数据
            if str(group_id) not in self.birthday_data:
                self.birthday_data[str(group_id)] = {}

            self.birthday_data[str(group_id)][str(user_id)] = birthday
            self.save_config()

            return True, f"生日设置成功：{birthday}"

        except Exception as e:
            _log.error(f"设置生日失败: {e}")
            return False, "设置生日失败，请稍后再试"

    async def get_birthday(self, user_id: int, group_id: int):
        """获取用户生日"""
        try:
            group_birthdays = self.birthday_data.get(str(group_id), {})
            birthday = group_birthdays.get(str(user_id))
            return birthday
        except Exception as e:
            _log.error(f"获取生日失败: {e}")
            return None

    @bot.group_event()
    async def handle_message(self, input: GroupMessage):
        """处理消息"""
        if not input.message:
            return

        message = input.message[0].get("data", {}).get("text", "")
        user_id = input.sender.user_id
        group_id = input.group_id

        # 检查生日成员
        await self.check_birthday_members(group_id)

        # 测试命令：获取群成员信息
        if message == "生日检查":
            _log.info(f"用户 {user_id} 在群 {group_id} 触发生日检查")

            # 获取所有群成员
            members = await self.get_all_group_members(group_id)

            if members:
                return await self.api.post_group_msg(
                    group_id=group_id,
                    text=f"群成员信息已输出到控制台，共 {len(members)} 个成员",
                )
            else:
                return await self.api.post_group_msg(
                    group_id=group_id, text="获取群成员信息失败，请稍后再试"
                )

        # 测试命令：获取指定成员信息
        if message.startswith("成员信息"):
            # 检查是否有艾特消息
            target_user_id = None
            for msg in input.message:
                if msg.get("type") == "at":
                    target_user_id = int(msg.get("data").get("qq"))
                    break

            if not target_user_id:
                return await self.api.post_group_msg(
                    group_id=group_id, text="请艾特要查询的成员"
                )

            # 获取指定成员信息
            member = await self.get_member_info(group_id, target_user_id)

            if member:
                avatar_url = CommonUtil.get_avatar(member.user_id)

                return await self.api.post_group_msg(
                    group_id=group_id,
                    rtf=MessageChain(
                        [
                            At(target_user_id),
                            Text(f" 的成员信息："),
                            ImageElement(avatar_url),
                            Text(f"\n昵称: {member.nickname}"),
                            Text(f"\nQQ: {member.user_id}"),
                            Text(f"\n等级: {member.level}"),
                            Text(f"\n角色: {member.role}"),
                            Text(f"\n群名片: {member.card or '未设置'}"),
                        ]
                    ),
                )
            else:
                return await self.api.post_group_msg(
                    group_id=group_id, text="获取成员信息失败，请稍后再试"
                )

        # 设置生日命令
        if message.startswith("设置生日"):
            parts = message.split()
            if len(parts) != 2:
                return await self.api.post_group_msg(
                    group_id=group_id, text="格式错误，请使用：设置生日 MM-DD"
                )

            birthday = parts[1]
            success, msg = await self.set_birthday(user_id, birthday, group_id)

            return await self.api.post_group_msg(group_id=group_id, text=msg)

        # 查询生日命令
        if message == "我的生日":
            birthday = await self.get_birthday(user_id, group_id)
            if birthday:
                return await self.api.post_group_msg(
                    group_id=group_id,
                    rtf=MessageChain([At(user_id), Text(f" 你的生日是：{birthday}")]),
                )
            else:
                return await self.api.post_group_msg(
                    group_id=group_id,
                    rtf=MessageChain(
                        [At(user_id), Text(" 你还没有设置生日，请使用：设置生日 MM-DD")]
                    ),
                )

        # 生日祝福命令（示例）
        if message == "生日快乐":
            # 随机选择一个祝福语
            blessing_messages = self.settings.get(
                "blessing_messages", DEFAULT_BIRTHDAY_BLESSINGS
            )
            blessing = random.choice(blessing_messages)

            return await self.api.post_group_msg(
                group_id=group_id, rtf=MessageChain([At(user_id), Text(f" {blessing}")])
            )
