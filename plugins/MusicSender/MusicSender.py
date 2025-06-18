from datetime import datetime
import logging
import os
import json
import base64
from ncatbot.core.message import GroupMessage
from ncatbot.core.element import MessageChain, Record
from ncatbot.plugin import CompatibleEnrollment, BasePlugin


bot = CompatibleEnrollment
log = logging.getLogger(__name__)


class MusicSender(BasePlugin):
    name = "MusicSender"  # 插件名称
    version = "1.0"  # 插件版本

    # 音乐配置
    music_config = {
        "重返街头": {
            "triggers": ["重返街头"],
            "path": "data/mp3/重返街头.mp3",
            "allowed_users": None,
        },
        "百万主播": {
            "triggers": ["百万主播"],
            "path": "data/mp3/百万主播.mp3",
            "allowed_users": None,
        },
    }

    async def on_load(self):
        """异步加载插件"""
        log.info(f"开始加载 {self.name} 插件 v{self.version}")
        # 检查所有音乐文件是否存在
        for cmd in self.music_config.values():
            if not os.path.exists(cmd["path"]):
                log.warning(f"音乐文件不存在: {cmd['path']}")
        log.info(f"{self.name} 插件加载完成")

    def _get_base64_audio(self, file_path: str) -> str:
        """将音频文件转换为base64格式"""
        try:
            with open(file_path, "rb") as f:
                audio_data = f.read()
                base64_data = base64.b64encode(audio_data).decode()
                return f"base64://{base64_data}"
        except Exception as e:
            log.error(f"读取音频文件失败: {str(e)}")
            return None

    @bot.group_event()
    async def handle_music(self, input: GroupMessage):
        message = input.raw_message.strip()

        # 检查消息是否以任何命令开头
        for command, config in self.music_config.items():
            for trigger in config["triggers"]:
                if message == trigger:
                    # 检查全局权限
                    if (
                        config["allowed_users"]
                        and input.sender.user_id not in config["allowed_users"]
                    ):
                        return

                    # 检查文件是否存在
                    if not os.path.exists(config["path"]):
                        log.error(f"音乐文件不存在: {config['path']}")
                        return

                    # 获取base64编码的音频数据
                    audio_base64 = self._get_base64_audio(config["path"])
                    if not audio_base64:
                        log.error("音频文件编码失败")
                        return

                    # 发送音乐文件
                    log.info(
                        f"Time:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {config['path']}"
                    )
                    await self.api.post_group_msg(
                        group_id=input.group_id,
                        rtf=MessageChain([Record(audio_base64)]),
                    )
                    return
