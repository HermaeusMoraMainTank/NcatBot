import logging
from dataclasses import dataclass
from ncatbot.core.message import GroupMessage
import json
import os
import httpx
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from difflib import SequenceMatcher


# 定义数据类
@dataclass
class Item:
    Data: Dict[str, int]


# 日志配置
log = logging.getLogger(__name__)


class Universalis:
    def __init__(self):
        # 加载物品数据
        try:
            with open(os.path.join(os.getcwd(), "data/json/Item.json"), "r", encoding="utf-8") as f:
                data = json.load(f)
                self.item = Item(data)
        except json.JSONDecodeError as e:
            log.error(f"JSON 解码错误: {e}")
            exit(1)
        except FileNotFoundError:
            log.error(f"文件未找到: {os.path.join(os.getcwd(), 'data/json/Item.json')}")
            exit(1)
        except Exception as e:
            log.error(f"发生错误: {e}")
            exit(1)

        # 服务器别名映射
        self.server_alias = {
            "猫": "猫小胖",
            "狗": "豆豆柴",
            "鸟": "陆行鸟",
            "猪": "莫古力",
            "柔风": "柔风海湾",
            "鲸鱼": "静语庄园",
            "琥珀": "琥珀原"
        }

        # 支持的服务器列表
        self.supported_servers = [
            "红玉海", "神意之地", "拉诺西亚", "幻影群岛", "萌芽池", "宇宙和音", "沃仙曦染",
            "晨曦王座", "白银乡", "白金幻象", "神拳痕", "潮风亭", "旅人栈桥", "拂晓之间", "龙巢神殿",
            "梦羽宝境", "紫水栈桥", "延夏", "静语庄园", "摩杜纳", "海猫茶屋", "柔风海湾", "琥珀原",
            "水晶塔", "银泪湖", "太阳海岸", "伊修加德", "红茶川", "黄金谷", "月牙湾", "雪松原"
        ]

        # 支持的大区列表
        self.supported_dc = [
            "陆行鸟", "莫古力", "猫小胖", "豆豆柴"
        ]

        # API 基础 URL
        self.api_url = "https://universalis.app/api/v2/"

    async def handle_universalis(self, input: GroupMessage):
        """
        处理物价查询
        """
        key = input.raw_message.split()
        if len(key) == 3 and key[0] == "物品查询":
            item_name = key[1]
            server_alias = key[2]  # 服务器别名是最后一个参数

            # 检查物品名称是否包含 hq/HQ
            hq = "hq" in item_name.lower()
            if hq:
                item_name = item_name.replace("hq", "").replace("HQ", "").strip()

            # 处理服务器别名
            server_name = self.server_alias.get(server_alias, server_alias)

            # 检查是否为大区名称
            if server_name in self.supported_dc:
                pass  # 直接使用大区名称
            elif server_name in self.supported_servers:
                pass  # 直接使用服务器名称
            else:
                await input.add_text(f"未知的服务器或大区: {server_alias}").reply()
                return

            # 处理物品名称缩写
            item_name = self.handle_item_name_abbr(item_name)

            # 获取物品 ID
            item_id = self.get_item_id(item_name)
            if item_id <= 0:
                # 如果本地找不到，尝试模糊搜索
                item_name, item_id = self.fuzzy_search_item(item_name)
                if item_id <= 0:
                    await input.add_text(f"未找到物品: {item_name}").reply()
                    return

            # 获取市场数据
            data = self.get_data(server_name, item_id)
            if not data:
                await input.add_text("没有找到数据喵").reply()
                return
            if not data.get("listings"):
                await input.add_text("没有数据喵").reply()
                return

            # 格式化输出
            msg = self.format_market_data(data, item_name, server_name, hq)
            await input.add_text(msg).reply()

    def get_item_id(self, item_name: str) -> int:
        """
        获取物品 ID
        """
        if item_name in self.item.Data["data"]:
            log.debug(f"使用本地物品 ID 搜索: {item_name}")
            return self.item.Data["data"][item_name]
        else:
            log.debug(f"未找到物品: {item_name}")
            return 0

    def fuzzy_search_item(self, item_name: str) -> Tuple[str, int]:
        """
        模糊搜索物品名称和 ID
        """
        # 移除 hq/HQ
        item_name = item_name.replace("hq", "").replace("HQ", "").strip()

        # 调用 XIVAPI 进行模糊搜索
        url = f"https://cafemaker.wakingsands.com/search?indexes=Item&string={item_name}&language=cn"
        try:
            res = httpx.get(url)
            if res.status_code == 200:
                data = res.json()
                results = data.get("Results", [])
                if results:
                    # 找到最匹配的物品
                    best_match = max(
                        results,
                        key=lambda x: SequenceMatcher(None, x["Name"], item_name).ratio()
                    )
                    item_name = best_match["Name"]
                    item_id = best_match["ID"]
                    log.debug(f"模糊搜索找到物品: {item_name} (ID: {item_id})")
                    return item_name, item_id
        except Exception as e:
            log.error(f"模糊搜索时发生错误: {e}")

        return item_name, 0

    def get_data(self, server_name: str, item_id: int) -> Optional[Dict]:
        """
        获取市场数据
        """
        url = f"{self.api_url}{server_name}/{item_id}"
        try:
            res = httpx.get(url)
            if res.status_code == 200:
                return res.json()
            else:
                log.error(f"API 请求失败: {res.status_code}")
                return None
        except Exception as e:
            log.error(f"获取市场数据时发生错误: {e}")
            return None

    def format_price(self, num: int) -> str:
        """
        格式化价格
        """
        str_num = str(num)
        if len(str_num) < 5:
            return str_num
        if len(str_num) < 9:
            a = str_num[:-4]
            b = str_num[-4:]
            return f"{a}万{b}"
        a = str_num[:-8]
        b = str_num[-8:]
        c = b[:-4]
        d = b[-4:]
        return f"{a}亿{c}万{d}"

    def format_market_data(self, data: Dict, item_name: str, server_name: str, hq: bool) -> str:
        """
        格式化市场数据，按照 Java 的输出格式
        """
        msg = f"{server_name} 的 {item_name}{' (HQ)' if hq else ''} 数据如下：\n"
        listing_cnt = 0

        for listing in data.get("listings", []):
            # 如果要求 HQ 且当前物品不是 HQ，则跳过
            if hq and not listing.get("hq", False):
                continue

            retainer_name = listing.get("retainerName", "")
            if "dcName" in data:
                retainer_name += f"({listing.get('worldName', '')})"

            # 格式化价格信息
            msg += "{:,d}x{:,d} = {:,d} {} {}\n".format(
                listing.get("pricePerUnit", 0),
                listing.get("quantity", 0),
                listing.get("total", 0),
                "HQ" if listing.get("hq", False) else "  ",
                retainer_name
            )

            listing_cnt += 1
            if listing_cnt >= 10:
                break

        # 添加更新时间
        last_upload_time = data.get("lastUploadTime", 0)
        if last_upload_time:
            last_upload_time_str = datetime.fromtimestamp(last_upload_time / 1000).strftime("%Y-%m-%d %H:%M:%S")
            msg += f"更新时间: {last_upload_time_str}\n"

        if listing_cnt == 0:
            msg = "未查询到数据，咋回事呢？"

        return msg

    def handle_item_name_abbr(self, item_name: str) -> str:
        """
        处理物品名称缩写和别名
        """
        # 处理第二期重建用的物品
        if item_name.startswith("第二期重建用的") and item_name.endswith("(检)"):
            item_name = item_name.replace("(", "（").replace(")", "）")
        if item_name.startswith("第二期重建用的") and not item_name.endswith("（检）"):
            item_name = item_name + "（检）"

        # 处理 G12-G15 地图
        item_name_upper = item_name.upper()
        if item_name_upper == "G12":
            item_name = "陈旧的缠尾蛟革地图"
        elif item_name_upper == "G11":
            item_name = "陈旧的绿飘龙革地图"
        elif item_name_upper == "G10":
            item_name = "陈旧的瞪羚革地图"
        elif item_name_upper == "G9":
            item_name = "陈旧的迦迦纳怪鸟革地图"
        elif item_name_upper == "G8":
            item_name = "陈旧的巨龙革地图"
        elif item_name_upper == "G7":
            item_name = "陈旧的飞龙革地图"
        elif item_name_upper == "G13":
            item_name = "陈旧的赛加羚羊革地图"
        elif item_name_upper == "G14":
            item_name = "陈旧的金毗罗鳄革地图"
        elif item_name_upper == "G15":
            item_name = "陈旧的蛇牛革地图"
        elif item_name == "绿图":
            item_name = "鞣革制的隐藏地图"

        return item_name
