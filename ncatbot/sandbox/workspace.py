"""受限工作区：所有文件型 skill 只能在这里读写。"""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Iterable

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")


class WorkspaceError(Exception):
    """路径越界、配额超限等。"""


def safe_segment(value: str, fallback: str = "run") -> str:
    value = _SAFE_NAME.sub("_", str(value or "").strip()).strip("._")
    return (value or fallback)[:80]


class Workspace:
    """一个 run / session 的沙箱目录。

    所有相对路径都先 resolve，再校验是否仍在 root 内；
    绝对路径、``..``、指向外部的符号链接都会被拒绝。
    """

    def __init__(
        self,
        root: Path | str,
        *,
        max_files: int = 200,
        max_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_files = max_files
        self.max_bytes = max_bytes

    # ------------------------------------------------------------------

    def path(self, relative: str = "") -> Path:
        rel = str(relative or "").replace("\\", "/").strip()
        if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
            raise WorkspaceError("只允许工作区内的相对路径")
        candidate = (self.root / rel).resolve() if rel else self.root
        if candidate != self.root and not candidate.is_relative_to(self.root):
            raise WorkspaceError("路径越出工作区")
        # resolve 后仍要防符号链接逃逸
        if candidate.exists() and candidate.is_symlink():
            raise WorkspaceError("不允许操作符号链接")
        return candidate

    def ensure_parent(self, relative: str) -> Path:
        target = self.path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.parent.resolve().is_relative_to(self.root):
            raise WorkspaceError("父目录越出工作区")
        return target

    def _check_quota(self, extra_bytes: int = 0) -> None:
        files = 0
        total = 0
        for entry in self.root.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                files += 1
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
        if files + 1 > self.max_files:
            raise WorkspaceError(f"工作区文件数超过上限 {self.max_files}")
        if total + extra_bytes > self.max_bytes:
            raise WorkspaceError(f"工作区体积超过上限 {self.max_bytes // 1024 // 1024}MB")

    def write_text(self, relative: str, text: str, *, encoding: str = "utf-8") -> Path:
        data = str(text).encode(encoding)
        return self.write_bytes(relative, data)

    def write_bytes(self, relative: str, data: bytes) -> Path:
        self._check_quota(len(data))
        target = self.ensure_parent(relative)
        target.write_bytes(data)
        return target

    def read_text(self, relative: str, *, encoding: str = "utf-8", limit: int = 200_000) -> str:
        target = self.path(relative)
        if not target.is_file():
            raise WorkspaceError(f"文件不存在：{relative}")
        with target.open("rb") as fh:
            raw = fh.read(limit + 1)
        if len(raw) > limit:
            raw = raw[:limit]
            return raw.decode(encoding, errors="replace") + "\n…（已截断）"
        return raw.decode(encoding, errors="replace")

    def read_bytes(self, relative: str, *, limit: int = 20 * 1024 * 1024) -> bytes:
        target = self.path(relative)
        if not target.is_file():
            raise WorkspaceError(f"文件不存在：{relative}")
        return target.read_bytes()[:limit]

    def list_files(self, relative: str = "", *, max_items: int = 100) -> list[dict]:
        base = self.path(relative)
        if not base.exists():
            return []
        items: list[dict] = []
        for entry in sorted(base.rglob("*")):
            if entry.is_symlink() or not entry.is_file():
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            items.append(
                {
                    "path": entry.relative_to(self.root).as_posix(),
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                }
            )
            if len(items) >= max_items:
                break
        return items

    def delete(self, relative: str) -> bool:
        target = self.path(relative)
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            return True
        try:
            target.unlink()
            return True
        except OSError:
            return False

    def clear(self) -> None:
        for entry in self.root.iterdir():
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                try:
                    entry.unlink()
                except OSError:
                    pass

    def size(self) -> dict[str, int]:
        files = 0
        total = 0
        for entry in self.root.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                files += 1
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
        return {"files": files, "bytes": total}


class WorkspaceManager:
    """按 session / run 分配工作区，并清理过期目录。"""

    def __init__(
        self,
        root: Path | str = "data/sandbox",
        *,
        max_files: int = 200,
        max_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_files = max_files
        self.max_bytes = max_bytes

    def get(self, key: str) -> Workspace:
        return Workspace(
            self.root / safe_segment(key),
            max_files=self.max_files,
            max_bytes=self.max_bytes,
        )

    def list_workspaces(self) -> list[dict]:
        out = []
        for entry in sorted(self.root.iterdir()):
            if not entry.is_dir():
                continue
            ws = Workspace(entry, max_files=self.max_files, max_bytes=self.max_bytes)
            stat = ws.size()
            out.append(
                {
                    "name": entry.name,
                    "path": str(entry),
                    "files": stat["files"],
                    "bytes": stat["bytes"],
                    "mtime": entry.stat().st_mtime,
                }
            )
        return out

    def cleanup(self, *, max_age_seconds: float = 7 * 86400, keep: Iterable[str] = ()) -> int:
        keep_set = set(keep)
        cutoff = time.time() - max_age_seconds
        removed = 0
        for entry in self.root.iterdir():
            if not entry.is_dir() or entry.name in keep_set:
                continue
            try:
                newest = max(
                    (p.stat().st_mtime for p in entry.rglob("*") if p.is_file()),
                    default=entry.stat().st_mtime,
                )
            except OSError:
                newest = entry.stat().st_mtime
            if newest < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        return removed


def atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
