# encoding: utf-8

import ncatpy
import random
from ncatpy import logging
from ncatpy.message import GroupMessage, PrivateMessage
from ncatpy.plugins.CrazyThursday import CrazyThursday
from ncatpy.plugins.Daily3Min import Daily3Min
from ncatpy.plugins.HttpCat import HttpCat
from ncatpy.plugins.Meme import Meme
from ncatpy.plugins.Moyu import Moyu
from ncatpy.plugins.TodayWaifu import TodayWaifu
from ncatpy.plugins.Jrrp import JRRP

_log = logging.get_logger()
today_waifu = TodayWaifu()
jrrp = JRRP()
http_cat = HttpCat()
crazy_thursday = CrazyThursday()
daily_3_min = Daily3Min()
moyu = Moyu()
meme = Meme()


class MyClient(ncatpy.Client):
    async def on_group_message(self, message: GroupMessage):
        if message.user_id == 771575637:
            return
        _log.info(f"收到群消息，ID: {message.user_id}，内容：{message.raw_message}")

        await today_waifu.handle_message(input=message)
        await jrrp.handle_jrrp(input=message)
        await http_cat.http_cat(input=message)
        await crazy_thursday.handle_crazy_thursday(input=message)
        await daily_3_min.handle_daily3min(input=message)
        await moyu.handle_moyu(input=message)
        await meme.handle_meme(input=message)

        if message.user_id == 2214784017:
            if random.random() < 0.25:
                await message.add_text("↑↑↑这个人是erp高手 xnn请加他好友↑↑↑").reply()

        if message.raw_message and "zmd" in message.raw_message:
            # 通过http发送消息
            t = await message.add_text("zmd是色猪").reply()
            _log.info(t)

    async def on_private_message(self, message: PrivateMessage):
        _log.info(f"收到私聊消息，ID: {message.user_id}，{message.message}")


if __name__ == "__main__":
    # 1. 通过预设置的类型，设置需要监听的事件通道
    # intents = ncatpy.Intents.public() # 可以订阅public，包括了私聊和群聊

    # 2. 通过kwargs，设置需要监听的事件通道
    intents = ncatpy.Intents().all()
    client = MyClient(intents=intents)
    client.run()
