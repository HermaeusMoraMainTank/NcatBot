"""出向能力：所有发消息的动作只从这里走。"""

from __future__ import annotations

from typing import Any

from ncatbot.sandbox import WorkspaceError
from ncatbot.skill import SkillContext, SkillResult, skill
from ncatbot.types import Image


def resolve_target(ctx: SkillContext) -> tuple[str, str]:
    group_id = str(ctx.group_id or "")
    if not group_id and ctx.event is not None:
        group_id = str(getattr(ctx.event, "group_id", "") or "")
    if group_id:
        return "group", group_id
    user_id = str(ctx.user_id or "")
    if not user_id and ctx.event is not None:
        user_id = str(getattr(ctx.event, "user_id", "") or "")
    if user_id:
        return "private", user_id
    return "", ""


def qq_api(ctx: SkillContext) -> Any:
    api = ctx.api
    if api is None and ctx.event is not None:
        api = getattr(ctx.event, "api", None)
    if api is None:
        return None
    return getattr(api, "qq", api)


def _message_id(result: Any) -> str:
    for key in ("message_id", "messageId"):
        value = getattr(result, key, None)
        if value:
            return str(value)
    if isinstance(result, dict):
        data = result.get("data") or result
        value = data.get("message_id") or data.get("messageId")
        if value:
            return str(value)
    return ""


class MessageSkills:
    @skill(
        "message.send",
        summary="向当前会话发一条文字消息",
        description="会直接发送到当前群/私聊。需要引用当前消息时 quote=true；需要 @ 某人时传 at_user。",
        params=[
            {"name": "text", "type": "string", "description": "正文", "required": True},
            {"name": "quote", "type": "boolean", "description": "是否引用当前消息"},
            {"name": "at_user", "type": "string", "description": "要 @ 的 QQ 号"},
        ],
        tags=["message", "write"],
        side_effects="message",
        requires=("event",),
    )
    async def skill_message_send(
        self,
        ctx: SkillContext,
        text: str,
        quote: bool = False,
        at_user: str = "",
    ) -> SkillResult:
        api = qq_api(ctx)
        kind, target = resolve_target(ctx)
        if api is None or not kind:
            return SkillResult.fail("当前没有可发送的会话")
        reply = None
        if quote and ctx.event is not None:
            reply = getattr(ctx.event, "message_id", None)
        try:
            if kind == "group":
                result = await api.post_group_msg(
                    target, text=text, reply=reply, at=at_user or None
                )
            else:
                result = await api.post_private_msg(target, text=text, reply=reply)
        except Exception as exc:
            return SkillResult.fail(f"发送失败：{type(exc).__name__}: {exc}")
        return SkillResult(
            ok=True,
            content="已发送",
            data={"message_id": _message_id(result)},
        )

    @skill(
        "message.send_image",
        summary="发一张图片（工作区文件或 URL）",
        description="path 是工作区内的相对路径；也可以直接用 url。",
        params=[
            {"name": "path", "type": "string", "description": "工作区相对路径"},
            {"name": "url", "type": "string", "description": "图片 URL"},
            {"name": "quote", "type": "boolean", "description": "是否引用当前消息"},
        ],
        tags=["message", "media", "write"],
        side_effects="message",
        requires=("event", "media"),
    )
    async def skill_message_send_image(
        self,
        ctx: SkillContext,
        path: str = "",
        url: str = "",
        quote: bool = False,
    ) -> SkillResult:
        api = qq_api(ctx)
        kind, target = resolve_target(ctx)
        if api is None or not kind:
            return SkillResult.fail("当前没有可发送的会话")
        if path:
            if ctx.workspace is None:
                return SkillResult.fail("当前没有工作区")
            try:
                file_path = ctx.workspace.path(path)
            except WorkspaceError as exc:
                return SkillResult.fail(str(exc))
            if not file_path.is_file():
                return SkillResult.fail(f"图片不存在：{path}")
            image = Image(file=str(file_path))
        elif url:
            image = Image(file=url)
        else:
            return SkillResult.fail("需要 path 或 url")
        reply = getattr(ctx.event, "message_id", None) if quote and ctx.event else None
        try:
            if kind == "group":
                result = await api.post_group_msg(target, image=image, reply=reply)
            else:
                result = await api.post_private_msg(target, image=image, reply=reply)
        except Exception as exc:
            return SkillResult.fail(f"发送失败：{type(exc).__name__}: {exc}")
        return SkillResult(ok=True, content="图片已发送", data={"message_id": _message_id(result)})

    @skill(
        "message.send_file",
        summary="发一个文件（工作区内的文件）",
        params=[
            {"name": "path", "type": "string", "description": "工作区相对路径", "required": True},
            {"name": "name", "type": "string", "description": "展示文件名，可空"},
        ],
        tags=["message", "file", "write"],
        side_effects="message",
        requires=("event", "fs.read"),
    )
    async def skill_message_send_file(
        self, ctx: SkillContext, path: str, name: str = ""
    ) -> SkillResult:
        api = qq_api(ctx)
        kind, target = resolve_target(ctx)
        if api is None or not kind:
            return SkillResult.fail("当前没有可发送的会话")
        if ctx.workspace is None:
            return SkillResult.fail("当前没有工作区")
        try:
            file_path = ctx.workspace.path(path)
        except WorkspaceError as exc:
            return SkillResult.fail(str(exc))
        if not file_path.is_file():
            return SkillResult.fail(f"文件不存在：{path}")
        try:
            if kind == "group":
                result = await api.send_group_file(target, str(file_path), name=name or file_path.name)
            else:
                result = await api.send_private_file(target, str(file_path), name=name or file_path.name)
        except Exception as exc:
            return SkillResult.fail(f"发送失败：{type(exc).__name__}: {exc}")
        return SkillResult(ok=True, content="文件已发送", data={"message_id": _message_id(result)})
