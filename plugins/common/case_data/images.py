"""图片缓存：按 URL 哈希落盘，失败不阻塞主流程。

图片都是运行时下载物，不进版本库；启动预热与渲染时按需补齐，
下坏/过期的文件由 :meth:`ImageCache.cleanup` 回收。
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Iterable
from pathlib import Path

import httpx

USER_AGENT = "NcatBot-CaseData/1.0 (+qq bot plugin)"
MAX_BYTES = 4 * 1024 * 1024
#: PNG / JPEG / GIF / WEBP 的文件头，用来识别下坏的图片
_IMAGE_MAGIC = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF")


def url_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]


def _is_image(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(12).startswith(_IMAGE_MAGIC)
    except OSError:
        return False


class ImageCache:
    def __init__(
        self,
        directory: Path,
        *,
        max_age: int = 30 * 24 * 3600,
        max_files: int | None = 3000,
    ) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_age = max_age
        self.max_files = max_files
        self._semaphore = asyncio.Semaphore(4)

    def path_for(self, url: str, suffix: str = ".png") -> Path:
        return self.directory / f"{url_key(url)}{suffix}"

    def cached(self, url: str) -> Path | None:
        """本地有完整图片就返回路径；损坏的文件直接删掉，留给 get() 重新下载。"""
        if not url:
            return None
        path = self.path_for(url)
        try:
            if path.stat().st_size <= 0:
                return None
        except OSError:
            return None
        if not _is_image(path):
            try:
                path.unlink()
            except OSError:
                pass
            return None
        return path

    async def get(self, url: str, *, timeout: float = 20.0) -> Path | None:
        """返回本地路径；缺失或损坏时下载，失败返回 None（调用方降级为纯文字）。"""
        if not url:
            return None
        cached = self.cached(url)
        if cached:
            return cached
        headers = {"User-Agent": USER_AGENT}
        if "liquipedia.net" in url:
            headers["Referer"] = "https://liquipedia.net/dota2/"
        data = b""
        async with self._semaphore:
            for attempt in range(2):
                try:
                    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                        response = await client.get(url, headers=headers)
                        response.raise_for_status()
                        data = response.content
                    break
                except Exception:  # noqa: BLE001 - 图片失败不影响开箱
                    if attempt:
                        return None
                    await asyncio.sleep(0.5)
        if not data or len(data) > MAX_BYTES:
            return None
        path = self.path_for(url)
        tmp = path.with_name(f"{path.name}.{time.time_ns()}.part")
        try:
            tmp.write_bytes(data)
            tmp.replace(path)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            return None
        return path

    async def prefetch(self, urls: Iterable[str]) -> tuple[int, int]:
        """批量补齐缺失图片，返回（成功数, 失败数）；已在本地的不重复下载。"""
        pending = sorted({url for url in urls if url and not self.cached(url)})
        if not pending:
            return 0, 0
        results = await asyncio.gather(
            *(self.get(url) for url in pending), return_exceptions=True
        )
        ok = sum(1 for result in results if isinstance(result, Path))
        return ok, len(pending) - ok

    def cleanup(self) -> int:
        """删除过期缓存，并把文件数压回 max_files，返回删除数量。"""
        cutoff = time.time() - self.max_age
        kept: list[tuple[float, Path]] = []
        removed = 0
        for path in self.directory.iterdir():
            try:
                if not path.is_file():
                    continue
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
            else:
                kept.append((mtime, path))
        if self.max_files is not None and len(kept) > self.max_files:
            kept.sort()
            for _, path in kept[: len(kept) - self.max_files]:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed
