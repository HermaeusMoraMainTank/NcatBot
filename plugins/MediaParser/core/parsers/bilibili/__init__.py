import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from re import Match
from typing import ClassVar

from bilibili_api import HEADERS, Credential, request_settings, select_client
from bilibili_api.login_v2 import QrCodeLogin, QrCodeLoginEvents
from bilibili_api.opus import Opus
from bilibili_api.video import Video, VideoCodecs, VideoQuality
from bs4 import BeautifulSoup
from msgspec import convert

from ...compat import _log, ConfigWrapper as AstrBotConfig, video_max_duration_seconds

from ...data import ImageContent, MediaContent, Platform
from ...exception import DownloadException, DurationLimitException
from ...utils import ck2dict
from ..base import (
    BaseParser,
    Downloader,
    ParseException,
    handle,
)

# 选择客户端
select_client("curl_cffi")
# 模拟浏览器，第二参数数值参考 curl_cffi 文档
# https://curl-cffi.readthedocs.io/en/latest/impersonate.html
request_settings.set("impersonate", "chrome131")


class BilibiliParser(BaseParser):
    # 平台信息
    platform: ClassVar[Platform] = Platform(name="bilibili", display_name="B站")

    def __init__(self, config: AstrBotConfig, downloader: Downloader):
        super().__init__(config, downloader)
        self.headers = HEADERS.copy()
        self._credential: Credential | None = None
        self.max_duration = video_max_duration_seconds(config)
        self.cache_dir = Path(config["cache_dir"])

        self.video_quality = getattr(
            VideoQuality, config["bili_video_quality"].upper(), VideoQuality._720P
        )
        self.codecs = getattr(
            VideoCodecs, config["bili_video_codecs"].upper(), VideoCodecs.AVC
        )
        self.bili_ck = config["bili_ck"]
        self._cookies_file = Path(config["data_dir"]) / "bilibili_cookies.json"

    @handle("b23.tv", r"b23\.tv/[A-Za-z\d\._?%&+\-=/#]+")
    @handle("bili2233", r"bili2233\.cn/[A-Za-z\d\._?%&+\-=/#]+")
    async def _parse_short_link(self, searched: Match[str]):
        """解析短链"""
        url = f"https://{searched.group(0)}"
        return await self.parse_with_redirect(url)

    @handle("BV", r"^(?P<bvid>BV[0-9a-zA-Z]{10})(?:\s)?(?P<page_num>\d{1,3})?$")
    @handle(
        "/BV",
        r"bilibili\.com(?:/video)?/(?P<bvid>BV[0-9a-zA-Z]{10})(?:\?p=(?P<page_num>\d{1,3}))?",
    )
    async def _parse_bv(self, searched: Match[str]):
        """解析视频信息"""
        bvid = str(searched.group("bvid"))
        page_num = int(searched.group("page_num") or 1)

        return await self.parse_video(bvid=bvid, page_num=page_num)

    @handle("bm", r"^bm(?P<bvid>BV[0-9a-zA-Z]{10})(?:\s(?P<page_num>\d{1,3}))?$")
    async def _parse_bv_bm(self, searched: Match[str]):
        bvid = searched.group("bvid")
        page = int(searched.group("page_num") or 1)
        _, a_url = await self.extract_download_urls(bvid=bvid, page_index=page - 1)
        if not a_url:
            raise ParseException("未找到音频链接")
        audio = self.create_audio_content(a_url)
        return self.result(
            title=f"BiliBili_audio_{bvid}",
            contents=[audio],
            url=a_url,
        )

    @handle("av", r"^av(?P<avid>\d{6,})(?:\s)?(?P<page_num>\d{1,3})?$")
    @handle(
        "/av",
        r"bilibili\.com(?:/video)?/av(?P<avid>\d{6,})(?:\?p=(?P<page_num>\d{1,3}))?",
    )
    async def _parse_av(self, searched: Match[str]):
        """解析视频信息"""
        avid = int(searched.group("avid"))
        page_num = int(searched.group("page_num") or 1)

        return await self.parse_video(avid=avid, page_num=page_num)

    @handle("/dynamic/", r"bilibili\.com/dynamic/(?P<dynamic_id>\d+)")
    @handle("t.bili", r"t\.bilibili\.com/(?P<dynamic_id>\d+)")
    async def _parse_dynamic(self, searched: Match[str]):
        """解析动态信息"""
        dynamic_id = int(searched.group("dynamic_id"))
        return await self.parse_dynamic(dynamic_id)

    @handle("live.bili", r"live\.bilibili\.com/(?P<room_id>\d+)")
    async def _parse_live(self, searched: Match[str]):
        """解析直播信息"""
        room_id = int(searched.group("room_id"))
        return await self.parse_live(room_id)

    @handle("/favlist", r"favlist\?fid=(?P<fav_id>\d+)")
    async def _parse_favlist(self, searched: Match[str]):
        """解析收藏夹信息"""
        fav_id = int(searched.group("fav_id"))
        return await self.parse_favlist(fav_id)

    @handle("/read/", r"bilibili\.com/read/cv(?P<read_id>\d+)")
    async def _parse_read(self, searched: Match[str]):
        """解析专栏信息"""
        read_id = int(searched.group("read_id"))
        return await self.parse_read(read_id)

    @handle("/opus/", r"bilibili\.com/opus/(?P<opus_id>\d+)")
    async def _parse_opus(self, searched: Match[str]):
        """解析图文动态信息"""
        opus_id = int(searched.group("opus_id"))
        return await self.parse_opus(opus_id)

    @handle(
        "mall.bili",
        r"mall\.bilibili\.com/[A-Za-z\d\._?%&+\-=/#]+itemsId=(?P<item_id>\d+)[A-Za-z\d\._?%&+\-=/#]*",
    )
    async def _parse_mall_item(self, searched: Match[str]):
        """解析会员购商品信息"""
        item_id = str(searched.group("item_id"))
        raw_url = searched.group(0)
        url = (
            raw_url
            if raw_url.startswith(("http://", "https://"))
            else f"https://{raw_url}"
        )

        async with self.client.get(
            url=url,
            headers=self.headers,
            allow_redirects=True,
            proxy=self.proxy,
        ) as resp:
            if resp.status >= 400:
                raise ParseException(f"会员购页面请求失败 {resp.status} {resp.reason}")
            html = await resp.text()
            final_url = str(resp.url)

        soup = BeautifulSoup(html, "html.parser")
        og_title = soup.find("meta", attrs={"property": "og:title"})
        title_tag = soup.find("title")
        og_desc = soup.find("meta", attrs={"property": "og:description"})
        og_cover = soup.find("meta", attrs={"property": "og:image"})

        title = (
            (og_title.get("content") if og_title else None)
            or (title_tag.get_text(strip=True) if title_tag else None)
            or f"B站会员购商品 {item_id}"
        )
        desc = og_desc.get("content") if og_desc else None
        cover = og_cover.get("content") if og_cover else None

        contents: list[MediaContent] = []
        if isinstance(cover, str) and cover.strip():
            contents.extend(self.create_image_contents([cover.strip()]))

        return self.result(
            title=title.strip()
            if isinstance(title, str)
            else f"B站会员购商品 {item_id}",
            text=desc.strip() if isinstance(desc, str) else None,
            url=final_url,
            contents=contents,
        )

    async def parse_video(
        self,
        *,
        bvid: str | None = None,
        avid: int | None = None,
        page_num: int = 1,
    ):
        """解析视频信息

        Args:
            bvid (str | None): bvid
            avid (int | None): avid
            page_num (int): 页码
        """

        from .video import AIConclusion, VideoInfo

        video = await self._get_video(bvid=bvid, avid=avid)
        # 转换为 msgspec struct
        video_info = convert(await video.get_info(), VideoInfo)
        # 获取简介
        text = f"简介: {video_info.desc}" if video_info.desc else None
        # up
        author = self.create_author(video_info.owner.name, video_info.owner.face)
        # 处理分 p
        page_info = video_info.extract_info_with_page(page_num)

        # 获取 AI 总结
        if self._credential:
            cid = await video.get_cid(page_info.index)
            ai_conclusion = await video.get_ai_conclusion(cid)
            ai_conclusion = convert(ai_conclusion, AIConclusion)
            ai_summary = ai_conclusion.summary
        else:
            ai_summary: str = "哔哩哔哩 cookie 未配置或失效, 无法使用 AI 总结"

        url = f"https://bilibili.com/{video_info.bvid}"
        url += f"?p={page_info.index + 1}" if page_info.index > 0 else ""

        # 视频下载 task
        async def download_video():
            output_path = self.cache_dir / f"{video_info.bvid}-{page_num}.mp4"
            if output_path.exists():
                return output_path
            v_url, a_url = await self.extract_download_urls(
                video=video, page_index=page_info.index
            )
            if self.max_duration > 0 and page_info.duration > self.max_duration:
                raise DurationLimitException(
                    duration=page_info.duration, limit_seconds=self.max_duration
                )
            if a_url is not None:
                return await self.downloader.download_av_and_merge(
                    v_url,
                    a_url,
                    output_path=output_path,
                    ext_headers=self.headers,
                    proxy=self.proxy,
                )
            else:
                return await self.downloader.streamd(
                    v_url,
                    file_name=output_path.name,
                    ext_headers=self.headers,
                    proxy=self.proxy,
                )

        video_task = asyncio.create_task(download_video())
        video_content = self.create_video_content(
            video_task,
            page_info.cover,
            page_info.duration,
        )

        return self.result(
            url=url,
            title=page_info.title,
            timestamp=page_info.timestamp,
            text=text,
            author=author,
            contents=[video_content],
            extra={"info": ai_summary},
        )

    async def parse_dynamic(self, dynamic_id: int):
        """解析动态信息

        Args:
            url (str): 动态链接
        """
        from bilibili_api.dynamic import Dynamic

        from .dynamic import DynamicItem

        dynamic = Dynamic(dynamic_id, await self.credential)

        # 转换为结构体
        dynamic_data = convert(await dynamic.get_info(), DynamicItem)
        dynamic_info = dynamic_data.item
        # 使用结构体属性提取信息
        author = self.create_author(dynamic_info.name, dynamic_info.avatar)

        # 下载图片
        contents: list[MediaContent] = []
        for image_url in dynamic_info.image_urls:
            img_task = self.downloader.download_img(
                image_url, ext_headers=self.headers, proxy=self.proxy
            )
            contents.append(ImageContent(img_task))

        return self.result(
            title=dynamic_info.title,
            text=dynamic_info.text,
            timestamp=dynamic_info.timestamp,
            author=author,
            contents=contents,
        )

    async def parse_opus(self, opus_id: int):
        """解析图文动态信息

        Args:
            opus_id (int): 图文动态 id
        """
        opus = Opus(opus_id, await self.credential)
        return await self._parse_opus_obj(opus)

    async def parse_read_old(self, read_id: int):
        """解析专栏信息, 已废弃

        Args:
            read_id (int): 专栏 id
        """
        from bilibili_api.article import Article

        article = Article(read_id)
        return await self._parse_opus_obj(await article.turn_to_opus())

    async def _parse_opus_obj(self, bili_opus: Opus):
        """解析图文动态信息

        Args:
            opus_id (int): 图文动态 id

        Returns:
            ParseResult: 解析结果
        """

        from .opus import ImageNode, OpusItem, TextNode

        opus_info = await bili_opus.get_info()
        if not isinstance(opus_info, dict):
            raise ParseException("获取图文动态信息失败")
        # 转换为结构体
        opus_data = convert(opus_info, OpusItem)
        _log.debug(f"opus_data: {opus_data}")
        author = self.create_author(*opus_data.name_avatar)

        # 按顺序处理图文内容（参考 parse_read 的逻辑）
        contents: list[MediaContent] = []
        current_text = ""

        for node in opus_data.gen_text_img():
            if isinstance(node, ImageNode):
                contents.append(
                    self.create_graphics_content(
                        node.url, current_text.strip(), node.alt
                    )
                )
                current_text = ""
            elif isinstance(node, TextNode):
                current_text += node.text

        return self.result(
            title=opus_data.title,
            author=author,
            timestamp=opus_data.timestamp,
            contents=contents,
            text=current_text.strip(),
        )

    async def parse_live(self, room_id: int):
        """解析直播信息

        Args:
            room_id (int): 直播 id

        Returns:
            ParseResult: 解析结果
        """
        from bilibili_api.live import LiveRoom

        from .live import RoomData

        room = LiveRoom(room_display_id=room_id, credential=await self.credential)
        info_dict = await room.get_room_info()

        room_data = convert(info_dict, RoomData)
        contents: list[MediaContent] = []
        # 下载封面
        if cover := room_data.cover:
            cover_task = self.downloader.download_img(
                cover, ext_headers=self.headers, proxy=self.proxy
            )
            contents.append(ImageContent(cover_task))

        # 下载关键帧
        if keyframe := room_data.keyframe:
            keyframe_task = self.downloader.download_img(
                keyframe, ext_headers=self.headers, proxy=self.proxy
            )
            contents.append(ImageContent(keyframe_task))

        author = self.create_author(room_data.name, room_data.avatar)

        url = f"https://www.bilibili.com/blackboard/live/live-activity-player.html?enterTheRoom=0&cid={room_id}"
        return self.result(
            url=url,
            title=room_data.title,
            text=room_data.detail,
            contents=contents,
            author=author,
        )

    async def parse_read(self, read_id: int):
        """专栏解析

        Args:
            read_id (int): 专栏 id

        Returns:
            texts: list[str], urls: list[str]
        """
        from bilibili_api.article import Article

        from .article import ArticleInfo, ImageNode, TextNode

        ar = Article(read_id)

        # 尝试使用 bilibili_api 获取内容
        try:
            await ar.fetch_content()
            data = ar.json()
            article_info = convert(data, ArticleInfo)
            _log.debug(f"article_info: {article_info}")

            contents: list[MediaContent] = []
            current_text = ""
            for child in article_info.gen_text_img():
                if isinstance(child, ImageNode):
                    contents.append(
                        self.create_graphics_content(
                            child.url, current_text.strip(), child.alt
                        )
                    )
                    current_text = ""
                elif isinstance(child, TextNode):
                    current_text += child.text

            author = self.create_author(*article_info.author_info)

            return self.result(
                title=article_info.title,
                timestamp=article_info.timestamp,
                text=current_text.strip(),
                author=author,
                contents=contents,
            )
        except (KeyError, Exception) as e:
            # bilibili_api 解析失败，使用备用API
            _log.warning(f"bilibili_api 解析文章失败: {e}，尝试使用备用API")
            return await self._parse_read_fallback(read_id)

    async def _parse_read_fallback(self, read_id: int):
        """专栏解析备用方法 - 直接调用API"""
        api_url = f"https://api.bilibili.com/x/article/viewinfo?id={read_id}&mobi_app=pc&from=web"

        async with self.client.get(api_url, headers=self.headers) as resp:
            result = await resp.json()

        if result.get("code") != 0:
            raise ParseException(
                f"获取文章信息失败: {result.get('message', '未知错误')}"
            )

        data = result.get("data", {})
        if not data:
            raise ParseException("文章数据为空")

        title = data.get("title", "")
        author_name = data.get("author_name", "")
        author_mid = data.get("mid", 0)
        cover_urls = data.get("origin_image_urls", [])
        stats = data.get("stats", {})

        # 构建描述文本
        desc_parts = []
        if stats:
            view = stats.get("view", 0)
            like = stats.get("like", 0)
            coin = stats.get("coin", 0)
            favorite = stats.get("favorite", 0)
            desc_parts.append(
                f"阅读: {view} | 点赞: {like} | 投币: {coin} | 收藏: {favorite}"
            )

        # 创建内容
        contents: list[MediaContent] = []
        if cover_urls:
            # 使用 create_image_contents 批量创建图片内容
            contents.extend(self.create_image_contents(cover_urls[:3]))

        author = self.create_author(author_mid, author_name, "")

        return self.result(
            title=title,
            text="\n".join(desc_parts) if desc_parts else "",
            author=author,
            contents=contents,
            url=f"https://www.bilibili.com/read/cv{read_id}",
        )

    async def parse_favlist(self, fav_id: int):
        """解析收藏夹信息

        Args:
            fav_id (int): 收藏夹 id

        Returns:
            list[GraphicsContent]: 图文内容列表
        """
        from bilibili_api.favorite_list import get_video_favorite_list_content

        from .favlist import FavData

        # 只会取一页，20 个
        fav_dict = await get_video_favorite_list_content(fav_id)

        if fav_dict["medias"] is None:
            raise ParseException("收藏夹内容为空, 或被风控")

        favdata = convert(fav_dict, FavData)

        return self.result(
            title=favdata.title,
            timestamp=favdata.timestamp,
            author=self.create_author(favdata.info.upper.name, favdata.info.upper.face),
            contents=[
                self.create_graphics_content(fav.cover, fav.desc)
                for fav in favdata.medias
            ],
        )

    async def _get_video(
        self, *, bvid: str | None = None, avid: int | None = None
    ) -> Video:
        """解析视频信息

        Args:
            bvid (str | None): bvid
            avid (int | None): avid
        """
        if avid:
            return Video(aid=avid, credential=await self.credential)
        elif bvid:
            return Video(bvid=bvid, credential=await self.credential)
        else:
            raise ParseException("avid 和 bvid 至少指定一项")

    async def extract_download_urls(
        self,
        video: Video | None = None,
        *,
        bvid: str | None = None,
        avid: int | None = None,
        page_index: int = 0,
    ) -> tuple[str, str | None]:
        """解析视频下载链接

        Args:
            bvid (str | None): bvid
            avid (int | None): avid
            page_index (int): 页索引 = 页码 - 1
        """

        from bilibili_api.video import (
            AudioStreamDownloadURL,
            VideoDownloadURLDataDetecter,
            VideoStreamDownloadURL,
        )

        if video is None:
            video = await self._get_video(bvid=bvid, avid=avid)

        # 获取下载数据
        download_url_data = await video.get_download_url(page_index=page_index)
        detecter = VideoDownloadURLDataDetecter(download_url_data)
        streams = detecter.detect_best_streams(
            video_max_quality=self.video_quality,
            codecs=[self.codecs],
            no_dolby_video=True,
            no_hdr=True,
        )
        video_stream = streams[0]
        if not isinstance(video_stream, VideoStreamDownloadURL):
            raise DownloadException("未找到可下载的视频流")
        _log.debug(
            f"视频流质量: {video_stream.video_quality.name}, 编码: {video_stream.video_codecs}"
        )

        audio_stream = streams[1]
        if not isinstance(audio_stream, AudioStreamDownloadURL):
            return video_stream.url, None
        _log.debug(f"音频流质量: {audio_stream.audio_quality.name}")
        return video_stream.url, audio_stream.url

    def _save_credential(self):
        """存储哔哩哔哩登录凭证"""
        if self._credential is None:
            return

        self._cookies_file.write_text(json.dumps(self._credential.get_cookies()))

    def _load_credential(self):
        """从文件加载哔哩哔哩登录凭证"""
        if not self._cookies_file.exists():
            return

        self._credential = Credential.from_cookies(
            json.loads(self._cookies_file.read_text())
        )

    async def login_with_qrcode(self) -> bytes:
        """通过二维码登录获取哔哩哔哩登录凭证"""
        self._qr_login = QrCodeLogin()
        await self._qr_login.generate_qrcode()

        qr_pic = self._qr_login.get_qrcode_picture()
        return qr_pic.content

    async def check_qr_state(self) -> AsyncGenerator[str, None]:
        """检查二维码登录状态"""
        scan_tip_pending = True

        for _ in range(30):
            state = await self._qr_login.check_state()
            match state:
                case QrCodeLoginEvents.DONE:
                    yield "登录成功"
                    self._credential = self._qr_login.get_credential()
                    self._save_credential()
                    break
                case QrCodeLoginEvents.CONF:
                    if scan_tip_pending:
                        yield "二维码已扫描, 请确认登录"
                        scan_tip_pending = False
                case QrCodeLoginEvents.TIMEOUT:
                    yield "二维码过期, 请重新生成"
                    break
            await asyncio.sleep(2)
        else:
            yield "二维码登录超时, 请重新生成"

    async def _init_credential(self):
        """初始化哔哩哔哩登录凭证"""
        if not self.bili_ck:
            self._load_credential()
            return

        credential = Credential.from_cookies(ck2dict(self.bili_ck))
        if await credential.check_valid():
            _log.info(f"`parser_bili_ck` 有效, 保存到 {self._cookies_file}")
            self._credential = credential
            self._save_credential()
        else:
            _log.info(f"`parser_bili_ck` 已过期, 尝试从 {self._cookies_file} 加载")
            self._load_credential()

    @property
    async def credential(self) -> Credential | None:
        """哔哩哔哩登录凭证"""

        if self._credential is None:
            await self._init_credential()
            return self._credential

        if not await self._credential.check_valid():
            _log.warning("哔哩哔哩凭证已过期, 请重新配置")
            return None

        if await self._credential.check_refresh():
            _log.info("哔哩哔哩凭证需要刷新")
            if self._credential.has_ac_time_value() and self._credential.has_bili_jct():
                await self._credential.refresh()
                _log.info(f"哔哩哔哩凭证刷新成功, 保存到 {self._cookies_file}")
                self._save_credential()
            else:
                _log.warning("哔哩哔哩凭证刷新需要包含 `SESSDATA`, `ac_time_value` 项")

        return self._credential
