import os
import json
import httpx
from datetime import datetime
from typing import List, Optional
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

from ncatbot.core.message import GroupMessage
from ncatbot.plugin.base_plugin import BasePlugin
from ncatbot.plugin.event import CompatibleEnrollment

bot = CompatibleEnrollment

class FF14LogsInfo(BasePlugin):
    # 常量定义
    TOKEN_ENDPOINT = "https://cn.fflogs.com/oauth/token"
    JSON_PAYLOAD = {
        "client_id": "9ae4db97-1f1b-4123-82d4-32aff00d3283",
        "client_secret": "rAeNnp3Z1VFxYI92TcpBLggKPM3wtkmK9FxZX2C1",
        "grant_type": "client_credentials",
    }
    TOKEN_FILE_PATH = "data/json/access_token.json"
    URL = "https://cn.fflogs.com/api/v2/client"
    FONT_PATH = "data/font/FZMiaoWuK.TTF"

    def __init__(self):
        self.api_token = ""
        self.zones = [
            self.Zone(54, "万魔殿 荒天之狱", 101),
            self.Zone(49, "万魔殿 炼净之狱", 101),
            self.Zone(44, "万魔殿 边境之狱", 101),
            self.Zone(62, "阿卡狄亚竞技场 轻量级", 101),
            self.Zone(53, "欧米茄绝境验证战", 100),
            self.Zone(45, "幻想龙诗绝境战", 100),
            self.Zone(43, "绝境战（旧版本）", 100),
        ]
        self.init()

    class Zone:
        def __init__(self, id: int, name: str, difficulty: int):
            self.id = id
            self.name = name
            self.difficulty = difficulty

    class RankingInfo:
        def __init__(
            self,
            encounter_id: int,
            encounter_name: str,
            rank_percent: float,
            total_kills: int,
            spec: str,
            best_amount: float,
        ):
            self.encounter_id = encounter_id
            self.encounter_name = encounter_name
            self.rank_percent = rank_percent
            self.total_kills = total_kills
            self.spec = spec
            self.best_amount = best_amount

        def get_boss_url(self) -> str:
            if self.encounter_id == 1068:
                return "https://assets.rpglogs.com/img/ff/bosses/1068-icon.jpg?v=2"
            return f"https://assets.rpglogs.com/img/ff/bosses/{self.encounter_id}-icon.jpg?v=2"

        def get_job_icon_url(self) -> str:
            return f"https://assets.rpglogs.com/img/ff/icons/{self.spec}.png"

    def init(self):
        self.retrieve_and_save_access_token()

    def retrieve_and_save_access_token(self):
        try:
            response = httpx.post(self.TOKEN_ENDPOINT, json=self.JSON_PAYLOAD)
            response.raise_for_status()
            access_token = response.json().get("access_token")
            if access_token:
                self.save_access_token_to_file(response.text)
                self.api_token = access_token
        except Exception as e:
            print(f"Error retrieving access token: {e}")

    def save_access_token_to_file(self, access_token_json: str):
        os.makedirs(os.path.dirname(self.TOKEN_FILE_PATH), exist_ok=True)
        with open(self.TOKEN_FILE_PATH, "w", encoding="utf-8") as file:
            file.write(access_token_json)

    def get_difficulty(self, zone_id: int) -> int:
        for zone in self.zones:
            if zone.id == zone_id:
                return zone.difficulty
        return 0

    def get_data(self, name: str, server: str, zone_id: int) -> Optional[str]:
        difficulty = self.get_difficulty(zone_id)
        graphql_query = f"""
        query hmmt {{
            characterData {{
                character(name: "{name}", serverRegion: "cn", serverSlug: "{server}") {{
                    zoneRankings(zoneID: {zone_id}, difficulty: {difficulty})
                }}
            }}
        }}
        """
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
            "Accept": "*/*",
            "User-Agent": "Apifox/1.0.0 (https://apifox.com)",
        }
        try:
            response = httpx.post(
                self.URL, json={"query": graphql_query}, headers=headers
            )
            response.raise_for_status()
            return response.text
        except Exception as e:
            print(f"Error fetching data: {e}")
            return None

    def parse_response_data(self, response_data: str) -> List[RankingInfo]:
        ranking_info_list = []
        data = json.loads(response_data)
        rankings = (
            data.get("data", {})
            .get("characterData", {})
            .get("character", {})
            .get("zoneRankings", {})
            .get("rankings", [])
        )
        for ranking in rankings:
            encounter_id = ranking.get("encounter", {}).get("id")
            encounter_name = ranking.get("encounter", {}).get("name")
            rank_percent = ranking.get("rankPercent")
            total_kills = ranking.get("totalKills")
            spec = ranking.get("spec")
            best_amount = ranking.get("bestAmount")
            if rank_percent is not None:
                ranking_info = self.RankingInfo(
                    encounter_id,
                    encounter_name,
                    rank_percent,
                    total_kills,
                    spec,
                    best_amount,
                )
                ranking_info_list.append(ranking_info)
        return ranking_info_list

    def generate_image(
        self, username: str, server: str, ranking_info_list: List[RankingInfo]
    ) -> str:
        width = 660
        height = 170 + len(ranking_info_list) * 80
        image = Image.new("RGB", (width, height), "black")
        draw = ImageDraw.Draw(image)

        # 加载字体
        try:
            font = ImageFont.truetype(self.FONT_PATH, 28)
        except Exception as e:
            print(f"Error loading font: {e}")
            return ""

        # 绘制标题
        draw.text((10, 60), f"{username} - {server}", fill="white", font=font)
        draw.text((20, 130), "Boss", fill=(180, 189, 255), font=font)
        draw.text((290, 130), "Best", fill=(180, 189, 255), font=font)
        draw.text((410, 130), "Highest RDPS", fill=(180, 189, 255), font=font)
        draw.text((590, 130), "Kills", fill=(180, 189, 255), font=font)

        # 绘制每个 RankingInfo
        y_offset = 160
        for ranking_info in ranking_info_list:
            self.draw_ranking_info(draw, ranking_info, y_offset, font, image)
            y_offset += 80

        # 保存图片
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        file_path = f"data/image/ff14/logs/image_{timestamp}.png"
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        image.save(file_path)
        return file_path

    def draw_ranking_info(
        self,
        draw: ImageDraw.Draw,
        ranking_info: RankingInfo,
        y_offset: int,
        font: ImageFont.FreeTypeFont,
        image: Image.Image,
    ):
        # 绘制 Boss 图标和名字
        try:
            boss_icon = Image.open(
                BytesIO(httpx.get(ranking_info.get_boss_url()).content)
            )
            boss_icon = boss_icon.resize((64, 64))
            image.paste(boss_icon, (10, y_offset + 8))
            draw.text(
                (84, y_offset + 20),
                ranking_info.encounter_name,
                fill=(180, 189, 255),
                font=font,
            )
        except Exception as e:
            print(f"Error drawing boss icon: {e}")

        # 绘制 rankPercent
        rank_color = self.get_rank_color(ranking_info.rank_percent)
        draw.text(
            (290, y_offset + 20),
            str(int(ranking_info.rank_percent)),
            fill=rank_color,
            font=font,
        )

        # 绘制 Job 图标
        try:
            job_icon = Image.open(
                BytesIO(httpx.get(ranking_info.get_job_icon_url()).content)
            )
            job_icon = job_icon.resize((32, 32))
            # 确保图标背景透明
            if job_icon.mode != "RGBA":
                job_icon = job_icon.convert("RGBA")

            # 创建一个新的透明背景图像
            transparent_icon = Image.new("RGBA", job_icon.size, (0, 0, 0, 0))
            transparent_icon.paste(job_icon, (0, 0), job_icon)  # 将图标粘贴到透明背景上

            # 绘制职业图标
            image.paste(
                transparent_icon, (320, y_offset + 20), transparent_icon
            )  # 使用透明背景
        except Exception as e:
            print(f"Error drawing job icon: {e}")

        # 绘制 totalKills 和 bestAmount
        draw.text(
            (610, y_offset + 20),
            str(ranking_info.total_kills),
            fill=(225, 242, 245),
            font=font,
        )
        draw.text(
            (450, y_offset + 20),
            f"{ranking_info.best_amount:.2f}",
            fill=(180, 189, 255),
            font=font,
        )

    def get_rank_color(self, rank: float) -> tuple:
        if rank == -1:
            return (128, 128, 128)
        if rank == 100:
            return (229, 204, 128)
        if rank >= 99:
            return (226, 104, 168)
        if rank >= 95:
            return (255, 128, 0)
        if rank >= 75:
            return (163, 53, 238)
        if rank >= 50:
            return (0, 112, 255)
        if rank >= 25:
            return (30, 255, 0)
        return (128, 128, 128)

    @bot.group_event()
    async def handle_ff14_logs(self, input: GroupMessage):
        message_parts = input.raw_message.split(" ")
        if len(message_parts) != 3 or message_parts[0] != "搜索玩家logs":
            return

        character_name = message_parts[1]
        server = message_parts[2]
        if character_name == "武术有栖" and server == "延夏":
            await input.add_text("?").reply()
            return

        combined_ranking_infos = []
        for zone in self.zones:
            if zone.name == "阿卡狄亚竞技场 轻量级":
                data = self.get_data(character_name, server, zone.id)
                if data:
                    try:
                        ranking_infos = self.parse_response_data(data)
                        combined_ranking_infos.extend(ranking_infos)
                    except Exception as e:
                        print(f"Error parsing data: {e}")

        if not combined_ranking_infos:
            await input.add_text("搜索不到该玩家或已隐藏").reply()
        else:
            image_path = self.generate_image(
                character_name, server, combined_ranking_infos
            )
            await input.add_text(
                "提醒:请谨慎使用该功能，用户使用引起其他玩家不快将会被禁止使用该功能。"
            ).reply()
            await input.add_image(image_path).reply()
