"""
飞书事件实体测试 — LarkMessageEvent / LarkGroupMessageEvent / LarkPrivateMessageEvent
                    LarkMessageReadEvent / LarkMessageRecalledEvent

规范:
  LKE-01: LarkMessageEvent 提供 api / user_id / sender / message_id / content / chat_id
  LKE-02: LarkGroupMessageEvent 继承基类并提供 group_id
  LKE-03: LarkPrivateMessageEvent 的 post_message 使用 open_id 发送
  LKE-04: LarkMessageEvent.reply() 调用 reply_text
  LKE-05: LarkMessageEvent.reply(MessageArray) 调用 reply_msg_array
  LKE-06: LarkMessageReadEvent 提供已读通知属性
  LKE-07: LarkMessageRecalledEvent 提供撤回通知属性
  LKE-08: 工厂函数 create_entity 正确映射飞书数据模型
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ncatbot.event.common.base import BaseEvent
from ncatbot.event.common.factory import create_entity
from ncatbot.event.common.mixins import Replyable, HasSender
from ncatbot.event.lark import (
    LarkGroupMessageEvent,
    LarkPrivateMessageEvent,
    LarkMessageReadEvent,
    LarkMessageRecalledEvent,
)
from ncatbot.event.lark.message import LarkMessageEvent
from ncatbot.types import MessageArray
from ncatbot.types.lark import (
    LarkGroupMessageEventData,
    LarkPrivateMessageEventData,
    LarkMessageReadEventData,
    LarkMessageRecalledEventData,
    LarkSender,
)


# ---- Helpers ----


def _mock_lark_api() -> MagicMock:
    """模拟 LarkBotAPI"""
    api = MagicMock()
    api.platform = "lark"
    api.send_text = AsyncMock(return_value={"message_id": "om_reply001"})
    api.reply_text = AsyncMock(return_value={"message_id": "om_reply002"})
    api.send_msg_array = AsyncMock(return_value={"message_id": "om_reply003"})
    api.reply_msg_array = AsyncMock(return_value={"message_id": "om_reply004"})
    api.send_post = AsyncMock(return_value={"message_id": "om_reply005"})
    return api


def _make_sender() -> LarkSender:
    return LarkSender(
        user_id="ou_user001",
        nickname="TestUser",
        open_id="ou_user001",
        union_id="on_union001",
        tenant_key="tk_test",
    )


_BASE = {"time": 0, "self_id": "lark_bot"}


# ---- LKE-01: LarkMessageEvent 公共属性 ----


class TestLarkMessageEvent:
    def test_lke01_base_event_provides_common_properties(self):
        """LKE-01: LarkMessageEvent 提供 api / user_id / sender / message_id / content / chat_id"""
        data = LarkGroupMessageEventData(
            **_BASE,
            message_id="om_msg001",
            chat_id="oc_chat001",
            chat_type="group",
            content="hello",
            sender=_make_sender(),
            user_id="ou_user001",
            group_id="oc_chat001",
        )
        api = _mock_lark_api()
        event = LarkGroupMessageEvent(data, api)

        assert event.api is api
        assert event.user_id == "ou_user001"
        assert event.sender.open_id == "ou_user001"
        assert event.message_id == "om_msg001"
        assert event.content == "hello"
        assert event.chat_id == "oc_chat001"
        assert isinstance(event, BaseEvent)
        assert isinstance(event, Replyable)
        assert isinstance(event, HasSender)


# ---- LKE-02: LarkGroupMessageEvent.group_id ----


class TestLarkGroupMessageEvent:
    def test_lke02_group_message_has_group_id(self):
        """LKE-02: LarkGroupMessageEvent 继承基类并提供 group_id"""
        data = LarkGroupMessageEventData(
            **_BASE,
            message_id="om_msg002",
            chat_id="oc_group123",
            chat_type="group",
            content="群消息",
            sender=_make_sender(),
            user_id="ou_user001",
            group_id="oc_group123",
        )
        event = LarkGroupMessageEvent(data, _mock_lark_api())
        assert event.group_id == "oc_group123"
        assert isinstance(event, LarkMessageEvent)


# ---- LKE-03: LarkPrivateMessageEvent.post_message ----


class TestLarkPrivateMessageEvent:
    async def test_lke03_private_post_message_uses_open_id(self):
        """LKE-03: LarkPrivateMessageEvent 的 post_message 使用 open_id 发送"""
        data = LarkPrivateMessageEventData(
            **_BASE,
            message_id="om_msg003",
            chat_id="oc_private001",
            chat_type="p2p",
            content="私聊",
            sender=_make_sender(),
            user_id="ou_user001",
        )
        api = _mock_lark_api()
        event = LarkPrivateMessageEvent(data, api)

        await event.post_message("hello private")
        api.send_text.assert_awaited_once_with(
            receive_id="ou_user001",
            text="hello private",
            receive_id_type="open_id",
        )


# ---- LKE-04: reply(str) ----


class TestReply:
    async def test_lke04_reply_text_calls_reply_text(self):
        """LKE-04: LarkMessageEvent.reply() 调用 reply_text"""
        data = LarkGroupMessageEventData(
            **_BASE,
            message_id="om_msg004",
            chat_id="oc_chat001",
            chat_type="group",
            content="原消息",
            sender=_make_sender(),
            user_id="ou_user001",
            group_id="oc_chat001",
        )
        api = _mock_lark_api()
        event = LarkGroupMessageEvent(data, api)

        await event.reply("回复内容")
        api.reply_text.assert_awaited_once_with(
            message_id="om_msg004",
            text="回复内容",
        )

    async def test_lke05_reply_msg_array(self):
        """LKE-05: LarkMessageEvent.reply(MessageArray) 调用 reply_msg_array"""
        data = LarkGroupMessageEventData(
            **_BASE,
            message_id="om_msg005",
            chat_id="oc_chat001",
            chat_type="group",
            content="",
            sender=_make_sender(),
            user_id="ou_user001",
            group_id="oc_chat001",
        )
        api = _mock_lark_api()
        event = LarkGroupMessageEvent(data, api)

        msg = MessageArray().add_text("富文本回复")
        await event.reply(msg, title="标题")
        api.reply_msg_array.assert_awaited_once_with(
            message_id="om_msg005",
            msg=msg,
            title="标题",
        )


# ---- LKE-06: LarkMessageReadEvent ----


class TestLarkMessageReadEvent:
    def test_lke06_message_read_properties(self):
        """LKE-06: LarkMessageReadEvent 提供已读通知属性"""
        data = LarkMessageReadEventData(
            **_BASE,
            message_id_list=["om_msg001", "om_msg002"],
            reader_open_id="ou_reader1",
            reader_union_id="on_reader1",
            reader_user_id="uid_reader1",
            read_time="1700000000",
            tenant_key="tk_read",
        )
        api = _mock_lark_api()
        event = LarkMessageReadEvent(data, api)

        assert event.api is api
        assert event.message_id_list == ["om_msg001", "om_msg002"]
        assert event.reader_open_id == "ou_reader1"
        assert event.reader_union_id == "on_reader1"
        assert event.read_time == "1700000000"
        assert event.tenant_key == "tk_read"
        assert isinstance(event, BaseEvent)


# ---- LKE-07: LarkMessageRecalledEvent ----


class TestLarkMessageRecalledEvent:
    def test_lke07_message_recalled_properties(self):
        """LKE-07: LarkMessageRecalledEvent 提供撤回通知属性"""
        data = LarkMessageRecalledEventData(
            **_BASE,
            message_id="om_msg001",
            chat_id="oc_chat001",
            recall_time="1700000000",
            recall_type="user",
        )
        api = _mock_lark_api()
        event = LarkMessageRecalledEvent(data, api)

        assert event.api is api
        assert event.message_id == "om_msg001"
        assert event.chat_id == "oc_chat001"
        assert event.recall_time == "1700000000"
        assert event.recall_type == "user"
        assert isinstance(event, BaseEvent)


# ---- LKE-08: 工厂函数 ----


_FACTORY_PAIRS = [
    (
        LarkGroupMessageEventData,
        LarkGroupMessageEvent,
        {
            "message_id": "om_msg001",
            "chat_id": "oc_chat001",
            "chat_type": "group",
            "content": "test",
            "sender": _make_sender(),
            "user_id": "ou_user001",
            "group_id": "oc_chat001",
        },
    ),
    (
        LarkPrivateMessageEventData,
        LarkPrivateMessageEvent,
        {
            "message_id": "om_msg002",
            "chat_id": "oc_p2p001",
            "chat_type": "p2p",
            "content": "test",
            "sender": _make_sender(),
            "user_id": "ou_user001",
        },
    ),
    (
        LarkMessageReadEventData,
        LarkMessageReadEvent,
        {
            "message_id_list": ["om_msg001"],
            "reader_open_id": "ou_reader1",
        },
    ),
    (
        LarkMessageRecalledEventData,
        LarkMessageRecalledEvent,
        {
            "message_id": "om_msg001",
            "chat_id": "oc_chat001",
        },
    ),
]


class TestFactory:
    @pytest.mark.parametrize("data_cls,event_cls,extra", _FACTORY_PAIRS)
    def test_lke08_factory_creates_correct_type(self, data_cls, event_cls, extra):
        """LKE-08: 工厂函数 create_entity 正确映射飞书数据模型"""
        data = data_cls(**_BASE, **extra)
        entity = create_entity(data, _mock_lark_api())
        assert isinstance(entity, event_cls)
