import os
import time
from pathlib import Path
from typing import Optional


def cleanup_old_files(
    directory: Path,
    keep_hours: int = 24,
    max_files: Optional[int] = None,
    extensions: Optional[tuple] = None,
) -> int:
    """清理指定目录下的旧文件。

    Args:
        directory: 目标目录
        keep_hours: 保留最近多少小时内的文件
        max_files: 最多保留文件数，超出则删除最旧的
        extensions: 只清理特定后缀的文件，如 ('.png', '.jpg')，None 表示所有

    Returns:
        删除的文件数量
    """
    if not directory.exists():
        return 0

    now = time.time()
    cutoff = now - keep_hours * 3600
    deleted = 0

    # 第一步：按过期时间删除
    for f in list(directory.iterdir()):
        if not f.is_file():
            continue
        if extensions and not f.suffix.lower().endswith(extensions):
            continue
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                deleted += 1
        except OSError:
            pass

    # 第二步：如果指定了 max_files，超出则删除最旧的
    if max_files is not None:
        remaining = sorted(
            [f for f in directory.iterdir() if f.is_file()],
            key=lambda f: f.stat().st_mtime,
        )
        while len(remaining) > max_files:
            try:
                remaining[0].unlink()
                deleted += 1
            except OSError:
                pass
            remaining.pop(0)

    return deleted
