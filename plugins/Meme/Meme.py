import json
import requests  # 使用 requests 代替 httpx
from typing import List, Optional
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from pathlib import Path
import tempfile

from ncatbot.core.message import GroupMessage
from ncatbot.core.element import Image, MessageChain
from ncatbot.plugin.base_plugin import BasePlugin
from ncatbot.plugin.compatible import CompatibleEnrollment

bot = CompatibleEnrollment


@dataclass_json
@dataclass
class ParamsType:
    min_images: int
    max_images: int
    min_texts: int
    max_texts: int
    default_texts: List[str]
    args_type: Optional[None]


@dataclass_json
@dataclass
class DataStructure:
    key: str
    params_type: ParamsType
    keywords: List[str]
    shortcuts: List[str]
    tags: List[str]
    date_created: str
    date_modified: str


class Meme(BasePlugin):
    name = "Meme"  # 插件名称
    version = "1.0"  # 插件版本
    baseurl = "http://127.0.0.1:2233/memes"
    keylist = []
    keywordslist: dict[str, DataStructure] = {}
    client = requests.Session()  # 使用 requests 的 Session

    def setup(self):
        """插件初始化设置"""
        self.get_meme_list()
        self.load_meme_data()

    def load_meme_data(self):
        try:
            with open("data/json/memeKeys.json", "r") as file:
                meme_data = json.load(file)
                self.keywordslist = {
                    keyword: DataStructure.from_dict(data)
                    for data in meme_data
                    for keyword in data["keywords"]
                }
        except Exception as e:
            print(f"Failed to load meme data: {e}")

    @bot.group_event()
    async def handle_meme(self, input: GroupMessage):
        # if input.raw_message == "meme":
        #     meme = requests.post(f"{self.baseurl}/render_list/")
        #
        #     await meme

        if input.message[0]["type"] == "text":
            com = input.message[0]["data"]["text"].strip()  # 去除空格
            coms = str(com).split(" ")  #
            if coms[0] in self.keywordslist:
                meme_config = self.keywordslist[coms[0]]
                params_type = meme_config.params_type
                if len(coms) > 1 and coms[1] == "info":
                    await self.send_meme_info(input, meme_config)
                    return

                avatar_files = self.collect_avatar_files(
                    input, params_type.min_images, params_type.max_images
                )
                texts = self.collect_texts(
                    input, len(coms) > 1 and input.message[1]["type"] == "at"
                )

                if (
                    len(avatar_files) < params_type.min_images
                    or len(avatar_files) > params_type.max_images
                ):
                    return
                if (
                    len(texts) < params_type.min_texts
                    or len(texts) > params_type.max_texts
                ):
                    return

                await self.send_meme_request(
                    input, meme_config.key, avatar_files, texts
                )

    async def send_meme_info(self, input: GroupMessage, meme_config: DataStructure):
        info_message = (
            f"关键词: {meme_config.key}\n"
            f"最少图片数量: {meme_config.params_type.min_images}\n"
            f"最多图片数量: {meme_config.params_type.max_images}\n"
            f"最少文字数量: {meme_config.params_type.min_texts}\n"
            f"最多文字数量: {meme_config.params_type.max_texts}"
        )
        await self.api.post_group_msg(group_id=input.group_id, text=info_message)

    def collect_avatar_files(
        self, input: GroupMessage, min_images: int, max_images: int
    ) -> List[Path]:
        """收集头像 URL，下载头像文件并返回文件路径列表"""
        avatar_urls = []
        for message in input.message:
            if message["type"] == "at":
                target_id = message["data"]["qq"]
                avatar_urls.append(f"http://q1.qlogo.cn/g?b=qq&nk={target_id}&s=640")
        while len(avatar_urls) < min_images:
            avatar_urls.insert(0, f"http://q1.qlogo.cn/g?b=qq&nk={input.user_id}&s=640")
        if len(avatar_urls) > max_images:
            avatar_urls = avatar_urls[:max_images]

        # 下载头像文件
        avatar_files = []
        for url in avatar_urls:
            try:
                response = requests.get(url)
                if response.status_code == 200:
                    # 使用临时文件保存头像
                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=".jpg"
                    ) as tmp_file:
                        tmp_file.write(response.content)
                        avatar_files.append(Path(tmp_file.name))
            except Exception as e:
                print(f"Failed to download avatar from {url}: {e}")
        return avatar_files

    def collect_texts(self, input: GroupMessage, has_at: bool) -> List[str]:
        texts = []
        for i in range(1, len(input.message)):
            message = input.message[i]
            if has_at and message["type"] == "at":
                continue
            if message["type"] == "plain":
                texts.extend(message["data"]["text"].split())
        return texts

    async def send_meme_request(
        self,
        input: GroupMessage,
        meme_key: str,
        avatar_files: List[Path],
        texts: List[str],
    ):
        """发送 meme 请求，上传头像文件和文本"""
        api_url = f"{self.baseurl}/{meme_key}/"

        # 使用内存中的文件内容而不是文件对象，避免文件提前关闭
        files = []
        for file in avatar_files:
            with open(file, "rb") as f:
                file_content = f.read()  # 读取文件内容到内存
                files.append(("images", (file.name, file_content, "image/jpeg")))

        data = {"texts": texts, "args": json.dumps({"circle": True})}

        try:
            response = requests.post(api_url, files=files, data=data)
            print(response)
            print(response.content)
            if response.status_code == 200:
                # 将生成的 meme 图片添加到消息中
                await self.api.post_group_msg(
                    group_id=input.group_id,
                    rtf=MessageChain(
                        [
                            Image(response.content),
                        ]
                    ),
                )
        finally:
            # 删除临时头像文件时确保文件已关闭
            for file in avatar_files:
                file.unlink()

    def get_meme_list(self):
        """获取 meme 列表并保存到文件"""
        response = self.client.get(f"{self.baseurl}/keys")
        if response.status_code == 200:
            meme_keys = response.json()
            meme_data = []
            for key in meme_keys:
                info_response = self.client.get(f"{self.baseurl}/{key}/info")
                if info_response.status_code == 200:
                    meme_data.append(info_response.json())
            with open("data/json/memeKeys.json", "w") as file:
                json.dump(meme_data, file, indent=4)
            print("Meme list saved to data/json/memeKeys.json")
        else:
            print("Failed to fetch meme keys")

    def get_meme_image(
        self, meme_key: str, avatar_files: List[Path], texts: List[str]
    ) -> Optional[bytes]:
        """获取生成的 meme 图片"""
        api_url = f"{self.baseurl}/{meme_key}/"
        files = [
            ("images", (file.name, open(file, "rb"), "image/jpeg"))
            for file in avatar_files
        ]
        data = {"texts": texts, "args": json.dumps({"circle": True})}
        response = self.client.post(api_url, files=files, data=data)
        if response.status_code == 200:
            return response.content
        return None


if __name__ == "__main__":
    meme = Meme()
    # 获取 meme 列表并保存到文件
    meme.get_meme_list()
