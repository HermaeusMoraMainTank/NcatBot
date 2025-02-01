import os
import sys
from ncatpy.message import PrivateMessage
import subprocess


class Reboot:
    def __init__(self):
        self.admin = [1271701079,273421673]
        pass

    async def Reboot(self, input: PrivateMessage):
        if input.raw_message == "重启" and input.user_id in self.admin:
            pass
            # 后面再说
            await input.add_text("重启了").reply()
            subprocess.Popen("git fetch origin", stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, shell=True)
            subprocess.Popen("git reset --hard", stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, shell=True)
            subprocess.Popen("git pull", stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, shell=True)
            os.execl(sys.executable, sys.executable, *sys.argv)
