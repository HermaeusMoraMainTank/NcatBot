import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins"))

from common.utils.QqSendUtil import QqSendUtil  # noqa: E402
from common.utils.QQMessageErrorHandler import QQMessageErrorHandler  # noqa: E402


def test_is_send_timeout_error_recognizes_protocol_timeout_messages():
    assert QqSendUtil.is_send_timeout_error(TimeoutError("API 请求超时: send_group_msg"))
    assert QqSendUtil.is_send_timeout_error(Exception("send failed [1200]"))
    assert not QqSendUtil.is_send_timeout_error(Exception("invalid group"))

    class TimeoutException(Exception):
        pass

    assert QqSendUtil.is_send_timeout_error(TimeoutException())


@pytest.mark.asyncio
async def test_post_group_msg_retry_retries_timeout():
    class FakeQqApi:
        def __init__(self):
            self.calls = 0

        async def post_group_msg(self, group_id, **kwargs):
            self.calls += 1
            if self.calls < 2:
                raise TimeoutError("API 请求超时: send_group_msg")

    api = FakeQqApi()
    await QqSendUtil.post_group_msg_retry(api, 123, text="hello", retries=1)

    assert api.calls == 2


@pytest.mark.asyncio
async def test_error_handler_uses_image_for_timeout_and_text_otherwise():
    class FakeQqApi:
        def __init__(self):
            self.messages = []

        async def post_group_msg(self, group_id, **kwargs):
            self.messages.append((group_id, kwargs))
            return {"message_id": "1"}

    api = FakeQqApi()
    await QQMessageErrorHandler.send_group_error(
        api, 123, TimeoutError("API 请求超时"), fallback_text="原始错误"
    )
    assert api.messages[-1][1]["rtf"].to_list()[0]["type"] == "image"

    await QQMessageErrorHandler.send_group_error(
        api, 123, ValueError("bad data"), fallback_text="原始错误"
    )
    assert api.messages[-1][1]["text"] == "原始错误"
