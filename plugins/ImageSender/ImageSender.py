from datetime import datetime
import logging
import os
import random
import re
import requests
import html
import hashlib
from ncatbot.core import GroupMessage, Image, MessageChain
from ncatbot.plugin_system import NcatBotPlugin, on_message
import asyncio
from common.constants.HMMT import HMMT


log = logging.getLogger(__name__)


class ImageSender(NcatBotPlugin):
    name = "ImageSender"  # 插件名称
    version = "1.0"  # 插件版本

    async def on_load(self):
        """异步加载插件"""
        log.info(f"开始加载 {self.name} 插件 v{self.version}")
        # 检查所有图片目录是否存在
        for cmd in self.commands.values():
            # 获取当前工作目录的绝对路径
            current_dir = os.getcwd()
            full_path = os.path.join(current_dir, cmd["path"])
            if not os.path.exists(full_path):
                log.warning(f"图片目录不存在: {full_path}")
        log.info(f"{self.name} 插件加载完成")

    max_count = 3  # 最大发送数量
    allowed_users = None  # 全局允许的用户ID列表，None表示所有用户都可以使用
    blacklist = ["1363021751"]  # 黑名单用户ID列表，黑名单用户无法使用任何功能

    # 命令配置
    commands = {
        "母肥": {
            "triggers": ["母肥", "肥肥"],
            "path": "C:\\Users\\27342\\Downloads\\lalafell\\lalafell",
            "allowed_users": None,
            "recall_time": None,  # 撤回时间（秒），None 表示不撤回
        },
        "zmd": {
            "triggers": [
                "zmd",
                "迷茫的时候 不如听听zmd说的话",
            ],
            "path": "data/image/zmd",
            "allowed_users": [
                "273421673",
                "635773721",
                "510337095",
                "3420347160",
                "1508864751",
                "10123121",
                "1607928177",
                "2779893879",
                "837089951",
            ],
            "recall_time": None,  # 撤回时间（秒）
        },
        "doro": {
            "triggers": ["doro"],
            "path": "data/image/doro",
            "allowed_users": None,
            "recall_time": None,  # 撤回时间（秒），None 表示不撤回
        },
        "柴郡": {
            "triggers": ["柴郡"],
            "path": "data/image/cheshire",
            "allowed_users": None,
            "recall_time": None,  # 撤回时间（秒），None 表示不撤回
        },
        "llm": {
            "triggers": ["llm", "迷茫的时候 不如听听llm说的话"],
            "path": "data/image/llm",
            "allowed_users": [
                "273421673",
                "2779893879",
                "361432025",
                "837089951",
                "3398902282",
            ],
            "recall_time": None,  # 撤回时间（秒），None 表示不撤回
        },
        "耄耋": {
            "triggers": ["耄耋"],
            "path": "data/image/耄耋",
            "allowed_users": None,
            "recall_time": None,  # 撤回时间（秒），None 表示不撤回
        },
        "xysk": {
            "triggers": [
                "咲夜saki",
                "迷茫的时候 不如听听咲夜saki说的话",
                "xysk",
                "xtxy",
            ],
            "path": "data/image/咲夜saki",
            "allowed_users": [
                "273421673",
                "635773721",
                "1506123340",
                "10123121",
                "1508864751",
                "2034756660",
                "1824159516",
            ],
            "recall_time": None,  # 撤回时间（秒），None 表示不撤回
        },
        "alice": {
            "triggers": ["alice"],
            "path": "data/image/alice",
            "allowed_users": None,
            "recall_time": None,  # 撤回时间（秒），None 表示不撤回
        },
        "kipfel": {
            "triggers": ["kipfel"],
            "path": "data/image/kipfel",
            "allowed_users": None,
            "recall_time": None,  # 撤回时间（秒），None 表示不撤回
        },
        "darkdog": {
            "triggers": ["darkdog"],
            "path": "data/image/darkdog",
            "allowed_users": None,
            "recall_time": None,  # 撤回时间（秒），None 表示不撤回
        },
        "whitecat": {
            "triggers": ["whitecat"],
            "path": "data/image/whitecat",
            "allowed_users": None,
            "recall_time": None,  # 撤回时间（秒），None 表示不撤回
        },
        "ybxa": {
            "triggers": ["ybxa"],
            "path": "data/image/ybxa",
            "allowed_users": ["273421673", "1310043427", "1079454672"],
            "recall_time": None,  # 撤回时间（秒），None 表示不撤回
        },
        "blb": {
            "triggers": ["blb", "菠萝包"],
            "path": "data/image/blb",
            "allowed_users": None,
            "recall_time": None,  # 撤回时间（秒），None 表示不撤回
        },
        "色图zmd": {
            "triggers": ["色图zmd"],
            "path": "data/image/zmd色图",
            "allowed_users": ["273421673", "635773721", "1508864751"],
            "recall_time": 1,  # 撤回时间（秒）
        },
        "猪": {
            "triggers": ["猪"],
            "path": "data/image/猪",
            "allowed_users": None,
            "recall_time": None,  # 撤回时间（秒），None 表示不撤回
        },
        "xqs": {
            "triggers": ["xqs"],
            "path": "data/image/xqs",
            "allowed_users": None,
            "recall_time": None,  # 撤回时间（秒），None 表示不撤回
        },
        "qqb": {
            "triggers": ["qqb", "千千坂", "干饭饭"],
            "path": "data/image/qqb",
            "allowed_users": None,
            "recall_time": None,  # 撤回时间（秒），None 表示不撤回
        },
        "光头zmd": {
            "triggers": ["光头zmd"],
            "path": "data/image/光头zmd",
            "allowed_users": None,
            "recall_time": None,  # 撤回时间（秒），None 表示不撤回
        },
        "df": {
            "triggers": ["df"],
            "path": "data/image/df",
            "allowed_users": None,
            "recall_time": None,  # 撤回时间（秒），None 表示不撤回
        },
    }

    @on_message
    async def handle_image(self, input: GroupMessage):
        message = input.raw_message.strip()

        # 检查黑名单
        if input.sender.user_id in self.blacklist:
            return  # 黑名单用户直接忽略

        # 处理上传功能
        if message.startswith("上传 "):
            await self.handle_upload(input, message)
            return

        # 检查消息是否以任何命令开头
        for command, config in self.commands.items():
            for trigger in config["triggers"]:
                if message.startswith(trigger):
                    # 检查全局权限
                    if (
                        self.allowed_users
                        and input.sender.user_id not in self.allowed_users
                    ):
                        return

                    # 检查命令特定权限
                    if (
                        config["allowed_users"]
                        and input.sender.user_id not in config["allowed_users"]
                    ):
                        return

                    # 处理 count 查询
                    if message.startswith(trigger + " count"):
                        await self.handle_count_query(input, command, config)
                        return

                    # 处理带数量的情况
                    if message.startswith(trigger + " "):
                        trimmed_message = message[len(trigger) + 1 :].strip()
                        if not trimmed_message.isdigit():
                            return

                        count = int(trimmed_message)
                        image_files = self.get_image_files(config["path"])

                        if count <= self.max_count:
                            # 收集所有要发送的图片
                            selected_files = []
                            for _ in range(count):
                                file = random.choice(image_files)
                                log.info(
                                    f"Time:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {file}"
                                )
                                selected_files.append(file)

                            # 一次性发送所有图片
                            if selected_files:
                                response = await self.api.post_group_msg(
                                    group_id=input.group_id,
                                    rtf=MessageChain(
                                        [Image(file) for file in selected_files]
                                    ),
                                )
                                # 响应直接就是消息ID
                                last_message_id = response
                                log.info(f"发送消息 ID: {last_message_id}")  # 添加日志
                                # 撤回消息
                                if config["recall_time"] and last_message_id:
                                    log.info(
                                        f"将在 {config['recall_time']} 秒后撤回消息 ID: {last_message_id}"
                                    )  # 添加日志
                                    await self.recall_message(
                                        last_message_id, config["recall_time"]
                                    )
                        else:
                            await self.api.post_group_msg(
                                group_id=input.group_id, text="别太贪心"
                            )
                    # 处理单个图片的情况
                    elif message == trigger:
                        image_files = self.get_image_files(config["path"])

                        if not image_files:
                            await self.api.post_group_msg(
                                group_id=input.group_id,
                                text=f"路径 {config['path']} 中没有找到图片文件！",
                            )
                            return

                        file = random.choice(image_files)
                        log.info(
                            f"Time:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {file}"
                        )
                        response = await self.api.post_group_msg(
                            group_id=input.group_id, rtf=MessageChain([Image(file)])
                        )
                        # 响应直接就是消息ID
                        last_message_id = response
                        log.info(f"发送消息 ID: {last_message_id}")  # 添加日志
                        # 撤回消息
                        if config["recall_time"] and last_message_id:
                            log.info(
                                f"将在 {config['recall_time']} 秒后撤回消息 ID: {last_message_id}"
                            )  # 添加日志
                            await self.recall_message(
                                last_message_id, config["recall_time"]
                            )
                    return

    async def handle_count_query(self, input: GroupMessage, command: str, config: dict):
        """处理 count 查询请求"""
        image_files = self.get_image_files(config["path"])
        image_count = len(image_files)

        # 构建权限信息
        if config["allowed_users"]:
            # 特殊处理：如果是色图zmd命令，在允许用户列表中添加506531786（仅显示，不影响实际权限）
            if command == "色图zmd":
                display_users = config["allowed_users"] + ["10123121"]
                allowed_users_text = f"允许用户: {', '.join(map(str, display_users))}"
            else:
                allowed_users_text = (
                    f"允许用户: {', '.join(map(str, config['allowed_users']))}"
                )
            upload_permission_text = "上传权限: 仅限允许用户"
        else:
            allowed_users_text = "允许用户: 所有用户"
            upload_permission_text = "上传权限: 所有用户"

        # 构建响应消息
        response = f"关键词: {command}\n"
        response += f"图片数量: {image_count}\n"
        response += f"{allowed_users_text}\n"
        response += f"{upload_permission_text}\n"
        response += f"最大发送数量: {self.max_count}"
        response += f"\n是否撤回: {'是' if config['recall_time'] else '否'}"
        response += (
            f"\n撤回时长: {config['recall_time']} 秒" if config["recall_time"] else ""
        )

        await self.api.post_group_msg(group_id=input.group_id, text=response)

    async def handle_upload(self, input: GroupMessage, message: str):
        """处理图片上传请求"""
        # 解析上传命令格式：上传 关键词[CQ:image,file=...,url=...]
        # 先提取关键词（支持没有空格的情况）
        keyword_match = re.match(r"上传\s*(\w+)", message)
        if not keyword_match:
            await self.api.post_group_msg(
                group_id=input.group_id, text="上传格式错误！请使用：上传 关键词[图片]"
            )
            return

        keyword = keyword_match.group(1)

        # 然后提取所有的图片标签（支持包含额外字段的情况）
        image_pattern = r"\[CQ:image,.*?file=([^,]+),.*?url=([^\]]+)\]"
        image_matches = re.findall(image_pattern, message)

        if not image_matches:
            await self.api.post_group_msg(
                group_id=input.group_id,
                text="未找到图片信息！请使用：上传 关键词[图片]",
            )
            return

        # 处理每个匹配的图片
        success_count = 0
        failed_count = 0
        duplicate_count = 0
        user_id = input.sender.user_id

        # 查找对应的命令配置
        command_config = None
        for cmd_name, config in self.commands.items():
            if cmd_name == keyword:
                command_config = config
                break

        if not command_config:
            await self.api.post_group_msg(
                group_id=input.group_id, text=f"未知的关键词：{keyword}"
            )
            return

        # 检查该关键词的上传权限（基于allowed_users）
        if (
            command_config["allowed_users"]
            and user_id not in command_config["allowed_users"]
        ):
            await self.api.post_group_msg(
                group_id=input.group_id, text="您没有上传权限！"
            )
            return

        for filename, url in image_matches:
            # 下载并保存图片
            result, status = await self.download_and_save_image(
                url, filename, command_config["path"], user_id
            )

            if result:
                success_count += 1
                log.info(f"用户 {user_id} 成功上传图片到 {keyword}: {filename}")
            elif status == "duplicate":
                duplicate_count += 1
                log.info(f"用户 {user_id} 上传的图片已存在于 {keyword}: {filename}")
            else:
                failed_count += 1

        # 发送上传结果
        result_message = f"上传完成！成功: {success_count} 张，重复: {duplicate_count} 张，失败: {failed_count} 张"
        await self.api.post_group_msg(group_id=input.group_id, text=result_message)

    async def download_and_save_image(
        self, url: str, filename: str, target_path: str, user_id: int
    ) -> tuple[bool, str]:
        """下载并保存图片到指定路径，返回(是否成功, 状态信息)"""
        try:
            # 如果路径不是绝对路径，则转换为绝对路径
            if not os.path.isabs(target_path):
                current_dir = os.getcwd()
                target_path = os.path.join(current_dir, target_path)

            # 确保目标目录存在
            os.makedirs(target_path, exist_ok=True)

            # 解码HTML实体（如 &amp; -> &）
            decoded_url = html.unescape(url)

            # 设置请求头，模拟浏览器
            headers = {
                "User-Agent": HMMT.USER_AGENT,
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://multimedia.nt.qq.com.cn/",
            }

            # 下载图片
            response = requests.get(decoded_url, headers=headers, timeout=30)
            response.raise_for_status()

            # 计算图片内容的MD5哈希值
            image_content = response.content
            image_hash = hashlib.md5(image_content).hexdigest()

            # 检查是否已存在相同内容的图片
            existing_files = self.get_image_files(target_path)
            for existing_file in existing_files:
                try:
                    with open(existing_file, "rb") as f:
                        existing_content = f.read()
                        existing_hash = hashlib.md5(existing_content).hexdigest()
                        if existing_hash == image_hash:
                            log.info(f"图片已存在，跳过上传: {existing_file}")
                            return False, "duplicate"  # 返回重复状态
                except Exception as e:
                    log.warning(f"读取现有文件失败 {existing_file}: {e}")
                    continue

            # 生成带时间戳和用户ID的文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            name, ext = os.path.splitext(filename)
            new_filename = f"{user_id}_{timestamp}_{name}{ext}"

            # 构建完整的文件路径
            file_path = os.path.join(target_path, new_filename)

            # 保存图片
            with open(file_path, "wb") as f:
                f.write(image_content)

            log.info(f"图片已保存到: {file_path}")
            return True, "success"

        except Exception as e:
            log.error(f"下载图片失败 {url}: {str(e)}")
            return False, "failed"

    @staticmethod
    def get_image_files(folder_path):
        # 如果路径不是绝对路径，则转换为绝对路径
        if not os.path.isabs(folder_path):
            current_dir = os.getcwd()
            folder_path = os.path.join(current_dir, folder_path)

        if os.path.isdir(folder_path):
            return [
                os.path.join(folder_path, f)
                for f in os.listdir(folder_path)
                if f.lower().endswith((".jpg", ".png", ".jpeg", ".gif"))
            ]
        return []

    async def recall_message(self, message_id: int, recall_time: int):
        """
        撤回消息
        """
        await asyncio.sleep(recall_time)
        log.info(f"正在撤回消息 ID: {message_id}")  # 添加日志
        # 撤回指定的消息
        await self.api.delete_msg(message_id)
