"""计算意图触发词检测。"""

from __future__ import annotations

import re

# 长词优先，避免「帮我算」只命中「帮我」
_TRIGGER_KEYWORDS: tuple[str, ...] = (
    "帮我计算",
    "帮忙计算",
    "帮我算",
    "帮忙算",
    "给我算",
    "给我计算",
    "等于多少",
    "结果多少",
    "结果是",
    "是多少",
    "得多少",
    "等于几",
    "算一下",
    "算算看",
    "计算",
    "验算",
    "口算",
    "开方",
    "算算",
    "算下",
    "算个",
    "求一下",
    "求值",
    "求解",
    "等于",
    "帮我",
    "帮忙",
    "求",
)

# 单独「算」，排除 打算 / 运算 / 算法 / 预算 / 核算 等
_SUAN_PATTERN = re.compile(r"(?<![打预核运])算(?![法术法评])")


def has_calc_trigger(text: str) -> bool:
    """消息是否带有「请计算」类意图。"""
    if not text:
        return False
    if any(keyword in text for keyword in _TRIGGER_KEYWORDS):
        return True
    return bool(_SUAN_PATTERN.search(text))
