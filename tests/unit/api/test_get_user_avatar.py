"""get_user_avatar 规范测试"""

import pytest

from ncatbot.adapter.mock.api import MockBotAPI
from ncatbot.api.qq import QQAPIClient
from ncatbot.types import ImageAttachment
from ncatbot.types.common.attachment import AttachmentKind


def _make_query():
    mock = MockBotAPI()
    return QQAPIClient(mock).query


class TestGetUserAvatar:
    @pytest.mark.asyncio
    async def test_returns_image_attachment(self):
        query = _make_query()
        result = await query.get_user_avatar(123456)
        assert isinstance(result, ImageAttachment)
        assert result.kind == AttachmentKind.IMAGE

    @pytest.mark.asyncio
    async def test_default_spec_640(self):
        query = _make_query()
        result = await query.get_user_avatar(123456)
        assert "spec=640" in result.url
        assert result.width == 640
        assert result.height == 640

    @pytest.mark.asyncio
    async def test_custom_spec(self):
        query = _make_query()
        result = await query.get_user_avatar(123456, spec=100)
        assert "spec=100" in result.url
        assert result.width == 100

    @pytest.mark.asyncio
    async def test_url_contains_user_id(self):
        query = _make_query()
        result = await query.get_user_avatar(999888)
        assert "dst_uin=999888" in result.url

    @pytest.mark.asyncio
    async def test_content_type_jpeg(self):
        query = _make_query()
        result = await query.get_user_avatar(123456)
        assert result.content_type == "image/jpeg"

    @pytest.mark.asyncio
    async def test_str_user_id(self):
        query = _make_query()
        result = await query.get_user_avatar("123456")
        assert "dst_uin=123456" in result.url
