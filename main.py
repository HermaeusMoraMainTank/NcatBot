from datetime import datetime
from ncatbot.core import BotClient, GroupMessage, PrivateMessage
from ncatbot.utils.config import config
from ncatbot.utils.logger import get_log

_log = get_log()

config.set_bot_uin("3555202423")  # 设置 bot qq 号 (必填)
config.set_ws_uri("ws://localhost:3001")  # 设置 napcat websocket server 地址
config.set_token("")  # 设置 token (napcat 服务器的 token)

bot = BotClient()


@bot.group_event()
async def on_group_message(message: GroupMessage):
    _log.info(
        f"收到群消息，Time:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}，群ID：{message.group_id}，ID: {message.user_id}，内容：{message.raw_message}")



@bot.private_event()
async def on_private_message(msg: PrivateMessage):
    _log.info(msg)
    if msg.raw_message == "测试":
        await bot.api.post_private_msg(msg.user_id, text="NcatBot 测试成功喵~")


if __name__ == "__main__":
    bot.run(reload=False)
