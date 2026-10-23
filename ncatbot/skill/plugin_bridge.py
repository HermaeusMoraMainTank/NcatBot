"""把每个已加载插件自动包装成一个通用 skill。

目标不是猜出插件内部 API，而是复用它自己的消息处理入口：
模型给出「插件名 + 要发的话」，桥接层构造一条合成消息，只分发给该插件的
handler，由插件按自己的触发词、权限、冷却逻辑处理。

这样 50+ 个插件不用逐个改代码，也能被 LLM 发现和调用。
"""

from __future__ import annotations

import functools
import inspect
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from ncatbot.core import CommandHook
from ncatbot.core.registry.builtin_hooks import KeywordHook, RegexHook, StartsWithHook
from ncatbot.sandbox import scrub_secrets
from ncatbot.types import At, MessageArray, PlainText
from ncatbot.types.qq import (
    GroupMessageEventData,
    GroupSender,
    PrivateMessageEventData,
    QQSender,
)
from ncatbot.utils import get_config_manager, get_log

from .registry import SkillRegistry
from .spec import SkillContext, SkillResult, SkillSpec

LOG = get_log("PluginBridge")

_STOP_HINTS = {
    "参数",
    "说明",
    "用法",
    "示例",
    "帮助",
    "注意",
    "支持",
    "功能",
    "命令",
    "触发",
    "格式",
    "返回",
    "默认",
    "可选",
    "必填",
}

_COMPARE_RE = re.compile(
    r"(?:raw_message|clean_message|message|text)\s*==\s*[\"']([^\"'\n]{1,16})[\"']"
)
_START_RE = re.compile(r"\.startswith\(\s*[\"']([^\"'\n]{1,16})")
_HELP_RE = re.compile(r"^\s*[·・\-*]?\s*([^\s：:]{1,12})[：:]")


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "")).strip("_")
    return cleaned or "plugin"


def _looks_like_trigger(value: str) -> bool:
    text = str(value or "").strip()
    if not text or len(text) > 20 or "\n" in text:
        return False
    if text in _STOP_HINTS:
        return False
    if text.startswith(("http://", "https://", "data/", "plugins/", "C:\\")):
        return False
    if text.startswith(("sk-", "Bearer ")):
        return False
    return any(ch.isalnum() or "\u4e00" <= ch <= "\u9fff" for ch in text)


class PluginBridge:
    """为一个 PluginLoader 提供「插件 → skill」的自动映射。"""

    def __init__(
        self,
        loader: Any,
        registry: SkillRegistry,
        *,
        enabled: bool = True,
        deny: Iterable[str] = (),
        allow: Iterable[str] | None = None,
    ) -> None:
        self.loader = loader
        self.registry = registry
        self.enabled = bool(enabled)
        self.deny = {str(item) for item in deny}
        self.allow = {str(item) for item in allow} if allow else None
        self._bound: dict[str, str] = {}
        self._hints: dict[str, list[str]] = {}

    # ------------------------------------------------------------------
    # 同步
    # ------------------------------------------------------------------

    def sync(self) -> int:
        if not self.enabled:
            return 0
        plugins = dict(getattr(self.loader, "plugins", {}) or {})
        for name in list(self._bound):
            if name not in plugins:
                self.remove(name)
        count = 0
        for plugin in plugins.values():
            name = str(getattr(plugin, "name", "") or "")
            if name in self._bound:
                continue
            if self.upsert(plugin):
                count += 1
        LOG.info(
            "PluginBridge 已同步 %d 个新插件 skill，当前共 %d 个：%s",
            count,
            len(self._bound),
            "、".join(sorted(self._bound)[:12]) or "无",
        )
        return count

    def upsert(self, plugin: Any) -> bool:
        if not self.enabled:
            return False
        name = str(getattr(plugin, "name", "") or "")
        if not name or name in self.deny:
            return False
        if self.allow is not None and name not in self.allow:
            return False
        if not self._handlers_for(name):
            # 没有消息 handler 的插件没有可复用的调用入口
            return False

        hints = self.trigger_hints(plugin)
        self._hints[name] = hints
        skill_name = f"plugin.{_safe_name(name)}.run"
        description = self._description(plugin, hints)
        plugin_desc = str(getattr(plugin, "description", "") or "").strip()
        if not plugin_desc:
            plugin_desc = (inspect.getdoc(type(plugin)) or "").splitlines()[0].strip()
        summary = f"{name} 插件"
        if plugin_desc:
            summary += f"：{plugin_desc[:32]}"
        elif hints:
            summary += f"（{hints[0]}）"
        spec = SkillSpec(
            name=skill_name,
            summary=summary,
            description=description,
            handler=functools.partial(self._invoke, plugin_name=name),
            plugin=name,
            parameters={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "要发给该插件的完整消息/命令",
                    },
                    "at_bot": {
                        "type": "boolean",
                        "description": "是否在消息里 @ 机器人，默认 false",
                    },
                    "_context": {
                        "type": "object",
                        "description": "MCP/后台调用时传 group_id、user_id；聊天中不需要",
                        "properties": {
                            "group_id": {"type": "string"},
                            "user_id": {"type": "string"},
                        },
                    },
                },
                "required": ["text"],
            },
            required=["text"],
            tags=["plugin", "auto", name.lower()],
            scopes=("user",),
            side_effects="message",
            requires=("event",),
            timeout=120,
            mcp_exposed=True,
            source=f"auto:{name}",
        )
        self.registry.register(spec)
        self._bound[name] = skill_name
        return True

    def remove(self, plugin_name: str) -> None:
        skill_name = self._bound.pop(plugin_name, None)
        self._hints.pop(plugin_name, None)
        if skill_name:
            self.registry.unregister(skill_name)

    # ------------------------------------------------------------------
    # 目录信息
    # ------------------------------------------------------------------

    def _handlers_for(self, plugin_name: str) -> list[Any]:
        hd = getattr(self.loader, "_handler_dispatcher", None)
        if hd is None:
            return []
        handlers = []
        for entries in hd.get_all_handlers().values():
            for entry in entries:
                if entry.plugin_name == plugin_name:
                    handlers.append(entry)
        return handlers

    def trigger_hints(self, plugin: Any) -> list[str]:
        strong: set[str] = set()
        for attr in ("LLM_TRIGGERS", "SKILL_TRIGGERS"):
            raw = getattr(plugin, attr, None)
            if isinstance(raw, (list, tuple, set)):
                strong.update(str(item) for item in raw)

        for entry in self._handlers_for(getattr(plugin, "name", "")):
            for hook in getattr(entry.func, "__hooks__", []) or []:
                if isinstance(hook, CommandHook):
                    strong.update(hook.names)
                elif isinstance(hook, StartsWithHook):
                    strong.add(hook.prefix)
                elif isinstance(hook, KeywordHook):
                    strong.update(hook.words)
                elif isinstance(hook, RegexHook):
                    # 正则本身不适合当触发词展示，跳过
                    continue

        commands = getattr(plugin, "commands", None)
        if isinstance(commands, dict):
            for key, config in commands.items():
                strong.add(str(key))
                if isinstance(config, dict):
                    for trigger in config.get("triggers") or []:
                        strong.add(str(trigger))

        module = sys.modules.get(getattr(type(plugin), "__module__", ""))
        if module is not None:
            for attr, value in vars(module).items():
                if attr.startswith("COMMAND_") and isinstance(value, str):
                    strong.add(value)
                elif attr in ("HELP_TEXT", "HELP", "HELP_MESSAGE") and isinstance(
                    value, str
                ):
                    strong.update(self._parse_help(value))

        hints = set(strong)
        # 只有拿不到标准触发词时，才去源码里做保守推测，避免把内部状态词当命令
        if not strong:
            try:
                source_path = inspect.getsourcefile(type(plugin))
                if source_path:
                    source = Path(source_path).read_text(
                        encoding="utf-8", errors="ignore"
                    )
                    hints.update(_COMPARE_RE.findall(source))
                    hints.update(_START_RE.findall(source))
            except (OSError, TypeError, UnicodeError):
                pass

        cleaned = []
        for hint in hints:
            text = str(hint or "").strip()
            if not _looks_like_trigger(text):
                continue
            text = scrub_secrets(text)
            if text not in cleaned:
                cleaned.append(text)
        cleaned.sort(key=lambda item: (len(item), item))
        return cleaned[:16]

    @staticmethod
    def _parse_help(text: str) -> set[str]:
        found: set[str] = set()
        for line in str(text or "").splitlines():
            match = _HELP_RE.match(line)
            if match:
                found.add(match.group(1))
        return found

    @staticmethod
    def _description(plugin: Any, hints: list[str]) -> str:
        name = str(getattr(plugin, "name", "") or "")
        desc = str(getattr(plugin, "description", "") or "").strip()
        doc = inspect.getdoc(type(plugin)) or ""
        if not desc and doc:
            desc = doc.splitlines()[0].strip()
        lines = [f"插件：{name}"]
        if desc:
            lines.append(desc)
        if hints:
            lines.append("检测到的触发词/命令：" + "、".join(hints))
        else:
            lines.append("未检测到固定触发词；可以尝试发送「帮助」或该插件的自然语言指令。")
        lines.append(
            "调用方式：text 会作为一条消息发给该插件，由它按自己的逻辑回复；"
            "如果插件要求 @机器人，把 at_bot 设为 true。"
        )
        lines.append("如果目录里已有该插件的专用 skill，优先用专用 skill。")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 调用
    # ------------------------------------------------------------------

    @staticmethod
    def _sender_name(ctx: SkillContext, user_id: str) -> str:
        """还原调用者昵称：extras → 事件 sender → 用户 ID。"""
        name = str(ctx.extras.get("nickname") or "").strip()
        if name:
            return name
        sender = getattr(ctx.event, "sender", None)
        if sender is not None:
            for field in ("card", "nickname"):
                value = str(getattr(sender, field, "") or "").strip()
                if value:
                    return value
        return f"用户{user_id}" if user_id else "用户"

    async def _invoke(
        self,
        ctx: SkillContext,
        text: str,
        at_bot: bool = False,
        _context: dict | None = None,
        *,
        plugin_name: str,
    ) -> SkillResult:
        plugin = getattr(self.loader, "plugins", {}).get(plugin_name)
        if plugin is None:
            return SkillResult.fail(f"插件未加载：{plugin_name}")
        hd = getattr(self.loader, "_handler_dispatcher", None)
        if hd is None:
            return SkillResult.fail("事件分发器不可用")

        body = re.sub(r"\[CQ:[^\]]+\]", "", str(text or ""))[:500].strip()
        if not body:
            return SkillResult.fail("text 不能为空")
        group_id = str(ctx.group_id or "").strip()
        user_id = str(ctx.user_id or "").strip()
        if not user_id and ctx.event is not None:
            user_id = str(getattr(ctx.event, "user_id", "") or "")
        if not user_id:
            return SkillResult.fail("缺少当前用户信息，无法模拟插件调用")

        try:
            self_id = str(get_config_manager().config.bot_uin)
        except Exception:
            self_id = "0"
        message_id = "0"
        if ctx.event is not None:
            message_id = str(getattr(ctx.event, "message_id", "0") or "0")

        segments: list[Any] = []
        raw_message = body
        if at_bot and self_id and self_id != "0":
            segments.append(At(user_id=self_id))
            raw_message = f"[CQ:at,qq={self_id}] {body}"
        segments.append(PlainText(text=body))
        message = MessageArray(segments)
        sender_name = self._sender_name(ctx, user_id)

        if group_id:
            data = GroupMessageEventData(
                time=int(time.time()),
                self_id=self_id,
                message_id=message_id,
                user_id=user_id,
                message=message,
                raw_message=raw_message,
                sender=GroupSender(
                    user_id=user_id,
                    nickname=sender_name,
                    card=sender_name,
                    role="member",
                ),
                group_id=group_id,
                synthetic=True,
            )
        else:
            data = PrivateMessageEventData(
                time=int(time.time()),
                self_id=self_id,
                message_id=message_id,
                user_id=user_id,
                message=message,
                raw_message=raw_message,
                sender=QQSender(user_id=user_id, nickname=sender_name),
                synthetic=True,
            )

        try:
            result = await hd.dispatch_event(
                data, platform="qq", plugin_filter=plugin_name
            )
        except Exception as exc:
            return SkillResult.fail(f"调用插件失败：{type(exc).__name__}: {exc}")

        if result.executed <= 0:
            hints = self._hints.get(plugin_name) or self.trigger_hints(plugin)
            hint_text = "、".join(hints[:10]) if hints else "未检测到"
            return SkillResult.fail(
                f"{plugin_name} 没有处理这条消息，可能是触发词不匹配。"
                f"可尝试：{hint_text}"
            )
        if result.errors >= result.executed:
            return SkillResult.fail(
                f"{plugin_name} 的 handler 执行失败（{result.errors}/{result.executed}）。"
                "请查看日志或换一种触发方式。"
            )
        content = (
            f"已交给 {plugin_name} 处理（{result.executed} 个 handler）。"
            "插件会按自己的逻辑回复，不要重复发送。"
        )
        if result.errors:
            content += f" 其中 {result.errors} 个 handler 报错，结果可能不完整。"
        return SkillResult(
            ok=True,
            content=content,
            data={
                "plugin": plugin_name,
                "handlers": result.executed,
                "errors": result.errors,
                "text": body,
            },
        )
