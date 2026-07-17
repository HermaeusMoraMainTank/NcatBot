"""贴纸目录扫描。"""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Dict, List, Optional

_log = logging.getLogger(__name__)

IMAGE_EXTS = {".webp", ".png", ".jpg", ".jpeg", ".gif"}


class StickerCatalog:
    def __init__(self, root: str | Path = "data/fakeai/stickers"):
        self.root = Path(root)
        self._map: Dict[str, List[Path]] = {}

    def reload(self) -> int:
        self._map.clear()
        if not self.root.is_dir():
            self.root.mkdir(parents=True, exist_ok=True)
            _log.info("[FakeAi Stickers] 已创建目录 %s", self.root)
            return 0

        # 先文件，再文件夹覆盖
        for p in sorted(self.root.iterdir()):
            if p.name.startswith("."):
                continue
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                name = p.stem
                self._map.setdefault(name, []).append(p.resolve())

        for p in sorted(self.root.iterdir()):
            if p.name.startswith(".") or not p.is_dir():
                continue
            variants = [
                f.resolve()
                for f in sorted(p.iterdir())
                if f.is_file()
                and not f.name.startswith(".")
                and f.suffix.lower() in IMAGE_EXTS
            ]
            if variants:
                self._map[p.name] = variants

        _log.info(
            "[FakeAi Stickers] 已加载 %s 个贴纸名 root=%s",
            len(self._map),
            self.root,
        )
        return len(self._map)

    def names(self) -> List[str]:
        return sorted(self._map.keys())

    def pick(self, name: str) -> Optional[Path]:
        paths = self._map.get(name) or []
        if not paths:
            return None
        return random.choice(paths)

    def prompt_block(self, max_chars: int = 800) -> str:
        names = self.names()
        if not names:
            return ""
        listing = "、".join(names)
        if len(listing) > max_chars:
            listing = listing[: max_chars - 1] + "…"
        return (
            "\n\n【可用贴纸】你已经有贴纸库存（就是这些图，别再说自己没表情包）。"
            "想配表情时，在 JSON 的 content 字符串里插入 :贴纸名: ，"
            "例如 {\"name\":\"蓝晴\",\"id\":\"0\",\"content\":\"笑死 :嘲笑:\"}；"
            "系统会去掉标记并另发一条图片。每轮最多 1 个。"
            "情绪到位时请主动用一张；真没有合适的再省略。\n"
            f"可用：{listing}"
        )


sticker_catalog = StickerCatalog()
