import os
import textwrap
import httpx
import re
from datetime import datetime
from typing import List, Optional, Dict, Tuple
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

from curl_cffi import requests
from common.constants.HMMT import HMMT
from ncatbot.core import Image as ImageElement, MessageChain, Reply, Text, GroupMessage
from ncatbot.plugin_system import NcatBotPlugin, on_message

# 用于存储搜索结果的字典
# 格式: {(group_id, user_id): [user_data, ...]}
search_results: Dict[Tuple[int, int], List[Dict]] = {}


class VrChatInfo(NcatBotPlugin):
    name = "VrChatInfo"  # 插件名称
    version = "1.0"  # 插件版本

    # API endpoints
    API_URL_SEARCH = "https://api.vrchat.cloud/api/1/users"
    FONT_PATH = "data/font/sakura.ttf"

    def read_cookie_from_file(self) -> str:
        """从文件读取Cookie"""
        try:
            with open("data/txt/vrchat_cookie.txt", "r") as f:
                return f.read().strip()
        except Exception as e:
            print(f"Error reading cookie file: {e}")
            return ""

    def search_player(self, username: str, cookie: str) -> List[Dict]:
        """搜索玩家信息"""
        params = {"search": username, "n": 10, "offset": 0}

        headers = {
            "User-Agent": HMMT.USER_AGENT,
            "Cookie": cookie,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        try:
            response = requests.get(
                self.API_URL_SEARCH,
                params=params,
                headers=headers,
                timeout=10,
                impersonate="chrome110",
            )

            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"搜索玩家时发生错误: {e}")
            if hasattr(e, "response"):
                print(f"Response status: {e.response.status_code}")
                print(f"Response text: {e.response.text}")
            return []

    def create_search_result_image(self, users: List[Dict]) -> str:
        """创建搜索结果图片"""
        try:
            # 创建图片
            width = 800
            height = 50 + len(users) * 40  # 标题高度 + 每个用户40像素
            image = Image.new("RGB", (width, height), color="white")
            draw = ImageDraw.Draw(image)

            try:
                # 尝试加载字体
                font = ImageFont.truetype(self.FONT_PATH, 20)
            except Exception as e:
                print(f"加载字体失败: {e}")
                font = ImageFont.load_default()

            # 绘制标题
            draw.text((20, 10), "搜索结果：", fill="black", font=font)

            # 绘制每个用户的信息
            for i, user in enumerate(users):
                text = f"{i + 1}. {user.get('displayName', '')} - 状态: {user.get('status', '')}"
                draw.text((20, 50 + i * 40), text, fill="black", font=font)

            # 保存图片
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            file_path = f"data/image/vrchat/searchResults/image_{timestamp}.png"
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            image.save(file_path)
            return file_path
        except Exception as e:
            print(f"创建图片时发生错误: {e}")
            return ""

    def generate_image(self, user_data: Dict) -> str:
        """生成用户信息图片（高度和宽度自适应）"""
        # 先计算所需高度和最大宽度
        base_y = 10
        height = base_y
        min_width = 800
        max_width = 3000
        font = ImageFont.truetype(self.FONT_PATH, 28)
        small_font = ImageFont.truetype(self.FONT_PATH, 24)
        badge_font = ImageFont.truetype(self.FONT_PATH, 20)
        italic_font = ImageFont.truetype(self.FONT_PATH, 22)
        max_text_width = min_width
        # 昵称
        display_name = user_data.get("displayName", "")
        max_text_width = max(max_text_width, font.getlength(display_name) + 240)
        height += 40
        # 个性签名
        status_desc = user_data.get("statusDescription", "")
        if status_desc:
            max_text_width = max(
                max_text_width, italic_font.getlength(status_desc) + 240
            )
            height += 32
        # 等级
        trust_rank_en_color = [
            ("system_trust_legend", "VETERAN USER", "#ffff0e"),
            ("system_trust_veteran", "TRUSTED USER", "#8641ea"),
            ("system_trust_trusted", "KNOWN USER", "#ff7e47"),
            ("system_trust_known", "USER", "#2ace5e"),
            ("system_trust_basic", "NEW USER", "#187bf2"),
        ]
        trust_rank = None
        trust_color = "#cdcdcd"
        tags = user_data.get("tags", [])
        for tag, en, color in trust_rank_en_color:
            if tag in tags:
                trust_rank = en
                trust_color = color
                break
        if not trust_rank:
            trust_rank = "VISITOR"
            trust_color = "#cdcdcd"
        max_text_width = max(max_text_width, font.getlength(trust_rank) + 240)
        height += 40
        # 语言
        language_map = {
            "language_zho": "中文 / Chinese",
            "language_eng": "English / 英语",
            "language_jpn": "日本語 / Japanese",
            "language_kor": "한국어 / Korean",
            "language_rus": "Русский / Russian",
            "language_spa": "Español / Spanish",
            "language_por": "Português / Portuguese",
            "language_deu": "Deutsch / German",
            "language_fra": "Français / French",
            "language_swe": "Svenska / Swedish",
            "language_nld": "Nederlands / Dutch",
            "language_pol": "Polski / Polish",
            "language_dan": "Dansk / Danish",
            "language_nor": "Norsk / Norwegian",
            "language_ita": "Italiano / Italian",
            "language_tha": "ภาษาไทย / Thai",
            "language_fin": "Suomi / Finnish",
            "language_hun": "Magyar / Hungarian",
            "language_ces": "Čeština / Czech",
            "language_tur": "Türkçe / Turkish",
            "language_ara": "العربية / Arabic",
            "language_ron": "Română / Romanian",
            "language_vie": "Tiếng Việt / Vietnamese",
            "language_ase": "American Sign Language / 美国手语",
            "language_bfi": "British Sign Language / 英国手语",
            "language_dse": "Dutch Sign Language / 荷兰手语",
            "language_fsl": "French Sign Language / 法国手语",
            "language_kvk": "Korean Sign Language / 韩国手语",
        }
        languages = [language_map[tag] for tag in tags if tag in language_map]
        for lang in languages:
            max_text_width = max(max_text_width, small_font.getlength(lang) + 240)
        height += 32 * len(languages)
        # 状态
        state = user_data.get("state", "offline")
        status = user_data.get("status", "offline")
        status_text = "offline" if state == "offline" else status
        max_text_width = max(max_text_width, small_font.getlength(status_text) + 250)
        height += 40
        # 平台、加入日期、开发者类型
        for key in ["platform", "date_joined", "developerType"]:
            val = user_data.get(key, "")
            if key == "platform":
                platform_map = {
                    "web": "On website",
                    "standalonewindows": "In-World",
                    "mobile": "On mobile",
                }
                if state != "offline":
                    val = platform_map.get(val, "")
                else:
                    val = ""
            if val:
                max_text_width = max(
                    max_text_width, small_font.getlength(str(val)) + 220
                )
                height += 40
        # ====== 预处理简介宽高 ======
        bio = user_data.get("bio", "")
        bio_height = 0
        if bio:
            bio_lines = bio.split("\n")
            bio_height += 40  # 简介标题
            for line in bio_lines:
                wrapped = textwrap.fill(line, width=40)
                for wrapped_line in wrapped.split("\n"):
                    max_text_width = max(
                        max_text_width, small_font.getlength(wrapped_line) + 240
                    )
                line_count = wrapped.count("\n") + 1
                bio_height += 30 * line_count
            bio_height += 20
        height += bio_height

        # ====== 预处理徽章宽高 ======
        badges = user_data.get("badges", [])
        badge_height = 0
        if badges:
            badge_height += 40  # "徽章："标题
            for badge in badges:
                badge_name = badge.get("badgeName", "")
                badge_desc = badge.get("badgeDescription", "")
                badge_height += 30  # 徽章名
                if badge_name:
                    max_text_width = max(
                        max_text_width, badge_font.getlength(badge_name) + 240
                    )
                if badge_desc:
                    for wrapped_line in textwrap.fill(badge_desc, width=50).split("\n"):
                        max_text_width = max(
                            max_text_width, badge_font.getlength(wrapped_line) + 260
                        )
                    desc_lines = textwrap.fill(badge_desc, width=50).count("\n") + 1
                    badge_height += 60 * desc_lines
            height += badge_height

        # ====== 预处理创角日期高度 ======
        date_joined = user_data.get("date_joined", "")
        date_joined_height = 0
        if date_joined:
            date_joined_height = 40
            height += date_joined_height

        # ====== 预处理实例信息，提前计算宽高 ======
        instance_extra_height = 0
        world_img_needed = False
        instance_info = None
        instance_room_name = ""
        if user_data.get("state", "offline") != "offline":
            location = user_data.get("location", "")
            instance_id_field = user_data.get("instanceId", "")
            if (
                location
                and ":" in location
                and not location.startswith("offline")
                and not location.startswith("private")
                and instance_id_field
            ):
                # 只保留世界图片、房间名、分类
                instance_extra_height += 100  # 世界图片高度
                instance_extra_height += 40  # 房间名
                instance_extra_height += 30  # 分类
                instance_extra_height += 20  # 分割线
                world_img_needed = True
                instance_info = (location, instance_id_field)
                # 预取房间名宽度
                try:
                    world_id, instance_id = location.split(":", 1)
                    cookie = self.read_cookie_from_file()
                    headers = {
                        "User-Agent": HMMT.USER_AGENT,
                        "Cookie": cookie,
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    }
                    url = f"https://api.vrchat.cloud/api/1/instances/{world_id}:{instance_id}"
                    response = requests.get(
                        url,
                        headers=headers,
                        timeout=10,
                        impersonate="chrome110",
                    )
                    if response.status_code == 200:
                        instance = response.json()
                        world_name = instance.get("world", {}).get("name", "")
                        inst_type = instance.get("type", "")
                        type_map = {
                            "public": "Public",
                            "hidden": "Friends+",
                            "friends": "Friends",
                            "private": "Invite+ / Invite",
                            "group": "Group",
                        }
                        inst_type_friendly = type_map.get(inst_type, inst_type)
                        instance_room_name = world_name
                        if world_name:
                            max_text_width = max(
                                max_text_width, font.getlength(world_name) + 350
                            )
                        if inst_type_friendly:
                            max_text_width = max(
                                max_text_width,
                                small_font.getlength(inst_type_friendly) + 350,
                            )
                except Exception as e:
                    print(f"预取房间名宽度失败: {e}")
        height += instance_extra_height

        # 最小宽度限制
        width = int(max(min_width, min(max_width, max_text_width)))
        min_height = 400
        height = max(min_height, height)
        # 创建图片
        image = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(image)
        self.draw_gradient_background(image, width, height)

        # 绘制头像
        avatar_url = user_data.get("currentAvatarThumbnailImageUrl")
        if avatar_url:
            try:
                cookie = self.read_cookie_from_file()
                if cookie:
                    headers = {
                        "User-Agent": HMMT.USER_AGENT,
                        "Cookie": cookie,
                        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                    }
                    with httpx.Client(follow_redirects=True) as client:
                        response = client.get(avatar_url, headers=headers, timeout=10)
                        if response.status_code == 200:
                            avatar_image = Image.open(BytesIO(response.content))
                            avatar_image = avatar_image.resize((200, 200))
                            mask = Image.new("L", (200, 200), 0)
                            mask_draw = ImageDraw.Draw(mask)
                            mask_draw.rounded_rectangle((0, 0, 200, 200), 25, fill=255)
                            image.paste(avatar_image, (10, 10), mask)
                        else:
                            print(f"获取头像失败，状态码: {response.status_code}")
                else:
                    print("未找到cookie，无法获取头像")
            except Exception as e:
                print(f"加载头像失败: {e}")

        # 绘制基本信息
        y_offset = 10
        # 显示名称
        draw.text((220, y_offset), display_name, fill="black", font=font)
        y_offset += 40

        # 个性签名（statusDescription）
        if status_desc:
            draw.text((220, y_offset), status_desc, fill="gray", font=italic_font)
            y_offset += 32

        # 用户等级（英文+专属颜色）和语言（每行一个）
        trust_rank_en_color = [
            ("system_trust_legend", "VETERAN USER", "#ffff0e"),
            ("system_trust_veteran", "TRUSTED USER", "#8641ea"),
            ("system_trust_trusted", "KNOWN USER", "#ff7e47"),
            ("system_trust_known", "USER", "#2ace5e"),
            ("system_trust_basic", "NEW USER", "#187bf2"),
        ]
        trust_rank = None
        trust_color = "#cdcdcd"
        for tag, en, color in trust_rank_en_color:
            if tag in tags:
                trust_rank = en
                trust_color = color
                break
        if not trust_rank:
            trust_rank = "VISITOR"
            trust_color = "#cdcdcd"
        draw.text((220, y_offset), trust_rank, fill=trust_color, font=font)
        y_offset += 40

        # 语言（每行一个，不加"语言："）
        for lang in languages:
            draw.text((220, y_offset), lang, fill="#388e3c", font=small_font)
            y_offset += 32

        # 状态（state/status）
        state = user_data.get("state", "offline")  # online, active, offline
        status = user_data.get(
            "status", "offline"
        )  # active, join me, ask me, busy, offline
        # 状态点颜色
        state_color = {
            "online": (76, 175, 80),  # 绿色
            "active": (255, 193, 7),  # 黄色
            "offline": (158, 158, 158),  # 灰色
        }.get(state, (158, 158, 158))
        # 状态显示逻辑
        status_text = "offline" if state == "offline" else status
        draw.text((220, y_offset), "●", fill=state_color, font=small_font)
        draw.text((250, y_offset), status_text, fill="black", font=small_font)
        y_offset += 40

        # 输出完整user_data内容，便于调试
        print(f"[DEBUG] user_data: {user_data}")
        # 平台信息（仅在线时显示，且用platform字段）
        platform = user_data.get("platform", "")
        platform_map = {
            "web": "On website",
            "standalonewindows": "In-World",
            "mobile": "On mobile",
        }
        print(
            f"[DEBUG] 用户state: {state}, platform: {platform}, platform_map: {platform_map}"
        )
        if state != "offline":
            platform_text = platform_map.get(platform, "")
            print(f"[DEBUG] platform_text: {platform_text}")
            if platform_text:
                draw.text((220, y_offset), platform_text, fill="black", font=small_font)
                y_offset += 40

        # 渲染简介（bio）
        if bio:
            bio_lines = bio.split("\n")
            draw.text((220, y_offset), "简介：", fill="black", font=small_font)
            y_offset += 40
            for line in bio_lines:
                wrapped_lines = textwrap.fill(line, width=40)
                draw.multiline_text(
                    (240, y_offset),
                    wrapped_lines,
                    fill="black",
                    font=small_font,
                    spacing=5,
                )
                y_offset += 30 * (wrapped_lines.count("\n") + 1)
            y_offset += 20

        # 渲染徽章信息
        if badges:
            draw.text((220, y_offset), "徽章：", fill="black", font=small_font)
            y_offset += 40
            for badge in badges:
                badge_name = badge.get("badgeName", "")
                badge_desc = badge.get("badgeDescription", "")
                if badge_name:
                    draw.text(
                        (240, y_offset),
                        f"• {badge_name}",
                        fill="black",
                        font=badge_font,
                    )
                    y_offset += 30
                    if badge_desc:
                        wrapped_desc = textwrap.fill(badge_desc, width=50)
                        draw.multiline_text(
                            (260, y_offset),
                            wrapped_desc,
                            fill="black",
                            font=badge_font,
                            spacing=5,
                        )
                        y_offset += 60 * (wrapped_desc.count("\n") + 1)

        # 渲染创角日期
        if date_joined:
            draw.text(
                (220, y_offset),
                f"创角日期: {date_joined}",
                fill="black",
                font=small_font,
            )
            y_offset += 40

        # 渲染instance信息（世界图片、房间名、分类）和分割线
        if world_img_needed and instance_info:
            try:
                world_id, instance_id = instance_info[0].split(":", 1)
                # 类型友好名称映射
                type_map = {
                    "public": "Public",
                    "hidden": "Friends+",
                    "friends": "Friends",
                    "private": "Invite+ / Invite",
                    "group": "Group",
                }
                # 请求实例详情
                cookie = self.read_cookie_from_file()
                headers = {
                    "User-Agent": HMMT.USER_AGENT,
                    "Cookie": cookie,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                }
                url = (
                    f"https://api.vrchat.cloud/api/1/instances/{world_id}:{instance_id}"
                )
                response = requests.get(
                    url,
                    headers=headers,
                    timeout=10,
                    impersonate="chrome110",
                )
                if response.status_code == 200:
                    instance = response.json()
                    world_name = instance.get("world", {}).get("name", "")
                    world_img = instance.get("world", {}).get("thumbnailImageUrl", "")
                    inst_type = instance.get("type", "")
                    inst_type_friendly = type_map.get(inst_type, inst_type)
                    # 分割线
                    draw.line(
                        [(220, y_offset), (width - 40, y_offset)],
                        fill="#cccccc",
                        width=2,
                    )
                    y_offset += 20
                    # 世界图片
                    if world_img:
                        try:
                            img_response = requests.get(
                                world_img,
                                headers=headers,
                                timeout=10,
                                impersonate="chrome110",
                            )
                            if img_response.status_code == 200:
                                thumb = Image.open(
                                    BytesIO(img_response.content)
                                ).resize((120, 90))
                                image.paste(thumb, (220, y_offset))
                        except Exception as e:
                            print(f"加载世界图片失败: {e}")
                    # 房间名
                    if world_name:
                        draw.text(
                            (350, y_offset),
                            world_name,
                            fill="#222",
                            font=font,
                        )
                    # 分类
                    if inst_type_friendly:
                        draw.text(
                            (350, y_offset + 40),
                            inst_type_friendly,
                            fill="#888",
                            font=small_font,
                        )
                    y_offset += 100
                else:
                    print(
                        f"[DEBUG] 未能获取到有效实例信息，状态码: {response.status_code}"
                    )
            except Exception as e:
                print(f"实例信息查询失败: {e}")

        # 保存图片
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        file_path = f"data/image/vrchat/userInfo/image_{timestamp}.png"
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        image.save(file_path)
        return file_path

    def draw_gradient_background(self, image: Image, width: int, height: int):
        """绘制渐变背景"""
        for y in range(height):
            r = int(240 + (220 - 240) * y / height)
            g = int(248 + (220 - 248) * y / height)
            b = int(255 + (250 - 255) * y / height)
            for x in range(width):
                image.putpixel((x, y), (r, g, b))

    @on_message
    async def handle_vrchat_search(self, input: GroupMessage):
        """处理查询命令"""
        message = input.raw_message.strip()

        # 处理回复消息
        reply_list = input.message.filter(Reply)
        if reply_list:
            try:
                reply_id = reply_list[0].id
                # get_msg 返回的是 GroupMessageEvent 对象
                reply_msg = await self.api.get_msg(reply_id)
                raw_message = reply_msg.raw_message
                if raw_message and "请回复数字选择要查看的玩家" in raw_message:
                    # 处理数字选择
                    # 先移除CQ码
                    clean_message = re.sub(
                        r"\[CQ:[^\]]+\]", "", message
                    ).strip()

                    # 提取数字
                    number_match = re.match(r"^\s*(\d+)", clean_message)
                    if number_match:
                        number = int(number_match.group(1))
                        key = (input.group_id, input.sender.user_id)

                        if key in search_results:
                            if 0 <= number - 1 < len(search_results[key]):
                                # 先查详细信息
                                user_id = search_results[key][number - 1].get(
                                    "id"
                                )
                                print(
                                    f"[DEBUG] 回复序号查详细信息 user_id: {user_id}"
                                )
                                cookie = self.read_cookie_from_file()
                                # 只保留一组auth和twoFactorAuth（如有多组可自行处理）
                                # 这里假设cookie文件内容已是单组
                                print(f"[DEBUG] cookie: {cookie}")
                                headers = {
                                    "User-Agent": HMMT.USER_AGENT,
                                    "Cookie": cookie,
                                    "Accept": "application/json",
                                    "Content-Type": "application/json",
                                }
                                print(f"[DEBUG] headers: {headers}")
                                url = f"https://api.vrchat.cloud/api/1/users/{user_id}"
                                print(f"[DEBUG] url: {url}")
                                try:
                                    response = requests.get(
                                        url,
                                        headers=headers,
                                        timeout=10,
                                        impersonate="chrome110",
                                    )
                                    print(
                                        f"[DEBUG] resp.status_code: {response.status_code}"
                                    )
                                    print(f"[DEBUG] resp.text: {response.text}")
                                    if response.status_code == 200:
                                        user_data = response.json()
                                        image_path = self.generate_image(
                                            user_data
                                        )
                                        await self.api.post_group_msg(
                                            group_id=input.group_id,
                                            rtf=MessageChain(
                                                [
                                                    ImageElement(image_path),
                                                    Reply(input.message_id),
                                                ]
                                            ),
                                        )
                                        # 清除搜索结果
                                        del search_results[key]
                                    else:
                                        await self.api.post_group_msg(
                                            group_id=input.group_id,
                                            text="获取详细信息失败，请重试",
                                        )
                                except Exception as e:
                                    print(f"获取用户详情失败: {e}")
                                    await self.api.post_group_msg(
                                        group_id=input.group_id,
                                        text="获取详细信息失败，请重试",
                                    )
                            else:
                                await self.api.post_group_msg(
                                    group_id=input.group_id,
                                    text="无效的选择，请重新选择",
                                )
                        else:
                            await self.api.post_group_msg(
                                group_id=input.group_id,
                                text="搜索结果已过期，请重新搜索",
                            )
                    return
            except Exception as e:
                print(f"处理回复时发生错误: {e}")

        # 处理搜索命令
        message_parts = message.split(" ")
        if len(message_parts) < 2 or message_parts[0] != "搜索vrc玩家":
            return

        username = " ".join(message_parts[1:])
        cookie = self.read_cookie_from_file()
        if not cookie:
            await self.api.post_group_msg(
                group_id=input.group_id, text="Cookie获取失败"
            )
            return

        users = self.search_player(username, cookie)
        if not users:
            await self.api.post_group_msg(group_id=input.group_id, text="找不到该玩家")
            return

        # 只搜索到一个用户，直接发送详细信息图片
        if len(users) == 1:
            image_path = self.generate_image(users[0])
            await self.api.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain(
                    [
                        ImageElement(image_path),
                        Reply(input.message_id),
                    ]
                ),
            )
            return

        # 存储搜索结果
        search_results[(input.group_id, input.sender.user_id)] = users

        # 生成并发送搜索结果图片
        image_path = self.create_search_result_image(users)
        await self.api.post_group_msg(
            group_id=input.group_id,
            rtf=MessageChain(
                [
                    ImageElement(image_path),
                    Text("请回复数字选择要查看的玩家"),
                    Reply(input.message_id),
                ]
            ),
        )
