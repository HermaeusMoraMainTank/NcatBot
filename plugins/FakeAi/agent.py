"""FakeAi 工具型 agent：模型自己决定发消息、发表情、拍一拍、搜索或沉默。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from common.utils.AiUtil import AiUtil

from .memory import memory_manager

_log = logging.getLogger(__name__)


@dataclass
class AgentAction:
    kind: str  # text | sticker | poke
    messages: list[str] = field(default_factory=list)
    sticker: Optional[str] = None
    user_id: Optional[str] = None
    at_user: Optional[str] = None
    quote: bool = False
    reply_to: Optional[str] = None
    message_id: Optional[str] = None
    emoji_id: Optional[str] = None


@dataclass
class AgentResult:
    actions: list[AgentAction] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    model: str = "deepseek-flash"
    finished: bool = False


AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": "在群里发言。messages 传字符串=一条；传数组=分多条发送。普通对话一条，最多 2~3 条，单条别写长。",
            "parameters": {
                "type": "object",
                "properties": {
                    "messages": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                        "description": "要发送的内容：字符串=一条；数组=分多条",
                    },
                    "atUserId": {
                        "type": "string",
                        "description": "要 @ 的群成员 QQ 号（可选，别滥用）",
                    },
                    "quote": {
                        "type": "boolean",
                        "description": "是否引用当前触发消息（可选）",
                    },
                    "replyToMessageId": {
                        "type": "string",
                        "description": "要引用的旧消息 #id（可选，先用 get_recent_messages 查）",
                    },
                },
                "required": ["messages"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_sticker",
            "description": "单独发一张本地收藏表情。name 必须是可用贴纸名。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "贴纸名"}
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_meme",
            "description": (
                "从梗鲸图库（网络梗图、蓝色大肥鱼、AI 娘梗图）按话题挑一张发出。"
                "想发本地贴纸以外的图时用；topic 填你此刻的情绪或话题关键词。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "情绪/话题关键词，例如 笑死、无语、晚安、破防",
                    },
                    "character": {
                        "type": "string",
                        "description": "角色筛选，可空",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_poke",
            "description": "戳一戳群友。不传 userId 就戳当前说话的人。",
            "parameters": {
                "type": "object",
                "properties": {
                    "userId": {"type": "string", "description": "目标 QQ 号，可选"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "联网搜索实时信息、新闻、新梗或不确定的事实。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "抓取网页正文 Markdown，用于阅读搜索结果或群友发的链接。",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_append",
            "description": "记录对某位群友的长期印象（风格、梗、雷点等稳定信息），不要记临时话题。",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "react",
            "description": "给消息点一个 QQ 表情赞，默认当前消息。不想说话但想表示看到了时用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "messageId": {
                        "type": "string",
                        "description": "消息 #id，可选，默认当前消息",
                    },
                    "emojiId": {
                        "type": "string",
                        "description": "QQ 表情 ID，默认 289（赞）",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_messages",
            "description": "查看最近消息（带 #id、发言人、是否含图片）。想引用旧消息或补上下文时用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "条数，默认 10，最多 20",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_message_images",
            "description": "查看某条旧消息里的图片内容。需要消息 #id（先用 get_recent_messages 查）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "messageId": {"type": "string", "description": "消息 #id"}
                },
                "required": ["messageId"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_query",
            "description": "检索长期记忆或对某人的印象。需要回忆以前的事、某人的风格/雷点时用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索词，可为空"},
                    "userId": {"type": "string", "description": "目标 QQ 号，可选"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "结束本次处理。看完不想说话，或说完想收尾时调用。",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
            },
        },
    },
]


def _extract_text(content: str) -> str:
    """兼容模型忽略工具协议、直接输出正文/JSON 的情况。"""
    text = (content or "").strip()
    if not text:
        return ""
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            value = obj.get("content")
            if isinstance(value, str):
                text = value.strip()
    except json.JSONDecodeError:
        pass
    text = _clean_qq_text(text)
    if len(text) > 200:
        return ""
    return text


def _clean_qq_text(text: str) -> str:
    """去掉 Markdown 和不可见字符，QQ 里看起来像正常打字。"""
    s = str(text or "").replace("\u200b", "").strip()
    s = re.sub(r"```[a-zA-Z0-9_+-]*\n?", "", s)
    s = s.replace("`", "")
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"^\s{0,3}#{1,6}\s*", "", s, flags=re.M)
    s = re.sub(r"\*\*|__", "", s)
    s = re.sub(r"^\s*[-*+]\s+", "", s, flags=re.M)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _entry_text(content) -> str:
    if isinstance(content, str):
        return content[:120]
    if isinstance(content, list):
        parts = []
        for seg in content:
            if not isinstance(seg, dict):
                continue
            if seg.get("type") == "text":
                parts.append(str((seg.get("data") or {}).get("text") or ""))
            elif seg.get("type") == "image":
                parts.append("[图片]")
        return " ".join(p for p in parts if p)[:120]
    return str(content or "")[:120]


async def _execute_tool(
    name: str,
    args: dict,
    *,
    actions: list[AgentAction],
    sticker_catalog,
    user_id: Optional[str],
    group_id: int,
    history_entries: Optional[list] = None,
) -> str:
    if name == "send_message":
        raw = args.get("messages")
        if isinstance(raw, str):
            messages = [raw]
        elif isinstance(raw, list):
            messages = [str(x) for x in raw]
        else:
            return "参数错误：messages 必须是字符串或字符串数组"
        messages = [_clean_qq_text(m) for m in messages]
        messages = [m for m in messages if m][:3]
        if not messages:
            return "消息为空，没有发送"
        existing = sum(
            len(a.messages) for a in actions if a.kind == "text"
        )
        if existing + len(messages) > 4:
            return "本轮已经说得够多了，不要再发，直接结束。"
        at_user = args.get("atUserId")
        reply_to = args.get("replyToMessageId")
        actions.append(
            AgentAction(
                kind="text",
                messages=messages,
                at_user=str(at_user) if at_user else None,
                quote=bool(args.get("quote")),
                reply_to=str(reply_to) if reply_to else None,
            )
        )
        return f"已加入发送队列：{len(messages)} 条。不要重复发送，也不要汇报。"

    if name == "send_sticker":
        from . import expression as expr_pkg

        if sum(1 for a in actions if a.kind == "sticker") >= max(
            1, int(expr_pkg.MAX_STICKERS_PER_REPLY)
        ):
            return "本轮表情已经够了，不要再发。"
        sticker_name = str(args.get("name") or "").strip()
        path = sticker_catalog.pick(sticker_name) if sticker_catalog else None
        if path is None:
            names = sticker_catalog.names()[:30] if sticker_catalog else []
            if not names:
                return "本地贴纸已关闭，请改用 send_meme(topic) 发网络梗图。"
            return f"没有这个贴纸：{sticker_name}。可用：{'、'.join(names)}"
        actions.append(AgentAction(kind="sticker", sticker=str(path)))
        return "表情已加入发送队列。"

    if name == "send_meme":
        from . import expression as expr_pkg

        if sum(1 for a in actions if a.kind == "sticker") >= max(
            1, int(expr_pkg.MAX_STICKERS_PER_REPLY)
        ):
            return "本轮表情已经够了，不要再发。"
        topic = str(args.get("topic") or "").strip()
        character = str(args.get("character") or "").strip()
        try:
            from .expression import whale_catalog

            path = await whale_catalog.fetch(topic, character)
        except Exception as e:
            return f"梗鲸图库不可用：{type(e).__name__}: {e}"
        if path is None:
            return "没找到合适的梗图，换张本地贴纸或直接说话。"
        actions.append(AgentAction(kind="sticker", sticker=str(path)))
        return "梗图已加入发送队列。"

    if name == "send_poke":
        if any(a.kind == "poke" for a in actions):
            return "本轮已经拍过了，不要再拍。"
        target = args.get("userId") or user_id
        if not target:
            return "缺少 userId，无法拍一拍"
        actions.append(AgentAction(kind="poke", user_id=str(target)))
        return "拍一拍已加入队列。"

    if name == "react":
        mid = args.get("messageId")
        emoji_id = str(args.get("emojiId") or "289")
        actions.append(
            AgentAction(
                kind="react",
                message_id=str(mid) if mid else None,
                emoji_id=emoji_id,
            )
        )
        return "点赞已加入队列。"

    if name == "get_recent_messages":
        limit = max(1, min(20, int(args.get("limit", 10) or 10)))
        lines = []
        for entry in (history_entries or [])[-limit:]:
            mid = entry.get("message_id")
            if mid is None:
                continue
            if str(entry.get("id", "")) == "0":
                who = "你（蓝晴）"
            else:
                who = (
                    entry.get("group_nickname")
                    or entry.get("name")
                    or entry.get("id")
                    or "?"
                )
            text = _entry_text(entry.get("content"))
            has_img = (
                " [图片]"
                if entry.get("image_urls") and "[图片]" not in text
                else ""
            )
            lines.append(f"#{mid} {who}: {text}{has_img}")
        return "\n".join(lines) or "暂无历史消息"

    if name == "get_message_images":
        mid = str(args.get("messageId") or "")
        entry = next(
            (
                e
                for e in (history_entries or [])
                if str(e.get("message_id")) == mid
            ),
            None,
        )
        if entry is None:
            return f"找不到消息 {mid}，先用 get_recent_messages 查 #id"
        urls = entry.get("image_urls") or []
        if not urls:
            return "这条消息没有可查看的图片"
        try:
            b64s = await AiUtil.download_images_as_base64(urls[:2])
        except Exception as e:
            return f"图片下载失败：{type(e).__name__}: {e}"
        if not b64s:
            return "图片下载失败"
        descriptions = []
        for b64 in b64s:
            result = await AiUtil.chat_with_vision(
                "简洁描述这张图的内容，如果有文字请转录。",
                image_base64_list=[b64],
                max_tokens=220,
            )
            if result and result.get("content"):
                descriptions.append(result["content"].strip())
        return "\n---\n".join(descriptions) or "图片识别失败"

    if name == "web_search":
        query = str(args.get("query") or "").strip()
        if not query:
            return "搜索词为空"
        data = await AiUtil.search_web(query, count=5)
        items = [
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "summary": item.get("summary"),
                "date": item.get("date"),
            }
            for item in (data.get("results") or [])
        ]
        return json.dumps(items, ensure_ascii=False)

    if name == "web_fetch":
        url = str(args.get("url") or "").strip()
        if not url:
            return "URL 为空"
        data = await AiUtil.fetch_web(url)
        return f"{data.get('title', '')}\n\n{(data.get('content') or '')[:6000]}"

    if name == "memory_query":
        query = str(args.get("query") or "").strip()
        target = args.get("userId") or user_id
        uid = int(target) if target and str(target).isdigit() else None
        try:
            data = await memory_manager.recall(
                query, limit=5, user_id=uid, group_id=group_id
            )
            items = [
                {
                    "judgment": item.get("judgment"),
                    "tags": item.get("tags"),
                    "scope": item.get("scope"),
                }
                for item in (data.get("items") or [])
            ]
            return json.dumps(items, ensure_ascii=False)
        except Exception as e:
            return f"记忆检索失败：{type(e).__name__}: {e}"

    if name == "memory_append":
        text = str(args.get("text") or "").strip()
        if not text:
            return "记忆内容为空"
        try:
            result = await memory_manager.remember(
                text,
                reasoning="agent tool",
                tags=["agent"],
                strength=60.0,
                memory_type="preference",
                scope=f"user:{user_id}" if user_id else "public",
                source_user_id=int(user_id) if user_id and str(user_id).isdigit() else None,
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return f"记忆写入失败：{type(e).__name__}: {e}"

    return f"未知工具：{name}"


async def run_agent(
    system_prompt: str,
    user_prompt: str,
    *,
    group_id: int,
    user_id: Optional[str],
    sticker_catalog,
    image_base64_list: Optional[list] = None,
    history_entries: Optional[list] = None,
    max_rounds: int = 12,
    force_speak: bool = False,
    max_total_tokens: int = 60000,
    skill_bridge=None,
) -> AgentResult:
    """运行工具循环，返回要执行的动作和聚合后的用量。"""
    user_content = user_prompt
    if image_base64_list:
        content_parts = [{"type": "text", "text": user_prompt}]
        for img_b64 in image_base64_list[:3]:
            if not img_b64.startswith("data:"):
                img_b64 = f"data:image/jpeg;base64,{img_b64}"
            content_parts.append(
                {"type": "image_url", "image_url": {"url": img_b64}}
            )
        user_content = content_parts

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    actions: list[AgentAction] = []
    usage_total = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 0,
    }
    model = "deepseek-flash"
    finished = False
    bridge_tools = skill_bridge.tools if skill_bridge is not None else []
    bridge_names = {t["function"]["name"] for t in bridge_tools}
    tools = list(AGENT_TOOLS) + bridge_tools
    if force_speak:
        # 强制开口时拿掉 finish，模型就没法用「安静结束」交差
        tools = [t for t in tools if t["function"]["name"] != "finish"]

    for _ in range(max_rounds):
        res = await AiUtil.chat_completion(
            messages,
            tools=tools,
            capability="chat",
            max_tokens=1200,
            temperature=0.9,
        )
        if not res:
            break
        usage = res.get("usage") or {}
        for key in usage_total:
            usage_total[key] += int(usage.get(key, 0) or 0)
        model = res.get("model") or model

        if (
            max_total_tokens
            and usage_total.get("total_tokens", 0) >= max_total_tokens
        ):
            _log.warning(
                "[FakeAi] agent 达到 token 预算 %s，提前收尾", max_total_tokens
            )
            break

        calls = res.get("tool_calls") or []
        if not calls:
            content = (res.get("content") or "").strip()
            if not actions and content:
                text = _extract_text(content)
                if text:
                    actions.append(AgentAction(kind="text", messages=[text]))
            break

        messages.append(
            {
                "role": "assistant",
                "content": res.get("content") or "",
                "tool_calls": calls,
            }
        )
        for call in calls:
            name = call.get("function", {}).get("name", "")
            if name == "finish":
                finished = True
                break
            try:
                args = json.loads(call.get("function", {}).get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            if skill_bridge is not None and name in bridge_names:
                try:
                    result = await skill_bridge.execute(name, args)
                except Exception as e:
                    result = f"skill 调用失败：{type(e).__name__}: {e}"
            else:
                result = await _execute_tool(
                    name,
                    args,
                    actions=actions,
                    sticker_catalog=sticker_catalog,
                    user_id=user_id,
                    group_id=group_id,
                    history_entries=history_entries,
                )
            messages.append(
                {"role": "tool", "tool_call_id": call.get("id"), "content": result}
            )
        if finished:
            break

    return AgentResult(
        actions=actions,
        usage=usage_total,
        model=model,
        finished=finished,
    )
