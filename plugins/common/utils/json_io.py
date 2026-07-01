"""JSON 持久化工具：项目根路径解析 + 原子写入。"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional, Type

_project_root: Optional[Path] = None
_root_lock = threading.Lock()


def get_project_root() -> Path:
    """定位项目根目录（含 pyproject.toml），避免 cwd 变化导致读写错位。"""
    global _project_root
    if _project_root is not None:
        return _project_root
    with _root_lock:
        if _project_root is not None:
            return _project_root
        candidates = [Path.cwd(), Path(__file__).resolve()]
        for base in candidates:
            for p in [base, *base.parents]:
                if (p / "pyproject.toml").exists():
                    _project_root = p
                    return _project_root
        _project_root = Path.cwd()
        return _project_root


def resolve_data_json(name: str) -> str:
    """
    解析 data/json 下的文件绝对路径。
    name 可为 ``message_group_stats.json`` 或 ``data/json/...``。
    """
    rel = name.replace("\\", "/").lstrip("/")
    if rel.startswith("data/json/"):
        return str(get_project_root() / rel)
    if rel.startswith("data/"):
        return str(get_project_root() / rel)
    return str(get_project_root() / "data" / "json" / rel)


def atomic_write_json(
    path: str,
    data: Any,
    *,
    encoder: Optional[Type[json.JSONEncoder]] = None,
    indent: int = 2,
) -> bool:
    """先写临时文件再替换，降低进程异常退出时 JSON 损坏的概率。"""
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            suffix=".tmp",
            prefix=f"{target.stem}_",
            dir=str(target.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=indent, cls=encoder)
            os.replace(tmp_path, target)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return True
    except Exception:
        return False


def load_json(path: str, default: dict) -> dict:
    if not os.path.exists(path):
        return default.copy()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default.copy()
