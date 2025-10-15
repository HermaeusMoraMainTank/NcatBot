from ncatbot.core import GroupMessage, MessageChain, Text, Record, Image
from ncatbot.plugin_system import NcatBotPlugin
from ncatbot.plugin_system.builtin_plugin.unified_registry.filter_system.decorators import (
    group_only,
)
from ncatbot.utils.logger import get_log
import json
from PIL import Image as PILImage, ImageDraw, ImageFont
import io
from typing import Dict, List, Tuple
import base64
import traceback
import requests
from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import pad
from common.constants.HMMT import HMMT
import re

_log = get_log()

# 用于存储搜索结果的字典
# 格式: {(group_id, user_id): [(song_id, song_name, artist_name), ...]}
search_results: Dict[Tuple[int, int], List[Tuple[int, str, str]]] = {}

# 网易云音乐API相关常量
AES_KEY = "0CoJUm6Qyw8W8jud"
AES_IV = "0102030405060708"
MODULUS_HEX = "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7"
EXPONENT_HEX = "010001"
CSRF_TOKEN = "7d327f98beb7cb91ebc9ad1fd50f4d19"


class NetEaseCloudMusic(NcatBotPlugin):
    name = "NetEaseCloudMusic"
    version = "1.0"

    async def on_load(self):
        """异步加载插件"""
        _log.info(f"开始加载 {self.name} 插件 v{self.version}")
        _log.info(f"{self.name} 插件加载完成")

    def _aes_encrypt(self, plain_text: str, key_str: str, iv: str) -> str:
        """AES加密"""
        key = key_str.encode("utf-8")
        iv_bytes = iv.encode("utf-8")
        plain_data = plain_text.encode("utf-8")
        cipher = AES.new(key, AES.MODE_CBC, iv_bytes)
        ciphertext = cipher.encrypt(pad(plain_data, AES.block_size))
        return base64.b64encode(ciphertext).decode("utf-8")

    def _rsa_encrypt(self, sec_key: str, pub_key: str, modulus: str) -> str:
        """RSA加密"""
        reversed_key = sec_key[::-1]
        hex_key = "".join(format(ord(c), "x") for c in reversed_key)
        key_int = int(hex_key, 16)
        pub_int = int(pub_key, 16)
        mod_int = int(modulus, 16)
        result = pow(key_int, pub_int, mod_int)
        hex_result = format(result, "x")
        modulus_len = len(modulus.lstrip("0"))
        padding_len = max(0, modulus_len - len(hex_result))
        return "0" * padding_len + hex_result

    def _build_request_data(self, params: str, enc_sec_key: str) -> dict:
        """构建请求数据"""
        return {"params": params, "encSecKey": enc_sec_key}

    def _weapi_encrypt(self, text: str) -> Tuple[str, str]:
        """网易云音乐API加密"""
        one = self._aes_encrypt(text, AES_KEY, AES_IV)
        params = self._aes_encrypt(one, AES_KEY, AES_IV)
        encSecKey = self._rsa_encrypt(AES_KEY, EXPONENT_HEX, MODULUS_HEX)
        return params, encSecKey

    def _get_headers(self) -> dict:
        """获取请求头"""
        return {
            "User-Agent": HMMT.USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://music.163.com",
            "Referer": "https://music.163.com/",
            "Cookie": "MUSIC_R_T=1449844717368; MUSIC_A_T=1449844707986; NTES_P_UTID=SArDOyha06R4IEBMf0K3Xfa1LBG53267|1734603754; JSESSIONID-WYYY=E6P7Si3YSx%5Czp8H7M%2FTFzqZZf73lIg6OcfAP2EPGeJh787k8xF6JYk3ZAGsDGqVNvNQmO8%5CY2aQV%5CmOrzMu8CWZlM5UmOYt9KRlc%2FYbPNc9wMMq%2F%5C4YHQbQhcVM8fkAdWH%2ByQ%2BNfbRZj6%5Ccye%2FplrlYUM8CXp38d0xWOjoJbFmGivFk%5C%3A1749695658991; _iuqxldmzr_=32; _ntes_nnid=c461cde80bd9c86f0e6eaea31ded1745,1749693859114; _ntes_nuid=c461cde80bd9c86f0e6eaea31ded1745; NMTID=00OpRPi67Y_2rk8Vk9GkpxUE8vyjnAAAAGXYeGFhA; WEVNSM=1.0.0; WNMCID=awwusa.1749693861634.01.0; sDeviceId=YD-HmR%2FaixTF%2BlBVgRFEEPHK2xWvRPGZo6Q; MUSIC_U=0048DD00867E5CFF739112EE5E2A1934613A24F251827A4536736F6A8836D7A992EEE5E9688B32D2DDCB31EA39180B156B059E786DB53FC8DC9486BA662D4ED1AF16ADADED08B8D5EAAB372825E352024E55C34469887F3A9F7DDB4BEEA7BBF301D6B2C391C71233D2CFFFD66CBC6DD638C5AB1615FE94CF881C8B112C7EFC662ED9ADA5B3AF3C266D1380C2160EF0A5D9804690C88B5E260DDDC1F383C7D50C025395E6E5D919F134E6839F8A61524C5CE46DB0D1CBBA8A236EE11BFDD5FBA13EEECA42907BE7D56F099E322D7876B585658ABCFA117FAC883A3FFBFF528A153D362C47526EFDEA63EC8D0E5F52431F9D1873B52E3E5583FB6143312B00C3E6C471E56B80C89D19BB2A13BFA9FB06033F3C71AB18FB76A9A2983A18760ABADEC983FFE3F2FB12EB5271444362F8B1C6AF8B68156479AF8A015DE4092C49FDFECC45811F038C1B913A7D1535D4DD7168D4F1CDFA53EE3754E242DBED0A8EA992A5; __csrf=3a9b3c7faab92050be3efc6d6beba48d; ntes_kaola_ad=1",
        }

    def _search_song(self, name: str) -> dict:
        """搜索歌曲"""
        s = json.dumps(
            {
                "hlpretag": '<span class="s-fc7">',
                "hlposttag": "</span>",
                "s": name,
                "type": "1",
                "offset": "0",
                "total": "true",
                "limit": "10",
                "csrf_token": CSRF_TOKEN,
            }
        )
        params, encSecKey = self._weapi_encrypt(s)
        d = self._build_request_data(params, encSecKey)
        res = requests.post(
            "https://music.163.com/weapi/cloudsearch/get/web",
            headers=self._get_headers(),
            data=d,
        )
        return res.json()

    def _get_song_url(self, song_id: str) -> dict:
        """获取歌曲URL"""
        s = json.dumps(
            {
                "ids": f"[{song_id}]",
                "level": "exhigh",
                "encodeType": "aac",
                "csrf_token": CSRF_TOKEN,
            }
        )
        params, encSecKey = self._weapi_encrypt(s)
        d = self._build_request_data(params, encSecKey)
        res = requests.post(
            "https://music.163.com/weapi/song/enhance/player/url/v1",
            headers=self._get_headers(),
            data=d,
        )
        return res.json()

    def create_search_result_image(self, songs: List[Tuple[int, str, str]]) -> str:
        """创建搜索结果图片"""
        try:
            # 创建图片
            width = 800
            height = 50 + len(songs) * 40  # 标题高度 + 每首歌40像素
            image = PILImage.new("RGB", (width, height), color="white")
            draw = ImageDraw.Draw(image)

            try:
                # 尝试加载字体，如果失败则使用默认字体
                font = ImageFont.truetype("simhei.ttf", 20)
            except Exception as e:
                _log.error(f"加载字体失败: {str(e)}")
                font = ImageFont.load_default()

            # 绘制标题
            draw.text((20, 10), "搜索结果：", fill="black", font=font)

            # 绘制每首歌的信息
            for i, (_, name, artist) in enumerate(songs):
                text = f"{i + 1}. {name} - {artist}"
                draw.text((20, 50 + i * 40), text, fill="black", font=font)

            # 将图片转换为base64字符串
            img_byte_arr = io.BytesIO()
            image.save(img_byte_arr, format="PNG")
            img_byte_arr.seek(0)
            img_base64 = base64.b64encode(img_byte_arr.getvalue()).decode()
            return f"base64://{img_base64}"
        except Exception as e:
            _log.error(f"创建图片时发生错误: {str(e)}")
            _log.error(traceback.format_exc())
            raise

    @group_only
    async def handle_music(self, input: GroupMessage):
        """处理音乐相关命令"""
        message = input.raw_message.strip()
        if not message:
            return

        # 处理回复消息
        if input.message and len(input.message) > 1:
            try:
                # 获取被回复的消息ID
                reply_id = None
                for msg in input.message:
                    if hasattr(msg, "type") and msg.type == "reply":
                        reply_id = msg.data.get("id")
                        break

                if reply_id:
                    msg_info = await self.api.get_msg(reply_id)
                    if msg_info.get("status") == "ok":
                        raw_message = msg_info["data"]["raw_message"]
                        if "请回复数字选择要播放的歌曲" in raw_message:
                            # 处理数字选择
                            # 先移除CQ码
                            clean_message = re.sub(
                                r"\[CQ:[^\]]+\]", "", message
                            ).strip()

                            # 提取数字
                            number_match = re.match(r"^\s*(\d+)", clean_message)
                            if number_match:
                                number = number_match.group(1)
                                key = (input.group_id, input.sender.user_id)

                                if key in search_results:
                                    try:
                                        index = int(number) - 1
                                        if 0 <= index < len(search_results[key]):
                                            song_id = search_results[key][index][0]
                                            # 发送初始响应
                                            await self.api.post_group_msg(
                                                input.group_id,
                                                rtf=MessageChain(
                                                    [Text("正在获取音乐，请稍候...")]
                                                ),
                                            )
                                            # 获取音乐URL
                                            song_url_data = self._get_song_url(
                                                str(song_id)
                                            )

                                            if song_url_data.get(
                                                "data"
                                            ) and song_url_data["data"][0].get("url"):
                                                mes = MessageChain(
                                                    [
                                                        Record(
                                                            song_url_data["data"][0][
                                                                "url"
                                                            ]
                                                        )
                                                    ]
                                                )
                                                await self.api.post_group_msg(
                                                    group_id=input.group_id, rtf=mes
                                                )
                                            else:
                                                _log.error("未获取到音乐URL")
                                            # 清除搜索结果
                                            del search_results[key]
                                        else:
                                            _log.error(
                                                f"索引超出范围: {index} >= {len(search_results[key])}"
                                            )
                                    except Exception as e:
                                        _log.error(f"处理歌曲时发生错误: {str(e)}")
                                        _log.error(traceback.format_exc())
                                else:
                                    _log.error(f"未找到搜索结果: {key}")
                            return
            except Exception as e:
                _log.error(f"处理回复时发生错误: {str(e)}")
                _log.error(traceback.format_exc())

        # 处理点歌命令
        if not message.startswith("点歌"):
            return

        try:
            # 提取歌曲名
            song_name = message[2:].strip()
            if not song_name:
                return

            _log.info(f"开始搜索歌曲: {song_name}")
            seadata = self._search_song(song_name)

            if seadata.get("code") != 200:
                await self.api.post_group_msg(
                    group_id=input.group_id,
                    rtf=MessageChain(
                        [Text(f"搜索失败，错误码：{seadata.get('code')}")]
                    ),
                )
                return

            if not seadata.get("result") or not seadata["result"].get("songs"):
                await self.api.post_group_msg(
                    group_id=input.group_id, rtf=MessageChain([Text("未找到相关歌曲")])
                )
                return

            # 提取歌曲信息
            songs = []
            for song in seadata["result"]["songs"][:10]:  # 只取前10首
                songs.append((song["id"], song["name"], song["ar"][0]["name"]))

            # 存储搜索结果
            search_results[(input.group_id, input.sender.user_id)] = songs

            # 生成并发送图片
            image_data = self.create_search_result_image(songs)
            mes = MessageChain(
                [Image(image_data), Text("\n请回复数字选择要播放的歌曲")]
            )
            await self.api.post_group_msg(group_id=input.group_id, rtf=mes)

        except Exception as e:
            _log.error(f"发生错误: {str(e)}")
            _log.error(traceback.format_exc())
            await self.api.post_group_msg(
                group_id=input.group_id,
                rtf=MessageChain([Text("搜索歌曲时发生错误，请稍后重试")]),
            )
