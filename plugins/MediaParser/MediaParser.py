"""
MediaParser - 万能链接解析器

整合了 astrbot_plugin_parser 的完整多平台功能。
支持的平台：B站、抖音、微博、小红书、快手、A站、TikTok、Twitter、YouTube、NGA、网易云、Instagram等
"""

import re
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Tuple
from pathlib import Path
from aiohttp import ClientSession

from ncatbot.core import Image, MessageChain, Reply, Text, Video, Record
from ncatbot.plugin_system import NcatBotPlugin, on_message
from ncatbot.utils.logger import get_log

from common.constants.HMMT import HMMT

# 导入签名模块（用于搜索功能）
try:
    from .sign import get_query, get_ticket

    HAS_BILI_SIGN = True
except ImportError:
    HAS_BILI_SIGN = False
    get_query = None
    get_ticket = None

# 导入核心模块
from .core.compat import create_config, ConfigWrapper
from .core.data import ParseResult
from .core.download import Downloader
from .core.render import Renderer
from .core.sender import MessageSender
from .core.debounce import Debouncer
from .core.arbiter import EmojiLikeArbiter, ArbiterContext
from .core.clean import CacheCleaner
from .core.utils import extract_json_url
from .core.parsers import BaseParser, BilibiliParser

# 导入所有解析器以触发注册（通过 BaseParser.get_all_subclass）
from .core.parsers import (  # noqa: F401
    AcfunParser,
    DouyinParser,
    InstagramParser,
    KuaiShouParser,
    NCMParser,
    NGAParser,
    TikTokParser,
    TwitterParser,
    WeiBoParser,
    XiaoHongShuParser,
    YouTubeParser,
)

_log = get_log()


class MediaParser(NcatBotPlugin):
    """万能链接解析器 - 支持多平台"""

    name = "MediaParser"
    version = "2.0"
    author = "Combined from multiple sources"
    description = "万能链接解析器，支持B站、抖音、微博、小红书、快手等十余个平台"

    # 数据目录
    data_dir: Path = Path("data/media_parser")
    cache_dir: Path = Path("data/media_parser/cache")

    # 配置
    config: ConfigWrapper = None

    # 核心组件
    downloader: Downloader = None
    renderer: Renderer = None
    sender: MessageSender = None
    debouncer: Debouncer = None
    arbiter: EmojiLikeArbiter = None
    cleaner: CacheCleaner = None
    _executor: ThreadPoolExecutor = None

    # 解析器映射
    parser_map: Dict[str, BaseParser] = {}
    key_pattern_list: List[Tuple[str, re.Pattern]] = []

    # 禁用的会话
    disabled_sessions: List[str] = []

    # Bot自身ID
    self_id: int = None

    # HTTP headers
    headers: dict = {"User-Agent": HMMT.USER_AGENT}

    # 是否启用搜索功能
    enable_search: bool = True

    async def on_load(self):
        """插件加载"""
        _log.info(f"开始加载 {self.name} 插件 v{self.version}")

        # 创建数据目录
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # 创建配置
        self.config = create_config(self.data_dir, self.cache_dir)

        # 创建线程池
        self._executor = ThreadPoolExecutor(max_workers=2)

        # 初始化下载器
        self.downloader = Downloader(self.config)

        # 初始化渲染器
        self.renderer = Renderer(self.config)

        # 加载渲染器资源
        try:
            await asyncio.get_event_loop().run_in_executor(
                self._executor, Renderer.load_resources
            )
            _log.info("渲染器资源加载完成")
        except Exception as e:
            _log.warning(f"渲染器资源加载失败: {e}")

        # 初始化防抖器
        self.debouncer = Debouncer(self.config)

        # 初始化仲裁器（用于多bot环境）
        self.arbiter = EmojiLikeArbiter()

        # 初始化消息发送器
        self.sender = MessageSender(self.config, self.renderer)

        # 初始化缓存清理器
        try:
            self.cleaner = CacheCleaner(None, self.config)
            _log.info("缓存清理器初始化成功")
        except Exception as e:
            _log.warning(f"缓存清理器初始化失败: {e}")

        # 注册解析器
        self._register_parsers()

        # 获取Bot自身ID
        try:
            login_info = await self.api.get_login_info()
            self.self_id = login_info.get("data", {}).get("user_id")
            _log.info(f"Bot ID: {self.self_id}")
        except Exception as e:
            _log.warning(f"获取Bot ID失败: {e}")

        _log.info(f"{self.name} 插件加载完成")

    def _register_parsers(self):
        """注册所有解析器"""
        # 获取所有解析器类
        all_subclass = BaseParser.get_all_subclass()

        # 过滤启用的平台
        enabled_platforms = self.config.get("enable_platforms", [])
        enabled_classes = [
            cls
            for cls in all_subclass
            if cls.platform.display_name in enabled_platforms
        ]

        # 注册解析器
        platform_names = []
        for cls in enabled_classes:
            try:
                parser = cls(self.config, self.downloader)
                platform_names.append(parser.platform.display_name)
                for keyword, _ in cls._key_patterns:
                    self.parser_map[keyword] = parser
            except Exception as e:
                _log.warning(f"注册解析器 {cls.__name__} 失败: {e}")

        _log.info(f"启用平台: {'、'.join(platform_names)}")

        # 生成关键词-正则对
        patterns: List[Tuple[str, re.Pattern]] = []
        for cls in enabled_classes:
            for kw, pt in cls._key_patterns:
                try:
                    compiled = re.compile(pt) if isinstance(pt, str) else pt
                    patterns.append((kw, compiled))
                except Exception as e:
                    _log.warning(f"编译正则失败 {kw}: {e}")

        # 按关键词长度排序（长优先）
        patterns.sort(key=lambda x: -len(x[0]))
        self.key_pattern_list = patterns

        _log.debug(f"关键词-正则对已生成：{[kw for kw, _ in patterns]}")

    def _get_parser_by_type(self, parser_type):
        """根据类型获取解析器实例"""
        for parser in self.parser_map.values():
            if isinstance(parser, parser_type):
                return parser
        raise ValueError(f"未找到类型为 {parser_type} 的 parser 实例")

    def _extract_at_target(self, message: str) -> int | None:
        """提取消息中第一个@的目标QQ号"""
        # 匹配 [CQ:at,qq=123456] 格式
        at_match = re.search(r"\[CQ:at,qq=(\d+)\]", message)
        if at_match:
            return int(at_match.group(1))
        return None

    # ==================== 消息处理 ====================

    @on_message
    async def handle_parse(self, event):
        """处理链接解析"""
        message = event.raw_message
        group_id = getattr(event, "group_id", None)
        message_id = getattr(event, "message_id", None)
        msg_time = getattr(event, "time", None)

        # 检查是否禁用
        session_id = f"group_{group_id}" if group_id else f"user_{event.user_id}"
        if session_id in self.disabled_sessions:
            return

        # 提取文本
        text = message

        # 检查是否@了其他bot（专门@其他bot的消息不解析）
        at_target = self._extract_at_target(message)
        if at_target and self.self_id and at_target != self.self_id:
            # 消息的第一个@不是本bot，跳过解析
            return

        # 尝试解析 JSON 卡片
        if "[CQ:json" in message or "&#91;CQ:json" in message:
            try:
                json_text = extract_json_url(message)
                if json_text:
                    text = json_text
                    _log.debug(f"从JSON卡片提取URL: {text}")
            except Exception as e:
                _log.debug(f"JSON解析失败: {e}")

        if not text:
            return

        # 核心匹配逻辑
        keyword: str = ""
        searched: re.Match = None

        for kw, pat in self.key_pattern_list:
            if kw not in text:
                continue
            if m := pat.search(text):
                keyword, searched = kw, m
                break

        if searched is None:
            return

        _log.debug(f"匹配结果: {keyword}, {searched.group(0)}")

        # 仲裁机制（多bot环境下决定哪个bot响应）
        # 注意：这需要 ncatbot 支持相关API，如果不支持会跳过
        if group_id and message_id and msg_time and self.self_id:
            try:
                # 尝试获取bot对象进行仲裁
                # 由于ncatbot的API可能不同，这里先跳过仲裁
                # is_win = await self.arbiter.compete(
                #     bot=self.api,
                #     ctx=ArbiterContext(
                #         message_id=message_id,
                #         msg_time=msg_time,
                #         self_id=self.self_id,
                #     ),
                # )
                # if not is_win:
                #     _log.debug("Bot在仲裁中输了, 跳过解析")
                #     return
                pass
            except Exception as e:
                _log.debug(f"仲裁失败，继续解析: {e}")

        # 基于链接防抖
        link = searched.group(0)
        if self.debouncer.hit_link(session_id, link):
            _log.warning(f"[链接防抖] 链接 {link} 在防抖时间内，跳过解析")
            return

        try:
            # 执行解析
            parser = self.parser_map.get(keyword)
            if not parser:
                _log.warning(f"未找到关键词 {keyword} 对应的解析器")
                return

            parse_result = await parser.parse(keyword, searched)

            if not parse_result:
                _log.warning("解析结果为空")
                return

            # 基于资源ID防抖
            resource_id = parse_result.get_resource_id()
            if self.debouncer.hit_resource(session_id, resource_id):
                _log.warning(f"[资源防抖] 资源 {resource_id} 在防抖时间内，跳过发送")
                return

            # 发送解析结果
            await self._send_parse_result(event, parse_result)

        except Exception as e:
            _log.exception(f"解析失败: {e}")
            # 发送错误提示
            if group_id:
                await self.api.post_group_msg(
                    group_id=group_id,
                    rtf=MessageChain(
                        [Reply(event.message_id), Text(f"解析失败: {str(e)[:100]}")]
                    ),
                )

    async def _send_parse_result(self, event, result: ParseResult):
        """发送解析结果"""
        group_id = getattr(event, "group_id", None)

        try:
            # 构建消息部分
            parts = await self.sender.build_message_parts(result)

            # 尝试渲染预览卡片
            preview_card = await self.sender.render_preview_card(result)

            # 构建消息链
            message_chain = []

            # 添加预览卡片
            if preview_card and preview_card.exists():
                message_chain.append(Image(str(preview_card)))

            # 添加文本信息
            text_parts = []
            if result.title:
                text_parts.append(f"标题：{result.title}")
            if result.text:
                # 限制描述长度
                desc = (
                    result.text[:200] + "..." if len(result.text) > 200 else result.text
                )
                text_parts.append(f"简介：{desc}")
            if result.author:
                text_parts.append(f"作者：{result.author.name}")
            if result.url:
                text_parts.append(f"链接：{result.url}")

            if text_parts:
                message_chain.append(Text("\n".join(text_parts)))

            # 添加媒体内容
            for part in parts:
                if part["type"] == "image":
                    message_chain.append(Image(part["data"]))
                elif part["type"] == "video":
                    message_chain.append(Video(part["data"]))
                elif part["type"] == "record":
                    message_chain.append(Record(part["data"]))
                elif part["type"] == "text":
                    message_chain.append(Text(part["data"]))

            # 如果没有内容，添加基本信息
            if not message_chain:
                basic_info = f"[{result.platform.display_name}] "
                if result.title:
                    basic_info += result.title
                if result.url:
                    basic_info += f"\n{result.url}"
                message_chain.append(Text(basic_info))

            # 发送消息
            # video/record 必须单独发送，不能与其他消息混合
            single_only_msgs = [
                m for m in message_chain if isinstance(m, (Video, Record))
            ]
            other_msgs = [
                m for m in message_chain if not isinstance(m, (Video, Record))
            ]

            if group_id:
                # 先发送普通消息（带回复）
                if other_msgs:
                    await self.api.post_group_msg(
                        group_id=group_id,
                        rtf=MessageChain([Reply(event.message_id)] + other_msgs),
                    )
                # 再单独发送每个 video/record
                for single_msg in single_only_msgs:
                    await self.api.post_group_msg(
                        group_id=group_id,
                        rtf=MessageChain([single_msg]),
                    )
            else:
                # 私聊消息
                if other_msgs:
                    await self.api.post_private_msg(
                        user_id=event.user_id, rtf=MessageChain(other_msgs)
                    )
                for single_msg in single_only_msgs:
                    await self.api.post_private_msg(
                        user_id=event.user_id, rtf=MessageChain([single_msg])
                    )

        except Exception as e:
            _log.exception(f"发送解析结果失败: {e}")
            # 发送简化版本
            if group_id:
                simple_msg = (
                    f"[{result.platform.display_name}] {result.title or '解析完成'}"
                )
                if result.url:
                    simple_msg += f"\n{result.url}"
                await self.api.post_group_msg(
                    group_id=group_id,
                    rtf=MessageChain([Reply(event.message_id), Text(simple_msg)]),
                )

    # ==================== 命令处理 ====================

    @on_message
    async def handle_commands(self, event):
        """处理命令"""
        message = event.raw_message.strip()
        group_id = getattr(event, "group_id", None)

        if message == "开启解析":
            session_id = f"group_{group_id}" if group_id else f"user_{event.user_id}"
            if session_id in self.disabled_sessions:
                self.disabled_sessions.remove(session_id)
                response = "解析已开启"
            else:
                response = "解析已开启，无需重复开启"

            if group_id:
                await self.api.post_group_msg(
                    group_id=group_id, rtf=MessageChain([Text(response)])
                )
            else:
                await self.api.post_private_msg(
                    user_id=event.user_id, rtf=MessageChain([Text(response)])
                )

        elif message == "关闭解析":
            session_id = f"group_{group_id}" if group_id else f"user_{event.user_id}"
            if session_id not in self.disabled_sessions:
                self.disabled_sessions.append(session_id)
                response = "解析已关闭"
            else:
                response = "解析已关闭，无需重复关闭"

            if group_id:
                await self.api.post_group_msg(
                    group_id=group_id, rtf=MessageChain([Text(response)])
                )
            else:
                await self.api.post_private_msg(
                    user_id=event.user_id, rtf=MessageChain([Text(response)])
                )

        elif message == "解析帮助" or message == "解析状态":
            # 显示支持的平台
            platforms = self.config.get("enable_platforms", [])
            response = f"MediaParser v{self.version}\n"
            response += f"支持的平台：{'、'.join(platforms)}\n"
            response += "命令：\n"
            response += "• 开启解析 - 开启当前会话的解析\n"
            response += "• 关闭解析 - 关闭当前会话的解析\n"
            response += "• 登录B站 - 扫码登录B站\n"
            if self.enable_search and HAS_BILI_SIGN:
                response += "• 搜视频 关键词 - 搜索B站视频"

            if group_id:
                await self.api.post_group_msg(
                    group_id=group_id, rtf=MessageChain([Text(response)])
                )
            else:
                await self.api.post_private_msg(
                    user_id=event.user_id, rtf=MessageChain([Text(response)])
                )

        elif message in ["登录B站", "登录b站", "blogin"]:
            # B站扫码登录
            await self._handle_bilibili_login(event, group_id)

        # 搜索视频功能
        elif (
            self.enable_search
            and HAS_BILI_SIGN
            and (
                message.startswith("搜视频")
                or message.startswith("查询视频")
                or message.startswith("搜索视频")
            )
        ):
            if message.startswith("搜视频"):
                keyword = message[3:].strip()
            elif message.startswith("查询视频"):
                keyword = message[4:].strip()
            elif message.startswith("搜索视频"):
                keyword = message[4:].strip()
            else:
                keyword = ""

            if keyword:
                await self._handle_search_video(event, group_id, keyword)
            else:
                response = "请输入搜索关键词，例如：搜视频 原神"
                if group_id:
                    await self.api.post_group_msg(
                        group_id=group_id, rtf=MessageChain([Text(response)])
                    )
                else:
                    await self.api.post_private_msg(
                        user_id=event.user_id, rtf=MessageChain([Text(response)])
                    )

    async def _handle_bilibili_login(self, event, group_id):
        """处理B站扫码登录"""
        try:
            parser: BilibiliParser = self._get_parser_by_type(BilibiliParser)

            # 获取二维码
            qrcode_bytes = await parser.login_with_qrcode()

            # 发送二维码图片
            # 将bytes转为临时文件
            qrcode_path = self.cache_dir / "bilibili_qrcode.png"
            with open(qrcode_path, "wb") as f:
                f.write(qrcode_bytes)

            if group_id:
                await self.api.post_group_msg(
                    group_id=group_id,
                    rtf=MessageChain(
                        [
                            Reply(event.message_id),
                            Text("请使用哔哩哔哩APP扫描下方二维码登录：\n"),
                            Image(str(qrcode_path)),
                        ]
                    ),
                )
            else:
                await self.api.post_private_msg(
                    user_id=event.user_id,
                    rtf=MessageChain(
                        [
                            Text("请使用哔哩哔哩APP扫描下方二维码登录：\n"),
                            Image(str(qrcode_path)),
                        ]
                    ),
                )

            # 轮询检查登录状态
            async for msg in parser.check_qr_state():
                if group_id:
                    await self.api.post_group_msg(
                        group_id=group_id, rtf=MessageChain([Text(msg)])
                    )
                else:
                    await self.api.post_private_msg(
                        user_id=event.user_id, rtf=MessageChain([Text(msg)])
                    )

        except ValueError as e:
            error_msg = f"登录失败: {e}"
            if group_id:
                await self.api.post_group_msg(
                    group_id=group_id, rtf=MessageChain([Text(error_msg)])
                )
            else:
                await self.api.post_private_msg(
                    user_id=event.user_id, rtf=MessageChain([Text(error_msg)])
                )
        except Exception as e:
            _log.exception(f"B站登录失败: {e}")
            error_msg = f"登录过程中发生错误: {str(e)[:50]}"
            if group_id:
                await self.api.post_group_msg(
                    group_id=group_id, rtf=MessageChain([Text(error_msg)])
                )

    async def _handle_search_video(self, event, group_id, keyword: str):
        """处理搜索视频命令"""
        try:
            _log.info(f"搜索视频: {keyword}")

            async with ClientSession(headers=self.headers) as session:
                # 先访问B站主站获取必要的cookie
                mainsite_url = "https://www.bilibili.com"
                async with session.get(mainsite_url) as resp:
                    if resp.status != 200:
                        raise Exception("无法访问B站主站")

                # 获取签名参数
                query = await get_query({"keyword": keyword})
                search_url = f"https://api.bilibili.com/x/web-interface/wbi/search/all/v2?{query}"

                # 获取 bili_ticket
                bili_ticket = await get_ticket()
                session.cookie_jar.update_cookies({"bili_ticket": bili_ticket})

                async with session.get(search_url) as resp:
                    result = await resp.json()

                if result["code"] == -412:
                    _log.warning(f"搜索被风控: {result}")
                    response = "搜索请求被风控，请稍后重试"
                    if group_id:
                        await self.api.post_group_msg(
                            group_id=group_id,
                            rtf=MessageChain([Reply(event.message_id), Text(response)]),
                        )
                    return

                if result["code"] != 0:
                    response = f"搜索失败: {result.get('message', '未知错误')}"
                    if group_id:
                        await self.api.post_group_msg(
                            group_id=group_id,
                            rtf=MessageChain([Reply(event.message_id), Text(response)]),
                        )
                    return

                # 查找视频结果
                video_url = None
                for item in result.get("data", {}).get("result", []):
                    if item.get("result_type") == "video":
                        data = item.get("data", [])
                        if data:
                            video_url = data[0].get("arcurl")
                            break

                if not video_url:
                    response = f"未找到与「{keyword}」相关的视频"
                    if group_id:
                        await self.api.post_group_msg(
                            group_id=group_id,
                            rtf=MessageChain([Reply(event.message_id), Text(response)]),
                        )
                    return

                # 找到视频后，触发解析
                _log.info(f"搜索到视频: {video_url}")

                # 尝试用解析器解析
                for kw, pat in self.key_pattern_list:
                    if kw in video_url:
                        if m := pat.search(video_url):
                            parser = self.parser_map.get(kw)
                            if parser:
                                parse_result = await parser.parse(kw, m)
                                if parse_result:
                                    await self._send_parse_result(event, parse_result)
                                    return

                # 如果无法解析，直接返回链接
                response = f"搜索结果：{video_url}"
                if group_id:
                    await self.api.post_group_msg(
                        group_id=group_id,
                        rtf=MessageChain([Reply(event.message_id), Text(response)]),
                    )

        except Exception as e:
            _log.exception(f"搜索视频失败: {e}")
            error_msg = f"搜索失败: {str(e)[:50]}"
            if group_id:
                await self.api.post_group_msg(
                    group_id=group_id,
                    rtf=MessageChain([Reply(event.message_id), Text(error_msg)]),
                )
            else:
                await self.api.post_private_msg(
                    user_id=event.user_id, rtf=MessageChain([Text(error_msg)])
                )

    async def on_unload(self):
        """插件卸载"""
        _log.info(f"开始卸载 {self.name} 插件")

        # 关闭下载器
        if self.downloader:
            try:
                await self.downloader.close()
            except Exception as e:
                _log.warning(f"关闭下载器失败: {e}")

        # 关闭所有解析器的会话
        if self.parser_map:
            unique_parsers = set(self.parser_map.values())
            for parser in unique_parsers:
                try:
                    await parser.close_session()
                except Exception as e:
                    _log.warning(f"关闭解析器会话失败: {e}")

        # 关闭缓存清理器
        if self.cleaner:
            try:
                await self.cleaner.stop()
            except Exception as e:
                _log.warning(f"关闭缓存清理器失败: {e}")

        # 关闭线程池
        if self._executor:
            self._executor.shutdown(wait=False)

        _log.info(f"{self.name} 插件已卸载")
