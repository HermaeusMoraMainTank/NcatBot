"""插件命令前缀与帮助文本的轻量工具。"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

DEFAULT_HELP_EXTRA = frozenset({"帮助", "help"})


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
    extra_triggers: Iterable[str] = DEFAULT_HELP_EXTRA,
) -> bool:
    """匹配 `<命令> 帮助` / `<命令> help`，或单独的「帮助」「help」。"""
    t = (text or "").strip()
    if not t:
        return False
    lower = t.lower()
    extras = set(extra_triggers) | DEFAULT_HELP_EXTRA
    if t in extras or lower in {x.lower() for x in extras}:
        return True
    for cmd in command_names:
        c = cmd.strip()
        if not c:
            continue
        if t == f"{c} 帮助" or lower == f"{c} help".lower():
            return True
    return False


def format_help(title: str, lines: Sequence[str], footer: str = "") -> str:
    body = "\n".join(line for line in lines if line)
    text = f"{title}\n{body}" if body else title
    if footer:
        text = f"{text}\n{footer}"
    return text
