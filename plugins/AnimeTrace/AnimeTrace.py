import os
import re
import asyncio
import base64
from io import BytesIO
from typing import List, Optional
from urllib.parse import quote
from dataclasses import dataclass

import requests
from PIL import Image as PILImage
from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.types import MessageArray as MessageChain, PlainText, Image, Reply
from ncatbot.plugin import NcatBotPlugin
from ncatbot.core import registrar
from ncatbot.utils import get_log
from common.utils.plugin_commands import format_help, is_help_message

_log = get_log()

# 命令前缀列表
COMMAND_PREFIXES = ["查询人物", "识别人物", "角色", "人物"]

HELP_TEXT = format_help(
    "AnimeTrace 动漫人物识别",
    [
        f"{' / '.join(COMMAND_PREFIXES)} [动漫|gal]：识别图片中的角色",
        "需附带图片或回复含图消息；默认动漫模式，加 gal 为 Galgame 模式",
    ],
)


@dataclass
class Character:
    """角色信息"""

    work: str
    character: str


@dataclass
class BoxItem:
    """检测框信息"""

    box: List[float]
    box_id: str
    character: List[Character]


@dataclass
class AnimeTraceResult:
    """识别结果"""

    ai: bool
    data: List[BoxItem]
    code: int
    trace_id: str


class AnimeTracePlugin(NcatBotPlugin):
    name = "AnimeTrace"
    version = "1.0"

    # 配置
    ANIME_MODEL = "anime_model_lovelive"  # 动漫模型
    GAL_MODEL = "full_game_model_kira"  # galgame模型
    MAX_RESULTS = 3  # 每个角色最多返回几个识别结果
    AI_DETECT = True  # 是否检测AI图

    # API地址
    API_URL = "https://api.animetrace.com/v1/search"

    async def on_load(self):
        """异步加载插件"""
        _log.info(f"开始加载 {self.name} 插件 v{self.version}")

    def _check_command(self, message: str) -> tuple[bool, str]:
        """
        检查消息是否匹配命令格式
        返回: (是否匹配, 模式类型: 'anime' 或 'gal')
        命令格式: 查询人物/识别人物/角色/人物 [动漫/gal]
        默认是动漫
        """
        message = message.strip()

        # 检查是否以命令前缀开头
        matched_prefix = None
        for prefix in COMMAND_PREFIXES:
            if message.startswith(prefix):
                matched_prefix = prefix
                break

        if not matched_prefix:
            return False, ""

        # 提取命令后的内容
        remaining = message[len(matched_prefix) :].strip()

        # 检查是否包含 gal（不区分大小写）
        if remaining.lower().startswith("gal") or " gal" in remaining.lower():
            return True, "gal"

        # 检查是否明确指定了动漫
        if remaining.lower().startswith("动漫") or " 动漫" in remaining.lower():
            return True, "anime"

        # 如果没有指定，默认是动漫
        return True, "anime"

    async def _get_image_from_message(
        self, message: GroupMessage
    ) -> Optional[PILImage.Image]:
        """
        从消息中获取图片
        优先从当前消息获取，如果没有则从回复消息获取
        """
        # 先从当前消息中查找图片
        images = message.message.filter(Image)
        if images:
            return await self._download_image(images[0].url)

        # 如果没有，尝试从回复消息中获取
        reply_list = message.message.filter(Reply)
        if reply_list:
            reply_id = reply_list[0].id
            # get_msg 返回的是 GroupMessageEvent 对象
            reply_msg = await self.api.qq.query.get_msg(reply_id)
            # 从回复消息中获取图片
            reply_images = reply_msg.message.filter(Image)
            if reply_images:
                return await self._download_image(reply_images[0].url)

        return None

    async def _download_image(self, url: str) -> PILImage.Image:
        """下载图片并返回PIL Image对象"""

        def _fetch() -> PILImage.Image:
            fetch_url = re.sub(r"&amp;", "&", url)
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/114.0.0.0 Safari/537.36 Edg/114.0.1823.67"
                ),
            }
            response = requests.get(
                fetch_url, headers=headers, timeout=30, verify=False
            )
            response.raise_for_status()
            return PILImage.open(BytesIO(response.content))

        return await asyncio.to_thread(_fetch)

    def _convert_image_to_jpeg(self, img: PILImage.Image) -> bytes:
        """将图片转换为JPEG格式的字节流"""
        # 如果图片是RGBA模式，转换为RGB
        if img.mode == "RGBA":
            # 创建白色背景
            rgb_img = PILImage.new("RGB", img.size, (255, 255, 255))
            rgb_img.paste(img, mask=img.split()[3])  # 使用alpha通道作为mask
            img = rgb_img
        elif img.mode not in ("RGB", "L"):
            # 其他模式也转换为RGB
            img = img.convert("RGB")

        # 保存为JPEG
        bio = BytesIO()
        img.save(bio, format="JPEG", quality=95)
        return bio.getvalue()

    def _search_character(
        self, base_img: PILImage.Image, model: str, ai_detect: int = 0
    ) -> AnimeTraceResult:
        """调用API识别角色"""
        # 将图片转换为base64
        img_bytes = self._convert_image_to_jpeg(base_img)
        img_b64 = base64.b64encode(img_bytes).decode()

        # 准备请求数据
        data = {
            "is_multi": 1,
            "model": model,
            "ai_detect": ai_detect,
            "base64": img_b64,
        }

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/114.0.0.0 Safari/537.36 Edg/114.0.1823.67"
            ),
        }

        # 发送请求
        response = requests.post(
            self.API_URL,
            headers=headers,
            data=data,
            timeout=30,
        )
        response.raise_for_status()

        # 解析响应
        result_data = response.json()

        # 转换为数据类
        box_items = []
        for item in result_data.get("data", []):
            characters = [
                Character(work=c.get("work", ""), character=c.get("character", ""))
                for c in item.get("character", [])
            ]
            box_items.append(
                BoxItem(
                    box=item.get("box", []),
                    box_id=str(item.get("box_id", "")),
                    character=characters,
                )
            )

        return AnimeTraceResult(
            ai=result_data.get("ai", False),
            data=box_items,
            code=result_data.get("code", 0),
            trace_id=str(result_data.get("trace_id", "")),
        )

    def _construct_result_message(
        self, base_img: PILImage.Image, result: AnimeTraceResult, mode: str
    ) -> MessageChain:
        """构造结果消息"""
        elements = []

        char_nums = len(result.data)
        if char_nums == 0:
            elements.append(PlainText(text="没有识别到任何角色"))
            return MessageChain(elements)

        # 开头消息
        start_msg = f"共识别到{char_nums}个角色\n更多模型请访问:https://ai.animedb.cn"
        if result.ai:
            start_msg = "该图可能是ai绘图!\n" + start_msg
        elements.append(PlainText(text=start_msg))

        # 处理每个识别结果
        for box_item in result.data:
            width, height = base_img.size
            box = box_item.box
            # 将相对坐标转换为绝对坐标
            box = (
                int(box[0] * width),
                int(box[1] * height),
                int(box[2] * width),
                int(box[3] * height),
            )

            # 裁剪图片
            item_img = base_img.crop(box)
            # 转换为JPEG格式
            img_data = self._convert_image_to_jpeg(item_img)
            img_bytes = BytesIO(img_data)
            img_bytes.seek(0)

            # 获取角色信息
            characters = box_item.character[: self.MAX_RESULTS]
            may_num = len(characters)

            # 添加角色信息
            txt_msg = f"该角色有{may_num}种可能\n"
            elements.append(PlainText(text=txt_msg))

            for i, char in enumerate(characters, 1):
                name = char.character
                q = quote(name)

                elements.append(PlainText(text=f"\n{i}\n"))
                elements.append(PlainText(text=f"角色:{name}\n"))
                elements.append(PlainText(text=f"来自{mode}:{char.work}\n"))

                # 添加萌娘百科链接（可选）
                moegirl_link = f"萌娘百科:zh.moegirl.org.cn/index.php?search={q}\n"
                elements.append(PlainText(text=moegirl_link))

                # 添加维基百科链接
                wiki_link = f"zh.wikipedia.org/w/index.php?search={q}\n"
                elements.append(PlainText(text=wiki_link))

            # 添加裁剪后的角色图片
            # 将图片转换为base64或保存到临时文件
            img_bytes.seek(0)
            img_data = img_bytes.getvalue()
            # 保存到临时文件
            temp_dir = os.path.join("data", "temp", "animetrace")
            os.makedirs(temp_dir, exist_ok=True)
            temp_file = os.path.join(temp_dir, f"{box_item.box_id}.jpg")
            with open(temp_file, "wb") as f:
                f.write(img_data)

            elements.append(Image(file=temp_file))

        return MessageChain(elements)

    @registrar.qq.on_group_message()
    async def handle_anime_trace(self, input: GroupMessage) -> None:
        """处理动漫/GAL角色识别命令"""
        try:
            # 移除 CQ 码后再检查命令
            message = re.sub(r"\[CQ:[^\]]+\]", "", input.raw_message).strip()

            if is_help_message(
                message,
                command_names=COMMAND_PREFIXES):
                await input.reply(text=HELP_TEXT, at_sender=False)
                return

            # 检查命令
            is_match, mode_type = self._check_command(message)
            if not is_match:
                return

            # 确定模型和模式
            if mode_type == "gal":
                model = self.GAL_MODEL
                mode = "galgame"
            else:
                model = self.ANIME_MODEL
                mode = "动漫"

            # 获取图片
            base_img = await self._get_image_from_message(input)
            if not base_img:
                await input.reply("请发送需要识别的图片，或回复一张图片")
                return

            # 发送识别中提示
            await input.reply("正在识别中，请稍候...")

            # 调用API识别
            ai_detect = 1 if self.AI_DETECT else 0
            try:
                result = await asyncio.to_thread(
                    self._search_character, base_img, model, ai_detect
                )
            except Exception as e:
                _log.error(f"识别失败: {e}")
                await input.reply(f"识别失败，换张图片试试吧~\n{repr(e)}")
                return

            # 检查识别结果
            if result.code != 0:
                await input.reply(f"出错啦~可能是图里角色太多了~\n响应码:{result.code}")
                return

            if len(result.data) == 0:
                await input.reply("没有识别到任何角色")
                return

            # 构造并发送结果消息
            result_message = self._construct_result_message(base_img, result, mode)
            await self.api.qq.post_group_msg(
                group_id=input.group_id, rtf=result_message, reply=input.message_id
            )

        except Exception as e:
            _log.error(f"[AnimeTrace] 处理命令失败: {e}")
            await input.reply("识别失败，请稍后重试")
