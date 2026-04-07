"""BaseLoggingProxy — 可复用的异步调用日志代理"""

from __future__ import annotations

import asyncio
import functools
import json
from typing import Any

from ncatbot.utils import get_log

LOG = get_log("APIProxy")

_LOG_TRUNCATE = 2000


class BaseLoggingProxy:
    """通用日志代理，所有异步调用自动打 DEBUG 日志"""

    __slots__ = ("_real",)

    def __init__(self, real: Any) -> None:
        self._real = real

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._real, name)
        if not (asyncio.iscoroutinefunction(attr) or name.startswith("_")):
            return attr

        @functools.wraps(attr)
        async def _logged(*args: Any, **kwargs: Any) -> Any:
            s = _fmt_call(name, args, kwargs)
            LOG.debug("API调用 %s", s)
            return await attr(*args, **kwargs)

        return _logged


def _fmt_call(name: str, args: tuple, kwargs: dict) -> str:
    parts = [name]
    for v in args:
        parts.append(_trunc(v))
    for k, v in kwargs.items():
        parts.append(f"{k}={_trunc(v)}")
    s = " ".join(parts)
    if len(s) > _LOG_TRUNCATE:
        s = s[:_LOG_TRUNCATE] + "..."
    return s


def _trunc(v: Any) -> str:
    if isinstance(v, (dict, list)):
        s = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    else:
        s = str(v)
    if len(s) > _LOG_TRUNCATE:
        s = s[:_LOG_TRUNCATE] + "..."
    return s
