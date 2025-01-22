import logging
from ncatpy.message import GroupMessage
from ncatpy.message import NoticeMessage
import time
import threading

log = logging.getLogger(__name__)


class GroupRecall:
    def __init__(self):
        self.msgMap = {}  # 初始化字典，用于存储消息
        self.expiration_time = 3600 * 24  # 消息保存时间为一天
        self.cleanup_thread = threading.Thread(target=self.clean_task, daemon=True)
        self.cleanup_thread.start()  # 启动定时清理线程
        pass

    async def clean_task(self):  # 定时清理任务
        while True:
            log.debug(f"消息储存量: {len(self.msgMap)}")  # 打印当前消息存储量
            time.sleep(60)  # 每分钟执行一次清理
            await self.clean()
            pass

    async def clean(self):
        """清理过期消息"""
        current_time = time.time()
        for k, v in list(self.msgMap.items()):  # 使用 list 避免字典修改异常
            if current_time - v["timestamp"] > self.expiration_time:
                del self.msgMap[k]

    async def handle_notice(self, input: NoticeMessage):
        """处理撤回通知"""
        if input.notice_type == "group_recall":
            if input.message_id in self.msgMap:
                msg_data = self.msgMap[input.message_id]
                msg = msg_data["message"]
                user_id = msg_data["user_id"]
                nickname = msg_data["nickname"]

                # 复现消息
                msg.messages = msg.messages + msg.message
                await msg.reply()
                await msg.add_text(f"{nickname} ({user_id})撤回了一条消息，↑本小可爱帮你复现了喵↑").reply()

                # 删除已处理的消息
                del self.msgMap[input.message_id]

    async def handle_group(self, input: GroupMessage):
        """处理群消息"""
        msg_time = time.time()  # 消息时间
        msg_id = input.message_id
        user_id = input.user_id
        nickname = input.sender.nickname

        # 存储消息及相关信息
        self.msgMap[msg_id] = {
            "message": input,  # 消息对象
            "timestamp": msg_time,  # 消息时间戳
            "user_id": user_id,  # 用户ID
            "nickname": nickname  # 用户昵称
        }
