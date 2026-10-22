"""插件目录：让模型知道「还有哪些前缀命令」，但不直接替用户执行。"""

from __future__ import annotations

import re
import time

from ncatbot.skill import SkillContext, SkillResult, skill


class PluginCatalogSkills:
    @skill(
        "plugin.list",
        summary="列出已加载插件及其说明",
        params=[],
        tags=["plugin", "read"],
        side_effects="none",
    )
    async def skill_plugin_list(self, ctx: SkillContext) -> SkillResult:
        loader = getattr(self, "_plugin_loader", None)
        if loader is None:
            return SkillResult.fail("插件加载器不可用")
        rows = []
        for name, plugin in (getattr(loader, "plugins", {}) or {}).items():
            rows.append(
                {
                    "name": name,
                    "version": getattr(plugin, "version", ""),
                    "description": getattr(plugin, "description", ""),
                }
            )
        lines = [f"{r['name']} v{r['version']}：{r['description']}" for r in rows]
        return SkillResult(ok=True, content="\n".join(lines), data=rows)

    @skill(
        "plugin.commands.list",
        summary="列出插件的前缀命令（只读）",
        description="用于告诉用户「这个功能要发什么命令」；不会执行命令。",
        params=[
            {"name": "plugin", "type": "string", "description": "只看某个插件，可空"},
        ],
        tags=["plugin", "read"],
        side_effects="none",
    )
    async def skill_plugin_commands(self, ctx: SkillContext, plugin: str = "") -> SkillResult:
        loader = getattr(self, "_plugin_loader", None)
        dispatcher = getattr(loader, "_handler_dispatcher", None) if loader else None
        if dispatcher is None:
            return SkillResult.fail("分发器不可用")
        try:
            from ncatbot.core import CommandHook
            from ncatbot.core.registry.builtin_hooks import KeywordHook, StartsWithHook
        except Exception:
            CommandHook = KeywordHook = StartsWithHook = ()  # type: ignore[assignment]

        rows = []
        for event_type, handlers in dispatcher.get_all_handlers().items():
            for entry in handlers:
                if plugin and entry.plugin_name != plugin:
                    continue
                for hook in getattr(entry.func, "__hooks__", []):
                    names: list[str] = []
                    kind = ""
                    if isinstance(hook, CommandHook):
                        names = list(hook.names)
                        kind = "command"
                    elif isinstance(hook, StartsWithHook):
                        names = [hook.prefix]
                        kind = "prefix"
                    elif isinstance(hook, KeywordHook):
                        names = list(hook.words)
                        kind = "keyword"
                    for name in names:
                        rows.append(
                            {
                                "command": name,
                                "kind": kind,
                                "plugin": entry.plugin_name,
                                "handler": getattr(entry.func, "__name__", ""),
                                "event": event_type,
                            }
                        )
        rows.sort(key=lambda r: (r["plugin"], r["command"]))
        lines = [
            f"{r['command']}（{r['plugin']}，{r['kind']}）" for r in rows[:80]
        ]
        return SkillResult(
            ok=True,
            content="\n".join(lines) or "没有注册前缀命令",
            data=rows[:200],
        )

    @skill(
        "plugin.command.run",
        summary="执行一个已开放的前缀命令（默认全部关闭）",
        description=(
            "只有 SkillHub 配置 exposed_commands 里的命令才允许模型代发。"
            "会把命令当成一条群消息投递给现有插件，插件会按自己的逻辑回复。"
        ),
        params=[
            {"name": "command", "type": "string", "description": "命令名，必须已开放", "required": True},
            {"name": "text", "type": "string", "description": "命令后面的参数文本，可空"},
        ],
        tags=["plugin", "message", "write"],
        side_effects="message",
        requires=("event",),
        timeout=120,
    )
    async def skill_plugin_command_run(
        self,
        ctx: SkillContext,
        command: str,
        text: str = "",
    ) -> SkillResult:
        config = getattr(self, "config", {}) or {}
        exposed = [str(item) for item in (config.get("exposed_commands", []) or [])]
        command = str(command or "").strip()
        if command not in exposed:
            return SkillResult.fail(
                f"命令「{command}」未开放给模型；请在 SkillHub 的 exposed_commands 里显式添加。"
            )
        loader = getattr(self, "_plugin_loader", None)
        handler_dispatcher = (
            getattr(loader, "_handler_dispatcher", None) if loader is not None else None
        )
        if handler_dispatcher is None:
            return SkillResult.fail("事件分发器不可用")
        gid = str(ctx.group_id or "").strip()
        uid = str(ctx.user_id or "").strip()
        if not gid or not uid:
            return SkillResult.fail("缺少当前群/用户信息，无法模拟群命令")

        from ncatbot.types import MessageArray, PlainText
        from ncatbot.types.qq import GroupMessageEventData, GroupSender
        from ncatbot.utils import get_config_manager

        text = re.sub(r"\[CQ:[^\]]+\]", "", str(text or ""))[:500]
        full_text = command if not text else f"{command} {text}"
        try:
            self_id = str(get_config_manager().config.bot_uin)
        except Exception:
            self_id = "0"
        message_id = "0"
        if ctx.event is not None:
            message_id = str(getattr(ctx.event, "message_id", "0") or "0")
        data = GroupMessageEventData(
            time=int(time.time()),
            self_id=self_id,
            message_id=message_id,
            user_id=uid,
            message=MessageArray([PlainText(text=full_text)]),
            raw_message=full_text,
            sender=GroupSender(
                user_id=uid,
                nickname="模型",
                card="模型",
                role="member",
            ),
            group_id=gid,
            synthetic=True,
        )
        try:
            result = await handler_dispatcher.dispatch_event(data, platform="qq")
        except Exception as exc:
            return SkillResult.fail(f"投递命令失败：{type(exc).__name__}: {exc}")
        if result.executed <= 0:
            return SkillResult.fail(
                f"命令「{command}」没有匹配到插件 handler；请检查命令名。"
            )
        if result.errors >= result.executed:
            return SkillResult.fail(
                f"命令「{command}」执行失败（{result.errors}/{result.executed}）。"
            )
        content = f"已执行命令「{command}」，插件已按自己的逻辑处理。不要重复执行同样的命令。"
        if result.errors:
            content += f" 其中 {result.errors} 个 handler 报错。"
        return SkillResult(
            ok=True,
            content=content,
            data={
                "command": command,
                "group_id": gid,
                "handlers": result.executed,
                "errors": result.errors,
            },
        )
