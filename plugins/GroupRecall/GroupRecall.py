# import asyncio
# from ncatbot.core.message import GroupMessage
# from ncatbot.core.element import MessageChain, Text
# from ncatbot.utils.logger import get_log
# from ncatbot.plugin.base_plugin import BasePlugin
# from ncatbot.plugin.compatible import CompatibleEnrollment
# import time

# bot = CompatibleEnrollment
# _log = get_log()


# class GroupRecall(BasePlugin):
#     name = "GroupRecall"  # 插件名称
#     version = "1.0"  # 插件版本
#     msgMap = {}  # 初始化字典，用于存储消息
#     expiration_time = 3600 * 24  # 消息保存时间为一天
#     loop = asyncio.get_event_loop()  # 获取事件循环

#     def __init__(self):
#         super().__init__()
#         self.loop.create_task(self.clean_task())  # 创建并启动定时清理任务

#     @bot.group_event()
#     async def handle_notice(self, input: Notice):
#         """处理撤回通知"""
#         if input.notice_type == "group_recall":
#             if input.message_id in self.msgMap:
#                 msg_data = self.msgMap[input.message_id]
#                 msg = msg_data["message"]
#                 user_id = msg_data["user_id"]
#                 nickname = msg_data["nickname"]

#                 # 复现消息
#                 message = MessageChain(msg.messages + msg.message)
#                 await input.api.post_group_msg(group_id=input.group_id, rtf=message)

#                 recall_message = MessageChain(
#                     [
#                         Text(
#                             f"{nickname} ({user_id})撤回了一条消息，↑本小可爱帮你复现了喵↑"
#                         )
#                     ]
#                 )
#                 await input.api.post_group_msg(
#                     group_id=input.group_id, rtf=recall_message
#                 )

#                 # 删除已处理的消息
#                 del self.msgMap[input.message_id]

#     @bot.group_event()
#     async def handle_group(self, input: GroupMessage):
#         """处理群消息"""
#         msg_time = time.time()  # 消息时间
#         msg_id = input.message_id
#         user_id = input.user_id
#         nickname = input.sender.nickname

#         # 存储消息及相关信息
#         self.msgMap[msg_id] = {
#             "message": input,  # 消息对象
#             "timestamp": msg_time,  # 消息时间戳
#             "user_id": user_id,  # 用户ID
#             "nickname": nickname,  # 用户昵称
#         }

#     async def clean_task(self):  # 定时清理任务
#         while True:
#             _log.debug(f"消息储存量: {len(self.msgMap)}")  # 打印当前消息存储量
#             await asyncio.sleep(60)  # 每分钟执行一次清理
#             await self.clean()

#     async def clean(self):
#         """清理过期消息"""
#         current_time = time.time()
#         for k, v in list(self.msgMap.items()):  # 使用 list 避免字典修改异常
#             if current_time - v["timestamp"] > self.expiration_time:
#                 del self.msgMap[k]
