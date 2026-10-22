"""FakeAi 引用消息与合并转发内容读取测试。"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ncatbot.adapter.mock.api import MockBotAPI
from ncatbot.api.qq import QQAPIClient
from ncatbot.testing.factories.qq import group_message
from ncatbot.types.napcat import ForwardMessageData, MessageData

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins"))

from FakeAi.FakeAi import FakeAi

fakeai_module = importlib.import_module("FakeAi.FakeAi")


def _text_of(content: list[dict]) -> str:
    return "".join(
        segment.get("data", {}).get("text", "")
        for segment in content
        if segment.get("type") == "text"
    )


@pytest.fixture
def fakeai_with_mock_api():
    mock_api = MockBotAPI()
    plugin = FakeAi.__new__(FakeAi)
    plugin.api = SimpleNamespace(qq=QQAPIClient(mock_api))
    return plugin, mock_api


@pytest.mark.asyncio
async def test_fakeai_reads_replied_message_content(fakeai_with_mock_api):
    """FA-REF-01: 回复消息的正文会被加入 FakeAi 上下文。"""
    plugin, mock_api = fakeai_with_mock_api
    mock_api.set_response(
        "get_msg",
        MessageData.model_validate(
            {
                "message_id": "reply-1",
                "sender": {"user_id": "200", "nickname": "原消息用户"},
                "message": [{"type": "text", "data": {"text": "被回复的内容"}}],
                "raw_message": "被回复的内容",
            }
        ),
    )

    event = group_message(
        "请看看",
        message=[
            {"type": "reply", "data": {"id": "reply-1"}},
            {"type": "text", "data": {"text": "请看看"}},
        ],
        raw_message="[CQ:reply,id=reply-1]请看看",
    )

    content = await plugin._build_message_content(event.message)

    assert "被回复的内容" in _text_of(content)
    assert mock_api.call_count("get_msg") == 1


@pytest.mark.asyncio
async def test_fakeai_reads_forwarded_message_nodes(fakeai_with_mock_api):
    """FA-REF-02: 合并转发的节点正文和发送者会被加入 FakeAi 上下文。"""
    plugin, mock_api = fakeai_with_mock_api
    mock_api.set_response(
        "get_forward_msg",
        ForwardMessageData.model_validate(
            {
                "messages": [
                    {
                        "message_id": "node-1",
                        "sender": {"user_id": "201", "nickname": "节点用户"},
                        "content": [
                            {"type": "text", "data": {"text": "转发里的内容"}}
                        ],
                        "raw_message": "转发里的内容",
                    }
                ]
            }
        ),
    )

    event = group_message(
        "总结一下",
        message=[
            {"type": "forward", "data": {"id": "forward-1"}},
            {"type": "text", "data": {"text": "总结一下"}},
        ],
        raw_message="[CQ:forward,id=forward-1]总结一下",
    )

    content = await plugin._build_message_content(event.message)

    text = _text_of(content)
    assert "节点用户" in text
    assert "转发里的内容" in text
    assert mock_api.call_count("get_forward_msg") == 1


@pytest.mark.asyncio
async def test_fakeai_group_toggle_is_limited_to_fixed_admin(fakeai_with_mock_api, monkeypatch):
    """FA-GROUP-01: 群启停命令只允许固定 QQ 执行，且不受群白名单拦截。"""
    plugin, mock_api = fakeai_with_mock_api
    changes = []

    async def fake_false():
        return False

    def fake_set_group_enabled(plugin, group_id, enabled):
        changes.append((group_id, enabled))

    plugin._is_from_excluded_plugin = lambda event: fake_false()
    monkeypatch.setattr(fakeai_module, "set_group_enabled", fake_set_group_enabled)

    await plugin.handle_fake_ai(
        group_message("蓝晴关闭", group_id="900001", user_id="273421673")
    )
    assert changes == [("900001", False)]
    assert mock_api.call_count("send_group_msg") == 1

    await plugin.handle_fake_ai(
        group_message("蓝晴启用", group_id="900001", user_id="other")
    )
    assert changes == [("900001", False)]
    assert mock_api.call_count("send_group_msg") == 2
