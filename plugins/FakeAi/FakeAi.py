import asyncio
import json
import random
import re
from datetime import datetime
from typing import Dict, List, Optional

import yaml
from common.entity.GroupMember import GroupMember
from common.utils.AiUtil import AiUtil
from ncatbot.core.element import At, MessageChain, Text
from ncatbot.plugin import CompatibleEnrollment, BasePlugin
from ncatbot.utils.logger import get_log
from ncatbot.core.message import GroupMessage

_log = get_log()
bot = CompatibleEnrollment

# 全局变量
trigger_interval = 600  # 每小时最多触发一次（秒）
group_reply_caches: Dict[int, "ReplyCache"] = {}  # 存储每个群的 ReplyCache
last_trigger_times: Dict[int, datetime] = {}  # 存储每个群的上次触发时间
user_trigger_times: Dict[int, datetime] = {}  # 存储每个用户的上次触发时间
enable_group_cd = True  # 群聊冷却开关
enable_user_cd = False  # 用户冷却开关
enable_callback = True  # 回调功能开关
callback_timeout = 30  # 回调超时时间（秒）


# 回调状态管理
class CallbackState:
    def __init__(self):
        self.waiting_users: Dict[
            int, Dict
        ] = {}  # {user_id: {"group_id": group_id, "start_time": datetime}}

    def add_waiting_user(self, user_id: int, group_id: int) -> None:
        self.waiting_users[user_id] = {
            "group_id": group_id,
            "start_time": datetime.now(),
        }

    def remove_waiting_user(self, user_id: int) -> None:
        if user_id in self.waiting_users:
            del self.waiting_users[user_id]

    def is_waiting(self, user_id: int) -> bool:
        return user_id in self.waiting_users

    def get_waiting_info(self, user_id: int) -> Optional[Dict]:
        return self.waiting_users.get(user_id)

    def check_timeout(self, user_id: int) -> bool:
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


class FakeAi(BasePlugin):
    name = "FakeAi"  # 插件名称
    version = "1.0"  # 插件版本

    @bot.group_event()
    async def handle_fake_ai(self, input: GroupMessage) -> None:
        group_id = input.group_id
        sender_id = input.user_id
        sender_name = input.sender.nickname
        content = input.message
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
                    {"name": sender_name, "id": sender_id, "content": content},
                    ensure_ascii=False,
                )
                reply_cache.add_reply(reply_json)
                answer = await answer_ai(group_id, group_reply_caches)
                _log.info(answer)
                await send_typing_response(self, input, answer)
                return

        # 判断是否是功能测试的指令
        if input.raw_message == "蓝晴说话":
            if sender_id in [
                1064163905,
                1141419351,
                1506123340,
                273421673,
            ] or group_id in [719518427, 853963912]:
                answer = await answer_ai(group_id, group_reply_caches)
                _log.info(answer)
                await send_typing_response(self, input, answer)
                return

        if is_at_message:
            # 检查用户CD（除了273421673用户）
            if sender_id != 273421673 and not check_user_cd(sender_id):
                return

            reply_cache = group_reply_caches.setdefault(group_id, ReplyCache())

            # 创建 JSON 格式的字符串并添加到 replyCache 中
            reply_json = json.dumps(
                {"name": sender_name, "id": sender_id, "content": content},
                ensure_ascii=False,
            )
            reply_cache.add_reply(reply_json)
            answer = await answer_ai(group_id, group_reply_caches)
            _log.info(answer)
            await send_typing_response(self, input, answer)

            # 如果启用了回调功能，添加用户到等待列表
            if enable_callback:
                callback_state.add_waiting_user(sender_id, group_id)

            # 更新用户触发时间
            if sender_id != 273421673:
                user_trigger_times[sender_id] = datetime.now()
            return

        # 获取或创建对应群的 ReplyCache
        reply_cache = group_reply_caches.setdefault(group_id, ReplyCache())

        # 创建 JSON 格式的字符串并添加到 replyCache 中
        reply_json = json.dumps(
            {"name": sender_name, "id": sender_id, "content": content},
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
        answer = await answer_ai(group_id, group_reply_caches)
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
        # 保留问号和感叹号，不保留句号和逗号
        keep_punctuation_pattern = r"([！？!?]+)"  # 需要保留的标点
        remove_punctuation_pattern = r"[。，,\.]+"  # 需要移除的标点

        # 先按需要保留的标点分割
        parts = re.split(keep_punctuation_pattern, content)
        sentences = []

        for i in range(0, len(parts), 2):
            if i + 1 < len(parts):
                # 将句子和需要保留的标点组合在一起
                sentence = (parts[i] + parts[i + 1]).strip()
                if sentence:
                    # 保护CQ码中的标点
                    cq_codes = []

                    def save_cq(match):
                        cq_codes.append(match.group(0))
                        return f"__CQ_CODE_{len(cq_codes) - 1}__"

                    # 保存所有CQ码
                    sentence = re.sub(r"\[CQ:[^\]]+\]", save_cq, sentence)

                    # 移除不需要保留的标点
                    sentence = re.sub(remove_punctuation_pattern, "", sentence)

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

                    # 移除不需要保留的标点
                    sentence = re.sub(remove_punctuation_pattern, "", sentence)

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
            content = re.sub(remove_punctuation_pattern, "", content)

            # 恢复CQ码
            for idx, cq_code in enumerate(cq_codes):
                content = content.replace(f"__CQ_CODE_{idx}__", cq_code)

            if content:
                sentences = [content]

        at_pattern = re.compile(r"\[CQ:at,qq=([\w\u4e00-\u9fff]+)]")
        group_id = input.group_id
        members_response = await self.api.get_group_member_list(group_id=group_id)
        members = [GroupMember(member) for member in members_response.get("data", [])]

        # 遍历句子
        for sentence in sentences:
            message = MessageChain([])  # 为每个句子创建新的MessageChain
            last_match_end = 0

            # 处理 CQ 码格式的 @ 消息
            for match in at_pattern.finditer(sentence):
                # 处理 @ 之前的文本
                text_before_at = sentence[last_match_end : match.start()]
                if text_before_at:
                    message.chain.append(Text(text_before_at))

                at_content = match.group(1)
                try:
                    # 尝试解析为 ID
                    user_id = int(at_content)
                except ValueError:
                    # 如果无法解析为 ID，从历史记录中查找对应的 ID
                    user_id = find_user_id_by_name(at_content, input.group_id)

                if user_id and any(member.user_id == user_id for member in members):
                    # 添加 @ 的用户
                    message.chain.append(At(user_id))
                    message.chain.append(Text(" "))

                last_match_end = match.end()

            # 处理 @ 之后的文本
            text_after_last_at = sentence[last_match_end:]
            if text_after_last_at:
                message.chain.append(Text(text_after_last_at))

            # 模拟打字的延时，根据句子的字符数设置延时
            delay = len(sentence) * 0.1  # 每个字符延时 0.1 秒
            await asyncio.sleep(delay)

            # 发送消息
            if message.chain:  # 确保消息链不为空
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

    # CD 冷却完成
    return remaining_time <= 0


def check_user_cd(user_id: int) -> bool:
    """检查用户CD是否冷却完成"""
    if not enable_user_cd:
        return True  # 如果用户冷却被禁用，直接返回True
    last_trigger_time = user_trigger_times.get(user_id)
    if not last_trigger_time:
        return True  # 如果没有记录，则表示冷却完成

    now = datetime.now()
    remaining_time = trigger_interval - (now - last_trigger_time).total_seconds()
    return remaining_time <= 0


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


async def answer_ai(group_id: int, group_reply_caches: Dict[int, ReplyCache]) -> str:
    # 加载 YAML 数据
    yaml_data = load_yaml_data(group_id)
    replace_time_in_system(yaml_data)
    update_yaml_with_replies(yaml_data, group_reply_caches.get(group_id, ReplyCache()))

    # 调用 AIUtil 的 search_deepseek 方法
    keyword = yaml_data.get("input", "")
    prompt = yaml_data.get("system", "")
    response = await AiUtil.search_deepseek(keyword, prompt)

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
                except:
                    pass

    # 如果无法提取有效的 JSON，返回原始响应
    return response
