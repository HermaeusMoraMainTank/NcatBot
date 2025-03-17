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
from ncatbot.plugin.compatible import CompatibleEnrollment
from ncatbot.utils.logger import get_log
from ncatbot.core.message import GroupMessage
from ncatbot.plugin.base_plugin import BasePlugin

_log = get_log()
bot = CompatibleEnrollment

# 全局变量
trigger_interval = 600  # 每小时最多触发一次（秒）
group_reply_caches: Dict[int, "ReplyCache"] = {}  # 存储每个群的 ReplyCache
last_trigger_times: Dict[int, datetime] = {}  # 存储每个群的上次触发时间
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
    def __init__(self, max_size: int = 10):
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
        # 判断是否是功能测试的指令
        if input.raw_message == "蓝晴说话":
            if (
                sender_id in [1064163905, 1141419351, 1506123340, 273421673]
                or group_id == 719518427
            ):
                answer = await answer_ai(group_id, group_reply_caches)
                _log.info(answer)
                await send_typing_response(self, input, answer)
                return

        if "[CQ:at,qq=3555202423]" in input.raw_message:
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


async def send_typing_response(self: FakeAi, input: GroupMessage, answer: str) -> None:
    try:
        replace = json.loads(answer.replace("{{", "{").replace("}}", "}"))
        content = replace.get("content", "")
    except Exception as e:
        _log.error(f"解析 JSON 失败: {e}")
        return

    # 使用正则表达式按照标点符号分割句子
    punctuation_pattern = r"[。！？!?]+"
    sentences = [s.strip() for s in re.split(punctuation_pattern, content) if s.strip()]

    # 如果没有标点符号分割出的句子，就把整个内容作为一个句子
    if not sentences:
        sentences = [content.strip()]

    at_pattern = re.compile(r"\[CQ:at,qq=([\w\u4e00-\u9fff]+)]")
    group_id = input.group_id
    members_response = await self.api.get_group_member_list(group_id=group_id)

    members = [GroupMember(member) for member in members_response.get("data", [])]

    # 遍历句子
    for sentence in sentences:
        message = MessageChain([])  # 为每个句子创建新的MessageChain
        last_match_end = 0
        for match in at_pattern.finditer(sentence):
            # 处理 @ 之前的文本
            text_before_at = sentence[last_match_end : match.start()].strip()
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

            last_match_end = match.end()

        # 处理 @ 之后的文本
        text_after_last_at = sentence[last_match_end:].strip()
        if text_after_last_at:
            message.chain.append(Text(text_after_last_at))

        # 模拟打字的延时，根据句子的字符数设置延时
        delay = len(sentence) * 0.1  # 每个字符延时 0.1 秒
        await asyncio.sleep(delay)

        # 发送消息
        await self.api.post_group_msg(group_id=input.group_id, rtf=message)

    # 将AI的回复加入到reply_cache中
    reply_cache = group_reply_caches.setdefault(group_id, ReplyCache())
    reply_json = json.dumps(
        {"name": "蓝晴", "id": "0", "content": content},
        ensure_ascii=False,
    )
    reply_cache.add_reply(reply_json)


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
    last_trigger_time = last_trigger_times.get(group_id)
    if not last_trigger_time:
        return True  # 如果没有记录，则表示冷却完成

    now = datetime.now()
    remaining_time = trigger_interval - (now - last_trigger_time).total_seconds()

    # CD 冷却完成
    return remaining_time <= 0


def load_yaml_data(group_id) -> Dict:
    # if group_id == 853963912:
    #     with open("ncatpy/data/yml/lanqingv2.yml", "r", encoding="utf-8") as file:
    #         return yaml.safe_load(file)
    with open("data/yml/lanqingv1.yml", "r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def replace_time_in_system(yaml_data: Dict) -> None:
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    system_content = yaml_data.get("system", "")
    if system_content:
        yaml_data["system"] = system_content.replace("{time}", current_time)


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


async def answer_ai(group_id: int, group_reply_caches: Dict[int, ReplyCache]) -> str:
    # 加载 YAML 数据
    yaml_data = load_yaml_data(group_id)
    replace_time_in_system(yaml_data)
    update_yaml_with_replies(yaml_data, group_reply_caches.get(group_id, ReplyCache()))

    # 调用 AIUtil 的 search_deepseek 方法
    keyword = yaml_data.get("input", "")
    prompt = yaml_data.get("system", "")
    return await AiUtil.search_deepseek(keyword, prompt)
