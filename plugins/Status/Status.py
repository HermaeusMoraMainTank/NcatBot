import os
import time
import psutil
from datetime import datetime

from ncatbot.core.message import GroupMessage
from ncatbot.plugin.base_plugin import BasePlugin
from ncatbot.plugin import CompatibleEnrollment

from common.constants.HMMT import HMMT

bot = CompatibleEnrollment


class Status(BasePlugin):
    name = "Status"  # 插件名称
    version = "1.0"  # 插件版本
    START_TIME = time.time()  # 程序启动时间

    @bot.group_event()
    async def handle_status(self, input: GroupMessage) -> None:
        if input.raw_message == "状态" and input.user_id == HMMT.HMMT_ID:
            await self.api.post_group_msg(
                group_id=input.group_id, text=self.get_system_status()
            )

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

    def get_disk_usage(self):
        """获取磁盘使用情况"""
        disk_info = []
        for partition in psutil.disk_partitions():
            try:
                partition_usage = psutil.disk_usage(partition.mountpoint)
                disk_info.append(
                    {
                        "device": partition.device,
                        "mountpoint": partition.mountpoint,
                        "total": partition_usage.total,
                        "used": partition_usage.used,
                        "free": partition_usage.free,
                        "percent": partition_usage.percent,
                    }
                )
            except PermissionError:
                continue
        return disk_info

    def get_network_info(self):
        """获取网络信息"""
        network_io = psutil.net_io_counters()
        return {
            "bytes_sent": network_io.bytes_sent,
            "bytes_recv": network_io.bytes_recv,
            "packets_sent": network_io.packets_sent,
            "packets_recv": network_io.packets_recv,
        }

    def get_system_status(self):
        """获取系统状态"""
        # 获取CPU利用率 - 使用更准确的方法
        cpu_count = psutil.cpu_count()
        cpu_percent = psutil.cpu_percent(interval=1, percpu=True)
        cpu_avg = sum(cpu_percent) / len(cpu_percent)

        # 获取CPU频率信息
        try:
            cpu_freq = psutil.cpu_freq()
            cpu_freq_current = cpu_freq.current if cpu_freq else "未知"
            cpu_freq_max = cpu_freq.max if cpu_freq else "未知"
        except:
            cpu_freq_current = "未知"
            cpu_freq_max = "未知"

        # 获取内存信息
        memory = psutil.virtual_memory()
        total_memory = memory.total
        used_memory = memory.used
        available_memory = memory.available
        memory_percent = memory.percent

        # 获取进程内存使用情况
        process = psutil.Process(os.getpid())
        process_memory_info = process.memory_info()
        process_rss = process_memory_info.rss  # 物理内存
        process_vms = process_memory_info.vms  # 虚拟内存
        process_percent = process.memory_percent()  # 进程内存占用百分比

        # 获取磁盘使用情况
        disk_info = self.get_disk_usage()

        # 获取网络信息
        network_info = self.get_network_info()

        # 计算已运行时长
        uptime_seconds = time.time() - self.START_TIME
        uptime = self.format_uptime(uptime_seconds)

        # 获取系统启动时间
        system_boot_time = datetime.fromtimestamp(psutil.boot_time())
        system_uptime = datetime.now() - system_boot_time
        system_uptime_hours = int(system_uptime.total_seconds() // 3600)

        # 获取当前时间
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 构建要发送的消息内容
        message = (
            "——— 蓝晴状态如下 ———\n"
            f"昵称：蓝晴\n"
            f"当前设备状态：\n"
            f"系统：Napcat+Ncatbot\n"
            f"系统启动时间：{system_boot_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"系统已运行：{system_uptime_hours}小时\n"
            f"程序已运行：{uptime}\n\n"
            f"CPU信息：\n"
            f"CPU核心数：{cpu_count}核\n"
            f"CPU频率：{cpu_freq_current:.1f}MHz / {cpu_freq_max:.1f}MHz\n"
            f"CPU利用率：{cpu_avg:.2f}%\n"
            f"各核心利用率：{', '.join([f'{p:.1f}%' for p in cpu_percent])}\n\n"
            f"内存信息：\n"
            f"总内存：{self.bytes_to_gb(total_memory):.2f}GB\n"
            f"已用内存：{self.bytes_to_gb(used_memory):.2f}GB ({memory_percent:.1f}%)\n"
            f"可用内存：{self.bytes_to_gb(available_memory):.2f}GB\n"
            f"程序占用：{self.bytes_to_mb(process_rss):.2f}MB (物理) / {self.bytes_to_mb(process_vms):.2f}MB (虚拟) ({process_percent:.1f}%)\n\n"
        )

        # 添加磁盘信息
        if disk_info:
            message += "磁盘信息：\n"
            for disk in disk_info:
                message += f"{disk['device']} ({disk['mountpoint']}): {self.bytes_to_gb(disk['used']):.2f}GB/{self.bytes_to_gb(disk['total']):.2f}GB ({disk['percent']:.1f}%)\n"
            message += "\n"

        # 添加网络信息
        message += (
            f"网络信息：\n"
            f"发送：{self.bytes_to_mb(network_info['bytes_sent']):.2f}MB ({network_info['packets_sent']}包)\n"
            f"接收：{self.bytes_to_mb(network_info['bytes_recv']):.2f}MB ({network_info['packets_recv']}包)\n\n"
            f"——— {current_time} ———"
        )

        return message
