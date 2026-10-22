"""子智能体：把一个任务丢给受限工作区里的子 agent。"""

from __future__ import annotations

import shutil
from pathlib import Path

from ncatbot.skill import SkillContext, SkillResult, skill

_CHILD_SKILLS = (
    "time.now",
    "file.write_text",
    "file.read_text",
    "file.list",
    "file.delete",
    "web.search",
    "web.fetch",
    "image.text",
)


class AgentSkills:
    @skill(
        "agent.spawn",
        summary="拉起一个子智能体完成任务（产出文件会带回当前工作区）",
        description=(
            "子智能体只能用文件/联网/文字图能力，不能发消息、不能继续派活。"
            "适合写文件、整理资料、生成一段脚本或文本。完成后返回结果和产物路径。"
        ),
        params=[
            {"name": "task", "type": "string", "description": "要子智能体完成的任务", "required": True},
        ],
        tags=["agent", "write"],
        side_effects="write",
        requires=("llm", "fs.write"),
        timeout=240,
    )
    async def skill_agent_spawn(self, ctx: SkillContext, task: str) -> SkillResult:
        runtime = ctx.runtime
        if runtime is None:
            return SkillResult.fail("当前没有 agent 运行时")
        task = str(task or "").strip()
        if not task:
            return SkillResult.fail("任务为空")
        if ctx.workspace is None:
            return SkillResult.fail("当前没有工作区")

        from ncatbot.agent import AgentContext

        parent = AgentContext(
            session_key=ctx.session_key or "skillhub",
            user_id=ctx.user_id,
            group_id=ctx.group_id,
            roles={"user"},
            event=None,
            api=None,
        )
        child = await runtime.run_child(
            task,
            parent=parent,
            parent_id=ctx.run_id or "skillhub",
            allowed_skills=_CHILD_SKILLS,
        )
        if child.status != "done":
            return SkillResult.fail(
                f"子智能体未完成（{child.status}）：{child.error or child.result}",
                data={"run_id": child.id, "status": child.status},
            )

        copied = []
        for index, artifact in enumerate(child.artifacts):
            raw = artifact.get("abs_path") if isinstance(artifact, dict) else None
            raw = raw or (
                artifact.get("path") if isinstance(artifact, dict) else None
            )
            if not raw:
                continue
            source = Path(raw)
            if not source.is_absolute():
                source = Path(child.workspace) / raw
            if not source.is_file():
                continue
            name = source.name or f"artifact-{index}"
            relative = f"sub/{child.id}/{name}"
            try:
                target = ctx.workspace.ensure_parent(relative)
                shutil.copy2(source, target)
            except Exception:
                continue
            copied.append(
                {
                    **artifact,
                    "path": relative,
                    "abs_path": str(target),
                    "name": name,
                }
            )

        lines = [child.result or "子智能体已完成"]
        if copied:
            lines.append("产物：" + "、".join(f"sub/{child.id}/{item['name']}" for item in copied))
        return SkillResult(
            ok=True,
            content="\n".join(lines),
            data={"run_id": child.id, "status": child.status},
            artifacts=copied,
        )
