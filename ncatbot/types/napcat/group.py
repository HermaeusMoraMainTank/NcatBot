"""群组相关 API 响应类型"""

from __future__ import annotations

from typing import List, Optional

from ._base import NapCatModel


class GroupInfo(NapCatModel):
    """群基本信息

    对应: ``get_group_info``, ``get_group_list`` 中的每一项
    """

    group_id: str = ""
    group_name: Optional[str] = None
    member_count: Optional[int] = None
    max_member_count: Optional[int] = None


class GroupMemberInfo(NapCatModel):
    """群成员信息

    对应: ``get_group_member_info``, ``get_group_member_list`` 中的每一项
    """

    group_id: str = ""
    user_id: str = ""
    nickname: Optional[str] = None
    card: Optional[str] = None
    sex: Optional[str] = None
    age: Optional[int] = None
    area: Optional[str] = None
    join_time: Optional[int] = None
    last_sent_time: Optional[int] = None
    level: Optional[str] = None
    role: Optional[str] = None
    title: Optional[str] = None
    title_expire_time: Optional[int] = None
    shut_up_timestamp: Optional[int] = None


class GroupNoticeMessage(NapCatModel):
    """群公告消息内容（内嵌结构）"""

    text: Optional[str] = None
    image: Optional[list] = None
    images: Optional[list] = None


class GroupNoticeSettings(NapCatModel):
    """群公告设置（内嵌结构）"""

    is_show_edit_card: Optional[int] = None
    remind_ts: Optional[int] = None
    tip_window_type: Optional[int] = None
    confirm_required: Optional[int] = None


class GroupNotice(NapCatModel):
    """群公告

    对应: ``get_group_notice`` 中的每一项
    """

    notice_id: str = ""
    sender_id: str = ""
    publish_time: Optional[int] = None
    message: Optional[GroupNoticeMessage] = None
    settings: Optional[GroupNoticeSettings] = None
    read_num: Optional[int] = None


class EssenceMessage(NapCatModel):
    """群精华消息

    对应: ``get_essence_msg_list`` 中的每一项
    """

    sender_id: str = ""
    sender_nick: Optional[str] = None
    operator_id: str = ""
    operator_nick: Optional[str] = None
    operator_time: Optional[int] = None
    message_id: str = ""
    msg_seq: Optional[int] = None
    msg_random: Optional[int] = None
    content: Optional[List[dict]] = None


class HonorUser(NapCatModel):
    """群荣誉用户条目

    对应: ``get_group_honor_info`` 中各列表的每一项
    """

    user_id: str = ""
    nickname: Optional[str] = None
    avatar: Optional[str] = None
    description: Optional[str] = None


class GroupHonorInfo(NapCatModel):
    """群荣誉信息

    对应: ``get_group_honor_info``
    """

    group_id: str = ""
    current_talkative: Optional[HonorUser] = None
    talkative_list: Optional[List[HonorUser]] = None
    performer_list: Optional[List[HonorUser]] = None
    legend_list: Optional[List[HonorUser]] = None
    strong_newbie_list: Optional[List[HonorUser]] = None
    emotion_list: Optional[List[HonorUser]] = None


class GroupAtAllRemain(NapCatModel):
    """群 @全体成员 剩余次数

    对应: ``get_group_at_all_remain``
    """

    can_at_all: bool = False
    remain_at_all_count_for_group: int = 0
    remain_at_all_count_for_uin: int = 0


class GroupShutInfo(NapCatModel):
    """群禁言信息

    对应: ``get_group_shut_list`` 中的每一项。
    NapCat 返回原生格式，使用 ``uin`` 和 ``shutUpTime``。
    """

    uid: Optional[str] = None
    uin: str = ""
    nick: Optional[str] = None
    shutUpTime: Optional[int] = None
    role: Optional[int] = None
    cardName: Optional[str] = None
    memberLevel: Optional[int] = None


class GroupSystemRequest(NapCatModel):
    """群系统消息中的请求条目

    对应: ``get_group_system_msg`` 中 ``invited_requests`` / ``join_requests`` 的每一项
    """

    request_id: str = ""
    invitor_uin: str = ""
    invitor_nick: Optional[str] = None
    group_id: str = ""
    group_name: Optional[str] = None
    checked: Optional[bool] = None
    actor: str = ""


class GroupSystemMsg(NapCatModel):
    """群系统消息

    对应: ``get_group_system_msg``
    """

    invited_requests: Optional[List[GroupSystemRequest]] = None
    join_requests: Optional[List[GroupSystemRequest]] = None


class GroupOwnerInfo(NapCatModel):
    """群主信息（群扩展信息内嵌）"""

    memberUin: Optional[str] = None
    memberUid: Optional[str] = None
    memberQid: Optional[str] = None


class GroupExtFlameData(NapCatModel):
    """群火焰数据（群扩展信息内嵌）"""

    switchState: Optional[int] = None
    state: Optional[int] = None
    dayNums: Optional[list] = None
    version: Optional[int] = None
    updateTime: Optional[str] = None
    isDisplayDayNum: Optional[bool] = None


class GroupExtInfo(NapCatModel):
    """群扩展详细信息（群扩展信息内嵌）"""

    groupInfoExtSeq: Optional[int] = None
    reserve: Optional[int] = None
    luckyWordId: Optional[str] = None
    lightCharNum: Optional[int] = None
    luckyWord: Optional[str] = None
    starId: Optional[int] = None
    essentialMsgSwitch: Optional[int] = None
    todoSeq: Optional[int] = None
    blacklistExpireTime: Optional[int] = None
    isLimitGroupRtc: Optional[int] = None
    companyId: Optional[int] = None
    hasGroupCustomPortrait: Optional[int] = None
    bindGuildId: Optional[str] = None
    groupOwnerId: Optional[GroupOwnerInfo] = None
    essentialMsgPrivilege: Optional[int] = None
    msgEventSeq: Optional[str] = None
    inviteRobotSwitch: Optional[int] = None
    gangUpId: Optional[str] = None
    qqMusicMedalSwitch: Optional[int] = None
    showPlayTogetherSwitch: Optional[int] = None
    groupFlagPro1: Optional[str] = None
    groupBindGuildSwitch: Optional[int] = None
    groupAioBindGuildId: Optional[str] = None
    fullGroupExpansionSwitch: Optional[int] = None
    fullGroupExpansionSeq: Optional[str] = None
    inviteRobotMemberSwitch: Optional[int] = None
    inviteRobotMemberExamine: Optional[int] = None


class GroupInfoEx(NapCatModel):
    """群扩展信息

    对应: ``get_group_info_ex`` — NapCat 扩展 API, 字段由服务端决定
    """

    groupCode: Optional[str] = None
    resultCode: Optional[int] = None
    extInfo: Optional[GroupExtInfo] = None
