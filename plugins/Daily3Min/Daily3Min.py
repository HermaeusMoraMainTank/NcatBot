# from ncatbot.core import Image, MessageChain, Reply, GroupMessage
# from ncatbot.plugin_system import NcatBotPlugin, on_message
# from ncatbot.utils.logger import get_log
# from common.constants.HMMT import HMMT

# _log = get_log()


# class Daily3Min(NcatBotPlugin):
#     name = "Daily3Min"  # 插件名称
#     version = "1.0"  # 插件版本

#     async def on_load(self):
#         """加载插件"""
#         # 添加每日10点定时任务
#         self.add_scheduled_task(
#             self._send_daily_to_all_groups,
#             "daily_3min",
#             "10:00",
#         )
#         _log.info("[Daily3Min] 每日10点定时任务已注册")

#     async def _send_daily_to_all_groups(self):
#         """每日定时发送每天三分钟到所有群组"""
#         _log.info("[Daily3Min] ========== 定时任务开始执行 ==========")

#         try:
#             # 获取机器人所在的所有群组
#             group_list = await self.api.get_group_list()

#             if not group_list:
#                 _log.warning("[Daily3Min] 获取群组列表为空")
#                 return

#             _log.info(f"[Daily3Min] 准备向 {len(group_list)} 个群组发送消息")

#             # 遍历所有群组发送消息
#             for group in group_list:
#                 try:
#                     # 根据返回类型获取 group_id
#                     if isinstance(group, dict):
#                         group_id = group.get("group_id")
#                     elif isinstance(group, str):
#                         # 如果直接是字符串，就是群组ID
#                         group_id = group
#                     else:
#                         group_id = getattr(group, "group_id", None)

#                     if group_id:
#                         # 检查是否在黑名单中
#                         if str(group_id) in HMMT.BLACKLIST_GROUPS:
#                             _log.info(f"[Daily3Min] 跳过黑名单群组 {group_id}")
#                             continue

#                         message = MessageChain([Image("https://api.03c3.cn/api/zb")])
#                         await self.api.post_group_msg(group_id=group_id, rtf=message)
#                         _log.info(f"[Daily3Min] 成功发送到群组 {group_id}")
#                     else:
#                         _log.warning(f"[Daily3Min] 无法解析群组ID: {group}")
#                 except Exception as e:
#                     _log.error(f"[Daily3Min] 发送到群组失败: {e}")

#         except Exception as e:
#             _log.error(f"[Daily3Min] 获取群组列表失败: {e}")

#         _log.info("[Daily3Min] 每日定时任务执行完成")

#     @on_message
#     async def handle_daily3min(self, input: GroupMessage):
#         if input.raw_message in [
#             "每天3分钟",
#             "每天三分钟",
#             "每日3分钟",
#             "每日三分钟",
#             "每天60秒",
#             "每天六十秒",
#             "每日60秒",
#             "每日六十秒",
#             "每天1分钟",
#             "每天一分钟",
#             "每日1分钟",
#             "每日一分钟",
#         ]:
#             message = MessageChain(
#                 [
#                     Image("https://api.03c3.cn/api/zb"),
#                     Reply(input.message_id),
#                 ]
#             )
#             await self.api.post_group_msg(group_id=input.group_id, rtf=message)
