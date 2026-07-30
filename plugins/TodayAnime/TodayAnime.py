from datetime import datetime
import aiohttp

from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.types import MessageArray as MessageChain, PlainText
from ncatbot.plugin import NcatBotPlugin
from ncatbot.core import registrar
from ncatbot.utils import get_log
from common.utils.plugin_commands import format_help, is_help_message

_log = get_log()

COMMAND_TODAY_ANIME = "今日番剧"

HELP_TEXT = format_help(
    "TodayAnime 今日番剧",
    [
        f"{COMMAND_TODAY_ANIME}：从 Bangumi 日历获取今日更新的番剧列表",
    ],
)


class TodayAnime(NcatBotPlugin):
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

    @registrar.qq.on_group_message()
    async def handle_TodayAnime_like(self, input: GroupMessage):
        raw = input.raw_message.strip()
        if is_help_message(
            raw,
            command_names=(COMMAND_TODAY_ANIME,)):
            await input.reply(text=HELP_TEXT, at_sender=False)
            return
        if raw != COMMAND_TODAY_ANIME:
            return
        data = await self.fetch_today_anime()
        if data is None:
            _log.error("data搜索数据为空")
            return
        todaydata = self.format_anime_data(data)
        if len(todaydata) == 0:
            _log.error("todaydata数据为空")
            return
        refdata = []
        for i in todaydata:
            refdata.append(PlainText(text=f"番剧名称:{i.get('title')}"))
            refdata.append(self.toimg((i.get("image"))))
            refdata.append(PlainText(text=f"更新时间:{i.get('air_date')}\n"))
        await self.api.qq.post_group_msg(
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
