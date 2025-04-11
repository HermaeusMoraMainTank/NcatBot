import requests
import uuid
import logging
from typing import Dict, Any
from common.constants.HMMT import HMMT
from ncatbot.core.message import GroupMessage
from ncatbot.plugin import CompatibleEnrollment, BasePlugin
from ncatbot.utils.config import config

bot = CompatibleEnrollment
logger = logging.getLogger("QQMusicSender")


class QQMusicAPI:
    def __init__(self):
        self.headers = {
            "User-Agent": HMMT.USER_AGENT,
            "Referer": "https://y.qq.com/",
        }

    def search_song(self, keyword: str) -> Dict[str, Any]:
        url = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
        params = {"w": keyword, "format": "json", "p": 1, "n": 1}
        try:
            resp = requests.get(url, headers=self.headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("data", {}).get("song", {}).get("list"):
                return {}
            song = data["data"]["song"]["list"][0]
            return {
                "songmid": song["songmid"],
                "title": song["songname"],
                "artist": ", ".join([s["name"] for s in song["singer"]]),
                "albumid": song["albumid"],
                "duration": song["interval"],
            }
        except Exception as e:
            logger.error(f"搜索失败: {str(e)}")
            return {}

    def get_play_url(self, songmid: str) -> str:
        guid = str(uuid.uuid4()).upper().replace("-", "")
        url = "https://u.y.qq.com/cgi-bin/musicu.fcg"
        payload = {
            "req": {
                "module": "vkey.GetVkeyServer",
                "method": "CgiGetVkey",
                "param": {
                    "guid": guid,
                    "songmid": [songmid],
                    "songtype": [0],
                    "uin": "0",
                    "platform": "20",
                },
            },
            "comm": {"uin": 0, "format": "json", "ct": 24, "cv": 0},
        }
        try:
            resp = requests.post(url, headers=self.headers, json=payload, timeout=10)
            data = resp.json()
            if (
                purl := data.get("req", {})
                .get("data", {})
                .get("midurlinfo", [{}])[0]
                .get("purl")
            ):
                return f"https://ws.stream.qqmusic.qq.com/{purl}"
        except Exception as e:
            logger.error(f"获取播放链接失败: {str(e)}")
            return ""


class QQMusicCardSender(BasePlugin):
    name = "QQMusicCardSender"
    version = "1.0"

    def build_card(self, song_info: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "type": "custom",
            "url": f"https://y.qq.com/n/ryqq/songDetail/{song_info['songmid']}",
            "audio": song_info["play_url"],
            "title": song_info["title"],
            "image": f"https://y.gtimg.cn/music/photo_new/T002R300x300M000{song_info['albumid']}.jpg",
            "singer": song_info["artist"],
        }

    async def send_to_group(self, group_id: int, song_info: Dict[str, Any]) -> bool:
        try:
            await self.api.post_group_msg(
                group_id=group_id, music=self.build_card(song_info)
            )
            return True
        except Exception as e:
            logger.error(f"发送失败: {str(e)}")
            return False

    @bot.group_event()
    async def handle_music_card(self, input: GroupMessage):
        message = input.raw_message.strip()
        if message.startswith("点歌 "):
            keyword = message[3:].strip()
            music_api = QQMusicAPI()
            song_info = music_api.search_song(keyword)
            if song_info:
                song_info["play_url"] = music_api.get_play_url(song_info["songmid"])
                if song_info["play_url"]:
                    if await self.send_to_group(input.group_id, song_info):
                        return
                    else:
                        await self.api.post_group_msg(
                            group_id=input.group_id,
                            text="音乐卡片发送失败，请检查日志。",
                        )
                else:
                    await self.api.post_group_msg(
                        group_id=input.group_id, text="获取播放链接失败，请检查日志。"
                    )
            else:
                await self.api.post_group_msg(
                    group_id=input.group_id, text="未找到相关歌曲。"
                )
