"""查询操作 API Mixin"""

from __future__ import annotations

import re
from typing import Optional

from ncatbot.types.bilibili.models import ParsedBiliId, VideoInfo
from ncatbot.utils import get_log

LOG = get_log("QueryAPI")

_BV_PATTERN = re.compile(r"[bB][vV]([1-9A-HJ-NP-Za-km-z]{10})")
_AV_PATTERN = re.compile(r"[aA][vV](\d+)")
_B23_PATTERN = re.compile(r"https?://b23\.tv/\S+")


class QueryAPIMixin:
    async def get_user_info(self, user_id: int) -> dict:
        from bilibili_api import user as bili_user

        u = bili_user.User(uid=user_id, credential=self._credential)
        return await u.get_user_info()

    async def get_video_info(self, video_id: str) -> Optional[VideoInfo]:
        """获取B站视频信息。

        Parameters
        ----------
        video_id:
            BV 号（如 ``BV1wa411Q7N6``）或 av 号（如 ``av258296202``）。
        """

        v = self._make_video(video_id)
        raw = await v.get_info()
        return VideoInfo.from_raw(raw)

    async def get_video_subtitle(
        self, video_id: str, *, page_index: int = 0, language: str = ""
    ) -> Optional[str]:
        """获取视频字幕文本，无字幕返回 None。"""
        import aiohttp

        v = self._make_video(video_id)
        info = await v.get_info()
        pages = info.get("pages", [])
        if not pages or page_index >= len(pages):
            return None

        cid = pages[page_index]["cid"]
        subtitle_info = await v.get_subtitle(cid=cid)
        subtitle_list = (subtitle_info or {}).get("subtitles", [])
        if not subtitle_list:
            return None

        # 选择字幕：优先匹配 language，否则取第一条
        chosen = subtitle_list[0]
        if language:
            for s in subtitle_list:
                if s.get("lan", "") == language:
                    chosen = s
                    break

        subtitle_url = chosen.get("subtitle_url", "")
        if not subtitle_url:
            return None
        if subtitle_url.startswith("//"):
            subtitle_url = "https:" + subtitle_url

        # 下载字幕 JSON 并拼接文本
        async with aiohttp.ClientSession() as session:
            async with session.get(subtitle_url) as resp:
                resp.raise_for_status()
                data = await resp.json()

        body = data.get("body", [])
        if not body:
            return None
        return "\n".join(item.get("content", "") for item in body)

    def _make_video(self, video_id: str):
        """根据 video_id 构造 bilibili Video 对象。"""
        from bilibili_api import video as bili_video

        if video_id.lower().startswith("av"):
            return bili_video.Video(aid=int(video_id[2:]), credential=self._credential)
        return bili_video.Video(bvid=video_id, credential=self._credential)

    async def get_video_audio_url(
        self, video_id: str, *, page_index: int = 0
    ) -> Optional[str]:
        """获取视频音频流 URL。

        优先使用 DASH 模式获取独立音频流（体积小），
        失败时回退到 html5 FLV 合并流。
        """
        from bilibili_api.video import (
            AudioStreamDownloadURL,
            VideoDownloadURLDataDetecter,
        )

        v = self._make_video(video_id)

        # 1. 优先 DASH 模式（独立音频流，需要登录凭据）
        try:
            download_data = await v.get_download_url(page_index=page_index)
            detector = VideoDownloadURLDataDetecter(download_data)
            streams = detector.detect_best_streams()
            LOG.debug("DASH streams: %s", [type(s).__name__ for s in streams])

            for stream in streams:
                if isinstance(stream, AudioStreamDownloadURL):
                    LOG.info("DASH 独立音频流: %s", stream.url[:80])
                    return stream.url
        except Exception as exc:
            LOG.warning("DASH 模式获取失败: %s", exc)

        # 2. 回退 html5 FLV 合并流（无需鉴权）
        try:
            download_data = await v.get_download_url(page_index=page_index, html5=True)
            detector = VideoDownloadURLDataDetecter(download_data)
            streams = detector.detect_best_streams()
            LOG.debug("html5 streams: %s", [type(s).__name__ for s in streams])

            for stream in streams:
                if isinstance(stream, AudioStreamDownloadURL):
                    return stream.url

            if streams and hasattr(streams[0], "url"):
                LOG.info("html5 合并流: %s", streams[0].url[:80])
                return streams[0].url
        except Exception as exc:
            LOG.warning("html5 模式获取失败: %s", exc)

        LOG.warning("无法获取视频 %s 的音频流", video_id)
        return None

    async def parse_bili_id(self, text: str) -> Optional[ParsedBiliId]:
        """从文本中解析B站视频 ID。

        支持 b23.tv 短链（自动重定向）、BV 号、av 号。
        返回 ``ParsedBiliId`` 或 ``None``（无法匹配时）。
        """
        import aiohttp

        raw_text = text

        # 1. 尝试匹配 b23 短链
        b23_match = _B23_PATTERN.search(text)
        if b23_match:
            short_url = b23_match.group(0)
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.head(
                        short_url,
                        allow_redirects=True,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        text = str(resp.url)
                        LOG.info("b23 重定向: %s -> %s", short_url, text)
            except Exception as e:
                LOG.warning("b23 重定向失败 %s: %s", short_url, e)
                return None

        # 2. 匹配 BV 号
        bv_match = _BV_PATTERN.search(text)
        if bv_match:
            bvid = "BV" + bv_match.group(1)
            return ParsedBiliId(
                id_type="bv",
                bvid=bvid,
                raw_url=raw_text,
                redirected_url=text if b23_match else "",
            )

        # 3. 匹配 av 号
        av_match = _AV_PATTERN.search(text)
        if av_match:
            aid = int(av_match.group(1))
            return ParsedBiliId(
                id_type="av",
                aid=aid,
                raw_url=raw_text,
                redirected_url=text if b23_match else "",
            )

        return None
