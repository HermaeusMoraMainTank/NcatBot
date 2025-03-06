import os
import time
import psutil
from datetime import datetime

from ncatbot.core.message import GroupMessage
from ncatbot.plugin.base_plugin import BasePlugin
from ncatbot.plugin.event import CompatibleEnrollment

from NcatBot.common.constants import HMMT

bot = CompatibleEnrollment


class Status(BasePlugin):
    START_TIME = time.time()  # 程序启动时间

    @bot.group_event()
    async def handle_status(self, input: GroupMessage) -> None:
        if input.raw_message == "状态" and input.user_id == HMMT.HMMT_ID:
            await self.api.post_group_msg(group_id=input.group_id, text=self.get_system_status())

    def format_uptime(self, uptime_seconds):
        """格式化运行时长"""
        hours = int(uptime_seconds // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        seconds = int(uptime_seconds % 60)
        return f"{hours}时{minutes}分{seconds}秒"

    def bytes_to_gb(self, bytes):
        """将字节转换为GB"""
        return bytes / (1024**3)

    def bytes_to_mb(self, bytes):
        """将字节转换为MB"""
        return bytes / (1024**2)

    def get_system_status(self):
        """获取系统状态"""
        # 获取CPU利用率
        cpu_load = psutil.cpu_percent(interval=1)

        # 获取内存信息
        memory = psutil.virtual_memory()
        total_memory = memory.total
        used_memory = memory.used

        # 获取进程内存使用情况
        process = psutil.Process(os.getpid())
        process_max_memory = process.memory_info().rss  # 获取进程占用的物理内存
        process_used_memory = process.memory_info().rss  # 直接使用 rss 作为已使用内存

        # 计算已运行时长
        uptime_seconds = time.time() - self.START_TIME
        uptime = self.format_uptime(uptime_seconds)

        # 获取当前时间
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 构建要发送的消息内容
        message = (
            "——— 蓝晴状态如下 ———\n"
            f"昵称：蓝晴\n"
            f"当前设备状态：\n"
            f"系统：Napcat+Ncatbot\n"
            f"CPU利用率：{cpu_load:.2f}%\n"
            f"内存：{self.bytes_to_gb(used_memory):.2f}GB/{self.bytes_to_gb(total_memory):.2f}GB\n"
            f"程序已申请内存：{self.bytes_to_mb(process_max_memory):.2f}MB\n"
            f"程序已使用内存：{self.bytes_to_mb(process_used_memory):.2f}MB\n"
            f"本次已持续运行：{uptime}\n"
            f"——— {current_time} ———"
        )

        return message
