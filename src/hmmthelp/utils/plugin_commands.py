"""插件命令前缀与帮助文本的轻量工具。

帮助请求必须绑定到具体命令，例如 ``钓鱼帮助``；裸 ``帮助`` 不属于任何插件。
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

HELP_SUFFIXES = ("帮助", "help")
HELP_SEPARATORS = ("", " ")


def cfg_value(plugin: Any, key: str, default: Any, defaults: dict[str, Any]) -> Any:
    if hasattr(plugin, "get_config"):
        return plugin.get_config(key, defaults.get(key, default))
    return defaults.get(key, default)


def cfg_str(plugin: Any, key: str, default: str, defaults: dict[str, Any]) -> str:
    raw = cfg_value(plugin, key, default, defaults)
    text = str(raw).strip()
    return text or default


def cfg_str_list(
    plugin: Any,
    key: str,
    default: Sequence[str],
    defaults: dict[str, Any],
) -> list[str]:
    raw = cfg_value(plugin, key, default, defaults)
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.replace("，", ",").split(",") if p.strip()]
        return parts or list(default)
    if isinstance(raw, (list, tuple, set, frozenset)):
        return [str(x).strip() for x in raw if str(x).strip()] or list(default)
    return list(default)


def is_help_message(
    text: str,
    *,
    command_names: Iterable[str] = (),
) -> bool:
    """匹配具体命令的帮助请求。

    只有 ``<命令>帮助`` / ``<命令> 帮助`` 和对应的 ``help`` 形式会匹配；
    裸 ``帮助`` / ``help`` 必须被拒绝，避免一条通用消息唤起所有插件。
    """
    t = (text or "").strip()
    if not t:
        return False
    lower = t.lower()
    for cmd in command_names:
        c = cmd.strip()
        if not c:
            continue
        for separator in HELP_SEPARATORS:
            for suffix in HELP_SUFFIXES:
                expected = f"{c}{separator}{suffix}"
                if t == expected or lower == expected.lower():
                    return True
    return False


def format_help(title: str, lines: Sequence[str], footer: str = "") -> str:
    body = "\n".join(line for line in lines if line)
    text = f"{title}\n{body}" if body else title
    if footer:
        text = f"{text}\n{footer}"
    return text
