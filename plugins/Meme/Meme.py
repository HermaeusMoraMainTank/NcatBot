import json
import requests
import logging
from typing import List, Optional, Any
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from pathlib import Path
import tempfile

from ncatbot.core.message import GroupMessage
from ncatbot.core.element import Image, MessageChain
from ncatbot.plugin.base_plugin import BasePlugin
from ncatbot.plugin.compatible import CompatibleEnrollment

# 配置日志
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)

bot = CompatibleEnrollment


@dataclass_json
@dataclass
class ParamsType:
    min_images: int
    max_images: int
    min_texts: int
    max_texts: int
    default_texts: List[str]
    args_type: Optional[Any] = None  # 修改为 Any 类型并设置默认值


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
    name = "Meme"
    version = "1.0"
    baseurl = "http://127.0.0.1:2233/memes"
    keylist = []
    keywordslist: dict[str, DataStructure] = {}
    timeout = 30  # 设置超时时间为30秒
    session = requests.Session()  # 使用 Session 来复用连接

    async def on_load(self):
        """异步加载插件"""
        logger.info(f"开始加载 {self.name} 插件 v{self.version}")
        try:
            # 检查服务是否可用
            response = self.session.get(f"{self.baseurl}/keys", timeout=self.timeout)
            if response.status_code != 200:
                logger.error(f"Meme 服务不可用: {response.status_code}")
                return
            logger.info("Meme 服务连接成功")
        except Exception as e:
            logger.error(f"无法连接到 Meme 服务: {e}")
            return

        await self.load_meme_data()
        logger.info(f"{self.name} 插件加载完成")

    async def load_meme_data(self):
        """异步加载 meme 数据"""
        try:
            with open("data/json/memeKeys.json", "r", encoding="utf-8") as file:
                meme_data = json.load(file)
                self.keywordslist = {
                    keyword: DataStructure.from_dict(data)
                    for data in meme_data
                    for keyword in data["keywords"]
                }
            logger.info(f"成功加载 {len(self.keywordslist)} 个 meme 关键词")
        except Exception as e:
            logger.error(f"加载 meme 数据失败: {e}", exc_info=True)

    @bot.group_event()
    async def handle_meme(self, input: GroupMessage):
        if input.raw_message == "meme":
            try:
                # 从 memeKeys.json 读取数据
                with open("data/json/memeKeys.json", "r", encoding="utf-8") as file:
                    meme_data = json.load(file)

                # 构建 meme_list
                meme_list = [
                    {"meme_key": data["key"], "disabled": False, "labels": []}
                    for data in meme_data
                ]

                # 构建请求数据
                request_data = {
                    "meme_list": meme_list,
                    "text_template": "{keywords}",
                    "add_category_icon": True,
                }

                response = self.session.post(
                    f"{self.baseurl}/render_list/",
                    json=request_data,
                    timeout=self.timeout,
                )

                if response.status_code == 200:
                    logger.info("成功获取 meme 列表")
                    # 保存响应内容为临时文件
                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=".jpg"
                    ) as tmp_file:
                        tmp_file.write(response.content)
                        tmp_path = Path(tmp_file.name)

                    try:
                        await self.api.post_group_msg(
                            group_id=input.group_id,
                            rtf=MessageChain([Image(str(tmp_path))]),
                        )
                    finally:
                        # 删除临时文件
                        try:
                            tmp_path.unlink()
                        except Exception as e:
                            logger.warning(f"删除临时文件失败: {e}")
                else:
                    logger.error(f"获取 meme 列表失败: {response.status_code}")
            except Exception as e:
                logger.error(f"请求 meme 列表时发生错误: {e}", exc_info=True)
            return

        if input.message[0]["type"] == "text":
            com = input.message[0]["data"]["text"].strip()
            coms = str(com).split(" ")

            if coms[0] in self.keywordslist:
                meme_config = self.keywordslist[coms[0]]
                params_type = meme_config.params_type

                if len(coms) > 1 and coms[1] == "info":
                    await self.send_meme_info(input, meme_config)
                    return

                avatar_files = self.collect_avatar_files(
                    input, params_type.min_images, params_type.max_images
                )
                # 检查是否有 @ 消息
                has_at = any(msg["type"] == "at" for msg in input.message)
                texts = self.collect_texts(input, has_at)

                if (
                    len(avatar_files) < params_type.min_images
                    or len(avatar_files) > params_type.max_images
                ):
                    logger.warning(f"头像数量不符合要求: {len(avatar_files)}")
                    return
                if (
                    len(texts) < params_type.min_texts
                    or len(texts) > params_type.max_texts
                ):
                    logger.warning(f"文本数量不符合要求: {len(texts)}")
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
                response = self.session.get(url, timeout=self.timeout)
                if response.status_code == 200:
                    # 使用临时文件保存头像
                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=".jpg"
                    ) as tmp_file:
                        tmp_file.write(response.content)
                        avatar_files.append(Path(tmp_file.name))
            except Exception as e:
                logger.error(f"下载头像失败 {url}: {e}")
        return avatar_files

    def collect_texts(self, input: GroupMessage, has_at: bool) -> List[str]:
        texts = []
        # 检查消息列表长度
        if len(input.message) <= 0:
            return texts

        # 处理第一个消息（命令）中的文本
        if input.message[0]["type"] == "text":
            command_text = input.message[0]["data"]["text"].strip()
            # 分割命令，跳过第一个（关键词）
            parts = command_text.split(" ", 1)
            if len(parts) > 1:
                texts.extend(parts[1].split())

        # 处理其他消息
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

        files = []
        for file in avatar_files:
            with open(file, "rb") as f:
                file_content = f.read()
                files.append(("images", (file.name, file_content, "image/jpeg")))

        data = {"texts": texts, "args": json.dumps({"circle": True})}

        try:
            response = self.session.post(
                api_url, files=files, data=data, timeout=self.timeout
            )

            if response.status_code == 200:
                logger.info("成功生成 meme 图片")
                # 保存响应内容为临时文件
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=".jpg"
                ) as tmp_file:
                    tmp_file.write(response.content)
                    tmp_path = Path(tmp_file.name)

                try:
                    await self.api.post_group_msg(
                        group_id=input.group_id,
                        rtf=MessageChain([Image(str(tmp_path))]),
                    )
                finally:
                    # 删除临时文件
                    try:
                        tmp_path.unlink()
                    except Exception as e:
                        logger.warning(f"删除临时文件失败: {e}")
            else:
                logger.error(
                    f"生成 meme 失败: {response.status_code} - {response.text}"
                )
        except requests.Timeout:
            logger.error("请求超时")
        except requests.ConnectionError:
            logger.error("无法连接到 Meme 服务，请确保服务已启动")
        except Exception as e:
            logger.error(f"发送 meme 请求时发生错误: {e}", exc_info=True)
        finally:
            for file in avatar_files:
                try:
                    file.unlink()
                except Exception as e:
                    logger.warning(f"删除临时文件失败: {e}")

    async def get_meme_list(self):
        """异步获取 meme 列表"""
        try:
            response = self.session.get(f"{self.baseurl}/keys", timeout=self.timeout)

            if response.status_code == 200:
                meme_keys = response.json()
                meme_data = []

                for key in meme_keys:
                    info_response = self.session.get(
                        f"{self.baseurl}/{key}/info", timeout=self.timeout
                    )
                    if info_response.status_code == 200:
                        meme_data.append(info_response.json())
                    else:
                        logger.warning(
                            f"获取 meme {key} 信息失败: {info_response.status_code}"
                        )

                with open("data/json/memeKeys.json", "w", encoding="utf-8") as file:
                    json.dump(meme_data, file, indent=4, ensure_ascii=False)
                logger.info(f"成功保存 {len(meme_data)} 个 meme 数据")
            else:
                logger.error(f"获取 meme 列表失败: {response.status_code}")
        except requests.Timeout:
            logger.error("获取 meme 列表超时")
        except requests.ConnectionError:
            logger.error("无法连接到 Meme 服务，请确保服务已启动")
        except Exception as e:
            logger.error(f"获取 meme 列表时发生错误: {e}", exc_info=True)

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
        response = self.session.post(api_url, files=files, data=data)
        if response.status_code == 200:
            return response.content
        return None


if __name__ == "__main__":
    meme = Meme()
    # 获取 meme 列表并保存到文件
    meme.get_meme_list()
