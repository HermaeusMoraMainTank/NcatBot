"""事件日志级别解析。"""

import logging
from typing import Dict, Optional


def resolve_event_log_level(
    event_type: str,
    overrides: Dict[str, str],
) -> Optional[int]:
    """根据事件类型字符串和配置覆盖表，返回应使用的日志级别。

    匹配策略：精确匹配优先 → 前缀匹配（如 ``"meta_event"`` 匹配
    ``"meta_event.heartbeat"``）。无匹配时返回 ``logging.INFO``。

    Args:
        event_type: 事件类型字符串，如 ``"meta_event.heartbeat"``。
        overrides:  配置映射，键为事件类型（前缀），值为级别字符串。
                    值已由 Pydantic 验证器归一化为大写。

    Returns:
        日志级别整数值，``None`` 表示不记录（对应 ``"NONE"``）。
    """
    if not overrides:
        return logging.INFO

    # 精确匹配
    if event_type in overrides:
        return _to_level(overrides[event_type])

    # 前缀匹配：按键长度降序，取最长匹配
    best: Optional[str] = None
    for key in overrides:
        if event_type.startswith(key + ".") or event_type == key:
            if best is None or len(key) > len(best):
                best = key
    if best is not None:
        return _to_level(overrides[best])

    return logging.INFO


def _to_level(level_str: str) -> Optional[int]:
    """将级别字符串转换为 logging 整数值，'NONE' 返回 None。"""
    if level_str == "NONE":
        return None
    return getattr(logging, level_str, logging.INFO)
