"""
QQ Sugar 方法单元测试

SG-01 ~ SG-09：覆盖消息组装、便捷发送和段冲突加固。
"""

import pytest

from ncatbot.adapter.mock.api import MockBotAPI
from ncatbot.api.qq.segment_validator import QQSegmentConflictError
from ncatbot.api.qq.sugar import QQMessageSugarMixin, _build_message_array
from ncatbot.types import Image, MessageArray


class _SugarHost(QQMessageSugarMixin):
    """宿主类，注入 MockBotAPI 作为 _api"""

    def __init__(self, api: MockBotAPI):
        self._api = api


@pytest.fixture
def api() -> MockBotAPI:
    return MockBotAPI()


@pytest.fixture
def sugar(api: MockBotAPI) -> _SugarHost:
    return _SugarHost(api)


class TestPostGroupMsg:
    """SG-01: post_group_msg 组装消息"""

    @pytest.mark.asyncio
    async def test_sg01_group_msg_with_multiple_params(self, sugar, api):
        """SG-01: post_group_msg 组装 text+at+reply+image → send_group_msg 调用正确"""
        await sugar.post_group_msg(
            "12345",
            text="hello",
            at="99999",
            reply="100",
            image="https://example.com/img.png",
        )

        assert api.called("send_group_msg")
        call = api.last_call("send_group_msg")
        group_id = call.params["group_id"]
        message = call.params["message"]

        assert group_id == "12345"
        types = [seg["type"] for seg in message]
        assert "reply" in types
        assert "at" in types
        assert "text" in types
        assert "image" in types


class TestPostPrivateMsg:
    """SG-02: post_private_msg 基本消息"""

    @pytest.mark.asyncio
    async def test_sg02_private_msg(self, sugar, api):
        """SG-02: post_private_msg 基本消息发送"""
        await sugar.post_private_msg("54321", text="hi")

        assert api.called("send_private_msg")
        call = api.last_call("send_private_msg")
        user_id = call.params["user_id"]
        message = call.params["message"]
        assert user_id == "54321"
        assert any(seg["type"] == "text" for seg in message)


class TestGroupSugar:
    """SG-03: 群快捷方法"""

    @pytest.mark.asyncio
    async def test_sg03_send_group_text_and_image(self, sugar, api):
        """SG-03: send_group_text / send_group_image 快捷方法"""
        await sugar.send_group_text("111", "hello")
        assert api.call_count("send_group_msg") == 1

        await sugar.send_group_image("111", "https://img.png")
        assert api.call_count("send_group_msg") == 2


class TestPrivateSugar:
    """SG-04: 私聊快捷方法"""

    @pytest.mark.asyncio
    async def test_sg04_send_private_text_and_image(self, sugar, api):
        """SG-04: send_private_text / send_private_image 快捷方法"""
        await sugar.send_private_text("222", "world")
        assert api.call_count("send_private_msg") == 1

        await sugar.send_private_image("222", "file:///img.png")
        assert api.call_count("send_private_msg") == 2


class TestForwardSugar:
    """SG-05: 转发消息"""

    @pytest.mark.asyncio
    async def test_sg05_forward_by_id(self, sugar, api):
        """SG-05: send_group_forward_msg_by_id 转发消息"""
        api.set_response(
            "get_msg",
            {"message": [{"type": "text", "data": {"text": "original"}}]},
        )
        await sugar.send_group_forward_msg_by_id("111", ["1001", "1002"])

        assert api.call_count("get_msg") == 2
        assert api.call_count("send_group_msg") == 2


class TestBuildMessageArray:
    """SG-06: _build_message_array 组合"""

    def test_sg06_build_with_multiple_params(self):
        """SG-06: _build_message_array 多参数组合"""
        msg = _build_message_array(
            text="hello",
            at="123",
            reply="456",
            image=Image(file="test.png"),
        )
        segments = msg.to_list()
        types = [s["type"] for s in segments]

        assert "reply" in types
        assert "at" in types
        assert "text" in types
        assert "image" in types

        # reply 应在最前面
        assert types[0] == "reply"


class TestSegmentConflictIntegration:
    """SG-07 ~ SG-09: 段冲突加固在 sugar 层的集成"""

    @pytest.mark.asyncio
    async def test_sg07_video_text_auto_split(self, sugar, api):
        """SG-07: post_group_msg(video=..., text=...) 触发自动拆分，发送两次"""
        await sugar.post_group_msg("12345", video="v.mp4", text="hello")
        # 冲突 → 自动拆为两条消息
        assert api.call_count("send_group_msg") == 2

    @pytest.mark.asyncio
    async def test_sg08_strict_raises(self, sugar, api):
        """SG-08: post_group_array_msg(strict=True) + Video+Text → 抛异常"""
        msg = MessageArray()
        msg.add_video("v.mp4")
        msg.add_text("hello")
        with pytest.raises(QQSegmentConflictError):
            await sugar.post_group_array_msg("12345", msg, strict=True)

    @pytest.mark.asyncio
    async def test_sg09_no_conflict_single_send(self, sugar, api):
        """SG-09: 无冲突时仍只发送一次"""
        await sugar.post_group_msg("12345", text="hi", image="img.png")
        assert api.call_count("send_group_msg") == 1
