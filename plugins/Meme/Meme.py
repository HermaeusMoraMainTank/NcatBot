import asyncio
import json
import logging
import re
import requests
from typing import List, Optional, Any
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from pathlib import Path
import tempfile

from common.utils.async_io import load_json

from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.types import At, Image, MessageArray as MessageChain, PlainText, Reply
from ncatbot.plugin import NcatBotPlugin
from ncatbot.core import registrar


# 配置日志
_log = logging.getLogger(__name__)
_log.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
_log.addHandler(handler)


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


class Meme(NcatBotPlugin):
    name = "Meme"
    version = "1.0"
    baseurl = "http://127.0.0.1:2233/memes"
    keylist = []
    keywordslist: dict[str, DataStructure] = {}
    timeout = 30  # 设置超时时间为30秒
    session = requests.Session()  # 使用 Session 来复用连接
    banlist = ["10123121"]  # 添加 banlist
    SPECIAL_USER_ID = "273421673"  # 特殊用户ID

    async def _session_request(self, method: str, url: str, **kwargs):
        return await asyncio.to_thread(getattr(self.session, method), url, **kwargs)

    async def on_load(self):
        """异步加载插件"""
        _log.info(f"开始加载 {self.name} 插件 v{self.version}")
        try:
            # 检查服务是否可用
            response = await self._session_request(
                "get", f"{self.baseurl}/keys", timeout=self.timeout
            )
            if response.status_code != 200:
                _log.error(f"Meme 服务不可用: {response.status_code}")
                return
            _log.info("Meme 服务连接成功")
        except Exception as e:
            _log.error(f"无法连接到 Meme 服务: {e}")
            return

        # 获取并保存 meme 列表
        await self.get_meme_list()

        await self.load_meme_data()
        _log.info(f"{self.name} 插件加载完成")

    async def load_meme_data(self):
        """异步加载 meme 数据"""
        try:
            meme_data = await load_json("data/json/memeKeys.json")
            self.keywordslist = {
                keyword: DataStructure.from_dict(data)
                for data in meme_data
                for keyword in data["keywords"]
            }
            _log.info(f"成功加载 {len(self.keywordslist)} 个 meme 关键词")
        except Exception as e:
            _log.error(f"加载 meme 数据失败: {e}", exc_info=True)

    @registrar.qq.on_group_message()
    async def handle_meme(self, input: GroupMessage):
        # 检查发送者是否在 banlist 中
        if str(input.sender.user_id) in self.banlist:
            return

        if input.raw_message == "meme":
            try:
                # 从 memeKeys.json 读取数据
                meme_data = await load_json("data/json/memeKeys.json")

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

                response = await self._session_request(
                    "post",
                    f"{self.baseurl}/render_list/",
                    json=request_data,
                    timeout=self.timeout,
                )

                if response.status_code == 200:
                    _log.info("成功获取 meme 列表")
                    # 保存响应内容为临时文件
                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=".jpg"
                    ) as tmp_file:
                        tmp_file.write(response.content)
                        tmp_path = Path(tmp_file.name)

                    try:
                        await self.api.qq.post_group_msg(
                            group_id=input.group_id,
                            rtf=MessageChain([Image(file=str(tmp_path))]),
                        )
                    finally:
                        # 删除临时文件
                        try:
                            tmp_path.unlink()
                        except Exception as e:
                            _log.warning(f"删除临时文件失败: {e}")
                else:
                    _log.error(f"获取 meme 列表失败: {response.status_code}")
            except Exception as e:
                _log.error(f"请求 meme 列表时发生错误: {e}", exc_info=True)
            return

        # 适配新的 MessageArray 结构，移除 CQ 码后获取纯文本
        message_text = ""
        for msg_segment in input.message:
            if hasattr(msg_segment, "text"):
                message_text = msg_segment.text
                break

        # 也尝试从 raw_message 中移除 CQ 码获取命令
        if not message_text:
            message_text = re.sub(r"\[CQ:[^\]]+\]", "", input.raw_message).strip()

        if message_text:
            com = message_text.strip()
            coms = str(com).split(" ")

            if coms[0] in self.keywordslist:
                # 检查消息中是否有被 ban 的用户
                for message in input.message:
                    if isinstance(message, At):
                        if str(message.user_id) in self.banlist:
                            return

                meme_config = self.keywordslist[coms[0]]
                params_type = meme_config.params_type

                if len(coms) > 1 and coms[1] == "info":
                    await self.send_meme_info(input, meme_config)
                    return

                reply_images = await self.get_images_from_reply(input)
                current_images = self._get_current_message_image_urls(input)
                has_image_source = bool(current_images or reply_images)

                avatar_files = await self.collect_avatar_files(
                    input, params_type.min_images, params_type.max_images
                )
                has_at = any(isinstance(msg, At) for msg in input.message)
                texts = self.collect_texts(input, has_at or has_image_source)

                if (
                    len(avatar_files) < params_type.min_images
                    or len(avatar_files) > params_type.max_images
                ):
                    _log.warning(f"头像数量不符合要求: {len(avatar_files)}")
                    return
                if (
                    len(texts) < params_type.min_texts
                    or len(texts) > params_type.max_texts
                ):
                    _log.warning(f"文本数量不符合要求: {len(texts)}")
                    return

                await self.send_meme_request(
                    input, meme_config.key, avatar_files, texts
                )

    async def send_meme_info(self, input: GroupMessage, meme_config: DataStructure):
        # 找到触发这个 meme 的关键词
        trigger_keyword = next(
            (
                keyword
                for keyword, data in self.keywordslist.items()
                if data.key == meme_config.key
            ),
            meme_config.key,
        )
        info_message = (
            f"触发关键词: {trigger_keyword}\n"
            f"最少图片数量: {meme_config.params_type.min_images}\n"
            f"最多图片数量: {meme_config.params_type.max_images}\n"
            f"最少文字数量: {meme_config.params_type.min_texts}\n"
            f"最多文字数量: {meme_config.params_type.max_texts}"
        )
        await self.api.qq.post_group_msg(group_id=input.group_id, text=info_message)

    async def get_images_from_reply(self, input: GroupMessage) -> List[str]:
        """从回复消息中获取图片 URL 列表"""
        image_urls: List[str] = []
        reply_list = input.message.filter(Reply)
        reply_id = reply_list[0].id if reply_list else None
        if reply_id is None:
            match = re.search(r"\[CQ:reply,id=(\d+)\]", input.raw_message)
            if match:
                reply_id = int(match.group(1))

        if reply_id is None:
            return image_urls

        reply_msg = await self.api.qq.query.get_msg(reply_id)
        segments = getattr(reply_msg, "message", [])

        if hasattr(segments, "filter"):
            for img in segments.filter(Image):
                if hasattr(img, "url") and img.url:
                    image_urls.append(img.url)
        elif isinstance(segments, list):
            for seg in segments:
                if isinstance(seg, Image):
                    if getattr(seg, "url", None):
                        image_urls.append(seg.url)
                elif isinstance(seg, dict) and seg.get("type") == "image":
                    url = (seg.get("data") or {}).get("url")
                    if url:
                        image_urls.append(url)

        return image_urls

    def _get_current_message_image_urls(self, input: GroupMessage) -> List[str]:
        """从当前消息中获取图片 URL"""
        urls: List[str] = []
        for img in input.message.filter(Image):
            if hasattr(img, "url") and img.url:
                urls.append(img.url)
        return urls

    async def collect_avatar_files(
        self, input: GroupMessage, min_images: int, max_images: int
    ) -> List[Path]:
        """收集图片 URL，下载后返回文件路径列表"""
        image_urls: List[str] = []
        current_user_id = str(input.sender.user_id)

        # 图片来源优先级：当前消息图片 > 回复图片 > @ 头像 > 发送者头像
        image_urls.extend(self._get_current_message_image_urls(input))

        if not image_urls:
            reply_images = await self.get_images_from_reply(input)
            if reply_images:
                image_urls.extend(reply_images)

        if not image_urls:
            for message in input.message:
                if isinstance(message, At):
                    target_id = str(message.user_id)
                    if (
                        target_id == self.SPECIAL_USER_ID
                        and current_user_id != self.SPECIAL_USER_ID
                    ):
                        target_id = current_user_id
                    image_urls.append(f"http://q1.qlogo.cn/g?b=qq&nk={target_id}&s=640")

        while len(image_urls) < min_images:
            image_urls.insert(
                0, f"http://q1.qlogo.cn/g?b=qq&nk={current_user_id}&s=640"
            )
        if len(image_urls) > max_images:
            image_urls = image_urls[:max_images]

        avatar_files: List[Path] = []
        for url in image_urls:
            try:
                url = re.sub(r"&amp;", "&", url)
                response = await self._session_request(
                    "get", url, timeout=self.timeout
                )
                if response.status_code == 200:
                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=".jpg"
                    ) as tmp_file:
                        tmp_file.write(response.content)
                        avatar_files.append(Path(tmp_file.name))
            except Exception as e:
                _log.error(f"下载图片失败 {url}: {e}")
        return avatar_files

    def collect_texts(self, input: GroupMessage, has_at: bool) -> List[str]:
        texts = []
        # 检查消息列表长度
        if len(input.message) <= 0:
            return texts

        # 找到包含命令的文本消息段索引
        command_text_index = -1
        first_message_text = ""
        for i, msg_segment in enumerate(input.message):
            if hasattr(msg_segment, "text") and msg_segment.text.strip():
                first_message_text = msg_segment.text
                command_text_index = i
                break

        if first_message_text:
            command_text = first_message_text.strip()
            # 分割命令，跳过第一个（关键词）
            parts = command_text.split(" ", 1)
            if len(parts) > 1:
                texts.extend(parts[1].split())

        # 处理其他消息
        for i, message in enumerate(input.message):
            # 跳过命令文本消息段
            if i == command_text_index:
                continue
            if has_at and isinstance(message, At):
                continue
            if isinstance(message, PlainText) and message.text:
                texts.extend(message.text.split())
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
            response = await self._session_request(
                "post",
                api_url,
                files=files,
                data=data,
                timeout=self.timeout,
            )

            if response.status_code == 200:
                _log.info("成功生成 meme 图片")
                # 保存响应内容为临时文件
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=".jpg"
                ) as tmp_file:
                    tmp_file.write(response.content)
                    tmp_path = Path(tmp_file.name)

                try:
                    await self.api.qq.post_group_msg(
                        group_id=input.group_id,
                        rtf=MessageChain([Image(file=str(tmp_path))]),
                    )
                finally:
                    # 删除临时文件
                    try:
                        tmp_path.unlink()
                    except Exception as e:
                        _log.warning(f"删除临时文件失败: {e}")
            else:
                _log.error(f"生成 meme 失败: {response.status_code} - {response.text}")
        except requests.Timeout:
            _log.error("请求超时")
        except requests.ConnectionError:
            _log.error("无法连接到 Meme 服务，请确保服务已启动")
        except Exception as e:
            _log.error(f"发送 meme 请求时发生错误: {e}", exc_info=True)
        finally:
            for file in avatar_files:
                try:
                    file.unlink()
                except Exception as e:
                    _log.warning(f"删除临时文件失败: {e}")

    async def get_meme_list(self):
        """异步获取 meme 列表"""
        try:
            response = await self._session_request(
                "get", f"{self.baseurl}/keys", timeout=self.timeout
            )

            if response.status_code == 200:
                meme_keys = response.json()
                meme_data = []

                for key in meme_keys:
                    info_response = await self._session_request(
                        "get",
                        f"{self.baseurl}/{key}/info",
                        timeout=self.timeout,
                    )
                    if info_response.status_code == 200:
                        meme_data.append(info_response.json())
                    else:
                        _log.warning(
                            f"获取 meme {key} 信息失败: {info_response.status_code}"
                        )

                def _save_meme_keys() -> None:
                    with open("data/json/memeKeys.json", "w", encoding="utf-8") as file:
                        json.dump(meme_data, file, indent=4, ensure_ascii=False)

                await asyncio.to_thread(_save_meme_keys)
                _log.info(f"成功保存 {len(meme_data)} 个 meme 数据")
            else:
                _log.error(f"获取 meme 列表失败: {response.status_code}")
        except requests.Timeout:
            _log.error("获取 meme 列表超时")
        except requests.ConnectionError:
            _log.error("无法连接到 Meme 服务，请确保服务已启动")
        except Exception as e:
            _log.error(f"获取 meme 列表时发生错误: {e}", exc_info=True)

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
