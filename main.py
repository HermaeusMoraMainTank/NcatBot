from datetime import datetime
from ncatbot.core import BotClient, GroupMessage, PrivateMessage
from ncatbot.utils import config, get_log

_log = get_log()

# 群名称缓存
_group_name_cache = {}

config.set_bot_uin("3555202423")  # 设置 bot qq 号 (必填)
config.set_root("273421673")  # 设置 bot 超级管理员账号 (建议填写)
config.set_ws_uri("ws://127.0.0.1:3002")  # 设置 napcat websocket server 地址
config.set_ws_token("y-6u8nt[nfuftYnE")  # 设置 token (websocket 的 token)
config.set_webui_uri("http://127.0.0.1:6099")  # 设置 napcat webui 地址
config.set_webui_token("a4ee53569810")  # 设置 token (webui 的 token)

bot = BotClient()


@bot.group_event()
async def on_group_message(message: GroupMessage):
    if "四个字" in message.raw_message and message.user_id == "635773721":
        await bot.api.post_group_msg(message.group_id, text="飞舞白墨")

    if "没有人喜欢我" in message.raw_message:
        await bot.api.post_group_msg(message.group_id, text="我喜欢你饱饱")

    if "消失了" in message.raw_message:
        await bot.api.post_group_msg(message.group_id, text="别消失")

    # 替换 &amp; 为 &
    processed_message = message.raw_message.replace("&amp;", "&")

    # 获取群名称（带缓存）
    group_id = str(message.group_id)
    if group_id not in _group_name_cache:
        try:
            group_info = await bot.api.get_group_info(group_id)
            _group_name_cache[group_id] = group_info.group_name
        except Exception:
            _group_name_cache[group_id] = group_id
    group_name = _group_name_cache[group_id]

    _log.info(
        f"收到群消息，Time:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}，群：{group_name}({group_id})，用户：{message.sender.nickname}({message.user_id})，内容：{processed_message}"
    )


@bot.private_event()
def on_private_message(msg: PrivateMessage):
    _log.info(msg)
    if msg.raw_message == "测试":
        bot.api.post_private_msg_sync(msg.user_id, text="NcatBot 测试成功喵~")


if __name__ == "__main__":
    bot.run()
