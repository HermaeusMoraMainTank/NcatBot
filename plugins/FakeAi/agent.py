"""FakeAi 工具型 agent：模型自己决定发消息、发表情、拍一拍、搜索或沉默。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from common.utils.AiUtil import AiUtil

from .memory import memory_manager

_log = logging.getLogger(__name__)


@dataclass
class AgentAction:
    kind: str  # text | sticker | poke | react | voice | image
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
    # 工具调用轨迹，供后台复盘（name / args / result 都是截断后的文本）
    tool_log: list = field(default_factory=list)
    rounds: int = 0


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
            "name": "sticker_vibe",
            "description": (
                "按此刻语气一步挑一张表情发出（先梗鲸、再本地图库），"
                "不用先查再发。topic 填情绪关键词。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "情绪/语气关键词，例如 笑死、无语、晚安",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_agent",
            "description": (
                "派一个子智能体去独立工作区干活（写文件、整理资料、跑多步任务），"
                "完成后把结果和产物带回来。适合需要好几步才能做完的事。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "要子智能体完成的任务，写清楚目标与产出",
                    },
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "group_history",
            "description": "翻更早的群聊记录（比 get_recent_messages 更早，存在本地库）。想接旧话题、查某人以前说过什么时用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "关键词，可空；为空就取最近的"},
                    "limit": {"type": "integer", "description": "条数，默认 15，最多 40"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_message",
            "description": "定一条稍后自动发到当前群的消息（比如 10 分钟后提醒）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "到点要发的内容"},
                    "minutes": {"type": "number", "description": "多少分钟后发，最少 1"},
                },
                "required": ["text", "minutes"],
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
            "name": "read_video",
            "description": "看一段视频：自动抽几帧后描述画面内容。需要消息 #id。",
            "parameters": {
                "type": "object",
                "properties": {
                    "messageId": {"type": "string", "description": "含视频的消息 #id"},
                    "frames": {
                        "type": "integer",
                        "description": "抽几帧，默认 4，最多 6",
                    },
                },
                "required": ["messageId"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sticker_image",
            "description": "看一眼自己表情库里某张图长什么样（需要图名）。",
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
            "name": "sticker_note",
            "description": "给表情写一句用途备注（例如「嘲讽」「无语」「晚安」），下次挑图更容易。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "贴纸名"},
                    "note": {
                        "type": "string",
                        "description": "用途或情绪，20 字以内；传空字符串表示删除备注",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_forward",
            "description": "展开一条合并转发消息，看里面到底说了什么。需要消息 #id。",
            "parameters": {
                "type": "object",
                "properties": {
                    "messageId": {"type": "string", "description": "合并转发的消息 #id"}
                },
                "required": ["messageId"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_voice",
            "description": "把一句话用语音发出来（TTS）。需要「发个语音」「用声音说」时用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "要念出来的内容，尽量短，不超过 80 字",
                    }
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_image",
            "description": "发送一张网图（给定 http/https 直链）。只发确认能打开的图片地址。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "图片直链，必须是 http(s)"}
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "collect_sticker",
            "description": "把群里某条消息里的图片收进自己的表情库，以后可以用 send_sticker 发。",
            "parameters": {
                "type": "object",
                "properties": {
                    "messageId": {
                        "type": "string",
                        "description": "含图片的消息 #id，不填就用最近一条带图的",
                    },
                    "name": {
                        "type": "string",
                        "description": "给这张图起个名字，方便以后按名字发；不填自动生成",
                    },
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


_THOUGHT_MARKERS = (
    "没点我名",
    "没人叫我",
    "不该接",
    "不接",
    "要不要",
    "该不该",
    "这句话",
    "用户",
    "群友",
    "对方",
    "应该回",
    "需要回",
    "跟我无关",
    "内心",
    "系统提醒",
)


def _looks_like_thought(text: str) -> bool:
    """判断这段正文是不是「内心独白」而不是要发出去的话。

    模型经常输出「（没点我名，漓那句就是玩梗，不接）」，这段被当成发言
    发到群里就是灾难性的伪人感。宁可多催一轮，也不要把它发出去。
    """
    t = (text or "").strip()
    if not t:
        return False
    if t[0] in "（(":
        return True
    if any(m in t for m in _THOUGHT_MARKERS):
        return True
    if len(t) > 80 and t.endswith("。"):
        return True
    return t.count("\n") >= 2


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


def _ffmpeg_bin(name: str = "ffmpeg") -> str:
    """找 ffmpeg/ffprobe：PATH → 用户目录 → winget Links。"""
    found = shutil.which(name)
    if found:
        return found
    exe = ".exe" if os.name == "nt" else ""
    candidates = []
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    if home:
        candidates.append(Path(home) / "ffmpeg" / "bin" / f"{name}{exe}")
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        candidates.append(Path(local) / "ffmpeg" / "bin" / f"{name}{exe}")
        candidates.append(
            Path(local) / "Microsoft" / "WinGet" / "Links" / f"{name}{exe}"
        )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return ""


async def _run_ffmpeg(
    args: list[str], timeout: float = 25.0, want_stdout: bool = False
) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE if want_stdout else asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as e:
        return 127, f"{type(e).__name__}: {e}"
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "timeout"
    if want_stdout:
        return proc.returncode or 0, (stdout or b"").decode("utf-8", "ignore")[:500]
    return proc.returncode or 0, (stderr or b"").decode("utf-8", "ignore")[:300]


async def _video_duration(path: Path) -> float:
    ffprobe = _ffmpeg_bin("ffprobe")
    if not ffprobe:
        return 0.0
    code, out = await _run_ffmpeg(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        timeout=15.0,
        want_stdout=True,
    )
    if code != 0:
        return 0.0
    try:
        return float(str(out).strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0.0


async def _video_frames(path: Path, count: int = 4) -> list[Path]:
    """抽 count 帧到临时目录；没装 ffmpeg 返回空列表。"""
    ffmpeg = _ffmpeg_bin("ffmpeg")
    if not ffmpeg:
        return []
    count = max(1, min(6, int(count or 4)))
    duration = await _video_duration(path)
    if duration > 0:
        times = [duration * (i + 1) / (count + 1) for i in range(count)]
    else:
        times = [0, 2, 5, 8, 12, 16][:count]
    out_dir = Path(tempfile.mkdtemp(prefix="ncatbot-video-"))
    frames: list[Path] = []
    for idx, ts in enumerate(times):
        target = out_dir / f"f{idx}.jpg"
        code, _ = await _run_ffmpeg(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{ts:.2f}",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                "scale=640:-2",
                "-y",
                str(target),
            ]
        )
        if code == 0 and target.is_file() and target.stat().st_size > 0:
            frames.append(target)
    return frames


def _forward_res_id(content) -> str:
    """从消息内容里抽出合并转发的 res_id（兼容 CQ 码与锚点两种形态）。"""
    if isinstance(content, str):
        m = re.search(r"\[CQ:forward,id=([^\],]+)", content)
        if m:
            return m.group(1)
        m = re.search(r"\[合并转发#([^\]]+)\]", content)
        return m.group(1) if m else ""
    if isinstance(content, list):
        for seg in content:
            if isinstance(seg, dict) and seg.get("type") == "forward":
                return str((seg.get("data") or {}).get("id") or "")
    return ""


def _segments_to_text(content, limit: int = 400) -> str:
    """把消息段列表渲染成一行可读文本（用于展开合并转发）。"""
    if not content:
        return ""
    if isinstance(content, str):
        return content[:limit]
    if not isinstance(content, list):
        return str(content)[:limit]
    parts: list[str] = []
    for seg in content:
        if not isinstance(seg, dict):
            continue
        kind = seg.get("type")
        data = seg.get("data") or {}
        if kind == "text":
            parts.append(str(data.get("text") or ""))
        elif kind == "image":
            parts.append("[图片]")
        elif kind == "face":
            parts.append("[表情]")
        elif kind == "record":
            parts.append("[语音]")
        elif kind == "video":
            parts.append("[视频]")
        elif kind == "at":
            parts.append(f"@{data.get('qq') or ''}")
    return " ".join(p for p in parts if p).strip()[:limit]


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
    bot_api=None,
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

    if name == "group_history":
        query = str(args.get("query") or "").strip()
        limit = max(1, min(40, int(args.get("limit", 15) or 15)))
        rows = await memory_manager.search_group_messages(
            group_id, query=query, limit=limit
        )
        if not rows:
            return "没有更早的记录了。"
        lines = []
        for row in reversed(rows):
            stamp = time.strftime("%m-%d %H:%M", time.localtime(row["ts"]))
            lines.append(f"[{stamp}] {row['name']}: {row['content']}")
        return "\n".join(lines)

    if name == "schedule_message":
        text = str(args.get("text") or "").strip()
        try:
            minutes = float(args.get("minutes", 0) or 0)
        except (TypeError, ValueError):
            minutes = 0
        if not text or minutes < 1:
            return "需要 text，且 minutes 至少 1。"
        due = time.time() + minutes * 60
        await memory_manager.add_scheduled_message(
            group_id, user_id or "", text, due
        )
        return f"已定在 {int(minutes)} 分钟后发送，到点会自动发出。"

    if name == "sticker_vibe":
        from . import expression as expr_pkg

        if sum(1 for a in actions if a.kind == "sticker") >= max(
            1, int(expr_pkg.MAX_STICKERS_PER_REPLY)
        ):
            return "本轮表情已经够了，不要再发。"
        topic = str(args.get("topic") or "").strip()
        path = None
        try:
            from .expression import whale_catalog

            if whale_catalog.count():
                path = await whale_catalog.fetch(topic)
        except Exception:
            path = None
        if path is None and sticker_catalog is not None:
            path = sticker_catalog.pick_any()
        if path is None:
            return "没有可用表情，直接说话吧。"
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

    if name == "send_voice":
        if any(a.kind == "voice" for a in actions):
            return "本轮已经发过语音了，不要再发。"
        text = str(args.get("text") or "").strip()[:80]
        if not text:
            return "缺少 text，没有发语音。"
        actions.append(AgentAction(kind="voice", messages=[text]))
        return "语音已加入发送队列。"

    if name == "send_image":
        url = str(args.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return "只支持 http/https 图片直链。"
        if any(a.kind == "image" for a in actions):
            return "本轮已经发过网图了，不要再发。"
        actions.append(AgentAction(kind="image", messages=[url]))
        return "图片已加入发送队列。"

    if name == "collect_sticker":
        mid = str(args.get("messageId") or "").strip()
        entry = None
        if mid:
            entry = next(
                (
                    e
                    for e in (history_entries or [])
                    if str(e.get("message_id")) == mid
                ),
                None,
            )
        if entry is None:
            entry = next(
                (
                    e
                    for e in reversed(history_entries or [])
                    if e.get("image_urls")
                ),
                None,
            )
        urls = list((entry or {}).get("image_urls") or [])
        if not urls:
            return "这条消息里没有图片，换一条带图的试试。"
        if sticker_catalog is None:
            return "本地表情库未启用，收藏不了。"
        data = await AiUtil.download_image_bytes(urls[0])
        if not data:
            return "图片下载失败，可能已经过期。"
        saved = sticker_catalog.save_image(
            str(args.get("name") or "").strip(), data
        )
        if not saved:
            return "这张图格式不支持，没收。"
        return f"已收进表情库：{saved}。之后可以用 send_sticker(name) 发它。"

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
        if not await AiUtil.vision_available():
            return "我这边看不了图片，别编造图里的内容；可以让群友说一句是什么。"

        descriptions = []
        for url in urls[:2]:
            data = await AiUtil.download_image_bytes(url)
            if not data:
                continue
            key = hashlib.md5(data).hexdigest()[:24]
            cached = await memory_manager.get_vision_cache(key, "brief")
            if cached and cached.get("content"):
                descriptions.append(str(cached["content"]).strip())
                continue
            b64 = await asyncio.to_thread(AiUtil.image_bytes_to_base64, data)
            if not b64:
                continue
            result = await AiUtil.chat_with_vision(
                "简洁描述这张图的内容，如果有文字请转录。",
                image_base64_list=[b64],
                max_tokens=220,
            )
            if result and result.get("content"):
                desc = result["content"].strip()
                descriptions.append(desc)
                try:
                    await memory_manager.save_vision_cache(key, result, "brief", url)
                except Exception as e:
                    _log.debug("[FakeAi] 识图缓存写入失败: %s", e)
        return "\n---\n".join(descriptions) or "图片识别失败"

    if name == "read_video":
        mid = str(args.get("messageId") or "").strip()
        urls: list[str] = []
        if mid:
            entry = next(
                (
                    e
                    for e in (history_entries or [])
                    if str(e.get("message_id")) == mid
                ),
                None,
            )
            urls = list((entry or {}).get("video_urls") or [])
        if not urls:
            return "这条消息里没有视频，或者视频已经过期。"
        if not _ffmpeg_bin("ffmpeg"):
            return "本机没装 ffmpeg，看不了视频。"
        if not await AiUtil.vision_available():
            return "我看不了画面，只能告诉你这里有个视频。"

        url = urls[0]
        temp_path: Optional[Path] = None
        frame_dir: Optional[Path] = None
        try:
            if url.startswith(("http://", "https://")):
                data = await AiUtil.download_image_bytes(
                    url, max_bytes=40 * 1024 * 1024
                )
                if not data:
                    return "视频下载失败，可能已经过期。"
                fd, name = tempfile.mkstemp(prefix="ncatbot-video-", suffix=".mp4")
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                temp_path = Path(name)
                path = temp_path
            else:
                raw = url[7:] if url.startswith("file://") else url
                path = Path(raw)
                if not path.is_file():
                    return "视频文件不存在，可能已经被清理。"

            frames = await _video_frames(
                path, int(args.get("frames", 4) or 4)
            )
            if not frames:
                return "抽帧失败，视频可能损坏或格式不支持。"
            frame_dir = frames[0].parent

            b64s: list[str] = []
            for frame in frames:
                raw_bytes = await asyncio.to_thread(frame.read_bytes)
                b64 = await asyncio.to_thread(
                    AiUtil.image_bytes_to_base64, raw_bytes
                )
                if b64:
                    b64s.append(b64)
            if not b64s:
                return "画面转码失败，看不了。"

            result = await AiUtil.chat_with_vision(
                f"这是一段视频按时间顺序抽的 {len(b64s)} 帧。"
                "用两三句话说清楚画面里发生了什么、有哪些人和物。",
                image_base64_list=b64s,
                max_tokens=260,
            )
            if not result or not result.get("content"):
                return "画面识别失败。"
            return result["content"].strip()
        finally:
            if frame_dir is not None:
                shutil.rmtree(frame_dir, ignore_errors=True)
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    if name == "get_sticker_image":
        target = str(args.get("name") or "").strip()
        if sticker_catalog is None:
            return "本地表情库未启用。"
        path = sticker_catalog.find(target)
        if path is None:
            names = sticker_catalog.names()[:30]
            return f"没有这个贴纸：{target}。可用：{'、'.join(names)}"
        if not await AiUtil.vision_available():
            return "我看不了图片，只能按名字和备注猜。"
        try:
            data = await asyncio.to_thread(lambda: Path(path).read_bytes())
        except OSError as e:
            return f"读取图片失败：{e}"
        b64 = await asyncio.to_thread(AiUtil.image_bytes_to_base64, data)
        if not b64:
            return "这张图格式不支持预览。"
        result = await AiUtil.chat_with_vision(
            "简洁描述这张表情包：画面内容 + 传达的情绪，40 字内。",
            image_base64_list=[b64],
            max_tokens=120,
        )
        if not result or not result.get("content"):
            return "没看出这张图是什么。"
        desc = result["content"].strip()
        current = sticker_catalog.note(target)
        suffix = f"（当前备注：{current}）" if current else ""
        return f"{target}：{desc}{suffix}"

    if name == "sticker_note":
        target = str(args.get("name") or "").strip()
        note = str(args.get("note") or "").strip()
        if sticker_catalog is None:
            return "本地表情库未启用。"
        if not sticker_catalog.set_note(target, note):
            names = sticker_catalog.names()[:30]
            return f"没有这个贴纸：{target}。可用：{'、'.join(names)}"
        if note:
            return f"已记下：{target} → {note[:60]}"
        return f"已清掉 {target} 的备注"

    if name == "read_forward":
        mid = str(args.get("messageId") or "").strip()
        if not mid:
            return "需要 messageId（先用 get_recent_messages 查 #id）。"
        if bot_api is None:
            return "当前环境无法展开合并转发。"
        # 模型可能传外层消息 id，也可能直接传转发段里的 res_id，两种都认
        target = mid
        entry = next(
            (e for e in (history_entries or []) if str(e.get("message_id")) == mid),
            None,
        )
        if entry is not None:
            extracted = _forward_res_id(entry.get("content"))
            if extracted:
                target = extracted
        try:
            data = await bot_api.qq.get_forward_msg(target)
        except Exception as e:
            return f"展开失败：{type(e).__name__}: {e}"
        nodes = getattr(data, "messages", None) or []
        lines: list[str] = []
        for node in nodes[:30]:
            sender = getattr(node, "sender", None)
            who = "?"
            if sender is not None:
                who = (
                    getattr(sender, "card", None)
                    or getattr(sender, "nickname", None)
                    or getattr(sender, "user_id", None)
                    or "?"
                )
            text = _segments_to_text(getattr(node, "message", None))
            if text:
                lines.append(f"{str(who)[:12]}: {text}")
        body = "\n".join(lines)[:3000]
        return body or "这条转发里没有可读内容"

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


RUN_TIMEOUT = 150.0      # 单轮工具循环总超时（秒）
TOOL_TIMEOUT = 15.0      # 轻量工具调用超时（秒）
SKILL_TIMEOUT = 45.0     # 插件/skill 调用超时（秒）：图库下载、TTS 之类本来就慢
SPAWN_TIMEOUT = 120.0    # 子智能体超时（秒），与 agent.spawn 的 spec 对齐


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
    tool_filter: Optional[set[str]] = None,
    max_total_tokens: int = 60000,
    skill_bridge=None,
    bot_api=None,
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
    tool_log: list = []
    rounds_used = 0
    nudged = 0
    bridge_tools = skill_bridge.tools if skill_bridge is not None else []
    bridge_names = {t["function"]["name"] for t in bridge_tools}
    tools = list(AGENT_TOOLS) + bridge_tools
    if tool_filter:
        # 闲聊轮只暴露轻量工具，别让模型在十几个工具里乱挑
        tools = [t for t in tools if t["function"]["name"] in tool_filter]
    if force_speak:
        # 强制开口时拿掉 finish，模型就没法用「安静结束」交差
        tools = [t for t in tools if t["function"]["name"] != "finish"]

    deadline = time.monotonic() + RUN_TIMEOUT
    for _round in range(max_rounds):
        rounds_used = _round + 1
        if time.monotonic() > deadline:
            _log.warning("[FakeAi] agent 本轮超时（%.0fs），提前收尾", RUN_TIMEOUT)
            break
        remaining = max_rounds - _round
        if remaining <= 2 and not actions:
            # 快没轮次了还没开口：催一次，避免模型把话写在正文里然后静默结束
            messages.append(
                {
                    "role": "user",
                    "content": "（系统提醒）你还没有发出任何消息。要说话就调用 send_message，"
                    "确定不说就 finish。",
                }
            )
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
                # 模型经常把「内心活动」写成正文（括号独白、分析自己的判断）。
                # 这不是发言，绝不能发到群里——催一次，让它改用工具。
                if _looks_like_thought(content):
                    nudged += 1
                    if nudged <= 2:
                        messages.append(
                            {
                                "role": "user",
                                "content": "（系统提醒）刚才那段是内心活动，不会发出去，"
                                "也不要发。要说话请调用 send_message；"
                                "决定不说就调用 finish。",
                            }
                        )
                        continue
                else:
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
            if name == "spawn_agent" and skill_bridge is not None:
                try:
                    result = await asyncio.wait_for(
                        skill_bridge.execute(
                            "invoke_skill",
                            {
                                "name": "agent.spawn",
                                "arguments": {"task": str(args.get("task") or "")},
                            },
                        ),
                        timeout=SPAWN_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    result = f"子智能体超时（>{SPAWN_TIMEOUT:.0f}s），先自己简单处理"
                except Exception as e:
                    result = f"派活失败：{type(e).__name__}: {e}"
            elif skill_bridge is not None and name in bridge_names:
                try:
                    result = await asyncio.wait_for(
                        skill_bridge.execute(name, args), timeout=SKILL_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    result = (
                        f"skill 调用超时（>{SKILL_TIMEOUT:.0f}s），"
                        "换个方式或直接说话"
                    )
                except Exception as e:
                    result = f"skill 调用失败：{type(e).__name__}: {e}"
            else:
                try:
                    result = await asyncio.wait_for(
                        _execute_tool(
                            name,
                            args,
                            actions=actions,
                            sticker_catalog=sticker_catalog,
                            user_id=user_id,
                            group_id=group_id,
                            history_entries=history_entries,
                            bot_api=bot_api,
                        ),
                        timeout=TOOL_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    result = f"工具超时（>{TOOL_TIMEOUT:.0f}s），换个方式或直接说话"
                except Exception as e:
                    # 单个工具炸掉不能让整轮哑火，回灌给模型自己决定怎么办
                    _log.warning("[FakeAi] 工具 %s 执行失败: %s", name, e)
                    result = f"工具执行失败：{type(e).__name__}: {e}"
            messages.append(
                {"role": "tool", "tool_call_id": call.get("id"), "content": result}
            )
            tool_log.append(
                {
                    "name": name,
                    "args": str(call.get("function", {}).get("arguments") or "")[:200],
                    "result": str(result or "")[:300],
                }
            )
        if finished:
            break

    return AgentResult(
        actions=actions,
        usage=usage_total,
        model=model,
        finished=finished,
        tool_log=tool_log,
        rounds=rounds_used,
    )
