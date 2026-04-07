"""QQ CQ 码解析"""

from __future__ import annotations

import re
from typing import Dict, List, Union

__all__ = [
    "parse_cq_code_to_onebot11",
]


def parse_cq_code_to_onebot11(
    cq_string: str,
) -> List[Dict[str, Union[str, Dict[str, str]]]]:
    """将 CQ 码字符串解析为 OB11 消息数组格式"""
    cq_pattern = re.compile(r"\[CQ:([^,\]]+)(?:,([^\]]+))?\]")
    segments: List[Dict[str, Union[str, Dict[str, str]]]] = []
    last_pos = 0
    html_unescape_map = {"&amp;": "&", "&#91;": "[", "&#93;": "]", "&#44;": ","}

    def unescape_cq(text: str) -> str:
        for escaped, unescaped in html_unescape_map.items():
            text = text.replace(escaped, unescaped)
        return text

    for match in cq_pattern.finditer(cq_string):
        text_before = cq_string[last_pos : match.start()]
        if text_before:
            segments.append(
                {"type": "text", "data": {"text": unescape_cq(text_before)}}
            )
        cq_type = match.group(1)
        cq_params_str = match.group(2) or ""
        params: Dict[str, str] = {}
        for param in cq_params_str.split(","):
            if "=" in param:
                key, value = param.split("=", 1)
                params[key] = unescape_cq(value)
        segments.append({"type": cq_type, "data": params})
        last_pos = match.end()

    text_after = cq_string[last_pos:]
    if text_after:
        segments.append({"type": "text", "data": {"text": unescape_cq(text_after)}})
    return segments
