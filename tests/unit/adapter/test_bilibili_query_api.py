"""
Bilibili API 查询方法单元测试

规范:
  BQ-01: parse_bili_id 匹配 BV 号
  BQ-02: parse_bili_id 匹配 av 号
  BQ-03: parse_bili_id 匹配 b23 短链（mock 重定向）
  BQ-04: parse_bili_id 无匹配返回 None
  BQ-05: parse_bili_id 从完整 URL 中提取 BV 号
  BQ-06: get_video_audio_url DASH 模式返回独立音频流
  BQ-07: get_video_audio_url DASH 失败时回退 html5
  BQ-08: get_video_audio_url 两种模式均失败返回 None
  BQ-09: get_video_subtitle 正常返回字幕文本
  BQ-10: get_video_subtitle 无字幕返回 None
  BQ-11: get_video_subtitle 按 language 选择字幕
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ncatbot.adapter.bilibili.api.query import QueryAPIMixin, _AV_PATTERN, _BV_PATTERN


class TestBiliIdRegex:
    """BQ-01/02: 正则匹配（纯同步）"""

    def test_bq01_bv_pattern_standard(self):
        """BQ-01: 标准 BV 号匹配"""
        m = _BV_PATTERN.search("BV1wa411Q7N6")
        assert m is not None
        assert m.group(1) == "1wa411Q7N6"

    def test_bq01_bv_pattern_in_url(self):
        """BQ-01: URL 中的 BV 号"""
        m = _BV_PATTERN.search(
            "https://www.bilibili.com/video/BV1MdAgziEdg/?spm_id_from=333"
        )
        assert m is not None
        assert m.group(1) == "1MdAgziEdg"

    def test_bq01_bv_case_insensitive(self):
        """BQ-01: bv 小写前缀"""
        m = _BV_PATTERN.search("bv1wa411Q7N6 some text")
        assert m is not None

    def test_bq02_av_pattern(self):
        """BQ-02: 标准 av 号匹配"""
        m = _AV_PATTERN.search("av258296202")
        assert m is not None
        assert m.group(1) == "258296202"

    def test_bq02_av_in_text(self):
        """BQ-02: 混合文本中的 av 号"""
        m = _AV_PATTERN.search("快去看 AV12345 这个视频")
        assert m is not None
        assert m.group(1) == "12345"

    def test_bq04_no_match(self):
        """BQ-04: 无匹配"""
        assert _BV_PATTERN.search("没有B站链接") is None
        assert _AV_PATTERN.search("没有B站链接") is None


class _FakeQueryMixin(QueryAPIMixin):
    """为测试提供 _credential 属性的 Mixin 子类"""

    def __init__(self):
        self._credential = None


class TestParseBiliId:
    """BQ-01~05: parse_bili_id 异步方法"""

    @pytest.fixture
    def mixin(self):
        return _FakeQueryMixin()

    @pytest.mark.asyncio
    async def test_bq01_parse_bv(self, mixin):
        """BQ-01: parse_bili_id 匹配 BV 号"""
        result = await mixin.parse_bili_id("BV1wa411Q7N6")
        assert result is not None
        assert result.id_type == "bv"
        assert result.bvid == "BV1wa411Q7N6"
        assert result.video_id == "BV1wa411Q7N6"

    @pytest.mark.asyncio
    async def test_bq02_parse_av(self, mixin):
        """BQ-02: parse_bili_id 匹配 av 号"""
        result = await mixin.parse_bili_id("av258296202")
        assert result is not None
        assert result.id_type == "av"
        assert result.aid == 258296202
        assert result.video_id == "av258296202"

    @pytest.mark.asyncio
    async def test_bq04_parse_no_match(self, mixin):
        """BQ-04: parse_bili_id 无匹配返回 None"""
        result = await mixin.parse_bili_id("没有任何B站链接的文本")
        assert result is None

    @pytest.mark.asyncio
    async def test_bq05_parse_full_url(self, mixin):
        """BQ-05: parse_bili_id 从完整 URL 中提取 BV 号"""
        result = await mixin.parse_bili_id(
            "https://www.bilibili.com/video/BV1MdAgziEdg/?spm_id_from=333.1387.homepage.video_card.click"
        )
        assert result is not None
        assert result.id_type == "bv"
        assert result.bvid == "BV1MdAgziEdg"

    @pytest.mark.asyncio
    async def test_bq03_parse_b23_mock(self, mixin):
        """BQ-03: parse_bili_id 匹配 b23 短链（mock 重定向）"""
        mock_resp = MagicMock()
        mock_resp.url = "https://www.bilibili.com/video/BV1wa411Q7N6?p=1"

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.head = MagicMock(return_value=mock_session_ctx)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await mixin.parse_bili_id("看这个 https://b23.tv/abcdef 太好看了")

        assert result is not None
        assert result.id_type == "bv"
        assert result.bvid == "BV1wa411Q7N6"
        assert (
            result.redirected_url == "https://www.bilibili.com/video/BV1wa411Q7N6?p=1"
        )


# ---- BQ-06 ~ BQ-08: get_video_audio_url ----


class TestGetVideoAudioUrl:
    """BQ-06~08: get_video_audio_url 音频流获取"""

    @pytest.fixture(autouse=True)
    def _mock_bilibili_video_module(self):
        """Mock bilibili_api.video 模块，CI 环境可能未安装 bilibili-api-python。"""
        self.AudioStreamDownloadURL = type("AudioStreamDownloadURL", (), {})
        mock_video_mod = MagicMock()
        mock_video_mod.AudioStreamDownloadURL = self.AudioStreamDownloadURL
        mock_video_mod.VideoDownloadURLDataDetecter = MagicMock()
        with patch.dict(
            sys.modules,
            {
                "bilibili_api": MagicMock(),
                "bilibili_api.video": mock_video_mod,
            },
        ):
            yield

    @pytest.fixture
    def mixin(self):
        return _FakeQueryMixin()

    @pytest.mark.asyncio
    async def test_bq06_dash_audio_stream(self, mixin):
        """BQ-06: DASH 模式返回独立音频流 URL"""
        mock_audio = MagicMock(spec=self.AudioStreamDownloadURL)
        mock_audio.url = "https://example.com/audio.m4s"

        mock_detector = MagicMock()
        mock_detector.detect_best_streams.return_value = [mock_audio]

        mock_video = AsyncMock()
        mock_video.get_download_url = AsyncMock(return_value={"dash": "data"})

        with (
            patch.object(mixin, "_make_video", return_value=mock_video),
            patch(
                "bilibili_api.video.VideoDownloadURLDataDetecter",
                return_value=mock_detector,
            ),
        ):
            result = await mixin.get_video_audio_url("BV1wa411Q7N6")

        assert result == "https://example.com/audio.m4s"
        mock_video.get_download_url.assert_awaited_once_with(page_index=0)

    @pytest.mark.asyncio
    async def test_bq07_fallback_to_html5(self, mixin):
        """BQ-07: DASH 失败时回退到 html5 合并流"""
        mock_flv = MagicMock()
        mock_flv.url = "https://example.com/video.flv"

        # DASH 模式抛异常
        mock_video = AsyncMock()
        call_count = 0

        async def fake_get_download_url(**kwargs):
            nonlocal call_count
            call_count += 1
            if not kwargs.get("html5"):
                raise Exception("DASH not available")
            return {"html5": "data"}

        mock_video.get_download_url = AsyncMock(side_effect=fake_get_download_url)

        mock_detector = MagicMock()
        mock_detector.detect_best_streams.return_value = [mock_flv]

        with (
            patch.object(mixin, "_make_video", return_value=mock_video),
            patch(
                "bilibili_api.video.VideoDownloadURLDataDetecter",
                return_value=mock_detector,
            ),
        ):
            result = await mixin.get_video_audio_url("BV1wa411Q7N6")

        assert result == "https://example.com/video.flv"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_bq08_both_fail_returns_none(self, mixin):
        """BQ-08: DASH 和 html5 均失败时返回 None"""
        mock_video = AsyncMock()
        mock_video.get_download_url = AsyncMock(
            side_effect=Exception("download failed")
        )

        with patch.object(mixin, "_make_video", return_value=mock_video):
            result = await mixin.get_video_audio_url("BV1wa411Q7N6")

        assert result is None


# ---- BQ-09 ~ BQ-11: get_video_subtitle ----


class TestGetVideoSubtitle:
    """BQ-09~11: get_video_subtitle 字幕获取"""

    @pytest.fixture
    def mixin(self):
        return _FakeQueryMixin()

    def _mock_aiohttp_json(self, json_data):
        """构造 mock aiohttp session 返回指定 JSON 数据"""
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value=json_data)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        return mock_session

    @pytest.mark.asyncio
    async def test_bq09_subtitle_normal(self, mixin):
        """BQ-09: 正常返回字幕拼接文本"""
        mock_video = AsyncMock()
        mock_video.get_info = AsyncMock(return_value={"pages": [{"cid": 12345}]})
        mock_video.get_subtitle = AsyncMock(
            return_value={
                "subtitles": [
                    {"lan": "zh-CN", "subtitle_url": "//example.com/sub.json"}
                ]
            }
        )

        subtitle_json = {
            "body": [
                {"content": "你好"},
                {"content": "世界"},
                {"content": "测试"},
            ]
        }
        mock_session = self._mock_aiohttp_json(subtitle_json)

        with (
            patch.object(mixin, "_make_video", return_value=mock_video),
            patch("aiohttp.ClientSession", return_value=mock_session),
        ):
            result = await mixin.get_video_subtitle("BV1wa411Q7N6")

        assert result == "你好\n世界\n测试"

    @pytest.mark.asyncio
    async def test_bq10_no_subtitle_returns_none(self, mixin):
        """BQ-10: 无字幕返回 None"""
        mock_video = AsyncMock()
        mock_video.get_info = AsyncMock(return_value={"pages": [{"cid": 12345}]})
        mock_video.get_subtitle = AsyncMock(return_value={"subtitles": []})

        with patch.object(mixin, "_make_video", return_value=mock_video):
            result = await mixin.get_video_subtitle("BV1wa411Q7N6")

        assert result is None

    @pytest.mark.asyncio
    async def test_bq11_language_selection(self, mixin):
        """BQ-11: 按 language 参数选择对应字幕"""
        mock_video = AsyncMock()
        mock_video.get_info = AsyncMock(return_value={"pages": [{"cid": 12345}]})
        mock_video.get_subtitle = AsyncMock(
            return_value={
                "subtitles": [
                    {"lan": "zh-CN", "subtitle_url": "//example.com/zh.json"},
                    {"lan": "en", "subtitle_url": "//example.com/en.json"},
                ]
            }
        )

        en_json = {"body": [{"content": "Hello"}, {"content": "World"}]}
        mock_session = self._mock_aiohttp_json(en_json)

        with (
            patch.object(mixin, "_make_video", return_value=mock_video),
            patch("aiohttp.ClientSession", return_value=mock_session),
        ):
            result = await mixin.get_video_subtitle("BV1wa411Q7N6", language="en")

        assert result == "Hello\nWorld"
        # 验证请求的是英文字幕 URL
        mock_session.get.assert_called_once_with("https://example.com/en.json")
