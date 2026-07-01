from PIL import Image as PILImage, ImageDraw, ImageFont
from datetime import datetime
import aiohttp
import os
import math
from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.types import Image, MessageArray as MessageChain, Reply
from ncatbot.plugin import NcatBotPlugin
from ncatbot.core import registrar

import json
from ncatbot.utils import get_log

_log = get_log()


class FF14House(NcatBotPlugin):
    name = "FF14House"  # 插件名称
    version = "1.0"  # 插件版本
    FONT_PATH = "data/font/FZMiaoWuK.TTF"
    CITY_IMAGE_PATH = "data/image/ff14/city/city.jpg"

    area_map = {
        "-1": "所有区域",
        "0": "海雾村",
        "1": "薰衣草苗圃",
        "2": "高脚孤丘",
        "3": "白银乡",
        "4": "穹顶皓天",
    }

    # 反向映射，用于根据名称查找ID
    area_name_to_id = {v: k for k, v in area_map.items()}

    team_map = {"0": "不限", "1": "部队", "2": "个人"}

    server_map = {
        "红玉海": 1167,
        "神意之地": 1081,
        "拉诺西亚": 1042,
        "幻影群岛": 1044,
        "萌芽池": 1060,
        "宇宙和音": 1173,
        "沃仙曦染": 1174,
        "晨曦王座": 1175,
        "白银乡": 1172,
        "白金幻象": 1076,
        "神拳痕": 1171,
        "潮风亭": 1170,
        "旅人栈桥": 1113,
        "拂晓之间": 1121,
        "龙巢神殿": 1166,
        "梦羽宝境": 1176,
        "紫水栈桥": 1043,
        "延夏": 1169,
        "静语庄园": 1106,
        "摩杜纳": 1045,
        "海猫茶屋": 1177,
        "柔风海湾": 1178,
        "琥珀原": 1179,
        "水晶塔": 1192,
        "银泪湖": 1183,
        "太阳海岸": 1180,
        "伊修加德": 1186,
        "红茶川": 1201,
    }

    usage_instructions = """指令使用方法：
1. 必填参数：
   - <server>：服务器名称（如"拂晓之间"）
   - <size>：房屋大小（S, M, L）

2. 可选参数：
   - [team]：房屋类型（部队 或 个人）不填视为不限制
   - [area]：区域名称（海雾村, 薰衣草苗圃, 高脚孤丘, 白银乡, 穹顶皓天）不填视为不限制

示例：
- 搜索房屋 拂晓之间 S 部队
- 搜索房屋 拂晓之间 M 个人 海雾村
"""

    def format_price(self, price: int) -> str:
        """将价格转为"万"单位并去除后面的零"""
        price_in_wan = price / 10000
        return f"{price_in_wan:.1f}万".replace(".0万", "万")

    def get_state(self, state: int) -> str:
        """获取房屋状态"""
        state_map = {
            0: "准备中",
            1: "可供购买",
            2: "结果公示阶段",
        }
        return state_map.get(state, "未知")

    def get_region_type(self, state: int) -> str:
        """获取房屋类型"""
        region_type_map = {1: "部队", 2: "个人"}
        return region_type_map.get(state, "未知")

    def get_size_label(self, size: int) -> str:
        """获取房屋大小标签"""
        size_map = {0: "(S)", 1: "(M)", 2: "(L)"}
        return size_map.get(size, "未知")

    def format_timestamp(self, timestamp: int) -> str:
        """将时间戳转为可读格式"""
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

    async def get_house_data(
        self, server: str, size: str, team: str = None, area: str = None
    ):
        """获取房屋数据"""
        _log.info(f"获取房屋数据: {server}, {size}, {team}, {area}")
        server_id = self.server_map.get(server)
        if not server_id:
            raise ValueError(f"未找到名为 {server} 的服务器，请检查输入。")
        _log.info(f"服务器ID: {server_id}")

        size_id = {"S": 0, "M": 1, "L": 2, "s": 0, "m": 1, "l": 2}.get(size)
        if size_id is None:
            raise ValueError(f"未找到名为 {size} 的房屋类型，请输入 S, M, L")

        # 处理区域参数
        area_id = None
        if area:
            if area in self.area_name_to_id:
                area_id = int(self.area_name_to_id[area])
            else:
                raise ValueError(f"未找到名为 {area} 的区域")

        # 处理团队参数
        team_id = None
        if team:
            if team == "部队":
                team_id = 1
            elif team == "个人":
                team_id = 2
            else:
                raise ValueError(f"未找到名为 {team} 的房屋类型，请输入 部队 或 个人")

        # 构建URL，确保server_id是字符串
        url = f"https://househelper.ffxiv.cyou/api/sales?server={str(server_id)}"

        # 添加请求头，确保正确处理中文
        headers = {
            "User-Agent": "蓝晴bot 2.0.0 / 武术有栖 <273421673@qq.com>",
            "Accept": "application/json",
            "Accept-Charset": "utf-8",
            "Content-Type": "application/json; charset=utf-8",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    response.raise_for_status()
                    data = await response.json()
        except aiohttp.ClientError as e:
            raise ValueError(f"请求失败: {str(e)}")
        except json.JSONDecodeError as e:
            raise ValueError(f"解析响应数据失败: {str(e)}")

        # 筛选符合条件的数据
        filtered_data = [
            item
            for item in data
            if (size_id is None or item["Size"] == size_id)
            and (area_id is None or item["Area"] == area_id)
            and (team_id is None or item["RegionType"] == team_id)
        ]

        return filtered_data

    def get_area_image(self, area_id):
        """根据区域ID获取对应的城市图片部分"""
        if not os.path.exists(self.CITY_IMAGE_PATH):
            raise FileNotFoundError(f"城市图片文件不存在: {self.CITY_IMAGE_PATH}")

        city_image = PILImage.open(self.CITY_IMAGE_PATH)
        width, height = city_image.size

        # 将图片分成5等份，每份对应一个区域
        segment_height = height // 5

        # 区域ID到图片索引的映射
        # 根据反馈调整映射关系：
        # 0: 海雾村 -> 应该显示高脚孤丘的图片(2)
        # 1: 薰衣草苗圃 -> 应该显示海雾村的图片(0)
        # 2: 高脚孤丘 -> 应该显示薰衣草苗圃的图片(1)
        area_to_index = {
            0: 2,  # 海雾村 -> 显示高脚孤丘的图片
            1: 0,  # 薰衣草苗圃 -> 显示海雾村的图片
            2: 1,  # 高脚孤丘 -> 显示薰衣草苗圃的图片
            3: 3,  # 白银乡 -> 保持不变
            4: 4,  # 穹顶皓天 -> 保持不变
        }

        index = area_to_index.get(area_id, 0)

        # 裁剪对应区域的图片
        area_image = city_image.crop(
            (0, index * segment_height, width, (index + 1) * segment_height)
        )
        return area_image.resize((250, 150), PILImage.LANCZOS)  # 调整大小以适应卡片

    def draw_house_data(self, data: list):
        """绘制房屋数据图像，按照一行六个的布局"""
        if not data:
            return PILImage.new("RGB", (800, 100), (255, 255, 255))

        # 设置卡片尺寸和间距
        card_width = 250
        card_height = 280
        horizontal_spacing = 10
        vertical_spacing = 20
        cards_per_row = 6
        corner_radius = 10  # 添加圆角半径

        # 计算总行数
        total_rows = math.ceil(len(data) / cards_per_row)

        # 计算画布尺寸
        canvas_width = (
            cards_per_row * card_width + (cards_per_row - 1) * horizontal_spacing
        )
        canvas_height = total_rows * card_height + (total_rows - 1) * vertical_spacing

        # 创建画布
        image = PILImage.new("RGB", (canvas_width, canvas_height), (255, 255, 255))
        ImageDraw.Draw(image)

        # 加载字体
        try:
            title_font = ImageFont.truetype(self.FONT_PATH, 16)
            info_font = ImageFont.truetype(self.FONT_PATH, 14)
            price_font = ImageFont.truetype(self.FONT_PATH, 18)
            small_font = ImageFont.truetype(self.FONT_PATH, 12)
            ward_font = ImageFont.truetype(self.FONT_PATH, 20)  # 新增加粗字体用于房号
        except Exception as e:
            _log.warning(f"Error loading font: {e}")
            return PILImage.new("RGB", (800, 100), (255, 255, 255))

        # 绘制每个房屋卡片
        for index, item in enumerate(data):
            # 计算卡片位置
            row = index // cards_per_row
            col = index % cards_per_row

            x_start = col * (card_width + horizontal_spacing)
            y_start = row * (card_height + vertical_spacing)

            # 创建一个带圆角的蒙版
            mask = PILImage.new("L", (card_width, card_height), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.rounded_rectangle(
                [(0, 0), (card_width - 1, card_height - 1)],
                radius=corner_radius,
                fill=255,
            )

            # 创建卡片背景
            card = PILImage.new("RGB", (card_width, card_height), (245, 245, 245))
            card_draw = ImageDraw.Draw(card)

            # 获取区域图片并粘贴到卡片上
            try:
                area_image = self.get_area_image(item["Area"])
                card.paste(area_image, (0, 0))
            except Exception as e:
                _log.warning(f"Error pasting area image: {e}")
                card_draw.rectangle(
                    [(0, 0), (card_width, 150)],
                    fill=(200, 200, 220),
                    outline=(180, 180, 200),
                )

            # 绘制区域和大小标题
            area_name = self.area_map.get(str(item["Area"]), "未知区域")
            size_label = self.get_size_label(item["Size"])
            title_text = f"{area_name} {size_label}"

            # 添加图标指示（这里用文字代替）
            card_draw.text((220, 5), "👤", font=title_font, fill=(50, 50, 50))

            # 绘制标题背景半透明条
            card_draw.rectangle([(0, 130), (card_width, 150)], fill=(0, 0, 0, 128))

            # 绘制标题
            card_draw.text(
                (10, 132),
                title_text,
                font=title_font,
                fill=(255, 255, 255),
            )

            # 绘制区号和房号（使用更大更粗的字体）
            ward_text = f"{item['Slot']} 区 {item['ID']} 号"
            card_draw.text(
                (10, 160),
                ward_text,
                font=ward_font,
                fill=(50, 50, 50),
            )

            # 绘制房屋类型
            house_type = "私家小屋 " + ("部队" if item["RegionType"] == 1 else "独家")
            card_draw.text(
                (10, 180),
                house_type,
                font=info_font,
                fill=(50, 50, 50),
            )

            # 绘制状态
            state_text = self.get_state(item["State"])
            card_draw.text(
                (10, 200),
                state_text,
                font=info_font,
                fill=(50, 50, 50),
            )

            # 绘制价格
            price_text = f"{self.format_price(item['Price'])}"
            card_draw.text(
                (10, 220),
                price_text,
                font=price_font,
                fill=(220, 20, 60),
            )

            # 绘制时间信息
            time_text = f"(准测数据) | {datetime.fromtimestamp(item['LastSeen']).strftime('%m-%d %H:%M')} 截止"
            card_draw.text(
                (10, 245),
                time_text,
                font=small_font,
                fill=(100, 100, 100),
            )

            # 绘制更新时间
            update_text = f"{datetime.fromtimestamp(item['FirstSeen']).strftime('%Y-%m-%d %H:%M:%S')} 更新"
            card_draw.text(
                (10, 260),
                update_text,
                font=small_font,
                fill=(100, 100, 100),
            )

            # 将卡片应用圆角蒙版并粘贴到主画布上
            card = PILImage.composite(
                card, PILImage.new("RGB", card.size, (255, 255, 255)), mask
            )
            image.paste(card, (x_start, y_start))

        return image

    @registrar.qq.on_group_message()
    async def handle_ff14house(self, input: GroupMessage):
        message_parts = input.raw_message.split(" ")
        if message_parts[0] != "搜索房屋":
            return
        try:
            if len(message_parts) < 3:
                return self.usage_instructions

            server = message_parts[1]
            size = message_parts[2].upper()
            if size in ["s", "S"]:
                await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text="s房数据过多，请在https://househelper.ffxiv.cyou/#/上进行查看",
                )
                return
            team = None
            area = None

            if len(message_parts) > 3:
                param = message_parts[3]
                if param in ["部队", "个人"]:
                    team = param
                elif param in self.area_map.values():
                    area = param
                else:
                    await self.api.qq.post_group_msg(
                        group_id=input.group_id,
                        text=f"无效的参数: {param}",
                    )
                    return

            if len(message_parts) > 4:
                param = message_parts[4]
                if param in ["部队", "个人"]:
                    if team:
                        await self.api.qq.post_group_msg(
                            group_id=input.group_id,
                            text=f"重复的房屋类型参数: {param}",
                        )
                        return
                    team = param
                elif param in self.area_map.values():
                    if area:
                        await self.api.qq.post_group_msg(
                            group_id=input.group_id,
                            text=f"重复的区域名称参数: {param}",
                        )
                        return
                    area = param
                else:
                    await self.api.qq.post_group_msg(
                        group_id=input.group_id,
                        text=f"无效的参数: {param}",
                    )
                    return

            # 获取房屋数据
            house_data = await self.get_house_data(server, size, team, area)

            if not house_data:
                await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text="未找到符合条件的房屋信息。",
                )
                return

            # 绘制房屋数据图像
            image = self.draw_house_data(house_data)
            # 使用二进制模式保存图片
            with open("house_data.png", "wb") as f:
                image.save(f, "PNG")

            # 发送图像
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain(
                    [
                        Image(file="house_data.png"),
                        Reply(id=input.message_id),
                    ]
                ),
            )

        except Exception as e:
            error_message = (
                f"查询房屋信息时出错了喵: {str(e)}\n\n{self.usage_instructions}"
            )
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                text=error_message,
            )