from datetime import datetime
import aiohttp

from ncatbot.core import MessageChain, Text, Image
from ncatbot.core.message import GroupMessage
from ncatbot.plugin import CompatibleEnrollment, BasePlugin
from ncatbot.utils import get_log

bot = CompatibleEnrollment
_log = get_log()


class TodayAnime(BasePlugin):
    name = "TodayAnime"
    description = "今日番剧"
    version = "1.0"
    author = "xww"
    apiurl = "https://api.bgm.tv/calendar"
    weekday_map = {
        "Monday": "星期一",
        "Tuesday": "星期二",
        "Wednesday": "星期三",
        "Thursday": "星期四",
        "Friday": "星期五",
        "Saturday": "星期六",
        "Sunday": "星期日",
    }

    async def on_load(self):
        """异步加载插件"""
        _log.info(f"开始加载 {self.name} 插件 v{self.version}")
        _log.info(f"{self.name} 插件加载完成")

    @bot.group_event()
    async def handle_TodayAnime_like(self, input: GroupMessage):
        if input.raw_message != "今日番剧":
            return
        data = await self.fetch_today_anime()
        if data == None:
            _log.error("data搜索数据为空")
            return
        todaydata = self.format_anime_data(data)
        if len(todaydata) == 0:
            _log.error("todaydata数据为空")
            return
        refdata = []
        for i in todaydata:
            refdata.append(Text(f"番剧名称:{i.get('title')}"))
            refdata.append(self.toimg((i.get("image"))))
            refdata.append(Text(f"更新时间:{i.get('air_date')}\n"))
        await self.api.post_group_msg(
            group_id=input.group_id,
            rtf=MessageChain(refdata),
        )

    async def fetch_today_anime(self):
        async with aiohttp.ClientSession() as session:
            async with session.get(self.apiurl) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data
                else:
                    return None

    def format_anime_data(self, data):
        today = datetime.now().strftime("%A")
        today_cn = self.weekday_map.get(today, "")
        today_anime = []
        for weekday in data:
            if weekday["weekday"]["cn"] == today_cn:
                for item in weekday["items"]:
                    image_url = item["images"]["large"]
                    anime_info = {
                        "title": item.get("name_cn", item["name"]),
                        "image": image_url,
                        "air_date": item["air_date"],
                    }
                    today_anime.append(anime_info)
        return today_anime

    def toimg(self, url):
        return {"type": "image", "data": {"file": url}}
