import re
from time import localtime, strftime
from typing import Dict, List, Optional, Tuple, Union
from aiohttp import ClientSession

from ncatbot.core import Image, MessageChain, Reply, Text
from ncatbot.plugin_system import NcatBotPlugin, on_message
from ncatbot.utils.logger import get_log
from common.constants.HMMT import HMMT
from .ExpiringCache import ExpiringCache
from .sign import get_query, get_ticket

_log = get_log()


class BilibiliAnalysis(NcatBotPlugin):
    name = "BilibiliAnalysis"
    version = "1.0"
    author = "Adapted from nonebot_plugin_analysis_bilibili"
    description = (
        "自动解析bilibili链接内容，支持视频、番剧、直播、文章、动态、个人空间、会员购等"
    )

    async def on_load(self):
        """异步加载插件"""
        _log.info(f"开始加载 {self.name} 插件 v{self.version}")

        # 缓存系统：{group_id: ExpiringCache}
        self.analysis_stat: Dict[int, ExpiringCache] = {}

        # 配置选项
        self.display_image = getattr(self.config, "analysis_display_image", True)
        self.display_image_list = getattr(
            self.config,
            "analysis_display_image_list",
            ["video", "bangumi", "live", "article", "dynamic", "space", "mall"],
        )
        self.images_size = getattr(self.config, "analysis_images_size", "")
        self.cover_images_size = getattr(self.config, "analysis_cover_images_size", "")
        self.reanalysis_time = getattr(self.config, "analysis_reanalysis_time", 0)
        self.enable_search = getattr(self.config, "analysis_enable_search", True)
        self.desc_blacklist = getattr(self.config, "analysis_desc_blacklist", [])
        self.trust_env = getattr(self.config, "analysis_trust_env", False)

        # HTTP headers
        self.headers = {"User-Agent": HMMT.USER_AGENT}

        # Bilibili链接匹配模式
        self.pattern = (
            r"^(?:(?:av|cv)\d+|BV[a-zA-Z0-9]{10})|"
            r"(?:b23\.tv|bili(?:22|23|33|2233)\.cn|\.bilibili\.com|space\.bilibili\.com|mall\.bilibili\.com|bilibili\.com/opus/\d+|QQ小程序(?:&amp;#93;|&#93;|\])哔哩哔哩).{0,500}"
        )

        _log.info(f"{self.name} 插件加载完成")

    def resize_image(self, src: str, is_cover=False) -> str:
        """调整图片大小"""
        if not src:
            return ""
        img_type = src[-3:]
        if self.cover_images_size and is_cover:
            return f"{src}@{self.cover_images_size}.{img_type}"
        if self.images_size:
            return f"{src}@{self.images_size}.{img_type}"
        return src

    def handle_num(self, num: int) -> str:
        """处理超过一万的数字"""
        if num > 10000:
            return f"{num / 10000:.2f}万"
        return str(num)

    def extract_card_info(self, text: str) -> dict:
        """从QQ分享卡片中提取信息"""
        card_info = {}
        try:
            # 尝试提取 JSON 卡片中的信息
            # 标题
            title_match = re.search(r'"title":"([^"]+)"', text)
            if title_match:
                card_info["title"] = title_match[1].replace("&#44;", ",")

            # 描述
            desc_match = re.search(r'"desc":"([^"]+)"', text)
            if desc_match:
                card_info["desc"] = desc_match[1].replace("&#44;", ",")

            # 预览图
            preview_match = re.search(r'"preview":"([^"]+)"', text)
            if preview_match:
                card_info["preview"] = preview_match[1].replace("&amp;", "&")

            # prompt（分享提示）
            prompt_match = re.search(r'"prompt":"([^"]+)"', text)
            if prompt_match:
                card_info["prompt"] = (
                    prompt_match[1]
                    .replace("&#91;", "[")
                    .replace("&#93;", "]")
                    .replace("&#44;", ",")
                )

            _log.info(f"从卡片中提取的信息: {card_info}")
        except Exception as e:
            _log.warning(f"提取卡片信息失败: {e}")
        return card_info

    def _build_space_fallback_msg(
        self, mid: str, vurl: str, card_info: dict
    ) -> Tuple[List[str], str]:
        """构建个人空间的备用消息（当API失败时使用卡片信息）"""
        has_image = self.display_image or "space" in self.display_image_list

        # 从卡片信息中提取
        title = card_info.get("title", "用户空间")
        preview = card_info.get("preview", "") if has_image else ""

        # 构建消息
        msg = []
        if preview:
            msg.append(preview)
            msg.append("\n")
        msg.append(f"UP主：{title}\n")
        msg.append(f"UID：{mid}\n")
        msg.append(f"主页：{vurl}\n")

        return msg, vurl

    def _build_mall_fallback_msg(
        self, item_id: str, vurl: str, card_info: dict
    ) -> Tuple[List[str], str]:
        """构建会员购的备用消息（当API失败时使用卡片信息）"""
        has_image = self.display_image or "mall" in self.display_image_list

        # 从卡片信息中提取
        title = card_info.get("title", "会员购商品")
        preview = card_info.get("preview", "") if has_image else ""
        desc = card_info.get("desc", "")

        # 构建消息
        msg = []
        if preview:
            msg.append(preview)
            msg.append("\n")
        msg.append(f"商品：{title}\n")
        if desc:
            msg.append(f"简介：{desc}\n")
        msg.append(f"链接：{vurl}\n")

        return msg, vurl

    def extract(self, text: str) -> Tuple[str, Optional[str], Optional[str]]:
        """提取Bilibili链接信息"""
        try:
            _log.info(f"开始提取链接信息: {text}")
            url = ""
            # 视频分p
            page = re.compile(r"([?&]|&amp;)p=\d+").search(text)
            # 视频播放定位时间
            time = re.compile(r"([?&]|&amp;)t=\d+").search(text)
            # 主站视频 av 号
            aid = re.compile(r"av\d+").search(text)
            # 主站视频 bv 号
            bvid = re.compile(r"BV([A-Za-z0-9]{10})+").search(text)
            # 番剧视频页
            epid = re.compile(r"ep\d+").search(text)
            # 番剧剧集ssid(season_id)
            ssid = re.compile(r"ss\d+").search(text)
            # 番剧详细页
            mdid = re.compile(r"md\d+").search(text)
            # 直播间
            room_id = re.compile(r"live.bilibili.com/(blanc/|h5/)?(\d+)").search(text)
            # 文章
            cvid = re.compile(r"(/read/(cv|mobile|native)(/|\?id=)?|^cv)(\d+)").search(
                text
            )
            # 动态
            dynamic_id_type2 = re.compile(
                r"(t|m).bilibili.com/(\d+)\?(.*?)(&|&amp;)type=2"
            ).search(text)
            # 动态
            dynamic_id = re.compile(r"(t|m).bilibili.com/(opus/)?(\d+)").search(text)
            # 新增：opus 动态链接
            opus_id = re.compile(r"bilibili\.com/opus/(\d+)").search(text)
            # 个人空间主页
            space_id = re.compile(r"space\.bilibili\.com/(\d+)").search(text)
            # 会员购商品
            mall_item_id = re.compile(r"mall\.bilibili\.com.*[?&]itemsId=(\d+)").search(
                text
            )

            _log.info(
                f"匹配结果 - aid: {aid}, bvid: {bvid}, epid: {epid}, ssid: {ssid}, mdid: {mdid}"
            )
            _log.info(
                f"匹配结果 - room_id: {room_id}, cvid: {cvid}, dynamic_id_type2: {dynamic_id_type2}"
            )
            _log.info(f"匹配结果 - dynamic_id: {dynamic_id}, opus_id: {opus_id}")
            _log.info(f"匹配结果 - space_id: {space_id}, mall_item_id: {mall_item_id}")

            if bvid:
                url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid[0]}"
            elif aid:
                url = f"https://api.bilibili.com/x/web-interface/view?aid={aid[0][2:]}"
            elif epid:
                url = (
                    f"https://api.bilibili.com/pgc/view/web/season?ep_id={epid[0][2:]}"
                )
            elif ssid:
                url = f"https://api.bilibili.com/pgc/view/web/season?season_id={ssid[0][2:]}"
            elif mdid:
                url = f"https://api.bilibili.com/pgc/review/user?media_id={mdid[0][2:]}"
            elif room_id:
                url = f"https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom?room_id={room_id[2]}"
            elif cvid:
                page = cvid[4]
                url = f"https://api.bilibili.com/x/article/viewinfo?id={page}&mobi_app=pc&from=web"
            elif dynamic_id_type2:
                url = f"https://api.bilibili.com/x/polymer/web-dynamic/v1/detail?rid={dynamic_id_type2[2]}&type=2"
            elif dynamic_id:
                url = f"https://api.bilibili.com/x/polymer/web-dynamic/v1/detail?id={dynamic_id[3]}"
            elif opus_id:
                url = f"https://api.bilibili.com/x/polymer/web-dynamic/v1/detail?id={opus_id[1]}"
            elif space_id:
                url = f"https://api.bilibili.com/x/space/wbi/acc/info?mid={space_id[1]}"
            elif mall_item_id:
                url = f"https://api.bilibili.com/mall/v2/h5/sku/info?itemsId={mall_item_id[1]}"

            _log.info(f"最终提取的URL: {url}")
            return url, page, time
        except Exception as e:
            _log.exception(f"提取链接时出错: {e}")
            return "", None, None

    async def b23_extract(self, text: str, session: ClientSession) -> str:
        """提取b23短链接"""
        b23 = re.compile(r"b23.tv/(\w+)|(bili(22|23|33|2233).cn)/(\w+)").search(
            text.replace("\\", "")
        )
        url = f"https://{b23[0]}"

        async with session.get(url) as resp:
            return str(resp.url)

    async def search_bili_by_title(self, title: str, session: ClientSession) -> str:
        """通过标题搜索Bilibili视频"""
        # set headers
        mainsite_url = "https://www.bilibili.com"
        async with session.get(mainsite_url) as resp:
            assert resp.status == 200

        query = await get_query({"keyword": title})
        search_url = (
            f"https://api.bilibili.com/x/web-interface/wbi/search/all/v2?{query}"
        )

        bili_ticket = await get_ticket()
        session.cookie_jar.update_cookies({"bili_ticket": bili_ticket})

        async with session.get(search_url) as resp:
            result = await resp.json()

        if result["code"] == -412:
            _log.warning(f"analysis_bilibili: {result}")
            return ""

        for i in result["data"]["result"]:
            if i.get("result_type") != "video":
                continue
            # 只返回第一个结果
            return i["data"][0].get("arcurl")

        return ""

    async def video_detail(
        self, url: str, session: ClientSession, **kwargs
    ) -> Tuple[List[str], str]:
        """获取视频详细信息"""
        try:
            async with session.get(url) as resp:
                res = (await resp.json()).get("data")
                if not res:
                    return "解析到视频被删了/稿件不可见或审核中/权限不足", url
            vurl = f"https://www.bilibili.com/video/av{res['aid']}"
            title = f"\n标题：{res['title']}\n"

            has_image = False
            if self.display_image or "video" in self.display_image_list:
                has_image = True

            cover = self.resize_image(res["pic"]) if has_image else ""
            vurl = "\n" + vurl if cover else vurl
            if page := kwargs.get("page"):
                page = page[0].replace("&amp;", "&")
                p = int(page[3:])
                if p <= len(res["pages"]):
                    vurl += f"?p={p}"
                    part = res["pages"][p - 1]["part"]
                    if part != res["title"]:
                        title += f"小标题：{part}\n"
            if time_location := kwargs.get("time_location"):
                time_location = time_location[0].replace("&amp;", "&")[3:]
                if page:
                    vurl += f"&t={time_location}"
                else:
                    vurl += f"?t={time_location}"
            pubdate = strftime("%Y-%m-%d %H:%M:%S", localtime(res["pubdate"]))
            tname = (
                f"类型：{res['tname']} | UP：{res['owner']['name']} | 日期：{pubdate}\n"
            )
            stat = f"播放：{self.handle_num(res['stat']['view'])} | 弹幕：{self.handle_num(res['stat']['danmaku'])} | 收藏：{self.handle_num(res['stat']['favorite'])}\n"
            stat += f"点赞：{self.handle_num(res['stat']['like'])} | 硬币：{self.handle_num(res['stat']['coin'])} | 评论：{self.handle_num(res['stat']['reply'])}\n"
            desc = f"简介：{res['desc']}"
            desc_list = desc.split("\n")
            desc = "".join(i + "\n" for i in desc_list if i)
            desc_list = desc.split("\n")
            if len(desc_list) > 4:
                desc = desc_list[0] + "\n" + desc_list[1] + "\n" + desc_list[2] + "……"
            msg = [cover, vurl, title, tname, stat, desc]
            return msg, vurl
        except Exception as e:
            msg = "视频解析出错--Error: {}".format(type(e))
            return msg, None

    async def bangumi_detail(
        self, url: str, time_location: str, session: ClientSession
    ) -> Tuple[List[str], str]:
        """获取番剧详细信息"""
        try:
            is_media = False
            if "media_id" in url:
                is_media = True
                async with session.get(url) as resp:
                    ssid = (
                        (await resp.json()).get("result").get("media").get("season_id")
                    )
                    if not ssid:
                        return None, None
                url = f"https://api.bilibili.com/pgc/view/web/season?season_id={ssid}"

            async with session.get(url) as resp:
                res = (await resp.json()).get("result")
                if not res:
                    return None, None

            has_image = False
            if self.display_image or "bangumi" in self.display_image_list:
                has_image = True

            cover = self.resize_image(res["cover"], is_cover=True) if has_image else ""
            title = f"番剧：{res['title']}\n"
            desc = f"{res['new_ep']['desc']}\n"
            long_title = ""
            styles = "".join(f"{i}," for i in res["styles"])
            styles = f"类型：{styles[:-1]}\n"
            evaluate = f"简介：{res['evaluate']}\n"
            if is_media:
                vurl = f"https://www.bilibili.com/bangumi/media/md{res['media_id']}"
            elif "season_id" in url:
                vurl = f"https://www.bilibili.com/bangumi/play/ss{res['season_id']}"
            else:
                epid = re.compile(r"ep_id=\d+").search(url)[0][len("ep_id=") :]
                for i in res["episodes"]:
                    if str(i["ep_id"]) == epid:
                        long_title = f"标题：{i['long_title']}\n"
                        break
                vurl = f"https://www.bilibili.com/bangumi/play/ep{epid}"
            if time_location:
                time_location = time_location[0].replace("&amp;", "&")[3:]
                vurl += f"?t={time_location}"
            vurl = "\n" + vurl if cover else vurl
            msg = [cover, f"{vurl}\n", title, long_title, desc, styles, evaluate]
            return msg, vurl
        except Exception as e:
            msg = "番剧解析出错--Error: {}".format(type(e))
            msg += f"\n{url}"
            return msg, None

    async def live_detail(
        self, url: str, session: ClientSession
    ) -> Tuple[List[str], str]:
        """获取直播详细信息"""
        try:
            async with session.get(url) as resp:
                res = await resp.json()
                if res["code"] != 0:
                    return None, None
            res = res["data"]
            uname = res["anchor_info"]["base_info"]["uname"]
            room_id = res["room_info"]["room_id"]
            title = res["room_info"]["title"]

            has_image = False
            if self.display_image or "live" in self.display_image_list:
                has_image = True

            cover = (
                self.resize_image(res["room_info"]["cover"], is_cover=True)
                if has_image
                else ""
            )
            live_status = res["room_info"]["live_status"]
            lock_status = res["room_info"]["lock_status"]
            parent_area_name = res["room_info"]["parent_area_name"]
            area_name = res["room_info"]["area_name"]
            online = res["room_info"]["online"]
            tags = res["room_info"]["tags"]
            watched_show = res["watched_show"]["text_large"]
            vurl = f"https://live.bilibili.com/{room_id}\n"
            if lock_status:
                lock_time = res["room_info"]["lock_time"]
                lock_time = strftime("%Y-%m-%d %H:%M:%S", localtime(lock_time))
                title = f"[已封禁]直播间封禁至：{lock_time}\n"
            elif live_status == 1:
                title = f"[直播中]标题：{title}\n"
            elif live_status == 2:
                title = f"[轮播中]标题：{title}\n"
            else:
                title = f"[未开播]标题：{title}\n"
            up = f"主播：{uname}  当前分区：{parent_area_name}-{area_name}\n"
            watch = f"观看：{watched_show}  直播时的人气上一次刷新值：{self.handle_num(online)}\n"
            if tags:
                tags = f"标签：{tags}\n"
            if live_status:
                player = f"独立播放器：https://www.bilibili.com/blackboard/live/live-activity-player.html?enterTheRoom=0&cid={room_id}"
            else:
                player = ""
            vurl = "\n" + vurl if cover else vurl
            msg = [cover, vurl, title, up, watch, tags, player]
            return msg, vurl
        except Exception as e:
            msg = "直播间解析出错--Error: {}".format(type(e))
            return msg, None

    async def article_detail(
        self, url: str, cvid: str, session: ClientSession
    ) -> Tuple[List[Union[List[str], str]], str]:
        """获取文章详细信息"""
        try:
            async with session.get(url) as resp:
                res = (await resp.json()).get("data")
                if not res:
                    return None, None

            has_image = False
            if self.display_image or "article" in self.display_image_list:
                has_image = True

            images = (
                [self.resize_image(i) for i in res["origin_image_urls"]]
                if has_image
                else []
            )
            vurl = f"https://www.bilibili.com/read/cv{cvid}"
            title = f"标题：{res['title']}\n"
            up = f"作者：{res['author_name']} (https://space.bilibili.com/{res['mid']})\n"
            view = f"阅读数：{self.handle_num(res['stats']['view'])} "
            favorite = f"收藏数：{self.handle_num(res['stats']['favorite'])} "
            coin = f"硬币数：{self.handle_num(res['stats']['coin'])}"
            share = f"分享数：{self.handle_num(res['stats']['share'])} "
            like = f"点赞数：{self.handle_num(res['stats']['like'])} "
            dislike = f"不喜欢数：{self.handle_num(res['stats']['dislike'])}"
            desc = view + favorite + coin + "\n" + share + like + dislike + "\n"
            msg = [images, title, up, desc, vurl]
            return msg, vurl
        except Exception as e:
            msg = "专栏解析出错--Error: {}".format(type(e))
            return msg, None

    async def dynamic_detail(
        self, url: str, session: ClientSession
    ) -> Tuple[List[Union[List[str], str]], str]:
        """获取动态详细信息"""
        try:
            _log.info(f"开始解析动态: {url}")
            async with session.get(url) as resp:
                res = await resp.json()
                _log.info(f"动态API响应: {res}")
                if res["code"] != 0:
                    _log.warning(f"动态API返回错误码: {res['code']}")
                    return None, None
            res = res.get("data").get("item")
            if not res:
                _log.warning("动态数据为空")
                return None, None

            dynamic_id = res["id_str"]
            vurl = f"https://t.bilibili.com/{dynamic_id}\n"

            # 动态内容
            module_dynamic = res["modules"]["module_dynamic"]
            module_type = res["type"]
            _log.info(f"动态类型: {module_type}")

            # 文字信息
            desc = module_dynamic["desc"] if module_dynamic["desc"] else {"text": ""}
            content = desc.get("text", "").replace("\r", "\n").replace("\n\n", "\n")
            _log.info(f"动态内容: {content}")

            has_image = False
            if self.display_image or "dynamic" in self.display_image_list:
                has_image = True

            # 额外信息(会员购)
            additional_msg = []
            additional = module_dynamic.get("additional")
            if isinstance(additional, dict):
                additional_type = additional.get("type")
                if additional_type == "ADDITIONAL_TYPE_GOODS":
                    items = additional.get("goods", {}).get("items", [])
                    for item in items:
                        additional_msg.append(
                            f"{item.get('name')}（{item.get('price')}）\n"
                        )

            # DRAW图片/ARCHIVE转发视频/null纯文字
            draws = []
            archive_cover = ""
            archive_msg = ""
            split = "\n----------------------------------------\n"
            major = module_dynamic["major"]
            if isinstance(major, dict):
                if module_type == "DYNAMIC_TYPE_DRAW":
                    split = split if additional_msg else ""
                    if has_image:
                        draws = [
                            self.resize_image(i.get("src"))
                            for i in major.get("draw").get("items", [])
                        ]
                    else:
                        items_len = len(major.get("draw").get("items", []))
                        content += f"\nPS：动态中包含{items_len}张图片"

                elif module_type == "DYNAMIC_TYPE_AV":
                    jump_url = major.get("archive").get("jump_url")
                    archive_cover = (
                        self.resize_image(major.get("archive").get("cover"))
                        if has_image
                        else ""
                    )
                    archive_msg += f"转发视频：https:{jump_url}\n"
                    archive_msg += f"简介：{major.get('archive').get('desc')}"

                elif module_type == "DYNAMIC_TYPE_ARTICLE":
                    # 处理文章类型动态
                    article = major.get("article", {})
                    if article:
                        title = article.get("title", "")
                        desc_text = article.get("desc", "")
                        jump_url = article.get("jump_url", "")
                        label = article.get("label", "")

                        if title:
                            content = f"文章标题：{title}\n"
                        if desc_text:
                            content += f"文章简介：{desc_text}\n"
                        if label:
                            content += f"阅读量：{label}\n"
                        if jump_url:
                            archive_msg += f"文章链接：https:{jump_url}\n"

                        # 处理文章封面
                        covers = article.get("covers", [])
                        if covers and has_image:
                            archive_cover = self.resize_image(covers[0])

            elif module_type == "DYNAMIC_TYPE_FORWARD":
                desc = module_dynamic["desc"]
                orig_id = res.get("orig").get("id_str")
                archive_msg += f"转发动态：https://t.bilibili.com/{orig_id}\n"
            else:
                split = ""

            msg = [
                content,
                draws,
                split,
                archive_cover,
                archive_msg,
                additional_msg,
                f"\n动态链接：{vurl}",
            ]
            _log.info(f"动态解析结果: {msg}")
            return msg, vurl
        except Exception as e:
            msg = "动态解析出错--Error: {}".format(type(e))
            _log.exception(e)
            return msg, None

    async def space_detail(
        self, url: str, session: ClientSession, card_info: dict = None
    ) -> Tuple[List[Union[List[str], str]], str]:
        """获取用户个人空间详细信息"""
        if card_info is None:
            card_info = {}
        try:
            _log.info(f"开始解析个人空间: {url}")

            # 从URL中提取mid
            mid_match = re.search(r"mid=(\d+)", url)
            if not mid_match:
                return "无法提取用户ID", None
            mid = mid_match[1]

            vurl = f"https://space.bilibili.com/{mid}"

            # 先访问主站获取必要的cookie
            mainsite_url = "https://www.bilibili.com"
            async with session.get(mainsite_url) as resp:
                assert resp.status == 200

            # 获取bili_ticket
            bili_ticket = await get_ticket()
            session.cookie_jar.update_cookies({"bili_ticket": bili_ticket})

            # 使用wbi签名
            query = await get_query({"mid": mid})
            signed_url = f"https://api.bilibili.com/x/space/wbi/acc/info?{query}"
            _log.info(f"签名后的URL: {signed_url}")

            async with session.get(signed_url) as resp:
                res = await resp.json()
                _log.info(f"个人空间API响应: {res}")
                if res["code"] != 0:
                    _log.warning(f"个人空间API返回错误: {res}")
                    # API失败，使用卡片信息作为备用
                    return self._build_space_fallback_msg(mid, vurl, card_info)

            data = res.get("data")
            if not data:
                return self._build_space_fallback_msg(mid, vurl, card_info)

            has_image = False
            if self.display_image or "space" in self.display_image_list:
                has_image = True

            # 头像
            face = self.resize_image(data.get("face", "")) if has_image else ""

            # 用户名
            name = data.get("name", "未知")
            # 性别
            sex = data.get("sex", "保密")
            # 签名
            sign = data.get("sign", "这个人很懒，什么都没写~")
            # 等级
            level = data.get("level", 0)
            # 生日（可能为空）
            birthday = data.get("birthday", "")

            # 获取粉丝数、关注数等统计信息
            stat_url = f"https://api.bilibili.com/x/relation/stat?vmid={mid}"
            async with session.get(stat_url) as resp:
                stat_res = await resp.json()
                follower = 0
                following = 0
                if stat_res.get("code") == 0 and stat_res.get("data"):
                    follower = stat_res["data"].get("follower", 0)
                    following = stat_res["data"].get("following", 0)

            # 获取投稿数
            upstat_url = f"https://api.bilibili.com/x/space/upstat?mid={mid}"
            async with session.get(upstat_url) as resp:
                upstat_res = await resp.json()
                archive_view = 0
                article_view = 0
                if upstat_res.get("code") == 0 and upstat_res.get("data"):
                    archive_view = upstat_res["data"].get("archive", {}).get("view", 0)
                    article_view = upstat_res["data"].get("article", {}).get("view", 0)

            # 构建消息
            title = f"UP主：{name}\n"
            info = f"性别：{sex} | 等级：Lv{level}"
            if birthday:
                info += f" | 生日：{birthday}"
            info += "\n"
            stat = f"粉丝：{self.handle_num(follower)} | 关注：{self.handle_num(following)}\n"
            view_stat = f"视频播放：{self.handle_num(archive_view)} | 文章阅读：{self.handle_num(article_view)}\n"
            desc = f"签名：{sign}\n" if sign else ""

            vurl_display = "\n" + vurl if face else vurl
            msg = [face, vurl_display, "\n", title, info, stat, view_stat, desc]
            return msg, vurl
        except Exception as e:
            _log.exception(f"个人空间解析出错: {e}")
            # 即使出错也使用卡片信息返回
            mid_match = re.search(r"mid=(\d+)", url)
            if mid_match:
                mid = mid_match[1]
                vurl = f"https://space.bilibili.com/{mid}"
                return self._build_space_fallback_msg(mid, vurl, card_info)
            return "个人空间解析出错", None

    async def mall_detail(
        self, url: str, session: ClientSession, card_info: dict = None
    ) -> Tuple[List[Union[List[str], str]], str]:
        """获取会员购商品详细信息"""
        if card_info is None:
            card_info = {}
        try:
            _log.info(f"开始解析会员购商品: {url}")

            # 从URL中提取itemsId
            item_id_match = re.search(r"itemsId=(\d+)", url)
            if not item_id_match:
                return "无法提取商品ID", None
            item_id = item_id_match[1]

            vurl = f"https://mall.bilibili.com/detail.html?itemsId={item_id}"

            data = None
            # 尝试多个API
            api_urls = [
                f"https://mall.bilibili.com/mall-up-c/up_shop/open/getItem?itemsId={item_id}",
                f"https://mall.bilibili.com/mall-c/common/item/getItemInfo?itemsId={item_id}",
            ]

            for api_url in api_urls:
                try:
                    _log.info(f"尝试会员购API: {api_url}")
                    async with session.get(api_url) as resp:
                        if resp.status != 200:
                            _log.warning(f"API返回状态码: {resp.status}")
                            continue
                        res = await resp.json()
                        _log.info(f"会员购API响应: {res}")
                        if res.get("code") == 0 and res.get("data"):
                            data = res.get("data")
                            break
                except Exception as e:
                    _log.warning(f"API调用失败: {api_url}, 错误: {e}")
                    continue

            if not data:
                # 无法获取详细信息，使用卡片信息
                _log.info("无法获取商品详情，使用卡片信息")
                return self._build_mall_fallback_msg(item_id, vurl, card_info)

            has_image = False
            if self.display_image or "mall" in self.display_image_list:
                has_image = True

            # 提取商品信息 - 适配不同API返回的字段
            name = data.get(
                "name", data.get("title", data.get("itemsName", "未知商品"))
            )

            # 价格处理
            price_val = data.get("price", data.get("priceStr", ""))
            price_prefix = data.get("pricePrefix", "￥")
            if price_val:
                # 如果价格是分为单位，转换为元
                if isinstance(price_val, int) and price_val > 100:
                    price = f"{price_prefix}{price_val / 100:.2f}"
                else:
                    price = f"{price_prefix}{price_val}"
            else:
                price = "价格未知"

            # 尝试不同的图片字段
            cover = ""
            if has_image:
                cover_url = data.get(
                    "cover",
                    data.get("img", data.get("image", data.get("itemsImg", ""))),
                )
                if cover_url:
                    # 确保URL是完整的
                    if cover_url.startswith("//"):
                        cover_url = "https:" + cover_url
                    cover = self.resize_image(cover_url, is_cover=True)

            # 商品状态
            status = data.get("status", data.get("itemsStatus", ""))
            status_text = ""
            if status == 1 or status == "1":
                status_text = "[在售] "
            elif status == 2 or status == "2":
                status_text = "[已售罄] "
            elif status == 3 or status == "3":
                status_text = "[已下架] "

            # 库存
            stock = data.get(
                "stock", data.get("totalStock", data.get("currentStock", ""))
            )
            stock_text = f"库存：{stock}\n" if stock else ""

            # 销量
            sales = data.get("sales", data.get("salesCount", data.get("soldNum", "")))
            sales_text = ""
            if sales:
                try:
                    sales_text = f"销量：{self.handle_num(int(sales))}\n"
                except (ValueError, TypeError):
                    sales_text = f"销量：{sales}\n"

            # 构建消息
            title = f"{status_text}商品：{name}\n"
            price_text = f"价格：{price}\n"

            vurl_display = "\n" + vurl if cover else vurl
            msg = [cover, vurl_display, "\n", title, price_text, stock_text, sales_text]
            return msg, vurl
        except Exception as e:
            _log.exception(f"会员购解析出错: {e}")
            # 即使出错也使用卡片信息返回
            item_id_match = re.search(r"itemsId=(\d+)", url)
            if item_id_match:
                item_id = item_id_match[1]
                vurl = f"https://mall.bilibili.com/detail.html?itemsId={item_id}"
                return self._build_mall_fallback_msg(item_id, vurl, card_info)
            return "会员购解析出错", None

    async def bili_keyword(
        self,
        group_id: Optional[int],
        text: str,
        session: ClientSession,
        card_info: dict = None,
    ) -> Union[List[Union[List[str], str]], str, bool]:
        """主要的Bilibili解析逻辑"""
        if card_info is None:
            card_info = {}
        try:
            # 提取url
            url, page, time_location = self.extract(text)
            # 如果是小程序就去搜索标题
            if not url:
                if title := re.search(r'"desc":("[^"哔哩]+")', text):
                    vurl = await self.search_bili_by_title(title[1], session)
                    if vurl:
                        url, page, time_location = self.extract(vurl)

            # 获取视频详细信息
            msg, vurl = "", ""
            if "view?" in url:
                msg, vurl = await self.video_detail(
                    url, page=page, time_location=time_location, session=session
                )
            elif "pgc" in url:
                msg, vurl = await self.bangumi_detail(url, time_location, session)
            elif "xlive" in url:
                msg, vurl = await self.live_detail(url, session)
            elif "article" in url:
                msg, vurl = await self.article_detail(url, page, session)
            elif "dynamic" in url:
                msg, vurl = await self.dynamic_detail(url, session)
            elif "space" in url or "wbi/acc/info" in url:
                msg, vurl = await self.space_detail(url, session, card_info)
            elif "mall" in url or "itemsId" in url:
                msg, vurl = await self.mall_detail(url, session, card_info)

            # 避免多个机器人解析重复推送
            if group_id:
                if group_id in self.analysis_stat:
                    if self.analysis_stat[group_id].get(vurl):
                        return False
                    self.analysis_stat[group_id].set(vurl)
                else:
                    self.analysis_stat[group_id] = ExpiringCache(
                        expire_seconds=self.reanalysis_time
                    )
                    self.analysis_stat[group_id].set(vurl)

        except Exception as e:
            msg = "bili_keyword Error: {}".format(type(e))
        return msg

    def format_msg(
        self, msg_list: List[Union[List[str], str]], is_plain_text: bool = False
    ):
        """格式化消息"""

        def flatten(container):
            for i in container:
                if isinstance(i, (list, tuple)):
                    yield from flatten(i)
                else:
                    yield i

        def is_image(msg: str) -> bool:
            # 检查标准图片扩展名
            if msg[-4:].lower() in [
                ".jpg",
                ".jpeg",
                ".png",
                ".gif",
                ".bmp",
                "jfif",
                "webp",
            ]:
                return True
            # 检查B站和QQ图片CDN（这些URL没有标准扩展名但确实是图片）
            image_cdn_patterns = [
                "pic.ugcimg.cn",  # B站预览图
                "qq.ugcimg.cn",  # QQ预览图
                "i0.hdslb.com",  # B站图片CDN
                "i1.hdslb.com",
                "i2.hdslb.com",
                "archive.biliimg.com",
                "article.biliimg.com",
            ]
            for pattern in image_cdn_patterns:
                if pattern in msg:
                    return True
            return False

        _log.info(f"开始格式化消息: {msg_list}")
        flatten_msg_list = list(flatten(msg_list))
        _log.info(f"扁平化后的消息列表: {flatten_msg_list}")

        if is_plain_text:
            result = "".join([i for i in flatten_msg_list if not is_image(i)])
            _log.info(f"纯文本格式化结果: {result}")
            return result

        message_chain = []
        for i in flatten_msg_list:
            if not i:
                _log.debug(f"跳过空元素: {i}")
                continue
            elif is_image(i):
                _log.info(f"添加图片: {i}")
                message_chain.append(Image(i))
            else:
                _log.info(f"添加文本: {i}")
                message_chain.append(Text(i))

        _log.info(f"最终消息链: {message_chain}")
        return message_chain

    async def get_msg(
        self, event, text: str, search: bool = False
    ) -> Union[List[str], bool]:
        """获取解析消息"""
        group_id = None
        if hasattr(event, "group_id"):
            group_id = event.group_id

        _log.info(f"开始处理消息: {text}")

        # 先从原始消息中提取卡片信息（用于API失败时的备用）
        card_info = self.extract_card_info(text)

        async with ClientSession(
            trust_env=self.trust_env, headers=self.headers
        ) as session:
            if search:
                text = await self.search_bili_by_title(text, session=session)
            else:
                if re.search(r"(b23.tv)|(bili(22|23|33|2233).cn)", text, re.I):
                    # 提前处理短链接，避免解析到其他的
                    text = await self.b23_extract(text, session=session)

            _log.info(f"处理后的文本: {text}")
            msg = await self.bili_keyword(
                group_id, text, session=session, card_info=card_info
            )

        _log.info(f"bili_keyword 返回结果: {msg}")

        if msg:
            if isinstance(msg, str):
                # 说明是错误信息
                _log.info(f"返回错误信息: {msg}")
                return msg

            if group_id in self.desc_blacklist:
                if msg[-1].startswith("简介"):
                    msg[-1] = ""

        return msg

    @on_message
    async def handle_analysis(self, event):
        """处理Bilibili链接解析"""
        message = event.raw_message

        # 检查是否是搜索命令
        if self.enable_search and (
            message.startswith("搜视频")
            or message.startswith("查询视频")
            or message.startswith("搜索视频")
        ):
            if message.startswith("搜视频"):
                keyword = message[3:].strip()
            elif message.startswith("查询视频"):
                keyword = message[4:].strip()
            elif message.startswith("搜索视频"):
                keyword = message[4:].strip()
            msg = await self.get_msg(event, keyword, search=True)
        else:
            # 检查是否匹配Bilibili链接模式
            if not re.search(self.pattern, message):
                return
            msg = await self.get_msg(event, message)

        if msg is False:
            return
        if msg is None:
            _log.warning("此次解析的内容为空，接口可能被修改，需要更新！")
            return
        if isinstance(msg, str):
            # 错误信息
            if hasattr(event, "group_id"):
                await self.api.post_group_msg(
                    group_id=event.group_id,
                    rtf=MessageChain([Reply(event.message_id), Text(msg)]),
                )
            else:
                await self.api.post_private_msg(
                    user_id=event.user_id, rtf=MessageChain([Text(msg)])
                )
            return

        # 格式化消息
        message_chain = self.format_msg(msg)

        # 发送消息
        try:
            if hasattr(event, "group_id"):
                await self.api.post_group_msg(
                    group_id=event.group_id,
                    rtf=MessageChain([Reply(event.message_id)] + message_chain),
                )
            else:
                await self.api.post_private_msg(
                    user_id=event.user_id, rtf=MessageChain(message_chain)
                )
        except Exception as e:
            _log.exception(e)
            _log.warning(f"错误的内容：{msg}\n此次解析的内容可能被风控！")
