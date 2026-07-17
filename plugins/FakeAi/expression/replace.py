"""从 AI 正文剥离 :贴纸名: 并解析路径。"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Tuple

from .catalog import StickerCatalog, sticker_catalog

_log = logging.getLogger(__name__)

_STICKER_RE = re.compile(r":([^:\s]{1,32}):")


def extract_stickers(
    content: str,
    *,
    catalog: StickerCatalog | None = None,
    max_count: int = 1,
) -> Tuple[str, List[Path]]:
    """返回 (去标记后的正文, 贴纸路径列表)。未知标记删除并 warn。"""
    cat = catalog or sticker_catalog
    paths: List[Path] = []
    if not content:
        return content or "", paths

    def repl(match: re.Match) -> str:
        nonlocal paths
        name = match.group(1)
        if len(paths) >= max_count:
            return ""
        path = cat.pick(name)
        if path is None:
            _log.warning("[FakeAi Stickers] 未知贴纸 :%s:", name)
            return ""
        paths.append(path)
        return ""

    cleaned = _STICKER_RE.sub(repl, content)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    return cleaned, paths
