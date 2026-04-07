"""消息相关 API 响应类型"""

from __future__ import annotations

from typing import List, Optional

from ._base import NapCatModel


class SendMessageResult(NapCatModel):
    """发送消息的返回结果

    对应: ``send_group_msg``, ``send_private_msg``,
    ``send_forward_msg``, 及所有 sugar 发送方法。
    """

    message_id: str = ""


class MessageSender(NapCatModel):
    """消息发送者信息

    群消息包含 ``role``，私聊消息无此字段。
    """

    user_id: str = ""
    nickname: Optional[str] = None
    card: Optional[str] = None
    role: Optional[str] = None


class MessageData(NapCatModel):
    """单条消息详情

    对应: ``get_msg``
    """

    message_id: str = ""
    real_id: Optional[int] = None
    time: Optional[int] = None
    message_type: Optional[str] = None
    sender: Optional[MessageSender] = None
    message: Optional[List[dict]] = None
    raw_message: Optional[str] = None


class MessageHistory(NapCatModel):
    """消息历史记录

    对应: ``get_group_msg_history``, ``get_friend_msg_history``
    """

    messages: List[MessageData] = []


class ForwardMessageData(NapCatModel):
    """转发消息内容

    对应: ``get_forward_msg``
    """

    messages: List[MessageData] = []
