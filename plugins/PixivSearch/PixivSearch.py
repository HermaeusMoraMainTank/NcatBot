import hashlib
import random
import requests
from urllib.parse import urlencode, quote
from pathlib import Path

from ncatbot.core.message import GroupMessage, Image, MessageChain
from ncatbot.plugin.base_plugin import BasePlugin
from ncatbot.plugin.event import CompatibleEnrollment

from NcatBot.common.constants import HMMT

last_page = {}
dir_path = Path("data/image/pixiv")

bot = CompatibleEnrollment


class PixivSearch(BasePlugin):
    def __init__(self):
        pass

    @bot.group_event()
    async def handle_pixiv_search(self, input: GroupMessage):
        commands = input.raw_message.split()
        if len(commands) < 2:
            return

        if len(commands) == 3 and commands[2].lower() == "r18":
            if input.user_id == 1271701079 or input.user_id == 273421673:
                await input.add_text("等一下").reply()
                return
            await input.add_text("你搜牛魔的r18，这里是qq群").reply()
            return

        if commands[0] == "搜索图片":
            keyword = commands[1]
            if keyword in ["物述有栖", "mononobe alice", "r18"]:
                return
            # if keyword  in ["珂莱塔",  "cartethyia"]:
            #     keyword == "钟离"
            page = last_page.get(keyword, 1)
            if keyword in last_page:
                page = random.randint(1, last_page[keyword])
            else:
                page = 1

            search_result, err = self.search_pixiv(keyword, False, page)
            if err:
                print(err)
                return

            if not search_result.get("body", {}).get("illustManga", {}).get("data"):
                return

            last_page[keyword] = search_result["body"]["illustManga"]["lastPage"]
            illusion_data = random.choice(search_result["body"]["illustManga"]["data"])
            illusion_id = illusion_data["id"]

            illusion_detail, err = self.get_illusion(illusion_id)
            if err:
                print(err)
                return

            image_url = illusion_detail["body"]["urls"]["original"]
            image_path = self.download_image(image_url)
            if image_path:
                await self.api.post_group_msg(group_id=input.group_id, rtf=MessageChain(
                    [
                        Image(image_path),
                    ]
                ))

    def download_image(self, url):
        headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "cache-control": "max-age=0",
            "priority": "u=0, i",
            "referer": "https://www.pixiv.net/",
            "sec-ch-ua": '"Not A(Brand";v="8", "Chromium";v="132", "Microsoft Edge";v="132"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "cross-site",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 Edg/132.0.0.0",
        }

        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            image_data = response.content
            if len(image_data) > 1000:
                image_hash = hashlib.md5(image_data).hexdigest()
                image_path = dir_path / image_hash
                with open(image_path, "wb") as f:
                    f.write(image_data)
                return str(image_path)
        return ""

    def search_pixiv(self, keyword, r18=False, page=1):
        excluded_tags = ["ai", "r18"]  # 要排除的标签
        excluded_str = " ".join([f"-{tag}" for tag in excluded_tags])
        search_keyword = f"{keyword} {excluded_str}".strip()

        params = {
            "word": search_keyword,
            "order": "date_d",
            "mode": "r18" if r18 else "safe",
            "p": page,
            "csw": "0",
            "s_mode": "s_tag",
            "type": "all",
            "lang": "zh",
            "version": "1514cd4826094c32a804b4de6def5f2209963922",
        }
        base_url = f"https://www.pixiv.net/ajax/search/artworks/{quote(search_keyword)}"

        url = base_url + "?" + urlencode(params)

        headers = {
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "Baggage": "sentry-environment=production,sentry-release=1514cd4826094c32a804b4de6def5f2209963922,sentry-public_key=7b15ebdd9cf64efb88cfab93783df02a,sentry-trace_id=96c04fd1748e43b5b58a8cc1007eef1c,sentry-sample_rate=0.0001",
            "Cache-Control": "max-age=0",
            "Cookie": "first_visit_datetime_pc=2024-10-27%2021%3A31%3A40; p_ab_id=2; p_ab_id_2=4; p_ab_d_id=692958056; yuid_b=QHl0dBI; c_type=25; privacy_policy_notification=0; a_type=0; b_type=1; PHPSESSID=40581705_rQmgRA1guZzvkWGVwysacRwWo2CcGh2C; device_token=6a83fbbbdb3dc55a762744511e49b9ca; _ga_MZ1NL4PHH0=GS1.1.1736429551.2.0.1736429573.0.0.0; privacy_policy_agreement=0; login_ever=yes; _gcl_au=1.1.987029509.1737216645; _gid=GA1.2.1484901853.1738498405; __cf_bm=wMi5L6y00VipuLNbBY4G3Ja9g2f6biFursX0W7PuHkc-1738505887-1.0.1.1-_xQv41W7vDvhF05MPgEFnO7ItcAQ1OugaUdT.AvsAHrszai3AgG5kY1Dw64jwjgUG7NvrcafLaTTMTGARgmrPLbDBsXaYtGvKzFo3wj.MVY; cf_clearance=AvLy6nioY1GkLeyqwVqEsTXh.a1IuxMKDRhK5bLXiqg-1738506871-1.2.1.1-O69GPQCbokRrCNUjwW.bSqjjdomkxW.e6ilk_BiRv__BXHX2lZPDwIM9py0OvU9Dy3Er5bDbS0385oZWusSUCmp4zh8AZzAeS2BsYKrTp0xduZn_phVENKu1lgfUimi2J3MCgCxTstdfMn_ESkWGwENXSor_Y_BQOLJ594BWls1rWo4aXvlxhQP2lmIzrCfUQD_C_sUONQCNGt.LiHPR8jAmNthvcqnusCY9y29bJaSnjf1gx_I3NS43vm1ARv1Is_pM4lmQ_pXt4HFlT8VjU8p1uIQoK4qrI4igi_OOfWQ; _gat_UA-1830249-3=1; _ga=GA1.1.1094039263.1730032302; _ga_75BBYNYN9J=GS1.1.1738505888.4.1.1738506875.0.0.0",
            "Priority": "u=1, i",
            "Referer": f"https://www.pixiv.net/tags/{quote(search_keyword)}/artworks",
            "Sec-CH-UA": '"Not A(Brand";v="8", "Chromium";v="132", "Microsoft Edge";v="132"',
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": HMMT.USER_AGENT,
        }

        response = requests.get(url, headers=headers)
        response.encoding = "utf-8"
        if response.status_code == 200:
            return response.json(), None
        else:
            return None, response.status_code

    def get_illusion(self, illusion_id):
        base_url = f"https://www.pixiv.net/ajax/illust/{illusion_id}"
        params = {"lang": "zh", "version": "1514cd4826094c32a804b4de6def5f2209963922"}
        url = base_url + "?" + urlencode(params)

        headers = {
            "Accept": "application/json",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "Baggage": "sentry-environment=production,sentry-release=1514cd4826094c32a804b4de6def5f2209963922,sentry-public_key=7b15ebdd9cf64efb88cfab93783df02a,sentry-trace_id=34b30c0222da402190dab7f95ab1ca69,sentry-sample_rate=0.0001",
            "Cache-Control": "max-age=0",
            "Cookie": "first_visit_datetime_pc=2024-10-27%2021%3A31%3A40; p_ab_id=2; p_ab_id_2=4; p_ab_d_id=692958056; yuid_b=QHl0dBI; c_type=25; privacy_policy_notification=0; a_type=0; b_type=1; PHPSESSID=40581705_rQmgRA1guZzvkWGVwysacRwWo2CcGh2C; device_token=6a83fbbbdb3dc55a762744511e49b9ca; _ga_MZ1NL4PHH0=GS1.1.1736429551.2.0.1736429573.0.0.0; privacy_policy_agreement=0; login_ever=yes; _gcl_au=1.1.987029509.1737216645; _gid=GA1.2.1484901853.1738498405; __cf_bm=wMi5L6y00VipuLNbBY4G3Ja9g2f6biFursX0W7PuHkc-1738505887-1.0.1.1-_xQv41W7vDvhF05MPgEFnO7ItcAQ1OugaUdT.AvsAHrszai3AgG5kY1Dw64jwjgUG7NvrcafLaTTMTGARgmrPLbDBsXaYtGvKzFo3wj.MVY; cf_clearance=AvLy6nioY1GkLeyqwVqEsTXh.a1IuxMKDRhK5bLXiqg-1738506871-1.2.1.1-O69GPQCbokRrCNUjwW.bSqjjdomkxW.e6ilk_BiRv__BXHX2lZPDwIM9py0OvU9Dy3Er5bDbS0385oZWusSUCmp4zh8AZzAeS2BsYKrTp0xduZn_phVENKu1lgfUimi2J3MCgCxTstdfMn_ESkWGwENXSor_Y_BQOLJ594BWls1rWo4aXvlxhQP2lmIzrCfUQD_C_sUONQCNGt.LiHPR8jAmNthvcqnusCY9y29bJaSnjf1gx_I3NS43vm1ARv1Is_pM4lmQ_pXt4HFlT8VjU8p1uIQoK4qrI4igi_OOfWQ; _gat_UA-1830249-3=1; _ga=GA1.1.1094039263.1730032302; _ga_75BBYNYN9J=GS1.1.1738505888.4.1.1738506875.0.0.0",
            "Priority": "u=1, i",
            "Referer": f"https://www.pixiv.net/artworks/{illusion_id}",
            "Sec-CH-UA": '"Not A(Brand";v="8", "Chromium";v="132", "Microsoft Edge";v="132"',
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": HMMT.USER_AGENT,
        }

        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json(), None
        else:
            return None, response.status_code
