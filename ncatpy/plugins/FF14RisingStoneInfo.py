import os
import httpx
from datetime import datetime
from typing import List, Optional, Dict
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

from ncatpy.common.constants.HMMT import HMMT
from ncatpy.message import GroupMessage


class FF14RisingStoneInfo:
    # API endpoints
    API_URL_SEARCH = "https://apiff14risingstones.web.sdo.com/api/common/search"
    API_URL_USER_INFO = (
        "https://apiff14risingstones.web.sdo.com/api/home/userInfo/getUserInfo"
    )
    FONT_PATH = "data/font/FZMiaoWuK.TTF"

    # 服务器映射
    SERVER_ALIAS = {
        "柔风": "柔风海湾",
        "鲸鱼": "静语庄园",
        "琥珀": "琥珀原",
        "鸟": "陆行鸟",
        "猪": "莫古力",
        "猫": "猫小胖",
        "狗": "豆豆柴",
    }

    # 区服列表
    LUXINGNIAO_SERVERS = {
        "拉诺西亚",
        "幻影群岛",
        "神意之地",
        "萌芽池",
        "红玉海",
        "宇宙和音",
        "沃仙曦染",
        "晨曦王座",
    }

    MOGULI_SERVERS = {
        "潮风亭",
        "神拳痕",
        "白银乡",
        "白金幻象",
        "旅人栈桥",
        "拂晓之间",
        "龙巢神殿",
        "梦羽宝境",
    }

    MAOXIAOPANG_SERVERS = {
        "紫水栈桥",
        "延夏",
        "静语庄园",
        "摩杜纳",
        "海猫茶屋",
        "柔风海湾",
        "琥珀原",
    }

    DOUDAOCHAI_SERVERS = {"水晶塔", "银泪湖", "太阳海岸", "伊修加德", "红茶川"}

    def __init__(self):
        self.init_job_icons()

    def init_job_icons(self):
        """初始化职业图标"""
        job_list = [
            "AST",
            "BLM",
            "BLU",
            "BRD",
            "DNC",
            "DRG",
            "DRK",
            "GNB",
            "MCH",
            "MNK",
            "NIN",
            "PCT",
            "PLD",
            "RDM",
            "RPR",
            "SAM",
            "SCH",
            "SGE",
            "SMN",
            "VPR",
            "WAR",
            "WHM",
        ]

        self.job_icons = {}
        for job in job_list:
            try:
                icon_path = f"data/image/ff14/icon/{job}.png"
                self.job_icons[job] = Image.open(icon_path)
            except Exception as e:
                print(f"Error loading job icon {job}: {e}")

        # 加载生产采集职业图标
        for i in range(11):
            try:
                icon_path = f"data/image/ff14/icon/sjob{i}.png"
                self.job_icons[f"sjob{i}"] = Image.open(icon_path)
            except Exception as e:
                print(f"Error loading sjob icon {i}: {e}")

    def read_cookie_from_file(self) -> str:
        """从文件读取Cookie"""
        try:
            with open("data/txt/cookie.txt", "r") as f:
                return f.read().strip()
        except Exception as e:
            print(f"Error reading cookie file: {e}")
            return ""

    def search_player(self, character_name: str, cookie: str) -> List[Dict]:
        """搜索玩家信息"""
        params = {
            "type": 6,
            "keywords": character_name,
            "part_id": "",
            "orderBy": "comment",
            "page": 1,
            "limit": 100,
            "pageTime": "",
        }

        headers = {
            "User-Agent": HMMT.USER_AGENT,
            "Host": "apiff14risingstones.web.sdo.com",
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "zh-CN,zh;q=0.9",
            "cache-control": "max-age=0",
            "Cookie": cookie,
            "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "none",
            "sec-fetch-user": "?1",
            "upgrade-insecure-httpx": "1",
            "priority":"u=1, i"
        }

        try:
            session = httpx.Client(verify=False)

            # 打印请求信息
            print("\n=== Request Info ===")
            print(f"URL: {self.API_URL_SEARCH}")
            print(f"Method: GET")
            print(f"Params: {params}")
            print("Headers:")
            for key, value in headers.items():
                print(f"  {key}: {value}")

            response = session.get(
                self.API_URL_SEARCH,
                params=params,
                headers=headers,
                timeout=10,
            )

            # 打印响应信息
            print("\n=== Response Info ===")
            print(f"Status Code: {response.status_code}")
            print("Response Headers:")
            for key, value in response.headers.items():
                print(f"  {key}: {value}")
            print(f"Response URL: {response.url}")
            print(f"Response Content: {response.text[:500]}...")  # 只打印前500个字符

            response.raise_for_status()
            result = response.json()

            if result.get("code") == 10000:
                return result.get("data", [])
            else:
                print(f"API返回错误：{result.get('msg')}")
                return []
        except Exception as e:
            print(f"\n=== Error Info ===")
            print(f"Error Type: {type(e).__name__}")
            print(f"Error Message: {str(e)}")
            if hasattr(e, "response"):
                print(f"Response Status: {e.response.status_code}")
                print(f"Response Headers:")
                for key, value in e.response.headers.items():
                    print(f"  {key}: {value}")
                print(f"Response Text: {e.response.text}")
            return []

    def get_user_info(self, uuid: str, cookie: str) -> Optional[Dict]:
        """获取用户详细信息"""
        try:
            session = httpx.Client(verify=False)
            # 然后获取详细信息
            response = session.get(
                f"{self.API_URL_USER_INFO}?uuid={uuid}&page=1&limit=30",
                headers={
                    "authority": "apiff14risingstones.web.sdo.com",
                    "scheme": "https",
                    "sec-ch-ua-platform": '"Windows"',
                    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 Edg/132.0.0.0",
                    "accept": "application/json, text/plain, */*",
                    "sec-ch-ua": '"Not A(Brand";v="8", "Chromium";v="132", "Microsoft Edge";v="132"',
                    "sec-ch-ua-mobile": "?0",
                    "origin": "https://ff14risingstones.web.sdo.com",
                    "referer": "https://ff14risingstones.web.sdo.com/",
                    "cookie": cookie
                },
            )
            print(response.json())
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"获取用户信息时发生错误: {e}")
            if hasattr(e, "response"):
                print(f"Response status: {e.response.status_code}")
                print(f"Response text: {e.response.text}")
            return None

    def generate_image(self, user_data: Dict) -> str:
        """生成角色信息图片"""
        # 基础设置
        width = 800
        height = 1000
        # 根据成就数量动态调整高度
        achievements = user_data.get("achieveInfo", [])
        filtered_achievements = [
            a
            for a in achievements
            if a.get("medal_type") not in ["职业满级", "剧情通关"]
        ]
        height = max(170 + len(filtered_achievements) * 60, height)

        # 创建渐变背景
        image = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(image)
        self.draw_gradient_background(image, width, height)

        try:
            font = ImageFont.truetype(self.FONT_PATH, 28)
            small_font = ImageFont.truetype(self.FONT_PATH, 24)
        except Exception as e:
            print(f"加载字体失败: {e}")
            return ""

        # 绘制基本信息区域
        self.draw_rounded_rectangle(draw, (10, 10, 790, 250), 30, (220, 220, 220, 100))

        # 绘制头像
        avatar_url = user_data.get("avatar")
        if avatar_url:
            try:
                avatar_image = Image.open(BytesIO(httpx.get(avatar_url).content))
                avatar_image = avatar_image.resize((200, 200))
                # 创建圆角蒙版
                mask = Image.new("L", (200, 200), 0)
                mask_draw = ImageDraw.Draw(mask)
                mask_draw.rounded_rectangle((0, 0, 200, 200), 25, fill=255)
                # 应用蒙版
                image.paste(avatar_image, (10, 10), mask)
            except Exception as e:
                print(f"加载头像失败: {e}")

        # 获取角色详细信息
        character_detail = user_data.get("characterDetail", [{}])[0]

        # 绘制基本文字信息
        draw.text(
            (230, 30),
            character_detail.get("character_name", ""),
            fill="black",
            font=font,
        )
        draw.text(
            (230, 80),
            f"服务器：{user_data.get('area_name')} {user_data.get('group_name')}",
            fill="black",
            font=small_font,
        )

        # 绘制详细信息区域
        self.draw_rounded_rectangle(draw, (10, 270, 790, 455), 30, (220, 220, 220, 100))

        # 左侧信息
        info_left = [
            ("种族", f"{character_detail.get('race', '未知')}"),
            ("性别", "男" if character_detail.get("gender") == "0" else "女"),
            ("部队", f"{character_detail.get('guild_name', '无')}"),
            ("创角时间", character_detail.get("create_time", "未知")),
            ("最近登录", character_detail.get("last_login_time", "未知")),
            ("房屋信息", character_detail.get("house_info", "无")),
        ]

        # 右侧信息
        info_right = [
            ("幻想药使用次数", character_detail.get("washing_num", "0")),
            ("纷争前线击倒数", character_detail.get("kill_times", "0")),
            ("水晶冲突最高段位", character_detail.get("crystal_rank", "未知")),
            ("钓鱼抛竿次数", character_detail.get("fish_times", "0")),
            ("称霸宝物库次数", character_detail.get("treasure_times", "0")),
            ("无人岛开拓等级", character_detail.get("newrank", "0")),
        ]

        y = 290
        for label, value in info_left:
            self.draw_info_pair(draw, small_font, 20, y, 360, label, value)
            y += 30

        y = 290
        for label, value in info_right:
            self.draw_info_pair(draw, small_font, 420, y, 360, label, value)
            y += 30

        # 绘制职业等级区域
        self.draw_rounded_rectangle(draw, (10, 470, 790, 770), 30, (220, 220, 220, 100))
        draw.text((20, 480), "职业等级", fill="black", font=font)

        # 绘制职业图标和等级
        career_levels = {
            career["career"]: career["character_level"]
            for career in user_data.get("careerLevel", [])
        }

        # 绘制战斗职业
        self.draw_battle_jobs(draw, small_font, career_levels)

        # 绘制生产采集职业
        self.draw_crafting_jobs(draw, small_font, career_levels)

        # 绘制成就区域
        if filtered_achievements:
            self.draw_rounded_rectangle(
                draw,
                (10, 790, 790, 790 + len(filtered_achievements) * 60),
                30,
                (220, 220, 220, 100),
            )
            draw.text((20, 800), "特殊成就", fill="black", font=font)
            self.draw_achievements(draw, small_font, filtered_achievements)

        # 保存图片
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        file_path = f"data/image/ff14/userInfo/image_{timestamp}.png"
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

    def draw_rounded_rectangle(
        self, draw: ImageDraw, xy: tuple, radius: int, fill: tuple
    ):
        """绘制圆角矩形"""
        x1, y1, x2, y2 = xy
        draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill)
        draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill)
        draw.pieslice([x1, y1, x1 + radius * 2, y1 + radius * 2], 180, 270, fill=fill)
        draw.pieslice([x2 - radius * 2, y1, x2, y1 + radius * 2], 270, 360, fill=fill)
        draw.pieslice([x1, y2 - radius * 2, x1 + radius * 2, y2], 90, 180, fill=fill)
        draw.pieslice([x2 - radius * 2, y2 - radius * 2, x2, y2], 0, 90, fill=fill)

    def draw_info_pair(
        self,
        draw: ImageDraw,
        font: ImageFont,
        x: int,
        y: int,
        width: int,
        label: str,
        value: str,
    ):
        """绘制标签值对"""
        draw.text((x, y), f"{label}：", fill="black", font=font)
        text_width = font.getlength(f"{label}：")
        draw.text(
            (x + width - font.getlength(value)), y, value, fill="black", font=font
        )

    def draw_battle_jobs(self, draw: ImageDraw, font: ImageFont, career_levels: Dict):
        """绘制战斗职业图标和等级"""
        # 坦克
        tanks = [
            ("PLD", "骑士"),
            ("WAR", "战士"),
            ("DRK", "暗黑骑士"),
            ("GNB", "绝枪战士"),
        ]
        x = 20
        y = 520
        for job_key, job_name in tanks:
            if job_key in self.job_icons:
                icon = self.job_icons[job_key].resize((50, 50))
                Image.Image.paste(icon, (x, y))
                level = career_levels.get(job_name, "0")
                self.draw_centered_text(draw, font, level, x, y + 60, 50)
            x += 55

        # DPS近战
        melee_dps = [
            ("MNK", "武僧"),
            ("DRG", "龙骑士"),
            ("NIN", "忍者"),
            ("SAM", "武士"),
            ("RPR", "钐镰客"),
            ("VPR", "蝰蛇剑士"),
        ]
        x = 20 + 55 * 4 + 15
        for job_key, job_name in melee_dps:
            if job_key in self.job_icons:
                icon = self.job_icons[job_key].resize((50, 50))
                Image.Image.paste(icon, (x, y))
                level = career_levels.get(job_name, "0")
                self.draw_centered_text(draw, font, level, x, y + 60, 50)
            x += 55

        # 远程物理
        ranged = [("BRD", "吟游诗人"), ("MCH", "机工士"), ("DNC", "舞者")]
        x = 20 + 55 * 10 + 30
        for job_key, job_name in ranged:
            if job_key in self.job_icons:
                icon = self.job_icons[job_key].resize((50, 50))
                Image.Image.paste(icon, (x, y))
                level = career_levels.get(job_name, "0")
                self.draw_centered_text(draw, font, level, x, y + 60, 50)
            x += 55

        # 治疗
        healers = [
            ("WHM", "白魔法师"),
            ("SCH", "学者"),
            ("AST", "占星术士"),
            ("SGE", "贤者"),
        ]
        x = 20
        y = 620
        for job_key, job_name in healers:
            if job_key in self.job_icons:
                icon = self.job_icons[job_key].resize((50, 50))
                Image.Image.paste(icon, (x, y))
                level = career_levels.get(job_name, "0")
                self.draw_centered_text(draw, font, level, x, y + 60, 50)
            x += 55

        # 法系DPS
        casters = [
            ("BLM", "黑魔法师"),
            ("SMN", "召唤师"),
            ("RDM", "赤魔法师"),
            ("PCT", "绘灵法师"),
            ("BLU", "青魔法师"),
        ]
        x = 20 + 55 * 4 + 15
        for job_key, job_name in casters:
            if job_key in self.job_icons:
                icon = self.job_icons[job_key].resize((50, 50))
                Image.Image.paste(icon, (x, y))
                level = career_levels.get(job_name, "0")
                self.draw_centered_text(draw, font, level, x, y + 60, 50)
            x += 55

    def draw_crafting_jobs(self, draw: ImageDraw, font: ImageFont, career_levels: Dict):
        """绘制生产采集职业图标和等级"""
        crafters = [
            ("sjob0", "刻木匠"),
            ("sjob1", "锻铁匠"),
            ("sjob2", "铸甲匠"),
            ("sjob3", "雕金匠"),
            ("sjob4", "制革匠"),
            ("sjob5", "裁衣匠"),
            ("sjob6", "炼金术士"),
            ("sjob7", "烹调师"),
        ]
        x = 20
        y = 720
        for job_key, job_name in crafters:
            if job_key in self.job_icons:
                icon = self.job_icons[job_key].resize((50, 50))
                Image.Image.paste(icon, (x, y))
                level = career_levels.get(job_name, "0")
                self.draw_centered_text(draw, font, level, x, y + 60, 50)
            x += 55

        gatherers = [("sjob8", "采矿工"), ("sjob9", "园艺工"), ("sjob10", "捕鱼人")]
        x = 20 + 55 * 8 + 15
        for job_key, job_name in gatherers:
            if job_key in self.job_icons:
                icon = self.job_icons[job_key].resize((50, 50))
                Image.Image.paste(icon, (x, y))
                level = career_levels.get(job_name, "0")
                self.draw_centered_text(draw, font, level, x, y + 60, 50)
            x += 55

    def draw_centered_text(
        self, draw: ImageDraw, font: ImageFont, text: str, x: int, y: int, width: int
    ):
        """绘制居中文字"""
        text_width = font.getlength(str(text))
        draw.text((x + (width - text_width) / 2, y), str(text), fill="black", font=font)

    def draw_achievements(
        self, draw: ImageDraw, font: ImageFont, achievements: List[Dict]
    ):
        """绘制成就信息"""
        y = 850
        for achievement in achievements:
            achieve_name = f"成就名: {achievement.get('achieve_name', '')}"
            achieve_time = f"完成时间: {achievement.get('achieve_time', '')}"
            draw.text((20, y), achieve_name, fill="black", font=font)
            draw.text((420, y), achieve_time, fill="black", font=font)
            y += 60

    async def handle_ff14_rising_stone_info(self, input: GroupMessage):
        """处理查询命令"""
        message_parts = input.raw_message.split(" ")
        if len(message_parts) < 2 or message_parts[0] != "搜索玩家":
            return

        character_name = message_parts[1]
        server = (
            None
            if len(message_parts) < 3
            else self.SERVER_ALIAS.get(message_parts[2], message_parts[2])
        )

        cookie = self.read_cookie_from_file()
        if not cookie:
            await input.add_text("Cookie获取失败").reply()
            return

        players = self.search_player(character_name, cookie)
        if not players:
            await input.add_text("找不到该玩家").reply()
            return

        # 根据服务器筛选玩家
        if server:
            filtered_players = [p for p in players if p.get("group_name") == server]
        else:
            filtered_players = players

        if len(filtered_players) == 0:
            await input.add_text("未找到匹配的玩家").reply()
        elif len(filtered_players) == 1:
            user_info = self.get_user_info(filtered_players[0].get("uuid"), cookie)
            if user_info and user_info.get("code") == 10000:
                image_path = self.generate_image(user_info.get("data", {}))
                print(image_path)
                await input.add_image(image_path).reply()
            else:
                await input.add_text("获取玩家信息失败").reply()
        else:
            response = "找到多个玩家:\n" + "\n".join(
                f"角色名: {p.get('character_name')} 区服: {p.get('group_name')}"
                for p in filtered_players
            )
            await input.add_text(response).reply()
