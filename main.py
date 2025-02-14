import json
import os
from pathlib import Path

from pyexpat.errors import messages

import ncatpy
from ncatpy import logging
from ncatpy.message import GroupMessage, PrivateMessage, NoticeMessage, BaseMessage
from ncatpy.plugins.CrazyThursday import CrazyThursday
from ncatpy.plugins.Daily3Min import Daily3Min
from ncatpy.plugins.FF14LogsInfo import FF14LogsInfo
from ncatpy.plugins.FakeAi import FakeAi
from ncatpy.plugins.Fortune import Fortune
from ncatpy.plugins.GroupRecall import GroupRecall
from ncatpy.plugins.HttpCat import HttpCat
from ncatpy.plugins.Lalafell import Lalafell
from ncatpy.plugins.Lottery import Lottery
from ncatpy.plugins.Meme import Meme
from ncatpy.plugins.Moyu import Moyu
from ncatpy.plugins.PixivSearch import PixivSearch
from ncatpy.plugins.RussianRoulette import RussianRoulette
from ncatpy.plugins.SendLike import SendLike
from ncatpy.plugins.Tarot import Tarot
from ncatpy.plugins.TodayWaifu import TodayWaifu
from ncatpy.plugins.Jrrp import JRRP
from ncatpy.plugins.Status import Status
from ncatpy.plugins.Universalis import Universalis
from ncatpy.plugins.NudgeEvent import NudgeEvent
from ncatpy.plugins.Crazy import Crazy
from ncatpy.plugins.FF14RisingStoneInfo import FF14RisingStoneInfo
from ncatpy.plugins.Reboot import Reboot

from ncatpy.plugins.Choujiang import Choujiang
from datetime import datetime

# 格式化时间
_log = logging.get_logger()
today_waifu = TodayWaifu()
jrrp = JRRP()
http_cat = HttpCat()
crazy_thursday = CrazyThursday()
daily_3_min = Daily3Min()
moyu = Moyu()
send_like = SendLike()
fake_ai = FakeAi()
russian_roulette = RussianRoulette()
status = Status()
lottery = Lottery()
universalis = Universalis()
tarot = Tarot()
crazy = Crazy()
ff14_logs_info = FF14LogsInfo()
ff14_rising_stone_info = FF14RisingStoneInfo()
fortune = Fortune()
lalafell = Lalafell()
reboot = Reboot()
pixiv_search = PixivSearch()
choujiang = Choujiang()
# meme = Meme()

nudge_event = NudgeEvent()
group_recall = GroupRecall()
class Ban:
    def __init__(self):
        self.adminid=[1271701079,273421673]
        self.conlist=["ban","unban"]
        self.bandata ={11:[22]}
        self.dir_path = Path("data/json")
        self.initdata()

    def chackban(self, input:GroupMessage):
        if input.group_id not in self.bandata:
            self.bandata[input.group_id] = []  #如果群数据不存在 创建一个
        if input.user_id in self.adminid:
            if len(input.message)==2:
                if input.message[0].get("data").get("text").strip()== "ban" and input.message[1].get("data").get("qq")!=None:
                    if not (input.message[1].get("data").get("qq") in self.bandata[input.group_id]):
                        self.bandata[input.group_id].append(input.message[1].get("data").get("qq"))
                        self.writedata()
                if input.message[0].get("data").get("text").strip()== "unban" and input.message[1].get("data").get("qq")!=None:
                    if input.message[1].get("data").get("qq") in self.bandata[input.group_id]:
                        self.bandata[input.group_id].remove(input.message[1].get("data").get("qq"))
                        self.writedata()
        if str(input.user_id) in self.bandata[input.group_id]:
            print("返回1")
            return True
        print("返回0")
        return False
        pass
    def initdata(self):
        if os.path.isfile(f"{self.dir_path}/bandata.json"):
            with open(f"{self.dir_path}/bandata.json") as f:
                self.bandata = json.load(f)
                return
        else:
            with open(f"{self.dir_path}/bandata.json", "w") as f:
                json.dump(self.bandata, f)
    def writedata(self):
        with open(f"{self.dir_path}/bandata.json", "w") as f:
            json.dump(self.bandata, f)
b=Ban()
class MyClient(ncatpy.Client):
    async def on_group_message(self, message: GroupMessage):
        # if message.group_id != 1064163905:
        #     return
        if b.chackban(input=message):
            return
        if message.user_id == 771575637:
            return

        _log.info(
            f"收到群消息，Time:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}，群ID：{message.group_id}，ID: {message.user_id}，内容：{message.raw_message}")
        await today_waifu.handle_message(input=message)
        await jrrp.handle_jrrp(input=message)
        await http_cat.http_cat(input=message)
        await crazy_thursday.handle_crazy_thursday(input=message)
        await daily_3_min.handle_daily3min(input=message)
        await moyu.handle_moyu(input=message)
        await send_like.handle_send_like(input=message)
        await fake_ai.handle_fake_ai(input=message)
        await russian_roulette.handle_message(input=message)
        await status.handle_status(input=message)
        await lottery.handle_lottery(input=message)
        await universalis.handle_universalis(input=message)
        await tarot.handle_tarot(input=message)
        await group_recall.handle_group(input=message)
        await crazy.handle_crazy(input=message)
        await ff14_logs_info.handle_ff14_logs_info(input=message)
        await ff14_rising_stone_info.handle_ff14_rising_stone_info(input=message)
        await fortune.handle_fortune(input=message)
        await lalafell.handle_lalafell(input=message)
        await pixiv_search.handle_pixiv_search(input=message)
        # await choujiang.handle_choujiang(input=message)
        # await meme.handle_meme(input=message)

    async def on_private_message(self, message: PrivateMessage):
        _log.info(f"收到私聊消息，ID: {message.user_id}，{message.message}")
        await reboot.Reboot(input=message)

    async def on_notice(self, message: NoticeMessage):
        _log.info(f"监听到事件，{message}")
        await nudge_event.handle_nudge(input=message)
        # await group_recall.handle_notice(input=message)


if __name__ == "__main__":
    # 1. 通过预设置的类型，设置需要监听的事件通道
    # intents = ncatpy.Intents.public() # 可以订阅public，包括了私聊和群聊
    # 2. 通过kwargs，设置需要监听的事件通道
    intents = ncatpy.Intents().all()
    client = MyClient(intents=intents)
    client.run()