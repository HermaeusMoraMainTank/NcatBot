from datetime import datetime
import logging
import random
import aiohttp
from PIL import Image, ImageDraw, ImageFont
import io
import base64
from ncatbot.core.message import GroupMessage
from ncatbot.core.element import Image as BotImage, MessageChain, Text, At
from ncatbot.plugin import CompatibleEnrollment, BasePlugin
from .commands import COMMANDS, CATEGORY_NAMES, CATEGORY_COMMANDS
import asyncio

bot = CompatibleEnrollment
log = logging.getLogger(__name__)

# 用户调用频率限制
USER_COOLDOWN = 60  # 1分钟冷却时间（秒）
user_last_call_times = {}  # 存储用户最后调用时间


def check_user_cooldown(user_id: int) -> bool:
    """检查用户是否在冷却时间内"""
    last_call_time = user_last_call_times.get(user_id)
    if not last_call_time:
        return True  # 如果没有记录，则表示可以调用

    now = datetime.now()
    remaining_time = USER_COOLDOWN - (now - last_call_time).total_seconds()
    return remaining_time <= 0


def update_user_call_time(user_id: int) -> None:
    """更新用户最后调用时间"""
    user_last_call_times[user_id] = datetime.now()


class MoehuImageSender(BasePlugin):
    name = "MoehuImageSender"  # 插件名称
    version = "1.0"  # 插件版本
    commands = COMMANDS  # 命令配置

    # Hololive角色列表
    HOLOLIVE_CHARACTERS = {
        "小鲨鱼": "gawr-gura",  # Gawr Gura
        "雪花菈米": "yukihana",  # Yukihana Lamy
        "夏色祭": "natsuiro",  # Natsuiro Matsuri
        "润羽露西娅": "uruha-rushia",  # Uruha Rushia
        "角卷绵芽": "tsunomaki-watame",  # Tsunomaki Watame
        "常暗永远": "tokoyami-towa",  # Tokoyami Towa
        "兔田佩克菈": "usada-pekora",  # Usada Pekora
        "一伊那尔栖": "ninomae",  # Ninomae Ina'nis
        "大神澪": "ookami-mio",  # Ookami Mio
        "樱巫女": "sakura-miko",  # Sakura Miko
        "木口EN": "holoen",  # Hololive EN
        "白上吹雪": "fubuki",  # Shirakami Fubuki
        "戌神沁音": "inugami-korone",  # Inugami Korone
        "阿夸": "aqua",  # Minato Aqua
    }

    async def on_load(self):
        """异步加载插件"""
        log.info(f"开始加载 {self.name} 插件 v{self.version}")
        self.commands = COMMANDS  # 初始化commands
        log.info(f"{self.name} 插件加载完成")

    max_count = 3  # 最大发送数量
    allowed_users = None  # 全局允许的用户ID列表，None表示所有用户都可以使用

    async def get_image_url(self, image_id: str) -> str:
        """获取图片URL"""
        url = f"https://img.moehu.org/pic.php?id={image_id}"
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=10) as response:
                        if response.status == 200:
                            return str(response.url)
                        log.warning(
                            f"获取图片失败，状态码: {response.status}，重试次数: {retry_count + 1}"
                        )
            except Exception as e:
                log.error(f"获取图片时发生错误: {e}，重试次数: {retry_count + 1}")

            retry_count += 1
            if retry_count < max_retries:
                await asyncio.sleep(1)  # 等待1秒后重试

        return None

    async def generate_help_image(self) -> bytes:
        """生成帮助图片"""
        # 创建图片
        width = 800
        # 计算所需高度：标题(60) + 每个分类(标题30 + 内容25*命令数 + 间距20)
        total_commands = sum(len(cmds) for cmds in CATEGORY_COMMANDS.values())
        height = 60 + sum(
            30 + len(cmds) * 25 + 20 for cmds in CATEGORY_COMMANDS.values()
        )
        # 添加底部边距
        height += 40

        image = Image.new("RGB", (width, height), color="white")
        draw = ImageDraw.Draw(image)

        try:
            font = ImageFont.truetype("simhei.ttf", 20)
            title_font = ImageFont.truetype("simhei.ttf", 24)
        except:
            font = ImageFont.load_default()
            title_font = ImageFont.load_default()

        # 绘制标题
        title = "随机表情包帮助"
        title_width = draw.textlength(title, font=title_font)
        draw.text(
            ((width - title_width) // 2, 20), title, fill="black", font=title_font
        )

        # 绘制分类和命令
        y = 60
        for category, name in CATEGORY_NAMES.items():
            # 绘制分类标题
            draw.text((20, y), f"【{name}】", fill="blue", font=title_font)
            y += 30

            # 绘制该分类下的所有命令
            for cmd_name, cmd_config in CATEGORY_COMMANDS[category].items():
                triggers = "、".join(cmd_config["triggers"])
                text = f"{cmd_name}: {triggers}"
                # 如果文本太长，进行换行处理
                if draw.textlength(text, font=font) > width - 60:
                    # 计算每行可以容纳的字符数
                    chars_per_line = int(
                        (width - 60) / (draw.textlength("测", font=font))
                    )
                    # 分行显示
                    for i in range(0, len(text), chars_per_line):
                        line = text[i : i + chars_per_line]
                        draw.text((40, y), line, fill="black", font=font)
                        y += 25
                else:
                    draw.text((40, y), text, fill="black", font=font)
                    y += 25

            y += 20  # 分类之间的间距

        # 将图片转换为字节流
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format="PNG")
        img_byte_arr.seek(0)
        return img_byte_arr.getvalue()

    async def ban_user(self, group_id: int, user_id: int, duration: int):
        """禁言用户"""
        try:
            await self.api.set_group_ban(
                group_id=group_id,
                user_id=user_id,
                duration=duration * 60,  # 转换为秒
            )
            return True
        except Exception as e:
            log.error(f"禁言用户失败: {e}")
            return False

    @bot.group_event()
    async def handle_image(self, input: GroupMessage):
        message = input.raw_message.strip()
        user_id = input.sender.user_id

        # 处理帮助命令
        if message == "随机表情包":
            try:
                help_image = await self.generate_help_image()
                # 将图片数据转换为base64
                image_base64 = base64.b64encode(help_image).decode()
                await self.api.post_group_msg(
                    group_id=input.group_id,
                    rtf=MessageChain([BotImage(f"base64://{image_base64}")]),
                )
            except Exception as e:
                log.error(f"发送帮助图片失败: {e}")
                await self.api.post_group_msg(
                    group_id=input.group_id, text="发送帮助图片失败，请稍后重试"
                )
            return

        # 检查消息是否完全匹配任何命令
        for command, config in self.commands.items():
            for trigger in config["triggers"]:
                if message == trigger or message.startswith(trigger + " "):
                    # 检查是否是Hololive角色
                    if command in self.HOLOLIVE_CHARACTERS:
                        # 获取请求的图片数量
                        count = 1
                        if message.startswith(trigger + " "):
                            trimmed_message = message[len(trigger) + 1 :].strip()
                            if trimmed_message.isdigit():
                                count = int(trimmed_message)

                        # 禁言用户
                        if await self.ban_user(
                            input.group_id, input.sender.user_id, count
                        ):
                            message = MessageChain(
                                [At(input.sender.user_id), Text(" 想似了是不")]
                            )
                            await self.api.post_group_msg(
                                group_id=input.group_id, rtf=message
                            )
                        return

                    # 检查用户是否在冷却时间内
                    if not check_user_cooldown(user_id):
                        remaining_time = int(
                            USER_COOLDOWN
                            - (
                                datetime.now() - user_last_call_times[user_id]
                            ).total_seconds()
                        )
                        await self.api.post_group_msg(
                            group_id=input.group_id,
                            rtf=MessageChain(
                                [
                                    At(user_id),
                                    Text(
                                        f" 你还需要等待 {remaining_time} 秒才能再次使用"
                                    ),
                                ]
                            ),
                        )
                        return

                    # 检查全局权限
                    if (
                        self.allowed_users
                        and input.sender.user_id not in self.allowed_users
                    ):
                        return

                    # 检查命令特定权限
                    if (
                        config.get("allowed_users")
                        and input.sender.user_id not in config["allowed_users"]
                    ):
                        return

                    try:
                        # 处理带数量的情况
                        if message.startswith(trigger + " "):
                            trimmed_message = message[len(trigger) + 1 :].strip()
                            if not trimmed_message.isdigit():
                                return

                            count = int(trimmed_message)
                            if count <= self.max_count:
                                # 收集所有图片URL
                                image_urls = []
                                for _ in range(count):
                                    image_url = await self.get_image_url(config["id"])
                                    if image_url:
                                        log.info(
                                            f"Time:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {image_url}"
                                        )
                                        image_urls.append(image_url)

                                # 一次性发送所有图片
                                if image_urls:
                                    await self.api.post_group_msg(
                                        group_id=input.group_id,
                                        rtf=MessageChain(
                                            [BotImage(url) for url in image_urls]
                                        ),
                                    )
                                    update_user_call_time(
                                        user_id
                                    )  # 只有在成功发送后才更新冷却时间
                                else:
                                    await self.api.post_group_msg(
                                        group_id=input.group_id,
                                        text="获取图片失败，请稍后重试",
                                    )
                            else:
                                await self.api.post_group_msg(
                                    group_id=input.group_id, text="别太贪心"
                                )
                        # 处理单个图片的情况
                        elif message == trigger:
                            image_url = await self.get_image_url(config["id"])
                            if image_url:
                                log.info(
                                    f"Time:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {image_url}"
                                )
                                try:
                                    await self.api.post_group_msg(
                                        group_id=input.group_id,
                                        rtf=MessageChain([BotImage(image_url)]),
                                    )
                                    update_user_call_time(
                                        user_id
                                    )  # 只有在成功发送后才更新冷却时间
                                except Exception as e:
                                    log.error(f"发送图片时发生错误: {e}")
                                    await self.api.post_group_msg(
                                        group_id=input.group_id,
                                        text="发送图片失败，请稍后重试",
                                    )
                            else:
                                await self.api.post_group_msg(
                                    group_id=input.group_id,
                                    text="获取图片失败，请稍后重试",
                                )
                    except Exception as e:
                        log.error(f"发送图片时发生错误: {e}")
                        await self.api.post_group_msg(
                            group_id=input.group_id, text="发送图片失败，请稍后重试"
                        )
                    return
