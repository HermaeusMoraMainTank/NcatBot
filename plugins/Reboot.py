import os
import sys
import time
import subprocess

from ncatbot.core.message import PrivateMessage
from ncatbot.plugin.base_plugin import BasePlugin
from ncatbot.plugin.event import CompatibleEnrollment

bot = CompatibleEnrollment


class Reboot(BasePlugin):
    admin = [1271701079, 273421673]

    @bot.group_event()
    async def Reboot(self, input: PrivateMessage):
        if input.raw_message == "重启" and input.user_id in self.admin:
            await input.add_text("重启了").reply()
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
