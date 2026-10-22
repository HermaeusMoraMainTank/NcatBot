"""可选的高风险能力：在受限工作区里跑一个程序。

默认关闭（sandbox.allow_subprocess=false）。
打开后也只有 owner 角色可用；argv 必须是列表，不经过 shell。
Linux 上若安装了 bwrap，会自动用命名空间隔离（只读系统目录、独立 /tmp、断网）。
"""

from __future__ import annotations

from ncatbot.skill import SkillContext, SkillResult, skill


class SandboxSkills:
    @skill(
        "sandbox.status",
        summary="查看子进程沙箱后端与开关",
        params=[],
        tags=["sandbox", "read"],
        side_effects="none",
    )
    async def skill_sandbox_status(self, ctx: SkillContext) -> SkillResult:
        runner = ctx.sandbox
        if runner is None:
            return SkillResult.fail("当前没有沙箱")
        return SkillResult(ok=True, content="", data=runner.describe())

    @skill(
        "sandbox.run",
        summary="在受限工作区执行一个程序（默认关闭）",
        description=(
            "argv 是参数列表，例如 [\"python\",\"script.py\"]；不经过 shell。"
            "只有开启 sandbox.allow_subprocess 且调用者是 owner 时可用。"
        ),
        params=[
            {
                "name": "argv",
                "type": "array",
                "items": {"type": "string"},
                "description": "命令与参数列表",
                "required": True,
            },
            {"name": "timeout", "type": "number", "description": "超时秒数，默认 30"},
        ],
        tags=["sandbox", "write"],
        side_effects="external",
        scopes=("owner",),
        requires=("subprocess", "fs.write"),
        timeout=150,
    )
    async def skill_sandbox_run(
        self, ctx: SkillContext, argv: list[str], timeout: float = 30
    ) -> SkillResult:
        runner = ctx.sandbox
        if runner is None:
            return SkillResult.fail("当前没有沙箱")
        if ctx.workspace is None:
            return SkillResult.fail("当前没有工作区")
        if not runner.allow_subprocess:
            return SkillResult.fail("sandbox.allow_subprocess 未开启")
        if not isinstance(argv, list) or not argv:
            return SkillResult.fail("argv 必须是非空字符串数组")
        argv = [str(item) for item in argv][:32]
        timeout = max(1.0, min(120.0, float(timeout or 30)))
        result = await runner.run(
            argv,
            cwd=ctx.workspace.root,
            timeout=timeout,
        )
        output = (result.stdout or "")[:4000]
        error = (result.stderr or "")[:2000]
        content = f"exit={result.returncode} backend={result.backend}"
        if result.timed_out:
            content += "（超时已终止）"
        if output:
            content += f"\nstdout:\n{output}"
        if error:
            content += f"\nstderr:\n{error}"
        return SkillResult(
            ok=result.ok,
            content=content,
            error="" if result.ok else (error or f"exit={result.returncode}"),
            data={"returncode": result.returncode, "backend": result.backend},
        )
