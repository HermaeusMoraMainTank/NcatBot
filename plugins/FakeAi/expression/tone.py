"""人味后处理：把已经生成好的短句往「打字打出来的」方向掰一点点。

灵感与规则来自 dsh-fish-tone（MIT，语料统计见其 corpus-report）：
B 站 10 万条评论里 81.3% 的句子没有结尾标点，语病密度只有 1.65/千字。
也就是说——**真人不是句子写得好，是几乎不加修饰**。

这里只做三件低风险的事：
1. 去掉句末的「。」（最大的人味信号，且几乎不会改变语义）
2. 建议句尾挂「吧」、评价句尾挂「的」（都要过前置条件，避免「你干什么了吧」）
3. 极少量的句首犹豫（嗯……/呃）

代码、路径、URL、数字、表格、列表一律不碰；短句（<4 字）、纯符号句也不碰。
"""

from __future__ import annotations

import random
import re
from typing import Iterable, List, Optional, Tuple

# —— 结构保护：整行不处理 ——
STRUCTURAL = re.compile(r"^\s*(#{1,6}\s|[-*+]\s|\d+[.)]\s|\||>|```|~~~)")
LOOKS_LIKE_CODE = re.compile(
    r"^\s*(\$|\.{0,2}/|~/|[A-Za-z]:\\|npm |pnpm |git |node |python|uv |pip )"
)
# —— 句子内保护：命中就整句跳过 ——
PROTECTED_SPAN = re.compile(
    r"(```|`[^`]+`|https?://\S+|/[\w.-]+/[\w./-]*|--[\w-]{2,}|@[A-Za-z0-9_-]+)"
)
CJK = re.compile(r"[\u4e00-\u9fff]")

SENTENCE_RE = re.compile(r"([^。！？!?…；;]+)([。！？!?…；;]*)")

# 建议句：只有这种句子挂「吧」才像人话
SUGGEST_HEAD = re.compile(r"^\s*(那就|就这样|先|不如|要不|还是|干脆|直接|咱|建议|不妨|再|快)")
SUGGEST_TAIL = re.compile(r"(一下|看看|试试|想想|再说|加油)$")

# 评价句：程度副词 + 形容词收尾，才挂得起「的」
DEGREE = re.compile(r"(挺|很|蛮|特别|非常|太|真|有点|够|最|更)")
ADJ_TAIL = set("好对行差快慢大小多少难易累忙怪冷热香甜苦远近高低久早晚真假强弱")
ADJ_WORDS = {
    "麻烦", "好看", "好听", "好玩", "好吃", "好用", "舒服", "离谱", "抽象",
    "恶心", "省事", "靠谱", "闹心", "上头", "带感", "漂亮", "干净", "简单",
    "复杂", "稳定", "流畅", "粗糙", "无聊", "有意思", "没意思",
}
ALREADY_SOFT = set("吧的啦嘛啊呀哦呢么吗呗")

HESITATIONS = ("嗯……", "呃，", "……")

# 每种改动的触发概率（按句）。实测语病密度 1.65/千字，宁少勿多。
_RATES = {
    "light": {"吧": 0.16, "的": 0.10, "犹豫": 0.03},
    "normal": {"吧": 0.26, "的": 0.16, "犹豫": 0.05},
    "max": {"吧": 0.34, "的": 0.22, "犹豫": 0.08},
}


def _is_suggestion(s: str) -> bool:
    return bool(SUGGEST_HEAD.search(s) or SUGGEST_TAIL.search(s))


def _can_take_de(s: str) -> bool:
    if not (s[-1] in ADJ_TAIL or s[-2:] in ADJ_WORDS):
        return False
    if not DEGREE.search(s):
        return False
    if "比" in s or "不如" in s:
        return False
    if "的" in s:
        return False
    return True


def _splittable(s: str) -> bool:
    if len(s) < 4:
        return False
    if not CJK.search(s):
        return False
    if PROTECTED_SPAN.search(s):
        return False
    last = s[-1]
    if last in ALREADY_SOFT:
        return False
    if last in "：:，,、；;（(【[「":
        return False
    if last.isdigit() or re.match(r"[A-Za-z]", last):
        return False
    return True


def _decorate(
    body: str, sep: str, rng, rates: dict
) -> Tuple[str, str]:
    s = body.strip()
    if not s:
        return body, sep

    # 1) 句末句号：81.3% 的人不打。这个改动最划算，无脑去掉。
    if sep == "。":
        sep = ""

    if not _splittable(s):
        return body, sep

    # 已经有语气词/问号/感叹号就不加尾巴
    if sep:
        return body, sep

    if _is_suggestion(s) and rng.random() < rates["吧"]:
        return body + "吧", sep

    if _can_take_de(s) and rng.random() < rates["的"]:
        return body + "的", sep

    return body, sep


def apply_tone(
    text: str,
    strength: str = "light",
    *,
    seed: Optional[int] = None,
) -> str:
    """给一段文本加一点点人味；不改结构和语义。"""
    if not text:
        return text
    strength = (strength or "light").lower()
    if strength == "off":
        return text
    rates = _RATES.get(strength) or _RATES["light"]
    rng = random.Random(seed) if seed is not None else random

    out_lines: List[str] = []
    for line in str(text).split("\n"):
        if not line.strip() or STRUCTURAL.match(line) or LOOKS_LIKE_CODE.match(line):
            out_lines.append(line)
            continue
        if PROTECTED_SPAN.search(line):
            out_lines.append(line)
            continue

        pieces: List[str] = []
        for body, sep in SENTENCE_RE.findall(line):
            new_body, new_sep = _decorate(body, sep, rng, rates)
            pieces.append(new_body + new_sep)
        result = "".join(pieces) if pieces else line

        # 犹豫只在整句够长时加，且一轮最多一次
        if (
            strength in ("normal", "max")
            and len(result) >= 8
            and rng.random() < rates["犹豫"]
            and not result.startswith(HESITATIONS)
        ):
            result = rng.choice(HESITATIONS) + result
        out_lines.append(result)

    return "\n".join(out_lines)


def apply_tone_many(
    texts: Iterable[str], strength: str = "light", *, seed: Optional[int] = None
) -> List[str]:
    return [apply_tone(t, strength, seed=seed) for t in texts]
