"""NapCat 查询 API 单元测试。

规范:
  NQ-01: get_forward_msg 使用 NapCat 规范的 message_id 参数
  NQ-02: get_forward_msg 保留超过 JavaScript 安全整数范围的完整 ID
"""

from unittest.mock import AsyncMock

import pytest

from ncatbot.adapter.napcat.api.query import QueryAPIMixin


class _FakeQueryMixin(QueryAPIMixin):
    """为测试提供底层 API 调用方法。"""

    def __init__(self):
        self._call_data = AsyncMock(return_value={"messages": []})


class TestNapCatQueryAPI:
    """NapCat 查询 API 参数转换测试。"""

    @pytest.mark.parametrize(
        "message_id",
        [
            "7672696335120716277",
            7672696335120716277,
        ],
    )
    @pytest.mark.asyncio
    async def test_nq01_get_forward_msg_preserves_long_id(self, message_id):
        """NQ-01/02: 转发消息长 ID 始终通过字符串 message_id 原样传递。"""
        api = _FakeQueryMixin()

        result = await api.get_forward_msg(message_id)

        api._call_data.assert_awaited_once_with(
            "get_forward_msg",
            {"message_id": "7672696335120716277"},
        )
        assert result.messages == []
