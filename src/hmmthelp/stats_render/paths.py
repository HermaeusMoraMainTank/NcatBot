import os
import time
from pathlib import Path
from typing import Optional

RESOURCES_PATH: Path = Path("data/stats_render/resources")
TEMP_PATH: Path = Path("data/stats_render/temp")


def ensure_dirs() -> None:
    TEMP_PATH.mkdir(parents=True, exist_ok=True)


def cleanup_temp(keep_hours: int = 24, max_files: int = 50) -> int:
    """清理过期 temp 渲染文件。

    Args:
        keep_hours: 保留最近多少小时内的文件，默认 24 小时
        max_files: 即使未过期，也最多保留多少个文件，超出则删除旧的

    Returns:
        删除的文件数量
    """
    if not TEMP_PATH.exists():
        return 0

    now = time.time()
    cutoff = now - keep_hours * 3600
    deleted = 0

    # 先按过期时间删除
    for f in TEMP_PATH.iterdir():
        if f.is_file():
            if f.stat().st_mtime < cutoff:
                try:
                    f.unlink()
                    deleted += 1
                except OSError:
                    pass

    # 如果还有超过 max_files 的文件，删除最旧的
    remaining = sorted(
        [f for f in TEMP_PATH.iterdir() if f.is_file()],
        key=lambda f: f.stat().st_mtime,
    )
    while len(remaining) > max_files:
        try:
            remaining[0].unlink()
            deleted += 1
            remaining.pop(0)
        except OSError:
            remaining.pop(0)

    return deleted
