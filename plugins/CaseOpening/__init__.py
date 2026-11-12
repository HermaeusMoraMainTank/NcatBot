"""CaseOpening 插件入口。

共享数据层 `common.case_data` 在插件热重载时不会被框架重新导入，
如果它刚被改过（新增函数/常量），插件会因 ImportError 静默加载失败。
这里在导入插件前主动 reload 一次共享模块：冷启动无副作用，热重载能自愈。
"""

from __future__ import annotations

import importlib
import sys


def _reload_shared() -> None:
    names = [
        name
        for name in list(sys.modules)
        if name == "common.case_data" or name.startswith("common.case_data.")
    ]
    # 先 reload 子模块，再 reload 包本身
    for name in sorted(names, key=lambda item: item.count("."), reverse=True):
        module = sys.modules.get(name)
        if module is None:
            continue
        try:
            importlib.reload(module)
        except Exception as exc:  # noqa: BLE001 - 重载失败就按原样继续，交给下面的 import 报错
            print(f"[CaseOpening] reload {name} failed: {exc}")
            continue


_reload_shared()

from .CaseOpening import CaseOpening

__all__ = ["CaseOpening"]
