"""将兼容层注入为 astrbot.* 模块，尽量不改上游 core 代码。"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

from ncatbot.utils import get_log

_log = get_log()
_installed = False


class AstrBotConfig(dict):
    """dict 子类，兼容 .get 嵌套用法。"""

    pass


class _Logger:
    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        _log.info(msg, *args)

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        _log.warning(msg, *args)

    def warn(self, msg: str, *args: Any, **kwargs: Any) -> None:
        _log.warning(msg, *args)

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        if kwargs.get("exc_info"):
            _log.exception(msg)
        else:
            _log.error(msg, *args)

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        _log.debug(msg, *args)


class StarTools:
    _data_dir: Path | None = None

    @classmethod
    def set_data_dir(cls, path: Path) -> None:
        cls._data_dir = path
        path.mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_data_dir(cls, name: str) -> Path:
        base = cls._data_dir or Path(__file__).resolve().parent.parent / "data"
        path = base
        path.mkdir(parents=True, exist_ok=True)
        return path


def _ensure_pkg(name: str) -> types.ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    mod = types.ModuleType(name)
    mod.__path__ = []  # type: ignore[attr-defined]
    sys.modules[name] = mod
    return mod


def install_shim(plugin_dir: Path) -> None:
    global _installed
    if _installed:
        return

    from . import html_renderer
    from . import message_components
    from . import session_waiter
    from .event import AstrMessageEvent

    html_renderer.set_output_dir(plugin_dir / "data" / "output")
    StarTools.set_data_dir(plugin_dir / "data")

    logger = _Logger()

    # astrbot
    _ensure_pkg("astrbot")
    api = _ensure_pkg("astrbot.api")
    api_event = _ensure_pkg("astrbot.api.event")
    api_star = _ensure_pkg("astrbot.api.star")
    api_mc = _ensure_pkg("astrbot.api.message_components")
    core = _ensure_pkg("astrbot.core")
    _ensure_pkg("astrbot.core.utils")
    core_sw = _ensure_pkg("astrbot.core.utils.session_waiter")
    _ensure_pkg("astrbot.core.message")
    core_mer = _ensure_pkg("astrbot.core.message.message_event_result")
    _ensure_pkg("astrbot.core.platform")
    core_ms = _ensure_pkg("astrbot.core.platform.message_session")
    _ensure_pkg("astrbot.core.star")
    _ensure_pkg("astrbot.core.star.filter")
    core_cmd = _ensure_pkg("astrbot.core.star.filter.command")

    # api exports
    api.AstrBotConfig = AstrBotConfig
    api.logger = logger
    api.html_renderer = html_renderer
    api.message_components = message_components

    api_event.AstrMessageEvent = AstrMessageEvent
    api_event.filter = types.SimpleNamespace()

    api_star.Context = object
    api_star.Star = object
    api_star.StarTools = StarTools

    for name in (
        "BaseMessageComponent",
        "Plain",
        "Reply",
        "Image",
        "Node",
        "Nodes",
        "MessageResult",
    ):
        setattr(api_mc, name, getattr(message_components, name))

    core.AstrBotConfig = AstrBotConfig

    core_sw.SessionController = session_waiter.SessionController
    core_sw.SessionFilter = session_waiter.SessionFilter
    core_sw.session_waiter = session_waiter.session_waiter

    class MessageChain:
        def __init__(self):
            self._parts: list = []

        def url_image(self, url: str) -> "MessageChain":
            self._parts.append(("image", url))
            return self

        def message(self, text: str) -> "MessageChain":
            self._parts.append(("text", text))
            return self

    core_mer.MessageChain = MessageChain

    class MessageSession:
        def __init__(self, raw: str):
            self.raw = raw

        @classmethod
        def from_str(cls, s: str) -> "MessageSession":
            return cls(s)

        def __str__(self) -> str:
            return self.raw

    core_ms.MessageSession = MessageSession

    class GreedyStr(str):
        pass

    core_cmd.GreedyStr = GreedyStr

    _installed = True
    _log.debug("[GalgameBox] astrbot shim installed")
