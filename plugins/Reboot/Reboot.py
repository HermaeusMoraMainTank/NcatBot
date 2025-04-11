import os
import sys
import time
import subprocess

from ncatbot.core.element import MessageChain, Text
from ncatbot.core.message import PrivateMessage
from ncatbot.plugin import CompatibleEnrollment, BasePlugin


bot = CompatibleEnrollment


class Reboot(BasePlugin):
    name = "Reboot"  # 插件名称
    version = "1.0"  # 插件版本
    admin = [1271701079, 273421673]

    @bot.group_event()
    async def Reboot(self, input: PrivateMessage):
        if input.raw_message == "重启" and input.user_id in self.admin:
            await self.api.post_private_msg(
                user_id=input.user_id,
                rtf=MessageChain(
                    [
                        Text("重启了"),
                    ]
                ),
            )
            subprocess.Popen(
                "git fetch origin",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=True,
            )
            subprocess.Popen(
                "git reset --hard",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=True,
            )
            subprocess.Popen(
                "git pull",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=True,
            )
            time.sleep(60)  # 睡眠1分钟等待pull代码
            os.execl(sys.executable, sys.executable, *sys.argv)
