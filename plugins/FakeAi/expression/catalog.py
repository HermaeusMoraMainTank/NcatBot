"""贴纸目录扫描（支持多套图库）。"""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Dict, List, Optional

_log = logging.getLogger(__name__)

IMAGE_EXTS = {".webp", ".png", ".jpg", ".jpeg", ".gif"}

# 根目录下直接放图片时，归到这个库（兼容旧布局）
LOOSE_LIBRARY = "darkdog"


class StickerCatalog:
    """按图库分组管理本地贴纸。

    目录约定::

        data/fakeai/stickers/
            darkdog/        ← 一套图库，目录名就是库名
                嘲笑.jpg
            other/          ← 另一套
            loose.jpg       ← 根下的散图归到 darkdog

    ``active`` 决定哪些库参与挑选，默认全部。
    """

    def __init__(self, root: str | Path = "data/fakeai/stickers"):
        self.root = Path(root)
        self.active: Optional[set[str]] = None
        self._libs: Dict[str, Dict[str, List[Path]]] = {}
        # 最近发过的图，挑选时避开，避免连着两张一样
        self._recent: List[Path] = []

    # ---- 加载 ----

    def reload(self) -> int:
        self._libs.clear()
        if not self.root.is_dir():
            self.root.mkdir(parents=True, exist_ok=True)
            _log.info("[FakeAi Stickers] 已创建目录 %s", self.root)
            return 0

        loose: Dict[str, List[Path]] = {}
        for p in sorted(self.root.iterdir()):
            if p.name.startswith("."):
                continue
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                loose.setdefault(p.stem, []).append(p.resolve())
            elif p.is_dir():
                variants: Dict[str, List[Path]] = {}
                for f in sorted(p.iterdir()):
                    if (
                        f.is_file()
                        and not f.name.startswith(".")
                        and f.suffix.lower() in IMAGE_EXTS
                    ):
                        variants.setdefault(f.stem, []).append(f.resolve())
                if variants:
                    self._libs[p.name] = variants
        if loose:
            self._libs.setdefault(LOOSE_LIBRARY, {}).update(loose)

        _log.info(
            "[FakeAi Stickers] 已加载 %s 套图库、%s 个贴纸名 root=%s",
            len(self._libs),
            len(self.names()),
            self.root,
        )
        return len(self.names())

    # ---- 查询 ----

    def libraries(self) -> Dict[str, int]:
        """库名 -> 贴纸名数量。"""
        return {name: len(items) for name, items in sorted(self._libs.items())}

    def _visible(self) -> Dict[str, Dict[str, List[Path]]]:
        if self.active is None:
            return self._libs
        return {k: v for k, v in self._libs.items() if k in self.active}

    def visible_libraries(self) -> Dict[str, int]:
        """当前启用且真的存在的库；配置成仅梗鲸时这里会是空。"""
        return {k: len(v) for k, v in self._visible().items()}

    def names(self) -> List[str]:
        out: set[str] = set()
        for items in self._visible().values():
            out.update(items)
        return sorted(out)

    def _remember(self, path: Path) -> None:
        self._recent.append(path)
        if len(self._recent) > 8:
            self._recent = self._recent[-8:]

    def pick(self, name: str) -> Optional[Path]:
        target = str(name or "").strip()
        if not target:
            return None
        for items in self._visible().values():
            paths = items.get(target)
            if paths:
                picked = random.choice(paths)
                self._remember(picked)
                return picked
        return None

    def pick_any(self, library: str = "") -> Optional[Path]:
        """随机挑一张，library 为空时在当前启用的库里挑。"""
        pools = self._visible()
        if library:
            if library not in pools:
                return None
            chosen: List[Path] = []
            for paths in pools[library].values():
                chosen.extend(paths)
        else:
            chosen = [p for items in pools.values() for paths in items.values() for p in paths]
        if not chosen:
            return None
        fresh = [p for p in chosen if p not in self._recent]
        picked = random.choice(fresh or chosen)
        self._remember(picked)
        return picked

    def prompt_block(self, max_chars: int = 800) -> str:
        names = self.names()
        if not names:
            return ""
        listing = "、".join(names)
        if len(listing) > max_chars:
            listing = listing[: max_chars - 1] + "…"
        libs = "、".join(self._visible())
        return (
            f"\n\n【可用贴纸】你已经有贴纸库存（图库：{libs}）。"
            "想配表情时调用 send_sticker(name)。"
            "情绪到位时主动配一张；真没有合适的再省略。\n"
            f"可用：{listing}"
        )


sticker_catalog = StickerCatalog()
