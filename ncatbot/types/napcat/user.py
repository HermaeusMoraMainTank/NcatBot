"""用户相关 API 响应类型"""

from __future__ import annotations

from typing import Optional

from ._base import NapCatModel


class LoginInfo(NapCatModel):
    """登录账号信息

    对应: ``get_login_info``
    """

    user_id: str = ""
    nickname: Optional[str] = None


class StrangerInfo(NapCatModel):
    """陌生人信息

    对应: ``get_stranger_info``
    """

    user_id: str = ""
    nickname: Optional[str] = None
    sex: Optional[str] = None
    age: Optional[int] = None


class FriendInfo(NapCatModel):
    """好友信息

    对应: ``get_friend_list`` 中的每一项
    """

    user_id: str = ""
    nickname: Optional[str] = None
    remark: Optional[str] = None
