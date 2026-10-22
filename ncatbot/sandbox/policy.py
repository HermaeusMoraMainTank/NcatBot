"""安全策略：密钥打码、配置脱敏、出向/入向检查。"""

from __future__ import annotations

import re
from typing import Any

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s,;\"']+"),
    re.compile(r"(?i)(https?://[^/\s:@]+:)[^/\s@]+(@)"),
]

_SENSITIVE_KEY = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|passwd|cookie|authorization|private[_-]?key)"
)


def scrub_secrets(text: Any) -> str:
    """把可能泄漏的密钥替换成 ***。用于模型输出、日志、审计。"""
    value = str(text or "")
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub(lambda m: m.group(0)[:0] + "***" if m.re.groups == 0 else _keep_prefix(m), value)
    return value


def _keep_prefix(match: re.Match) -> str:
    if match.lastindex:
        return "".join(g or "" for g in match.groups()) + "***"
    return "***"


def is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY.search(str(key or "")))


def mask_mapping(data: Any, *, depth: int = 0) -> Any:
    """对配置字典做展示层脱敏，保留结构便于前端编辑非敏感项。"""
    if depth > 6:
        return "…"
    if isinstance(data, dict):
        out = {}
        for key, value in data.items():
            if is_sensitive_key(key) and isinstance(value, str) and value:
                out[key] = "***"
            else:
                out[key] = mask_mapping(value, depth=depth + 1)
        return out
    if isinstance(data, list):
        return [mask_mapping(item, depth=depth + 1) for item in data[:50]]
    return data


def unmask_guard(updates: dict[str, Any]) -> dict[str, Any]:
    """前端把脱敏值原样提交时，不覆盖真实密钥（递归）。"""
    cleaned: dict[str, Any] = {}
    for key, value in updates.items():
        if value == "***":
            continue
        if isinstance(value, dict):
            cleaned[key] = unmask_guard(value)
        else:
            cleaned[key] = value
    return cleaned


def merge_keep_secrets(current: Any, updates: Any) -> Any:
    """把前端提交的嵌套配置合并进当前配置，保留被脱敏的旧密钥。"""
    if isinstance(current, dict) and isinstance(updates, dict):
        merged = dict(current)
        for key, value in updates.items():
            if value == "***":
                continue
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = merge_keep_secrets(merged[key], value)
            else:
                merged[key] = value
        return merged
    return updates


def redact_url(url: str) -> str:
    return re.sub(r"(?i)(https?://[^/\s:@]+:)[^/\s@]+(@)", r"\1***\2", str(url or ""))
