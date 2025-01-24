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


bot = BotClient()

@bot.group_event
async def on_group_message(msg:GroupMessage):
    if msg.user_id == 771575637:
        return
    _log.info(f"收到群消息，Time:{formatted_time}，ID: {msg.user_id}，内容：{msg.raw_message}")
    await today_waifu.handle_message(input=msg)
    await jrrp.handle_jrrp(input=msg)
    await http_cat.http_cat(input=msg)
    await crazy_thursday.handle_crazy_thursday(input=msg)
    await daily_3_min.handle_daily3min(input=msg)
    await moyu.handle_moyu(input=msg)
    await meme.handle_meme(input=msg)
    await send_like.handle_send_like(input=msg)
    await fake_ai.handle_fake_ai(input=msg)
    await russian_roulette.handle_message(input=msg)
    await status.handle_status(input=msg)
    await lottery.handle_lottery(input=msg)
    await universalis.handle_universalis(input=msg)
    await tarot.handle_tarot(input=msg)
    await group_recall.handle_group(input=msg)
    await crazy.handle_crazy(input=msg)
    await ff14_logs_info.handle_ff14_logs_info(input=msg)
    await ff14_rising_stone_info.handle_ff14_rising_stone_info(input=msg)


@bot.private_event
async def on_private_message(msg:PrivateMessage):
    _log.info(f"收到私聊消息，ID: {msg.user_id}，{msg.message}")

@bot.notice_event
async def on_notice(msg):
    _log.info(f"监听到事件，{msg}")
    await nudgeEvent.handle_nudge(input=msg)
    # await group_recall.handle_notice(input=message)
@bot.request_event
async def on_request(msg):
    print(msg)

bot.run(reload=True)




