from ncatbot.plugin import BasePlugin, CompatibleEnrollment
from ncatbot.core.request import Request
from ncatbot.core.message import GroupMessage, PrivateMessage
from ncatbot.core.element import At, MessageChain, Text
from ncatbot.utils.logger import get_log
import html

_log = get_log()
bot = CompatibleEnrollment


class AdminNotify(BasePlugin):
    name = "AdminNotify"
    version = "1.0"

    ADMIN_ID = 273421673  # 管理员ID

    @bot.request_event()
    async def on_request_event(self, msg: Request):
        _log.info(f"收到{msg.request_type}请求: {msg.user_id}")
        # 好友请求，通过则私聊管理员
        if msg.request_type == "friend":
            return await self.api.post_private_msg(
                self.ADMIN_ID,
                f"收到来自 [{msg.user_id}] 的好友请求\n验证消息: {msg.comment}\nflag: [{msg.flag}]",
            )

        # 加群请求，在群里发送并艾特管理员
        if msg.request_type == "group":
            message = MessageChain(
                [
                    At(self.ADMIN_ID),
                    Text(
                        f"\n收到来自 [{msg.user_id}] 的入群申请\n验证消息: {msg.comment}\nflag: [{msg.flag}]"
                    ),
                ]
            )
            return await self.api.post_group_msg(group_id=msg.group_id, rtf=message)

    # 管理员处理好友请求
    @bot.private_event()
    async def handle_friend_request(self, msg: PrivateMessage):
        if msg.user_id == self.ADMIN_ID:
            if (
                len(msg.message) >= 2
                and msg.message[1]["type"] == "text"
                and msg.message[1]["data"]["text"] == "同意"
            ):
                msg_info = await self.api.get_msg(msg.message[0]["data"]["id"])
                if msg_info.get("status") != "ok":
                    _log.error(f"获取消息失败: {msg_info}")
                    return await msg.reply("获取消息失败,请稍后重试")
                # 获取flag
                try:
                    raw_message = html.unescape(msg_info["data"]["raw_message"])
                    parts = raw_message.split("flag: [")
                    if len(parts) < 2:
                        _log.error(f"消息格式不正确: {raw_message}")
                        return await msg.reply("获取flag失败，消息格式不正确")
                    flag = parts[1].split("]")[0]
                except IndexError:
                    _log.error(f"获取flag失败: {msg_info}")
                    return await msg.reply("获取flag失败，请检查消息格式")
                resp = await self.api.set_friend_add_request(
                    flag, True, "管理员已通过好友请求"
                )
                if resp.get("status") != "ok":
                    _log.error(f"接受好友请求失败: {resp}")
                    return await msg.reply("接受好友请求失败,请稍后重试")
                return await msg.reply("已接受好友请求")

    # 管理员处理群聊请求
    @bot.group_event()
    async def handle_group_request(self, msg: GroupMessage):
        if msg.user_id == self.ADMIN_ID:
            if (
                len(msg.message) >= 2
                and msg.message[1]["type"] == "text"
                and msg.message[1]["data"]["text"] == "同意"
            ):
                msg_info = await self.api.get_msg(msg.message[0]["data"]["id"])
                if msg_info.get("status") != "ok":
                    _log.error(f"获取消息失败: {msg_info}")
                    return await msg.reply("获取消息失败,请稍后重试")
                # 获取flag
                try:
                    raw_message = html.unescape(msg_info["data"]["raw_message"])
                    parts = raw_message.split("flag: [")
                    if len(parts) < 2:
                        _log.error(f"消息格式不正确: {raw_message}")
                        return await msg.reply("获取flag失败，消息格式不正确")
                    flag = parts[1].split("]")[0]
                except IndexError:
                    _log.error(f"获取flag失败: {msg_info}")
                    return await msg.reply("获取flag失败，请检查消息格式")
                resp = await self.api.set_group_add_request(
                    flag, True, "管理员已通过群聊请求"
                )
                if resp.get("status") != "ok":
                    _log.error(f"接受群聊请求失败: {resp}")
                    return await msg.reply("接受群聊请求失败,请稍后重试")
                return await msg.reply("已接受群聊请求")
