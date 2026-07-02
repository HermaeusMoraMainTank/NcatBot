import asyncio
import os
import json
import time
import threading
from datetime import datetime, date
from typing import Dict, List, Optional, Union
from dataclasses import dataclass, asdict

from ncatbot.event.qq import GroupMessageEvent as GroupMessage, NoticeEvent
from ncatbot.types import At, Image, MessageArray as MessageChain, PlainText
from ncatbot.plugin import NcatBotPlugin
from ncatbot.core import registrar
from ncatbot.utils import get_log

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
    is_recalled: bool = False  # 是否已被撤回

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

    # 「查询撤回」机器人回复在多少秒后自动撤回（秒）
    QUERY_REPLY_RECALL_SECONDS = 5

    # 数据存储路径
    DATA_DIR = os.path.join("data", "GroupRecall")
    DATA_FILE = os.path.join(DATA_DIR, "GroupRecall.json")

    # 消息存储：按群组ID存储消息
    recall_messages: Dict[str, List[RecallMessage]] = None

    # 数据保存控制
    _save_lock = threading.Lock()

    # 2天 = 48小时 = 172800秒
    EXPIRATION_TIME = 2 * 24 * 3600

    # 记录上一次清理的日期
    last_cleanup_date: date = None

    # 使用说明
    usage_instructions = """撤回消息查询指令：
1. 查询撤回：查询撤回 [数量] [@用户]
   
   参数说明：
   - 数量可选（默认显示最近5条）：
     * 数字：显示最近N条撤回消息
     * 全部：显示所有撤回消息
   - @用户可选：只显示指定用户的撤回消息
   
   权限要求：只有群主或管理员才能使用此功能
   
示例：
- 查询撤回
- 查询撤回 10
- 查询撤回 全部
- 查询撤回 @用户
- 查询撤回 10 @用户
"""

    async def on_load(self):
        """异步加载插件"""
        _log.info(f"开始加载 {self.name} 插件 v{self.version}")

        # 初始化数据存储
        if self.recall_messages is None:
            self.recall_messages = {}

        # 加载保存的数据
        self._load_recall_json()

        # 启动时执行一次清理
        self._cleanup_expired_messages()
        self.last_cleanup_date = date.today()
        _log.info(f"[GroupRecall] 启动时清理完成，清理日期: {self.last_cleanup_date}")

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

    def _load_recall_json(self):
        """加载撤回记录 JSON（勿命名为 _load_data，以免覆盖 DataMixin）。"""
        try:
            # 确保目录存在
            os.makedirs(self.DATA_DIR, exist_ok=True)

            if self.recall_messages is None:
                self.recall_messages = {}

            if os.path.exists(self.DATA_FILE):
                with open(self.DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)

                    # 加载撤回消息数据
                    new_recall_messages = {}
                    recall_data = data.get("recall_messages")
                    if not isinstance(recall_data, dict):
                        recall_data = {}

                    for group_id, messages_data in recall_data.items():
                        try:
                            messages = []
                            if not isinstance(messages_data, list):
                                continue
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

    def _save_recall_json(self):
        """保存撤回记录 JSON（勿命名为 _save_data，以免覆盖 DataMixin）。"""
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
        import threading

        def cleanup_task():
            """在单独线程中运行的清理任务"""
            while True:
                try:
                    time.sleep(300)  # 每5分钟执行一次清理
                    self._cleanup_expired_messages()
                except Exception as e:
                    _log.error(f"[GroupRecall] 清理任务异常: {e}")

        # 在单独线程中启动清理任务
        cleanup_thread = threading.Thread(target=cleanup_task, daemon=True)
        cleanup_thread.start()

    def _cleanup_expired_messages(self):
        """清理过期的消息和对应的图片文件"""
        current_time = time.time()
        cleaned_count = 0
        deleted_images_count = 0

        # 收集所有在数据中引用的图片路径
        referenced_images = set()
        for group_id, messages in self.recall_messages.items():
            for msg in messages:
                if msg.images:
                    for img_path in msg.images:
                        if img_path:
                            # 转换为绝对路径以便比较
                            abs_path = os.path.abspath(img_path)
                            referenced_images.add(abs_path)

        for group_id in list(self.recall_messages.keys()):
            original_messages = self.recall_messages[group_id]
            expired_messages = []
            valid_messages = []

            for msg in original_messages:
                if current_time - msg.timestamp <= self.EXPIRATION_TIME:
                    # 消息未过期，保留
                    valid_messages.append(msg)
                else:
                    # 消息已过期，标记为需要删除
                    expired_messages.append(msg)

            # 删除过期消息对应的图片文件
            for msg in expired_messages:
                if msg.images:
                    for img_path in msg.images:
                        try:
                            if os.path.exists(img_path):
                                os.remove(img_path)
                                deleted_images_count += 1
                                _log.debug(f"[GroupRecall] 删除过期图片: {img_path}")
                        except Exception as e:
                            _log.error(f"[GroupRecall] 删除图片失败 {img_path}: {e}")

            # 更新消息列表
            self.recall_messages[group_id] = valid_messages
            cleaned_count += len(expired_messages)

            # 如果群组没有消息了，删除群组
            if not self.recall_messages[group_id]:
                del self.recall_messages[group_id]

        # 清理DATA_DIR目录下所有不在数据中引用的图片文件（遗留图片）
        orphaned_images_count = 0
        orphaned_images_size = 0
        if os.path.exists(self.DATA_DIR):
            for filename in os.listdir(self.DATA_DIR):
                # 只处理图片文件
                if not filename.lower().endswith(
                    (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
                ):
                    continue

                file_path = os.path.join(self.DATA_DIR, filename)
                if not os.path.isfile(file_path):
                    continue

                # 转换为绝对路径以便比较
                abs_path = os.path.abspath(file_path)

                # 如果文件不在引用列表中，删除它
                if abs_path not in referenced_images:
                    try:
                        file_size = os.path.getsize(file_path)
                        os.remove(file_path)
                        orphaned_images_count += 1
                        orphaned_images_size += file_size
                        _log.debug(f"[GroupRecall] 删除遗留图片: {file_path}")
                    except Exception as e:
                        _log.error(f"[GroupRecall] 删除遗留图片失败 {file_path}: {e}")

        if cleaned_count > 0 or orphaned_images_count > 0:
            total_deleted = deleted_images_count + orphaned_images_count
            total_size_mb = (orphaned_images_size) / (1024 * 1024)
            _log.info(
                f"[GroupRecall] 清理了 {cleaned_count} 条过期消息，删除了 {total_deleted} 张图片"
                f"（过期消息图片: {deleted_images_count}，遗留图片: {orphaned_images_count}），"
                f"释放了 {total_size_mb:.2f} MB 空间"
            )
            self._save_recall_json()

    def _save_message_content(self, message: GroupMessage) -> tuple:
        """保存消息内容，返回文本内容和图片路径列表"""
        text_content = ""
        image_paths = []

        for msg_element in message.message:
            if isinstance(msg_element, PlainText):
                text_content += msg_element.text
            elif isinstance(msg_element, Image):
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
            image_url = getattr(image_element, "url", None) or getattr(
                image_element, "file", None
            )
            if not image_url:
                return None

            import requests

            response = requests.get(image_url, timeout=30)
            if response.status_code != 200:
                return None

            timestamp = int(time.time() * 1000)
            filename = f"recall_image_{timestamp}.jpg"
            filepath = os.path.join(self.DATA_DIR, filename)

            with open(filepath, "wb") as f:
                f.write(response.content)

            return filepath
        except Exception as e:
            _log.error(f"[GroupRecall] 保存图片异常: {e}")

        return None

    def _schedule_query_reply_recall(
        self, message_id: Optional[Union[str, int]]
    ) -> None:
        """查询撤回功能发出的机器人消息，在 QUERY_REPLY_RECALL_SECONDS 秒后自动撤回。"""

        if not message_id:
            return

        delay = self.QUERY_REPLY_RECALL_SECONDS

        async def _delete_later() -> None:
            await asyncio.sleep(delay)
            try:
                # NcatBot5: delete_msg 在 messaging 分组，部分版本的 manage 不提供该方法
                if hasattr(self.api.qq, "messaging") and hasattr(
                    self.api.qq.messaging, "delete_msg"
                ):
                    await self.api.qq.messaging.delete_msg(message_id)
                else:
                    await self.api.qq.delete_msg(message_id)
            except Exception as e:
                _log.warning("[GroupRecall] 查询回复自动撤回失败: %s", e)

        asyncio.create_task(_delete_later())

    def _extract_message_id(
        self, send_result: Optional[Union[str, int, dict, object]]
    ) -> Optional[Union[str, int]]:
        """从发送结果中提取 message_id，兼容 dict / 模型对象 / 原始ID。"""
        if send_result is None:
            return None
        if isinstance(send_result, (str, int)):
            return send_result
        if isinstance(send_result, dict):
            data = send_result.get("data", send_result)
            if isinstance(data, dict):
                return data.get("message_id") or data.get("id")
            return None
        if hasattr(send_result, "model_dump"):
            dumped = send_result.model_dump()
            data = dumped.get("data", dumped)
            if isinstance(data, dict):
                return data.get("message_id") or data.get("id")
        if hasattr(send_result, "message_id"):
            return getattr(send_result, "message_id")
        if hasattr(send_result, "id"):
            return getattr(send_result, "id")
        return None

    async def is_admin_or_owner(self, group_id: int, user_id: int) -> bool:
        """检查用户是否为群主或管理员"""
        try:
            member_info = await self.api.qq.query.get_group_member_info(
                group_id=group_id, user_id=user_id
            )
            # 新API直接返回GroupMemberInfo对象
            if member_info and hasattr(member_info, "role"):
                # onebot协议中role字段的值：owner(群主), admin(管理员), member(普通成员)
                return member_info.role in ["owner", "admin"]
            return False
        except Exception as e:
            _log.error(f"[GroupRecall] 获取用户权限信息失败: {e}")
            return False

    @registrar.qq.on_group_message()
    async def handle_group_message(self, input: GroupMessage) -> None:
        """处理群消息 - 存储消息内容"""
        try:
            # 检查日期是否已经跨天，如果是，则执行清理操作
            current_date = date.today()
            if self.last_cleanup_date is None or current_date != self.last_cleanup_date:
                self._cleanup_expired_messages()
                self.last_cleanup_date = current_date
                _log.info(
                    f"[GroupRecall] 跨天清理完成，清理日期: {self.last_cleanup_date}"
                )

            # 确保 group_id 和 user_id 都是字符串类型
            group_id = str(input.group_id)
            user_id = str(input.sender.user_id)
            nickname = input.sender.nickname or str(user_id)

            # 保存消息内容
            text_content, image_paths = await asyncio.to_thread(
                self._save_message_content, input
            )

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
            self._save_recall_json()

        except Exception as e:
            _log.error(f"[GroupRecall] 处理群消息失败: {e}")

    @registrar.on_notice()
    async def handle_recall_notice(self, input: NoticeEvent) -> None:
        """处理撤回通知"""
        try:
            # 检查是否是撤回通知
            if not hasattr(input, "notice_type") or input.notice_type != "group_recall":
                return

            group_id = str(input.group_id)
            message_id = str(input.message_id)

            # 查找对应的消息并标记为已撤回
            if group_id in self.recall_messages:
                for msg in self.recall_messages[group_id]:
                    if msg.message_id == message_id:
                        # 找到被撤回的消息，标记为已撤回
                        msg.is_recalled = True
                        _log.info(
                            f"[GroupRecall] 检测到消息撤回: 群组 {group_id}, 用户 {msg.user_id}, 消息ID {message_id}"
                        )
                        # 保存数据
                        self._save_recall_json()
                        break

        except Exception as e:
            _log.error(f"[GroupRecall] 处理撤回通知失败: {e}")

    @registrar.qq.on_group_message()
    async def handle_query_recall(self, input: GroupMessage) -> None:
        """处理查询撤回消息命令"""
        try:
            message = input.raw_message.strip()
            if not message.startswith("查询撤回"):
                return

            # 检查权限 - 只有群主或管理员才能查询撤回消息，但用户273421673例外
            user_id_str = str(input.sender.user_id)
            if user_id_str != "273421673" and not await self.is_admin_or_owner(
                input.group_id, input.sender.user_id
            ):
                mid = await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text="只有群主或管理员才能查询撤回消息",
                    reply=input.message_id,
                )
                self._schedule_query_reply_recall(self._extract_message_id(mid))
                return

            # 解析命令参数
            parts = message.split()
            count = 5  # 默认显示5条
            target_user_id = None  # 目标用户ID

            # 解析参数
            for i, part in enumerate(parts[1:], 1):
                if part == "全部":
                    count = None  # 显示全部
                elif part.startswith("@"):
                    # 提取@的用户ID
                    try:
                        # 从消息链中查找@的用户
                        for msg_element in input.message:
                            if isinstance(msg_element, At):
                                target_user_id = str(msg_element.user_id)
                                break
                    except Exception as e:
                        _log.error(f"[GroupRecall] 解析@用户失败: {e}")
                else:
                    try:
                        count = int(part)
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
                mid = await input.reply("该群组暂无撤回消息记录")
                self._schedule_query_reply_recall(self._extract_message_id(mid))
                return

            # 过滤出被撤回的消息
            recalled_messages = [
                msg for msg in self.recall_messages[group_id] if msg.is_recalled
            ]

            # 如果指定了用户，进一步过滤
            if target_user_id:
                recalled_messages = [
                    msg for msg in recalled_messages if msg.user_id == target_user_id
                ]

            # 按时间倒序排列
            messages = sorted(
                recalled_messages, key=lambda x: x.timestamp, reverse=True
            )

            if count is not None:
                messages = messages[:count]

            # 构建回复消息
            if not messages:
                if target_user_id:
                    mid = await input.reply("该用户暂无撤回消息记录")
                else:
                    mid = await input.reply("暂无撤回消息记录")
                self._schedule_query_reply_recall(self._extract_message_id(mid))
                return

            # 构建标题
            if target_user_id:
                # 查找目标用户的昵称
                target_nickname = "未知用户"
                for msg in self.recall_messages[group_id]:
                    if msg.user_id == target_user_id:
                        target_nickname = msg.nickname
                        break
                reply_elements = [
                    PlainText(text=f"=== {target_nickname} 的撤回消息 ===\n")
                ]
            else:
                reply_elements = [PlainText(text="=== 最近撤回消息 ===\n")]

            for i, msg in enumerate(messages, 1):
                # 格式化时间
                msg_time = datetime.fromtimestamp(msg.timestamp).strftime(
                    "%m-%d %H:%M:%S"
                )

                # 添加消息信息
                reply_elements.append(
                    PlainText(text=f"{i}. {msg.nickname} ({msg_time})\n")
                )

                # 添加文本内容
                if msg.content:
                    reply_elements.append(PlainText(text=f"   文本: {msg.content}\n"))

                # 添加图片
                if msg.images:
                    for img_path in msg.images:
                        if os.path.exists(img_path):
                            try:
                                reply_elements.append(Image(file=img_path))
                            except Exception as e:
                                _log.error(f"[GroupRecall] 加载图片失败: {e}")
                                reply_elements.append(
                                    PlainText(text="   [图片加载失败]\n")
                                )

                reply_elements.append(PlainText(text="\n"))

            # 发送回复
            reply_message = MessageChain(reply_elements)
            mid = await self.api.qq.post_group_msg(
                group_id=input.group_id, rtf=reply_message, reply=input.message_id
            )
            self._schedule_query_reply_recall(self._extract_message_id(mid))

        except Exception as e:
            _log.error(f"[GroupRecall] 处理查询命令失败: {e}")
            mid = await input.reply("查询撤回消息失败，请稍后重试")
            self._schedule_query_reply_recall(self._extract_message_id(mid))
