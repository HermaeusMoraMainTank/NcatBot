"""
特殊群组处理模块
用于处理群组585479130的特殊规则：
1. 别人默认不会抽到3860435136和273421673
2. 这两个人触发今日老婆时默认为对方
"""

from typing import Optional, List
from common.entity.GroupMember import GroupMember


class SpecialGroupHandler:
    """特殊群组处理器"""

    # 特殊群组ID
    SPECIAL_GROUP_ID = "585479130"

    # 特殊用户ID列表
    SPECIAL_USER_IDS = ["3860435136", "273421673"]

    @classmethod
    def is_special_group(cls, group_id) -> bool:
        """判断是否为特殊群组"""
        return str(group_id) == cls.SPECIAL_GROUP_ID

    @classmethod
    def is_special_user(cls, user_id) -> bool:
        """判断是否为特殊用户"""
        return str(user_id) in cls.SPECIAL_USER_IDS

    @classmethod
    def get_special_partner(cls, user_id) -> Optional[str]:
        """获取特殊用户的默认伴侣"""
        if not cls.is_special_user(user_id):
            return None

        # 3860435136 的伴侣是 273421673
        if str(user_id) == "3860435136":
            return "273421673"
        # 273421673 的伴侣是 3860435136
        elif str(user_id) == "273421673":
            return "3860435136"

        return None

    @classmethod
    def filter_special_members(
        cls, members: List[GroupMember], current_user_id: str
    ) -> List[GroupMember]:
        """过滤特殊群组的成员列表，排除特殊用户（除非是当前用户自己）"""
        if not cls.is_special_group(members[0].group_id if members else "0"):
            return members

        filtered_members = []
        for member in members:
            # 如果是特殊用户且不是当前用户，则跳过
            if (
                cls.is_special_user(str(member.user_id))
                and str(member.user_id) != current_user_id
            ):
                continue
            filtered_members.append(member)

        return filtered_members

    @classmethod
    def should_use_special_logic(cls, group_id: str, user_id: str) -> bool:
        """判断是否应该使用特殊逻辑"""
        return cls.is_special_group(group_id) and cls.is_special_user(user_id)
