"""梗鲸表情包（aigengtu.com）：纯静态图库，免 Key。

索引是 ``https://aigengtu.com/gallery-index.json``，图片直链在 img.aigengtu.com。
图床有两个坑：非浏览器 UA 会 403；图不存在时返回 404 + HTML 错误页，
所以下载后要认魔数，别把 HTML 当图片发出去。
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

_log = logging.getLogger(__name__)

INDEX_URL = "https://aigengtu.com/gallery-index.json"
SITE = "https://aigengtu.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
CACHE_DIR = Path("data/fakeai/whale")
INDEX_CACHE = CACHE_DIR / "index.json"
TMP_DIR = CACHE_DIR / "tmp"
REFRESH_SECONDS = 12 * 3600
TMP_MAX_AGE = 48 * 3600
MAX_BYTES = 12 * 1024 * 1024


def _looks_like_image(data: bytes) -> bool:
    if len(data) < 12:
        return False
    if data[:3] == b"\xff\xd8\xff":
        return True
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return False


class WhaleCatalog:
    """梗鲸图库：索引缓存 + 按话题挑图 + 下载到本地临时目录。"""

    def __init__(self) -> None:
        self._gallery: Dict[str, Dict[str, Any]] = {}
        self._loaded_at = 0.0
        self._lock = asyncio.Lock()

    # ---- 索引 ----

    @property
    def ready(self) -> bool:
        return bool(self._gallery)

    def characters(self) -> List[str]:
        return sorted(self._gallery)

    def count(self) -> int:
        return sum(len(v.get("images") or []) for v in self._gallery.values())

    def _read_cache(self) -> Optional[dict]:
        try:
            if not INDEX_CACHE.is_file():
                return None
            if time.time() - INDEX_CACHE.stat().st_mtime > REFRESH_SECONDS:
                return None
            return json.loads(INDEX_CACHE.read_text(encoding="utf-8"))
        except Exception as exc:
            _log.debug("[Whale] 读取缓存失败：%s", exc)
            return None

    def load_cache(self) -> int:
        data = self._read_cache()
        if not data:
            return 0
        gallery = data.get("gallery") or {}
        if isinstance(gallery, dict):
            self._gallery = gallery
            self._loaded_at = time.time()
        return self.count()

    async def ensure_index(self, force: bool = False) -> int:
        """确保索引可用；网络失败时退回旧缓存。"""
        async with self._lock:
            if not force:
                if self._gallery and time.time() - self._loaded_at < REFRESH_SECONDS:
                    return self.count()
                if self.load_cache():
                    return self.count()
            try:
                timeout = aiohttp.ClientTimeout(total=25)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(
                        INDEX_URL, headers={"User-Agent": UA}
                    ) as resp:
                        if resp.status != 200:
                            raise RuntimeError(f"HTTP {resp.status}")
                        raw = await resp.text()
                data = json.loads(raw)
                gallery = data.get("gallery") or {}
                if not isinstance(gallery, dict) or not gallery:
                    raise RuntimeError("索引结构异常")
                self._gallery = gallery
                self._loaded_at = time.time()
                try:
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    INDEX_CACHE.write_text(
                        json.dumps(data, ensure_ascii=False), encoding="utf-8"
                    )
                except Exception as exc:
                    _log.debug("[Whale] 写入索引缓存失败：%s", exc)
                _log.info(
                    "[Whale] 梗鲸图库已就绪：%s 个角色、%s 张图",
                    len(self._gallery),
                    self.count(),
                )
                return self.count()
            except Exception as exc:
                _log.warning("[Whale] 拉取梗鲸索引失败：%s", exc)
                if not self._gallery:
                    self.load_cache()
                return self.count()

    # ---- 挑选 ----

    def _all_images(self, character: str = "") -> List[Dict[str, Any]]:
        if character:
            item = self._gallery.get(character)
            return list((item or {}).get("images") or [])
        out: List[Dict[str, Any]] = []
        for item in self._gallery.values():
            out.extend(item.get("images") or [])
        return out

    @staticmethod
    def _tokens(topic: str) -> List[str]:
        text = str(topic or "")
        tokens = re.findall(r"[\u4e00-\u9fff]{2,4}", text)
        tokens += re.findall(r"[A-Za-z]{2,}", text)
        return [t for t in tokens if t not in {"什么", "怎么", "一个", "这个", "那个"}]

    def pick(self, topic: str = "", character: str = "") -> Optional[Dict[str, Any]]:
        images = self._all_images(character)
        if not images:
            return None
        tokens = self._tokens(topic)
        if not tokens:
            return random.choice(images)
        scored: List[tuple[int, Dict[str, Any]]] = []
        for item in images:
            haystack = " ".join(
                str(item.get(k) or "") for k in ("name", "alt", "category")
            )
            story = item.get("story") or {}
            if isinstance(story, dict):
                haystack += " " + str(story.get("zh") or "")
            score = sum(haystack.count(tok) for tok in tokens)
            if score:
                scored.append((score, item))
        if not scored:
            return random.choice(images)
        scored.sort(key=lambda x: -x[0])
        top = [item for score, item in scored if score >= scored[0][0]]
        return random.choice(top[:20])

    # ---- 下载 ----

    async def download(self, image: Dict[str, Any]) -> Optional[Path]:
        url = str(image.get("original") or image.get("preview") or "")
        if not url.startswith("http"):
            return None
        try:
            TMP_DIR.mkdir(parents=True, exist_ok=True)
            timeout = aiohttp.ClientTimeout(total=25)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers={"User-Agent": UA}) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.content.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES or not _looks_like_image(data):
                _log.debug("[Whale] 下载内容不是图片：%s", url)
                return None
            suffix = ".jpg"
            if data[:4] == b"RIFF":
                suffix = ".webp"
            elif data[:8] == b"\x89PNG\r\n\x1a\n":
                suffix = ".png"
            elif data[:3] == b"GIF":
                suffix = ".gif"
            name = re.sub(r"[^0-9A-Za-z]+", "", str(image.get("name") or ""))[:24]
            path = TMP_DIR / f"whale-{int(time.time() * 1000)}-{name or 'meme'}{suffix}"
            path.write_bytes(data)
            self._cleanup_tmp()
            return path.resolve()
        except Exception as exc:
            _log.warning("[Whale] 下载梗图失败 %s：%s", url, exc)
            return None

    @staticmethod
    def _cleanup_tmp() -> None:
        try:
            cutoff = time.time() - TMP_MAX_AGE
            for item in TMP_DIR.iterdir():
                if item.is_file() and item.stat().st_mtime < cutoff:
                    item.unlink(missing_ok=True)
        except Exception:
            pass

    async def fetch(
        self, topic: str = "", character: str = ""
    ) -> Optional[Path]:
        if not self._gallery:
            await self.ensure_index()
        for _ in range(3):
            image = self.pick(topic, character)
            if not image:
                return None
            path = await self.download(image)
            if path:
                return path
        return None


whale_catalog = WhaleCatalog()
