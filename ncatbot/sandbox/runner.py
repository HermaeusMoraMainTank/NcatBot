"""无 Docker 的进程执行器：没有 shell、环境变量白名单、超时与输出上限。

默认不向任何 agent 暴露任意代码执行；只有显式启用 ``allow_subprocess``
的 skill 才会走到这里。Linux 上若存在 ``bwrap`` 会自动加上命名空间隔离。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ncatbot.utils import get_log

LOG = get_log("SandboxRunner")

_ENV_WHITELIST = (
    "PATH",
    "PYTHONPATH",
    "PYTHONIOENCODING",
    "LANG",
    "LC_ALL",
    "TZ",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
)


@dataclass
class RunResult:
    returncode: int = -1
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    backend: str = "process"
    argv: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "backend": self.backend,
        }


def _detect_bwrap() -> str | None:
    if sys.platform.startswith("linux"):
        path = shutil.which("bwrap")
        if path:
            return path
    return None


class SandboxRunner:
    """跨平台的最小执行沙箱。

    - 永远 ``create_subprocess_exec``，不经过 shell，argv 由调用方给列表
    - 环境变量只透传白名单，密钥不会进子进程
    - 超时后强杀；stdout/stderr 截断
    - Linux + bwrap 时：只读系统目录 + 只写工作区 + 断网（可选）
    """

    def __init__(
        self,
        *,
        allow_subprocess: bool = False,
        backend: str = "auto",
        max_output: int = 64_000,
        network: bool = False,
    ) -> None:
        self.allow_subprocess = allow_subprocess
        self.backend = backend
        self.max_output = max_output
        self.network = network
        self._bwrap = _detect_bwrap()

    def describe(self) -> dict[str, Any]:
        return {
            "allow_subprocess": self.allow_subprocess,
            "requested_backend": self.backend,
            "effective_backend": self._effective_backend(),
            "bwrap": self._bwrap,
            "network": self.network,
        }

    def _effective_backend(self) -> str:
        if not self.allow_subprocess:
            return "disabled"
        if self.backend == "bwrap":
            return "bwrap" if self._bwrap else "process"
        if self.backend == "auto":
            return "bwrap" if self._bwrap else "process"
        return "process"

    def _env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = {key: os.environ[key] for key in _ENV_WHITELIST if key in os.environ}
        env.update(
            {
                "PYTHONUNBUFFERED": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "NO_PROXY": "*",
                "no_proxy": "*",
            }
        )
        if extra:
            for key, value in extra.items():
                if not isinstance(value, str):
                    continue
                if key.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}:
                    continue
                env[key] = value
        return env

    def _wrap_bwrap(self, argv: list[str], cwd: Path) -> list[str]:
        assert self._bwrap
        base = [
            self._bwrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
        ]
        if not self.network:
            base.append("--unshare-net")
        for system_dir in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc"):
            if Path(system_dir).exists():
                base.extend(["--ro-bind", system_dir, system_dir])
        base.extend(["--bind", str(cwd), "/workspace", "--chdir", "/workspace"])
        return base + ["--"] + argv

    async def run(
        self,
        argv: list[str],
        *,
        cwd: Path | str,
        timeout: float = 30,
        env_extra: dict[str, str] | None = None,
    ) -> RunResult:
        if not self.allow_subprocess:
            return RunResult(
                returncode=-1,
                stderr="未启用子进程执行（sandbox.allow_subprocess=false）",
                backend="disabled",
                argv=list(argv),
            )
        if not argv or not isinstance(argv, list):
            return RunResult(returncode=-1, stderr="argv 必须是非空列表", backend="process")
        workdir = Path(cwd).resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        backend = self._effective_backend()
        command = self._wrap_bwrap(argv, workdir) if backend == "bwrap" else list(argv)
        exec_cwd = "/workspace" if backend == "bwrap" else str(workdir)

        kwargs: dict[str, Any] = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": exec_cwd,
            "env": self._env(env_extra),
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x00000200  # CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True

        LOG.info("sandbox exec backend=%s argv=%s cwd=%s", backend, argv[:1], workdir)
        try:
            proc = await asyncio.create_subprocess_exec(*command, **kwargs)
        except FileNotFoundError as exc:
            return RunResult(returncode=-1, stderr=f"命令不存在：{exc}", backend=backend, argv=argv)

        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                stdout, stderr = b"", b""
        except asyncio.CancelledError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise

        return RunResult(
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout=(stdout or b"").decode("utf-8", errors="replace")[: self.max_output],
            stderr=(stderr or b"").decode("utf-8", errors="replace")[: self.max_output],
            timed_out=timed_out,
            backend=backend,
            argv=list(argv),
        )
