"""TodayWaifu 插件入口（独立模块名，避免热重载复用失败残留的旧代码）。"""

from __future__ import annotations

import sys

_pkg = __package__ or "TodayWaifu"
for _key in list(sys.modules):
    if _key.startswith(_pkg + ".") and _key != __name__:
        del sys.modules[_key]

from .TodayWaifu import TodayWaifu  # noqa: E402

__all__ = ["TodayWaifu"]
