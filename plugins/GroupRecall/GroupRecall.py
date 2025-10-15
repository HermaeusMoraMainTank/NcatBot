import os
import json
import time
import threading
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

from ncatbot.core import MessageChain, Text, Image
from ncatbot.core.message import GroupMessage
from ncatbot.core.event.notice import NoticeEvent
from ncatbot.plugin_system import NcatBotPlugin
from ncatbot.plugin_system.builtin_plugin.unified_registry.filter_system.decorators import (
    group_only,
    on_notice,
)
from ncatbot.utils.logger import get_log

_log = get_log()


@dataclass
class RecallMessage:
    """撤回消息的数据结构"""

    message_id: str
    user_id: str
    nickname: str
    timestamp: float
    content: str  # 文本内容
    images: List[str] = None  # 图片路径列表
    message_type: str = "text"  # 消息类型：text, image, mixed

    def __post_init__(self):
        if self.images is None:
            self.images = []

    def to_dict(self) -> dict:
        """转换为字典"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RecallMessage":
        """从字典创建对象"""
        return cls(**data)


class GroupRecallPlugin(NcatBotPlugin):
    name = "GroupRecall"
    version = "1.0"

    # 数据存储路径
    DATA_DIR = os.path.join("data", "GroupRecall")
    DATA_FILE = os.path.join(DATA_DIR, "GroupRecall.json")

    # 消息存储：按群组ID存储消息
    recall_messages: Dict[str, List[RecallMessage]] = None

    # 数据保存控制
    _save_lock = threading.Lock()

    # 24小时 = 86400秒
    EXPIRATION_TIME = 24 * 3600

    # 使用说明
    usage_instructions = """撤回消息查询指令：
1. 查询撤回：查询撤回 [数量]
   
   数量可选（默认显示最近5条）：
   - 数字：显示最近N条撤回消息
   - 全部：显示所有撤回消息
   
示例：
- 查询撤回
- 查询撤回 10
- 查询撤回 全部
"""

    async def on_load(self):
        """异步加载插件"""
        _log.info(f"开始加载 {self.name} 插件 v{self.version}")

        # 初始化数据存储
        if self.recall_messages is None:
            self.recall_messages = {}

        # 加载保存的数据
        self._load_data()

        # 启动定时清理任务
        self._start_cleanup_task()

        _log.info(f"{self.name} 插件加载完成")

    def _reinit_(self):
        """插件重新加载时同步处理钩子 - 保护内存中的数据"""
        # 保存当前内存中的数据
        temp_recall_messages = (
            self.recall_messages.copy() if self.recall_messages else {}
        )

        # 重新初始化
        if self.recall_messages is None:
            self.recall_messages = {}

        # 恢复数据
        self.recall_messages.update(temp_recall_messages)

    def _load_data(self):
        """加载保存的数据"""
        try:
            # 确保目录存在
            os.makedirs(self.DATA_DIR, exist_ok=True)

            if os.path.exists(self.DATA_FILE):
                with open(self.DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)

                    # 加载撤回消息数据
                    new_recall_messages = {}
                    recall_data = data.get("recall_messages", {})

                    for group_id, messages_data in recall_data.items():
                        try:
                            messages = []
                            for msg_data in messages_data:
                                messages.append(RecallMessage.from_dict(msg_data))
                            new_recall_messages[group_id] = messages
                        except Exception as e:
                            _log.error(
                                f"[GroupRecall] 加载群组 {group_id} 数据失败: {e}"
                            )
                            continue

                    # 合并数据而不是直接替换，避免覆盖现有数据
                    for group_id, messages in new_recall_messages.items():
                        if group_id not in self.recall_messages:
                            self.recall_messages[group_id] = []
                        self.recall_messages[group_id].extend(messages)

        except Exception as e:
            _log.error(f"[GroupRecall] 加载数据失败: {e}")

    def _save_data(self):
        """保存数据"""
        with self._save_lock:
            try:
                # 确保目录存在
                os.makedirs(self.DATA_DIR, exist_ok=True)

                # 准备保存的数据
                data = {
                    "recall_messages": {
                        group_id: [msg.to_dict() for msg in messages]
                        for group_id, messages in self.recall_messages.items()
                    }
                }

                # 写入文件
                with open(self.DATA_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

            except Exception as e:
                _log.error(f"[GroupRecall] 保存数据失败: {e}")

    def _start_cleanup_task(self):
        """启动定时清理任务"""
        import asyncio

        async def cleanup_task():
            while True:
                try:
                    await asyncio.sleep(300)  # 每5分钟执行一次清理
                    self._cleanup_expired_messages()
                except Exception as e:
                    _log.error(f"[GroupRecall] 清理任务异常: {e}")

        # 创建并启动清理任务
        asyncio.create_task(cleanup_task())

    def _cleanup_expired_messages(self):
        """清理过期的消息"""
        current_time = time.time()
        cleaned_count = 0

        for group_id in list(self.recall_messages.keys()):
            original_count = len(self.recall_messages[group_id])
            # 过滤掉过期的消息
            self.recall_messages[group_id] = [
                msg
                for msg in self.recall_messages[group_id]
                if current_time - msg.timestamp <= self.EXPIRATION_TIME
            ]

            cleaned_count += original_count - len(self.recall_messages[group_id])

            # 如果群组没有消息了，删除群组
            if not self.recall_messages[group_id]:
                del self.recall_messages[group_id]

        if cleaned_count > 0:
            _log.info(f"[GroupRecall] 清理了 {cleaned_count} 条过期消息")
            self._save_data()

    def _save_message_content(self, message: GroupMessage) -> tuple:
        """保存消息内容，返回文本内容和图片路径列表"""
        text_content = ""
        image_paths = []

        for msg_element in message.message:
            if hasattr(msg_element, "msg_seg_type"):
                if msg_element.msg_seg_type == "text":
                    text_content += msg_element.text
                elif msg_element.msg_seg_type == "image":
                    # 保存图片
                    try:
                        image_path = self._save_image(msg_element)
                        if image_path:
                            image_paths.append(image_path)
                    except Exception as e:
                        _log.error(f"[GroupRecall] 保存图片失败: {e}")

        return text_content.strip(), image_paths

    def _save_image(self, image_element) -> Optional[str]:
        """保存图片到本地"""
        try:
            # 获取图片数据
            if hasattr(image_element, "url"):
                import requests

                response = requests.get(image_element.url)
                if response.status_code == 200:
                    # 生成文件名
                    timestamp = int(time.time() * 1000)
                    filename = f"recall_image_{timestamp}.jpg"
                    filepath = os.path.join(self.DATA_DIR, filename)

                    # 保存图片
                    with open(filepath, "wb") as f:
                        f.write(response.content)

                    return filepath
        except Exception as e:
            _log.error(f"[GroupRecall] 保存图片异常: {e}")

        return None

    @group_only
    async def handle_group_message(self, input: GroupMessage) -> None:
        """处理群消息 - 存储消息内容"""
        try:
            # 确保 group_id 和 user_id 都是字符串类型
            group_id = str(input.group_id)
            user_id = str(input.sender.user_id)
            nickname = input.sender.nickname or str(user_id)

            # 保存消息内容
            text_content, image_paths = self._save_message_content(input)

            # 确定消息类型
            message_type = "text"
            if text_content and image_paths:
                message_type = "mixed"
            elif image_paths:
                message_type = "image"

            # 创建消息记录
            recall_msg = RecallMessage(
                message_id=str(input.message_id),
                user_id=user_id,
                nickname=nickname,
                timestamp=time.time(),
                content=text_content,
                images=image_paths,
                message_type=message_type,
            )

            # 存储到对应群组
            if group_id not in self.recall_messages:
                self.recall_messages[group_id] = []

            self.recall_messages[group_id].append(recall_msg)

            # 保存数据
            self._save_data()

        except Exception as e:
            _log.error(f"[GroupRecall] 处理群消息失败: {e}")

    @on_notice
    async def handle_recall_notice(self, input: NoticeEvent) -> None:
        print(input)
        """处理撤回通知"""
        try:
            # 检查是否是撤回通知
            if not hasattr(input, "notice_type") or input.notice_type != "group_recall":
                return

            group_id = str(input.group_id)
            message_id = str(input.message_id)

            # 查找对应的消息
            if group_id in self.recall_messages:
                for msg in self.recall_messages[group_id]:
                    if msg.message_id == message_id:
                        # 找到被撤回的消息，记录撤回信息
                        _log.info(
                            f"[GroupRecall] 检测到消息撤回: 群组 {group_id}, 用户 {msg.user_id}, 消息ID {message_id}"
                        )
                        break

        except Exception as e:
            _log.error(f"[GroupRecall] 处理撤回通知失败: {e}")

    @group_only
    async def handle_query_recall(self, input: GroupMessage) -> None:
        """处理查询撤回消息命令"""
        try:
            message = input.raw_message.strip()
            if not message.startswith("查询撤回"):
                return

            # 解析命令参数
            parts = message.split()
            count = 5  # 默认显示5条

            if len(parts) > 1:
                if parts[1] == "全部":
                    count = None  # 显示全部
                else:
                    try:
                        count = int(parts[1])
                        if count <= 0:
                            count = 5
                    except ValueError:
                        count = 5

            group_id = str(input.group_id)

            # 获取该群组的撤回消息
            if (
                group_id not in self.recall_messages
                or not self.recall_messages[group_id]
            ):
                await input.reply("该群组暂无撤回消息记录")
                return

            # 按时间倒序排列，获取最新的消息
            messages = sorted(
                self.recall_messages[group_id], key=lambda x: x.timestamp, reverse=True
            )

            if count is not None:
                messages = messages[:count]

            # 构建回复消息
            if not messages:
                await input.reply("暂无撤回消息记录")
                return

            reply_elements = [Text("=== 最近撤回消息 ===\n")]

            for i, msg in enumerate(messages, 1):
                # 格式化时间
                msg_time = datetime.fromtimestamp(msg.timestamp).strftime("%m-%d %H:%M")

                # 添加消息信息
                reply_elements.append(Text(f"{i}. {msg.nickname} ({msg_time})\n"))

                # 添加文本内容
                if msg.content:
                    reply_elements.append(Text(f"   文本: {msg.content}\n"))

                # 添加图片
                if msg.images:
                    for img_path in msg.images:
                        if os.path.exists(img_path):
                            try:
                                reply_elements.append(Image(img_path))
                            except Exception as e:
                                _log.error(f"[GroupRecall] 加载图片失败: {e}")
                                reply_elements.append(Text("   [图片加载失败]\n"))

                reply_elements.append(Text("\n"))

            # 发送回复
            reply_message = MessageChain(reply_elements)
            await self.api.post_group_msg(
                group_id=input.group_id, rtf=reply_message, reply=input.message_id
            )

        except Exception as e:
            _log.error(f"[GroupRecall] 处理查询命令失败: {e}")
            await input.reply("查询撤回消息失败，请稍后重试")
