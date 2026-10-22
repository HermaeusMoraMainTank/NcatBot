"""基础能力：时间、工作区文件、联网。"""

from __future__ import annotations

import time

from ncatbot.sandbox import WorkspaceError
from ncatbot.skill import SkillContext, SkillResult, skill


class FileWebSkills:
    """文件与网络类 skill（不直接发消息，副作用可控）。"""

    @skill(
        "time.now",
        summary="获取当前日期和时间",
        params=[],
        tags=["system", "read"],
        side_effects="none",
    )
    async def skill_time_now(self, ctx: SkillContext) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S %A")

    @skill(
        "file.write_text",
        summary="在工作区写一个文本文件",
        description="只能写当前任务的工作区；文件名用相对路径，例如 notes.txt 或 sub/a.md。",
        params=[
            {"name": "filename", "type": "string", "description": "相对路径", "required": True},
            {"name": "content", "type": "string", "description": "文本内容", "required": True},
            {"name": "append", "type": "boolean", "description": "是否追加，默认覆盖"},
        ],
        tags=["file", "write"],
        side_effects="write",
        requires=("fs.write",),
    )
    async def skill_file_write(
        self, ctx: SkillContext, filename: str, content: str, append: bool = False
    ) -> SkillResult:
        if ctx.workspace is None:
            return SkillResult.fail("当前没有工作区")
        try:
            if append:
                old = ""
                try:
                    old = ctx.workspace.read_text(filename)
                except WorkspaceError:
                    old = ""
                path = ctx.workspace.write_text(filename, old + content)
            else:
                path = ctx.workspace.write_text(filename, content)
        except WorkspaceError as exc:
            return SkillResult.fail(str(exc))
        size = len(content.encode("utf-8"))
        relative = str(path.relative_to(ctx.workspace.root)).replace("\\", "/")
        return SkillResult(
            ok=True,
            content=f"已写入 {relative}（{size} bytes）",
            artifacts=[
                {
                    "kind": "file",
                    "name": path.name,
                    "path": relative,
                    "abs_path": str(path),
                    "size": size,
                }
            ],
        )

    @skill(
        "file.read_text",
        summary="读取工作区内的文本文件",
        params=[{"name": "filename", "type": "string", "description": "相对路径", "required": True}],
        tags=["file", "read"],
        side_effects="read",
        requires=("fs.read",),
    )
    async def skill_file_read(self, ctx: SkillContext, filename: str) -> SkillResult:
        if ctx.workspace is None:
            return SkillResult.fail("当前没有工作区")
        try:
            return SkillResult.text(ctx.workspace.read_text(filename))
        except WorkspaceError as exc:
            return SkillResult.fail(f"读取失败：{exc}")

    @skill(
        "file.list",
        summary="列出工作区内的文件",
        params=[{"name": "path", "type": "string", "description": "子目录，可空"}],
        tags=["file", "read"],
        side_effects="read",
        requires=("fs.read",),
    )
    async def skill_file_list(self, ctx: SkillContext, path: str = "") -> SkillResult:
        if ctx.workspace is None:
            return SkillResult.fail("当前没有工作区")
        try:
            items = ctx.workspace.list_files(path)
        except WorkspaceError as exc:
            return SkillResult.fail(str(exc))
        if not items:
            return SkillResult(ok=True, content="工作区是空的", data=[])
        lines = [f"{item['path']} ({item['size']} bytes)" for item in items]
        return SkillResult(ok=True, content="\n".join(lines), data=items)

    @skill(
        "file.delete",
        summary="删除工作区内的文件",
        params=[{"name": "filename", "type": "string", "description": "相对路径", "required": True}],
        tags=["file", "write"],
        side_effects="write",
        requires=("fs.write",),
    )
    async def skill_file_delete(self, ctx: SkillContext, filename: str) -> str:
        if ctx.workspace is None:
            return "当前没有工作区"
        try:
            return "已删除" if ctx.workspace.delete(filename) else "删除失败"
        except WorkspaceError as exc:
            return f"删除失败：{exc}"

    @skill(
        "web.search",
        summary="联网搜索，返回标题/链接/摘要",
        params=[
            {"name": "query", "type": "string", "description": "搜索词", "required": True},
            {"name": "count", "type": "integer", "description": "条数，默认 5"},
        ],
        tags=["web", "read"],
        side_effects="external",
        requires=("network",),
        timeout=45,
    )
    async def skill_web_search(self, ctx: SkillContext, query: str, count: int = 5) -> SkillResult:
        from common.utils.AiUtil import AiUtil

        data = await AiUtil.search_web(query, count=max(1, min(10, count)))
        items = [
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "summary": item.get("summary"),
                "date": item.get("date"),
            }
            for item in (data.get("results") or [])
        ]
        if not items:
            return SkillResult.fail("没有搜到结果")
        lines = [f"- {i.get('title')}\n  {i.get('url')}\n  {(i.get('summary') or '')[:180]}" for i in items]
        return SkillResult(ok=True, content="\n".join(lines), data=items)

    @skill(
        "web.fetch",
        summary="抓取网页正文（Markdown）",
        params=[{"name": "url", "type": "string", "description": "完整 URL", "required": True}],
        tags=["web", "read"],
        side_effects="external",
        requires=("network",),
        timeout=45,
    )
    async def skill_web_fetch(self, ctx: SkillContext, url: str) -> str:
        from common.utils.AiUtil import AiUtil

        if not url.startswith(("http://", "https://")):
            return "只支持 http/https URL"
        data = await AiUtil.fetch_web(url)
        title = data.get("title") or ""
        content = (data.get("content") or "")[:12000]
        return f"{title}\n\n{content}" if title else content
