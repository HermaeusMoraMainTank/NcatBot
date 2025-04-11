from datetime import datetime
from ncatbot.core import BotClient, GroupMessage, PrivateMessage
from ncatbot.utils.logger import get_log
from ncatbot.utils.config import config
_log = get_log()


config.set_bot_uin("3555202423")  # 设置 bot qq 号 (必填)
config.set_root("273421673")  # 设置 bot 超级管理员账号 (建议填写)
config.set_ws_uri("ws://localhost:3001")  # 设置 napcat websocket server 地址

bot = BotClient()


@bot.group_event()
async def on_group_message(message: GroupMessage):
    # 替换 &amp; 为 &
    processed_message = message.raw_message.replace("&amp;", "&")
    _log.info(
        f"收到群消息，Time:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}，群ID：{message.group_id}，ID: {message.user_id}，昵称：{message.sender.nickname}，内容：{processed_message}"
    )


@bot.private_event()
def on_private_message(msg: PrivateMessage):
    _log.info(msg)
    if msg.raw_message == "测试":
        bot.api.post_private_msg_sync(msg.user_id, text="NcatBot 测试成功喵~")


if __name__ == "__main__":
    bot.run()
