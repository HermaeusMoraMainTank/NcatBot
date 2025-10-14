import asyncio
import json
import random
import re
from datetime import datetime
from typing import Dict, List, Optional

import yaml
from common.utils.AiUtil import AiUtil
from common.utils.CommonUtil import CommonUtil
from ncatbot.core import At, MessageChain, Text
from ncatbot.plugin_system.builtin_mixin.ncatbot_plugin import NcatBotPlugin
from ncatbot.plugin_system.builtin_plugin.unified_registry.filter_system.decorators import (
    group_only,
)
from ncatbot.utils.logger import get_log
from ncatbot.core.message import GroupMessage

_log = get_log()

# 全局变量
trigger_interval = 1200
group_reply_caches: Dict[int, "ReplyCache"] = {}  # 存储每个群的 ReplyCache
last_trigger_times: Dict[int, datetime] = {}  # 存储每个群的上次触发时间
user_trigger_times: Dict[int, datetime] = {}  # 存储每个用户的上次触发时间
enable_group_cd = True  # 群聊冷却开关
enable_user_cd = True  # 用户冷却开关
enable_callback = True  # 回调功能开关
callback_timeout = 15  # 回调超时时间（秒）


# 回调状态管理
class CallbackState:
    def __init__(self):
        self.waiting_users: Dict[
            str, Dict
        ] = {}  # {user_id: {"group_id": group_id, "start_time": datetime}}

    def add_waiting_user(self, user_id: str, group_id: int) -> None:
        self.waiting_users[user_id] = {
            "group_id": group_id,
            "start_time": datetime.now(),
        }

    def remove_waiting_user(self, user_id: str) -> None:
        if user_id in self.waiting_users:
            del self.waiting_users[user_id]

    def is_waiting(self, user_id: str) -> bool:
        return user_id in self.waiting_users

    def get_waiting_info(self, user_id: str) -> Optional[Dict]:
        return self.waiting_users.get(user_id)

    def check_timeout(self, user_id: str) -> bool:
        if user_id not in self.waiting_users:
            return False
        wait_time = (
            datetime.now() - self.waiting_users[user_id]["start_time"]
        ).total_seconds()
        return wait_time > callback_timeout


callback_state = CallbackState()

group_ids = [
    719518427,  # oob
    626192977,  # e7
    700644107,  # 花园猫
    594529103,  # 结束
    817304322,  # 母肥2
    853963912,  # 母肥
    1064163905,  # hmmt
    812078719,  # 高难
]


class ReplyCache:
    def __init__(self, max_size: int = 20):
        self.replies = []
        self.max_size = max_size

    def add_reply(self, reply_json: str) -> None:
        if len(self.replies) >= self.max_size:
            self.replies.pop(0)
        self.replies.append(reply_json)

    def get_replies(self) -> List[str]:
        return self.replies


class GlobalReplyCacheManager:
    _instance = None
    reply_caches: Dict[int, ReplyCache] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(GlobalReplyCacheManager, cls).__new__(cls)
        return cls._instance

    def get_reply_cache(self, group_id: int) -> ReplyCache:
        if group_id not in self.reply_caches:
            self.reply_caches[group_id] = ReplyCache()
        return self.reply_caches[group_id]

    def add_reply(self, group_id: int, reply_json: str) -> None:
        cache = self.get_reply_cache(group_id)
        cache.add_reply(reply_json)

    def get_replies(self, group_id: int) -> List[str]:
        cache = self.get_reply_cache(group_id)
        return cache.get_replies()


class FakeAi(NcatBotPlugin):
    name = "FakeAi"  # 插件名称
    version = "1.0"  # 插件版本

    # 添加排除插件列表
    excluded_plugins = [
        "NetEaseCloudMusic",
        "VrChatInfo",
    ]  # 在这里添加不需要触发FakeAi的插件名称

    async def _is_from_excluded_plugin(self, input: GroupMessage) -> bool:
        """检查消息是否来自排除的插件"""
        # 检查是否是回复消息
        if input.message and len(input.message) > 1:
            for msg in input.message:
                if hasattr(msg, "type") and msg.type == "reply":
                    # 检查被回复的消息是否包含特定文本
                    try:
                        reply_id = msg.data.get("id")
                        msg_info = await self.api.get_msg(reply_id)
                        if msg_info.get("status") == "ok":
                            raw_message = msg_info["data"]["raw_message"]
                            if (
                                "请回复数字选择要播放的歌曲" in raw_message
                                or "请回复数字选择要查看的玩家" in raw_message
                            ):
                                return True
                    except Exception as e:
                        _log.error(f"检查回复消息时发生错误: {str(e)}")
                        continue
        return False

    async def handle_balance_query(self, input: GroupMessage) -> None:
        """处理查询余额命令"""
        try:
            # 调用 AiUtil 查询余额
            balance_data = await AiUtil.get_deepseek_balance()

            if "error" in balance_data:
                # 查询失败，发送错误信息
                error_msg = Text(f"查询余额失败: {balance_data['error']}")
                await self.api.post_group_msg(
                    group_id=input.group_id, rtf=MessageChain([error_msg])
                )
                return

            # 解析余额信息
            is_available = balance_data.get("is_available", False)
            balance_infos = balance_data.get("balance_infos", [])

            # 构建回复消息
            message_parts = [Text("💰 DeepSeek API 余额查询结果:\n")]

            if is_available:
                message_parts.append(Text("✅ 账户状态: 可用\n"))
            else:
                message_parts.append(Text("❌ 账户状态: 不可用\n"))

            if balance_infos:
                for balance_info in balance_infos:
                    currency = balance_info.get("currency", "未知")
                    total_balance = balance_info.get("total_balance", "0")
                    granted_balance = balance_info.get("granted_balance", "0")
                    topped_up_balance = balance_info.get("topped_up_balance", "0")

                    currency_name = (
                        "人民币"
                        if currency == "CNY"
                        else "美元"
                        if currency == "USD"
                        else currency
                    )

                    message_parts.append(Text(f"💵 货币: {currency_name}\n"))
                    message_parts.append(Text(f"💎 总余额: {total_balance}\n"))
                    message_parts.append(Text(f"🎁 赠金余额: {granted_balance}\n"))
                    message_parts.append(Text(f"💳 充值余额: {topped_up_balance}\n"))
            else:
                message_parts.append(Text("❌ 未获取到余额信息"))

            # 发送消息
            message = MessageChain(message_parts)
            await self.api.post_group_msg(group_id=input.group_id, rtf=message)

        except Exception as e:
            _log.error(f"处理余额查询时发生错误: {e}")
            error_msg = Text(f"查询余额时发生错误: {str(e)}")
            await self.api.post_group_msg(
                group_id=input.group_id, rtf=MessageChain([error_msg])
            )

    @group_only
    async def handle_fake_ai(self, input: GroupMessage) -> None:
        # 检查消息是否来自排除的插件
        if await self._is_from_excluded_plugin(input):
            return

        group_id = input.group_id
        sender_id = input.sender.user_id
        sender_name = input.sender.nickname

        # 将 MessageArray 转换为可序列化的格式
        content = []
        for msg_segment in input.message:
            if hasattr(msg_segment, "text"):
                content.append({"type": "text", "data": {"text": msg_segment.text}})
            elif (
                hasattr(msg_segment, "msg_seg_type")
                and msg_segment.msg_seg_type == "at"
            ):
                content.append({"type": "at", "data": {"qq": msg_segment.qq}})
            elif (
                hasattr(msg_segment, "msg_seg_type")
                and msg_segment.msg_seg_type == "image"
            ):
                content.append(
                    {
                        "type": "image",
                        "data": {
                            "url": getattr(msg_segment, "url", ""),
                            "file": getattr(msg_segment, "file", ""),
                            "summary": getattr(msg_segment, "summary", ""),
                        },
                    }
                )
            else:
                # 尝试处理其他类型的消息
                if (
                    hasattr(msg_segment, "data")
                    and "image" in str(msg_segment.data).lower()
                ):
                    content.append({"type": "image", "data": {"raw": str(msg_segment)}})

        is_at_message = "[CQ:at,qq=3555202423]" in input.raw_message

        # 检查是否是等待回调的用户
        if enable_callback and callback_state.is_waiting(sender_id):
            # 检查是否超时
            if callback_state.check_timeout(sender_id):
                callback_state.remove_waiting_user(sender_id)
                return

            # 获取等待信息
            wait_info = callback_state.get_waiting_info(sender_id)
            if wait_info and wait_info["group_id"] == group_id:
                # 如果是艾特消息，不触发回调（避免连续艾特导致的重复触发）
                if is_at_message:
                    callback_state.remove_waiting_user(sender_id)
                    return

                # 移除等待状态
                callback_state.remove_waiting_user(sender_id)
                # 直接回复，不检查CD
                reply_cache = group_reply_caches.setdefault(group_id, ReplyCache())
                reply_json = json.dumps(
                    {
                        "name": sender_name,
                        "id": sender_id,
                        "content": content,
                        "group_nickname": input.sender.card or sender_name,
                    },
                    ensure_ascii=False,
                )
                reply_cache.add_reply(reply_json)
                answer = await answer_ai(
                    group_id, group_reply_caches, str(sender_id), "callback"
                )
                _log.info(answer)
                await send_typing_response(self, input, answer)
                return

        # 判断是否是功能测试的指令
        if input.raw_message == "蓝晴说话":
            if sender_id in [
                "273421673",
            ] or group_id in [719518427, 853963912]:
                answer = await answer_ai(
                    group_id, group_reply_caches, str(sender_id), "test"
                )
                _log.info(answer)
                await send_typing_response(self, input, answer)
                return

        # 处理查询余额命令
        if input.raw_message == "查询余额":
            await self.handle_balance_query(input)
            return

        if is_at_message:
            # 检查用户CD（除了273421673用户）
            if sender_id != "273421673" and not check_user_cd(sender_id):
                return

            reply_cache = group_reply_caches.setdefault(group_id, ReplyCache())

            # 创建 JSON 格式的字符串并添加到 replyCache 中
            reply_json = json.dumps(
                {
                    "name": sender_name,
                    "id": sender_id,
                    "content": content,
                    "group_nickname": input.sender.card or sender_name,
                },
                ensure_ascii=False,
            )
            reply_cache.add_reply(reply_json)
            answer = await answer_ai(
                group_id, group_reply_caches, str(sender_id), "active"
            )
            _log.info(answer)
            await send_typing_response(self, input, answer)

            # 如果启用了回调功能，添加用户到等待列表
            if enable_callback:
                callback_state.add_waiting_user(sender_id, group_id)

            # 更新用户触发时间
            if sender_id != "273421673":
                user_trigger_times[sender_id] = datetime.now()
            return

        # 获取或创建对应群的 ReplyCache
        reply_cache = group_reply_caches.setdefault(group_id, ReplyCache())

        # 创建 JSON 格式的字符串并添加到 replyCache 中
        reply_json = json.dumps(
            {
                "name": sender_name,
                "id": sender_id,
                "content": content,
                "group_nickname": input.sender.card or sender_name,
            },
            ensure_ascii=False,
        )
        reply_cache.add_reply(reply_json)

        # 检查冷却时间
        if not check_cd(group_id):
            return

        # 随机触发逻辑
        if random.random() > 0.01:
            return

        # 记录本次触发的时间
        last_trigger_times[group_id] = datetime.now()
        answer = await answer_ai(
            group_id, group_reply_caches, str(sender_id), "passive"
        )
        _log.info(answer)
        reply_cache.add_reply(answer)
        if not answer or answer.strip() == "" or answer == '""':
            return
        await send_typing_response(self, input, answer)

        # 如果启用了回调功能，添加用户到等待列表
        if enable_callback:
            callback_state.add_waiting_user(sender_id, group_id)


async def send_typing_response(self: FakeAi, input: GroupMessage, answer: str) -> None:
    try:
        # 尝试解析 JSON
        try:
            replace = json.loads(answer.replace("{{", "{").replace("}}", "}"))
            content = replace.get("content", "")
        except json.JSONDecodeError:
            # 如果不是 JSON 格式，直接使用原始内容
            content = answer

        # 使用正则表达式分割句子
        # 先用句号、问号、感叹号分割，将逗号替换为空格，然后去掉句号
        sentence_split_pattern = r"([。！？!?]+)"  # 用于分割句子的标点
        comma_replace_pattern = r"[，]+"  # 需要替换为空格的中文逗号
        period_remove_pattern = r"[。]+"  # 需要移除的句号

        # 先按句号、问号、感叹号分割
        parts = re.split(sentence_split_pattern, content)
        sentences = []

        for i in range(0, len(parts), 2):
            if i + 1 < len(parts):
                # 将句子和标点组合在一起
                sentence = (parts[i] + parts[i + 1]).strip()
                if sentence:
                    # 保护CQ码中的标点
                    cq_codes = []

                    def save_cq(match):
                        cq_codes.append(match.group(0))
                        return f"__CQ_CODE_{len(cq_codes) - 1}__"

                    # 保存所有CQ码
                    sentence = re.sub(r"\[CQ:[^\]]+\]", save_cq, sentence)

                    # 将中文逗号替换为空格
                    sentence = re.sub(comma_replace_pattern, " ", sentence)
                    # 将多个连续空格替换为单个空格
                    sentence = re.sub(r"\s+", " ", sentence)

                    # 移除句号，但保留问号和感叹号
                    sentence = re.sub(period_remove_pattern, "", sentence)

                    # 恢复CQ码
                    for idx, cq_code in enumerate(cq_codes):
                        sentence = sentence.replace(f"__CQ_CODE_{idx}__", cq_code)

                    if sentence:
                        sentences.append(sentence)
            else:
                # 处理最后一个部分
                if parts[i].strip():
                    # 保护CQ码中的标点
                    cq_codes = []

                    def save_cq(match):
                        cq_codes.append(match.group(0))
                        return f"__CQ_CODE_{len(cq_codes) - 1}__"

                    # 保存所有CQ码
                    sentence = re.sub(r"\[CQ:[^\]]+\]", save_cq, parts[i].strip())

                    # 将中文逗号替换为空格
                    sentence = re.sub(comma_replace_pattern, " ", sentence)
                    # 将多个连续空格替换为单个空格
                    sentence = re.sub(r"\s+", " ", sentence)

                    # 移除句号，但保留问号和感叹号
                    sentence = re.sub(period_remove_pattern, "", sentence)

                    # 恢复CQ码
                    for idx, cq_code in enumerate(cq_codes):
                        sentence = sentence.replace(f"__CQ_CODE_{idx}__", cq_code)

                    if sentence:
                        sentences.append(sentence)

        # 如果没有分割出的句子，就把整个内容作为一个句子
        if not sentences:
            # 保护CQ码中的标点
            cq_codes = []

            def save_cq(match):
                cq_codes.append(match.group(0))
                return f"__CQ_CODE_{len(cq_codes) - 1}__"

            # 保存所有CQ码
            content = re.sub(r"\[CQ:[^\]]+\]", save_cq, content.strip())

            # 移除不需要保留的标点
            content = re.sub(comma_replace_pattern, " ", content)
            # 将多个连续空格替换为单个空格
            content = re.sub(r"\s+", " ", content)
            content = re.sub(period_remove_pattern, "", content)

            # 恢复CQ码
            for idx, cq_code in enumerate(cq_codes):
                content = content.replace(f"__CQ_CODE_{idx}__", cq_code)

            if content:
                sentences = [content]

        at_pattern = re.compile(r"\[CQ:at,qq=([\w\u4e00-\u9fff]+)]")
        group_id = input.group_id
        members_response = await self.api.get_group_member_list(group_id=group_id)
        members = CommonUtil.parse_group_member_list(members_response)

        # 遍历句子
        for sentence in sentences:
            message_elements = []  # 为每个句子创建新的消息元素列表
            last_match_end = 0

            # 处理 CQ 码格式的 @ 消息
            for match in at_pattern.finditer(sentence):
                # 处理 @ 之前的文本
                text_before_at = sentence[last_match_end : match.start()].strip()
                if text_before_at:
                    message_elements.append(Text(text_before_at))

                at_content = match.group(1)
                try:
                    # 尝试解析为 ID
                    user_id = int(at_content)
                except ValueError:
                    # 如果无法解析为 ID，从历史记录中查找对应的 ID
                    user_id = find_user_id_by_name(at_content, input.group_id)

                if user_id and any(
                    member.user_id == str(user_id) for member in members
                ):
                    # 添加 @ 的用户
                    message_elements.append(At(user_id))
                    message_elements.append(Text(" "))

                last_match_end = match.end()

            # 处理 @ 之后的文本
            text_after_last_at = sentence[last_match_end:].strip()
            if text_after_last_at:
                message_elements.append(Text(text_after_last_at))

            # 模拟打字的延时，根据句子的字符数设置延时
            delay = len(sentence) * 0.1  # 每个字符延时 0.1 秒
            await asyncio.sleep(delay)

            # 发送消息
            if message_elements:  # 确保消息链不为空
                message = MessageChain(message_elements)
                await self.api.post_group_msg(group_id=input.group_id, rtf=message)

        # 将AI的回复加入到reply_cache中
        reply_cache = group_reply_caches.setdefault(group_id, ReplyCache())
        reply_json = json.dumps(
            {"name": "蓝晴", "id": "0", "content": content},
            ensure_ascii=False,
        )
        reply_cache.add_reply(reply_json)

    except Exception as e:
        _log.error(f"发送消息时发生错误: {e}")
        return


def find_user_id_by_name(name: str, group_id: int) -> Optional[int]:
    # 获取对应群的历史记录
    reply_cache = group_reply_caches.get(group_id)
    if not reply_cache:
        return None

    # 遍历历史记录，查找匹配的用户名和 ID
    for reply in reply_cache.get_replies():
        try:
            reply_json = json.loads(reply)
            if name in reply_json.get("name", ""):
                return reply_json.get("id")
        except json.JSONDecodeError:
            continue

    return None  # 未找到匹配的用户


def check_cd(group_id: int) -> bool:
    if not enable_group_cd:
        return True  # 如果群聊冷却被禁用，直接返回True
    last_trigger_time = last_trigger_times.get(group_id)
    if not last_trigger_time:
        return True  # 如果没有记录，则表示冷却完成

    now = datetime.now()
    remaining_time = trigger_interval - (now - last_trigger_time).total_seconds()
    _log.info(f"群CD检查: {group_id}, 剩余时间: {remaining_time:.2f}秒")

    # CD 冷却完成
    return remaining_time <= 0


def check_user_cd(user_id: str) -> bool:
    """检查用户CD是否冷却完成"""
    if not enable_user_cd:
        _log.info(f"用户CD被禁用，直接通过: {user_id}")
        return True  # 如果用户冷却被禁用，直接返回True
    last_trigger_time = user_trigger_times.get(user_id)
    if not last_trigger_time:
        _log.info(f"用户首次触发，CD通过: {user_id}")
        return True  # 如果没有记录，则表示冷却完成

    now = datetime.now()
    remaining_time = trigger_interval - (now - last_trigger_time).total_seconds()
    _log.info(f"用户CD检查: {user_id}, 剩余时间: {remaining_time:.2f}秒")
    return remaining_time <= 0


def record_ai_usage_to_json(
    group_id: str, user_id: str, tokens: int = 0, trigger_type: str = "active"
):
    """直接保存AI使用统计数据到JSON文件"""
    import json
    import os
    from datetime import datetime

    # 数据文件路径
    GROUP_DATA_FILE = "data/json/ai_group_stats.json"
    USER_DATA_FILE = "data/json/ai_user_stats.json"

    now = datetime.now()
    today = now.date().isoformat()

    # 保存群组统计
    try:
        # 确保目录存在
        os.makedirs(os.path.dirname(GROUP_DATA_FILE), exist_ok=True)

        # 读取现有数据
        group_data = {"group_stats": {}}
        if os.path.exists(GROUP_DATA_FILE):
            try:
                with open(GROUP_DATA_FILE, "r", encoding="utf-8") as f:
                    group_data = json.load(f)
            except Exception:
                pass

        # 确保有 group_stats 字段
        if "group_stats" not in group_data:
            group_data["group_stats"] = {}

        # 更新群组统计
        if group_id not in group_data["group_stats"]:
            group_data["group_stats"][group_id] = {
                "daily_counts": {},
                "daily_tokens": {},
                "last_used": None,
                "total_count": 0,
                "total_tokens": 0,
            }

        stats = group_data["group_stats"][group_id]
        if today not in stats["daily_counts"]:
            stats["daily_counts"][today] = 0
        if today not in stats["daily_tokens"]:
            stats["daily_tokens"][today] = 0

        stats["daily_counts"][today] += 1
        stats["daily_tokens"][today] += tokens
        stats["total_count"] += 1
        stats["total_tokens"] += tokens
        stats["last_used"] = now.isoformat()

        # 保存群组数据
        with open(GROUP_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(group_data, f, ensure_ascii=False, indent=2)

    except Exception as e:
        print(f"保存群组AI统计数据失败: {e}")

    # 保存用户统计
    try:
        # 确保目录存在
        os.makedirs(os.path.dirname(USER_DATA_FILE), exist_ok=True)

        # 读取现有数据
        user_data = {"user_stats": {}}
        if os.path.exists(USER_DATA_FILE):
            try:
                with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
                    user_data = json.load(f)
            except Exception:
                pass

        # 确保有 user_stats 字段
        if "user_stats" not in user_data:
            user_data["user_stats"] = {}
        if group_id not in user_data["user_stats"]:
            user_data["user_stats"][group_id] = {}

        # 更新用户统计
        if user_id not in user_data["user_stats"][group_id]:
            user_data["user_stats"][group_id][user_id] = {
                "daily_counts": {},
                "daily_tokens": {},
                "last_used": None,
                "total_count": 0,
                "total_tokens": 0,
            }

        user_stats = user_data["user_stats"][group_id][user_id]
        if today not in user_stats["daily_counts"]:
            user_stats["daily_counts"][today] = 0
        if today not in user_stats["daily_tokens"]:
            user_stats["daily_tokens"][today] = 0

        user_stats["daily_counts"][today] += 1
        user_stats["daily_tokens"][today] += tokens
        user_stats["total_count"] += 1
        user_stats["total_tokens"] += tokens
        user_stats["last_used"] = now.isoformat()

        # 保存用户数据
        with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(user_data, f, ensure_ascii=False, indent=2)

    except Exception as e:
        print(f"保存用户AI统计数据失败: {e}")


def load_yaml_data(group_id) -> Dict:
    # if group_id == 719518427:
    #     with open("data/yml/lanqingv1_ai.yml", "r", encoding="utf-8") as file:
    #         return yaml.safe_load(file)
    with open("data/yml/lanqingv1.yml", "r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def replace_time_in_system(yaml_data: Dict) -> None:
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    system_content = yaml_data.get("system", "")
    input_content = yaml_data.get("input", "")
    if system_content:
        yaml_data["system"] = system_content.replace("{time}", current_time)
    if input_content:
        yaml_data["input"] = input_content.replace("{time}", current_time)


def update_yaml_with_replies(yaml_data: Dict, reply_cache: ReplyCache) -> Dict:
    replies = reply_cache.get_replies()
    if replies:
        last_reply = replies[-1]
        new_history = "\n".join(replies)
        replace_placeholder(yaml_data, "{history_new}", new_history)
        replace_placeholder(yaml_data, "{history_last}", last_reply)
    return yaml_data


def replace_placeholder(data: Dict, placeholder: str, new_value: str) -> None:
    for key, value in data.items():
        if isinstance(value, str):
            if placeholder in value:
                data[key] = value.replace(placeholder, new_value)
        elif isinstance(value, dict):
            replace_placeholder(value, placeholder, new_value)


def remove_thinking_process(response: str) -> str:
    """移除回复中的思考过程和注释部分"""
    if not response:
        return response

    # 移除以 // 开头的行
    lines = response.split("\n")
    filtered_lines = [line for line in lines if not line.strip().startswith("//")]

    # 检查是否有"思考过程："这样的标记
    thinking_markers = ["思考过程：", "思考过程:", "// 思考过程", "思考：", "思考:"]
    for marker in thinking_markers:
        if marker in response:
            # 找到标记的位置
            marker_index = response.find(marker)
            # 截取标记之前的内容
            before_marker = response[:marker_index].strip()
            return before_marker

    return "\n".join(filtered_lines)


async def answer_ai(
    group_id: int,
    group_reply_caches: Dict[int, ReplyCache],
    user_id: str = None,
    trigger_type: str = "active",
) -> str:
    # 加载 YAML 数据
    yaml_data = load_yaml_data(group_id)
    replace_time_in_system(yaml_data)
    update_yaml_with_replies(yaml_data, group_reply_caches.get(group_id, ReplyCache()))

    # 调用 AIUtil 的 search_deepseek 方法
    keyword = yaml_data.get("input", "")
    prompt = yaml_data.get("system", "")
    ai_response = await AiUtil.search_deepseek(keyword, prompt)

    # 处理AI响应
    if isinstance(ai_response, dict):
        response = ai_response.get("content", "")
        usage_info = ai_response.get("usage", {})
        tokens = usage_info.get("total_tokens", 0)
    else:
        response = ai_response or ""
        tokens = 0

    # 记录AI使用统计
    if user_id:
        try:
            # 直接保存AI使用统计数据到JSON文件
            record_ai_usage_to_json(str(group_id), str(user_id), tokens, trigger_type)
        except Exception as e:
            _log.error(f"记录AI使用统计失败: {e}")

    # 首先移除思考过程
    response = remove_thinking_process(response)

    # 过滤掉思考过程（以 // 开头的内容）
    if response:
        # 先尝试直接解析为 JSON
        try:
            import json

            # 检查是否是有效的 JSON 字符串
            parsed = json.loads(response)
            if isinstance(parsed, dict) and all(
                k in parsed for k in ["name", "id", "content"]
            ):
                # 已经是干净的 JSON 格式，直接返回原始响应
                return response
        except json.JSONDecodeError:
            pass

        # 使用正则表达式提取 JSON 部分
        import re

        # 匹配标准的 JSON 格式，处理内容中可能有转义引号的情况
        json_pattern = r'(\{"name":"[^"]+","id":"[^"]+","content":"(?:[^"\\]|\\.)*"\})'
        match = re.search(json_pattern, response)
        if match:
            # 只返回匹配到的 JSON 部分
            return match.group(1)

        # 如果上面的匹配失败，尝试一种更宽松的模式
        json_start = response.find('{"name":"')
        if json_start != -1:
            # 找到 JSON 开始的位置，然后尝试找最后的 }
            json_end = response.find("}", json_start)
            if json_end != -1:
                # 提取可能的 JSON 部分
                possible_json = response[json_start : json_end + 1]
                # 验证提取的内容是否是有效的 JSON
                try:
                    parsed = json.loads(possible_json)
                    if isinstance(parsed, dict) and all(
                        k in parsed for k in ["name", "id", "content"]
                    ):
                        return possible_json
                except Exception:
                    pass

    # 如果无法提取有效的 JSON，返回原始响应
    return response
