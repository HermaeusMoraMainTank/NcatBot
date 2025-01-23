# encoding: utf-8
import time

import ncatpy
import random
from ncatpy import logging
from ncatpy.message import GroupMessage, PrivateMessage, NoticeMessage
from ncatpy.plugins.CrazyThursday import CrazyThursday
from ncatpy.plugins.Daily3Min import Daily3Min
from ncatpy.plugins.FF14LogsInfo import FF14LogsInfo
from ncatpy.plugins.FakeAi import FakeAi
from ncatpy.plugins.GroupRecall import GroupRecall
from ncatpy.plugins.HttpCat import HttpCat
from ncatpy.plugins.Lottery import Lottery
from ncatpy.plugins.Meme import Meme
from ncatpy.plugins.Moyu import Moyu
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
from datetime import datetime
import os

# 获取当前时间
current_time = datetime.now()

# 格式化时间
formatted_time = current_time.strftime("%Y-%m-%d %H:%M:%S")
_log = logging.get_logger()
today_waifu = TodayWaifu()
jrrp = JRRP()
http_cat = HttpCat()
crazy_thursday = CrazyThursday()
daily_3_min = Daily3Min()
moyu = Moyu()
meme = Meme()
send_like = SendLike()
fake_ai = FakeAi()
russian_roulette = RussianRoulette()
status = Status()
lottery = Lottery()
universalis = Universalis()
tarot = Tarot()
nudgeEvent = NudgeEvent()
group_recall = GroupRecall()
crazy = Crazy()
ff14_logs_info = FF14LogsInfo()
ff14_rising_stone_info = FF14RisingStoneInfo()

class MyClient(ncatpy.Client):
    async def on_group_message(self, message: GroupMessage):
        # if message.group_id != 1064163905:
        #     return

        if message.user_id == 771575637:
            return
        _log.info(f"收到群消息，Time:{formatted_time}，ID: {message.user_id}，内容：{message.raw_message}")
        await today_waifu.handle_message(input=message)
        await jrrp.handle_jrrp(input=message)
        await http_cat.http_cat(input=message)
        await crazy_thursday.handle_crazy_thursday(input=message)
        await daily_3_min.handle_daily3min(input=message)
        await moyu.handle_moyu(input=message)
        await meme.handle_meme(input=message)
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

        # if message.user_id == 2214784017:
        #     if random.random() < 0.25:
        #         await message.add_text("↑↑↑这个人是erp高手 xnn请加他好友↑↑↑").reply()
        #
        # if message.raw_message and "zmd" in message.raw_message:
        #     t = await message.add_text("zmd是色猪").reply()
        #     _log.info(t)

    async def on_private_message(self, message: PrivateMessage):
        _log.info(f"收到私聊消息，ID: {message.user_id}，{message.message}")

    async def on_notice(self, message: NoticeMessage):
        _log.info(f"监听到事件，{message}")
        await nudgeEvent.handle_nudge(input=message)
        # await group_recall.handle_notice(input=message)


if __name__ == "__main__":
    # 1. 通过预设置的类型，设置需要监听的事件通道
    # intents = ncatpy.Intents.public() # 可以订阅public，包括了私聊和群聊
    # 2. 通过kwargs，设置需要监听的事件通道
    intents = ncatpy.Intents().all()
    client = MyClient(intents=intents)
    client.run()
