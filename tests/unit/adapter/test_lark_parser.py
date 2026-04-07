"""
飞书事件解析器测试 — LarkEventParser

规范:
  LK-01: parse_message 群消息 → LarkGroupMessageEventData
  LK-02: parse_message 私聊消息 → LarkPrivateMessageEventData
  LK-03: parse_message 提取消息文本（JSON content）
  LK-04: parse_message 非 JSON content 降级为原始字符串
  LK-05: parse_message 解析失败返回 None
  LK-06: parse_message_read → LarkMessageReadEventData
  LK-07: parse_message_read reader_id 缺失时字段降级为空字符串
  LK-08: parse_message_recalled → LarkMessageRecalledEventData
  LK-09: parse_message sender 信息提取正确
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ncatbot.adapter.lark.parser import LarkEventParser
from ncatbot.types.lark import (
    LarkGroupMessageEventData,
    LarkPrivateMessageEventData,
    LarkMessageReadEventData,
    LarkMessageRecalledEventData,
)


# ---- Helpers: 模拟 lark-oapi SDK 事件对象 ----


def _make_sender_id(open_id="ou_test123", union_id="on_union456", user_id=""):
    return SimpleNamespace(open_id=open_id, union_id=union_id, user_id=user_id)


def _make_sender(open_id="ou_test123", union_id="on_union456", tenant_key="tk_abc"):
    return SimpleNamespace(
        sender_id=_make_sender_id(open_id, union_id),
        tenant_key=tenant_key,
    )


def _make_message(
    chat_id="oc_group123",
    chat_type="group",
    message_id="om_msg001",
    content='{"text":"hello world"}',
):
    return SimpleNamespace(
        chat_id=chat_id,
        chat_type=chat_type,
        message_id=message_id,
        content=content,
    )


def _make_p2_message_event(chat_type="group", text="hello world"):
    content = f'{{"text":"{text}"}}'
    return SimpleNamespace(
        event=SimpleNamespace(
            message=_make_message(chat_type=chat_type, content=content),
            sender=_make_sender(),
        )
    )


def _make_p2_read_event(
    message_ids=None,
    open_id="ou_reader1",
    union_id="on_reader1",
    user_id="uid_reader1",
    read_time="1700000000",
    tenant_key="tk_read",
):
    reader_id = SimpleNamespace(open_id=open_id, union_id=union_id, user_id=user_id)
    reader = SimpleNamespace(
        reader_id=reader_id,
        read_time=read_time,
        tenant_key=tenant_key,
    )
    return SimpleNamespace(
        event=SimpleNamespace(
            message_id_list=message_ids or ["om_msg001", "om_msg002"],
            reader=reader,
        )
    )


def _make_p2_recalled_event(
    message_id="om_msg001",
    chat_id="oc_chat001",
    recall_time="1700000000",
    recall_type="user",
):
    return SimpleNamespace(
        event=SimpleNamespace(
            message_id=message_id,
            chat_id=chat_id,
            recall_time=recall_time,
            recall_type=recall_type,
        )
    )


@pytest.fixture
def parser():
    return LarkEventParser(self_id="lark_bot")


# ---- LK-01: 群消息 ----


class TestParseGroupMessage:
    def test_lk01_group_message(self, parser):
        """LK-01: parse_message 群消息 → LarkGroupMessageEventData"""
        data = _make_p2_message_event(chat_type="group", text="你好")
        result = parser.parse_message(data)

        assert isinstance(result, LarkGroupMessageEventData)
        assert result.platform == "lark"
        assert result.message_type == "group"
        assert result.content == "你好"
        assert result.chat_type == "group"
        assert result.chat_id == "oc_group123"
        assert result.group_id == "oc_group123"
        assert result.message_id == "om_msg001"
        assert result.self_id == "lark_bot"


# ---- LK-02: 私聊消息 ----


class TestParsePrivateMessage:
    def test_lk02_private_message(self, parser):
        """LK-02: parse_message 私聊消息 → LarkPrivateMessageEventData"""
        data = _make_p2_message_event(chat_type="p2p", text="私聊测试")
        result = parser.parse_message(data)

        assert isinstance(result, LarkPrivateMessageEventData)
        assert result.platform == "lark"
        assert result.message_type == "private"
        assert result.content == "私聊测试"
        assert result.chat_type == "p2p"


# ---- LK-03: JSON content 提取文本 ----


class TestContentExtraction:
    def test_lk03_json_content_extracts_text(self, parser):
        """LK-03: parse_message 从 JSON content 提取 text 字段"""
        sdk_event = SimpleNamespace(
            event=SimpleNamespace(
                message=_make_message(
                    chat_type="group", content='{"text":"extracted text"}'
                ),
                sender=_make_sender(),
            )
        )
        result = parser.parse_message(sdk_event)
        assert result.content == "extracted text"

    def test_lk04_non_json_content_fallback(self, parser):
        """LK-04: parse_message 非 JSON content 降级为原始字符串"""
        sdk_event = SimpleNamespace(
            event=SimpleNamespace(
                message=_make_message(
                    chat_type="p2p", content="plain text without json"
                ),
                sender=_make_sender(),
            )
        )
        result = parser.parse_message(sdk_event)
        assert result.content == "plain text without json"


# ---- LK-05: 解析失败 ----


class TestParseFailure:
    def test_lk05_parse_message_returns_none_on_error(self, parser):
        """LK-05: parse_message 解析失败返回 None"""
        result = parser.parse_message(SimpleNamespace(event=None))
        assert result is None


# ---- LK-06: 消息已读 ----


class TestParseMessageRead:
    def test_lk06_message_read(self, parser):
        """LK-06: parse_message_read → LarkMessageReadEventData"""
        data = _make_p2_read_event()
        result = parser.parse_message_read(data)

        assert isinstance(result, LarkMessageReadEventData)
        assert result.platform == "lark"
        assert result.post_type == "notice"
        assert result.notice_type == "message_read"
        assert result.message_id_list == ["om_msg001", "om_msg002"]
        assert result.reader_open_id == "ou_reader1"
        assert result.reader_union_id == "on_reader1"
        assert result.reader_user_id == "uid_reader1"
        assert result.read_time == "1700000000"
        assert result.tenant_key == "tk_read"

    def test_lk07_message_read_no_reader_id(self, parser):
        """LK-07: parse_message_read reader_id 缺失时字段降级为空字符串"""
        # reader 存在但没有 reader_id 属性
        sdk_event = SimpleNamespace(
            event=SimpleNamespace(
                message_id_list=["om_msg001"],
                reader=SimpleNamespace(
                    read_time="1700000000",
                    tenant_key="tk_test",
                ),
            )
        )
        result = parser.parse_message_read(sdk_event)

        assert isinstance(result, LarkMessageReadEventData)
        assert result.reader_open_id == ""
        assert result.reader_union_id == ""
        assert result.reader_user_id == ""


# ---- LK-08: 消息撤回 ----


class TestParseMessageRecalled:
    def test_lk08_message_recalled(self, parser):
        """LK-08: parse_message_recalled → LarkMessageRecalledEventData"""
        data = _make_p2_recalled_event()
        result = parser.parse_message_recalled(data)

        assert isinstance(result, LarkMessageRecalledEventData)
        assert result.platform == "lark"
        assert result.post_type == "notice"
        assert result.notice_type == "message_recalled"
        assert result.message_id == "om_msg001"
        assert result.chat_id == "oc_chat001"
        assert result.recall_time == "1700000000"
        assert result.recall_type == "user"


# ---- LK-09: Sender 信息 ----


class TestSenderExtraction:
    def test_lk09_sender_fields(self, parser):
        """LK-09: parse_message sender 信息提取正确"""
        data = _make_p2_message_event(chat_type="group", text="test")
        result = parser.parse_message(data)

        assert result.sender is not None
        assert result.sender.open_id == "ou_test123"
        assert result.sender.union_id == "on_union456"
        assert result.sender.tenant_key == "tk_abc"
        assert result.user_id == "ou_test123"
