"""贴纸目录扫描（支持多套图库）。"""

from __future__ import annotations

import hashlib
import json
import os
import logging
import random
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

_log = logging.getLogger(__name__)

IMAGE_EXTS = {".webp", ".png", ".jpg", ".jpeg", ".gif"}

# 模型只认这 6 个情绪桶；图库里的细 tag 归到桶里（参考 dsh-meme）
MOOD_BUCKETS = {
    "happy": ["happy", "like", "meow", "givemoney", "color", "smile", "excited"],
    "angry": ["angry", "fool", "baka", "annoyed"],
    "sad": ["sad", "sigh", "cry", "tired"],
    "shy": ["shy", "blush"],
    "confused": ["confused", "surprised", "see", "question"],
    "daily": [
        "daily", "sleep", "morning", "work", "cpu", "reply", "eat", "study",
    ],
}

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
        # 使用次数：按名字统计，用于提示里优先列常用的
        self._usage: Dict[str, int] = {}
        self._usage_path = Path("data/fakeai/sticker_usage.json")
        self._usage_dirty = 0
        self._load_usage()
        # 表情备注：模型自己写的「这张图什么意思、什么时候用」
        self._notes: Dict[str, str] = {}
        self._notes_path = Path("data/fakeai/sticker_notes.json")
        self._load_notes()
        # 图库元数据（来自 pack.json）：库名 -> 名字 -> {mood, caption, keywords}
        self._meta: Dict[str, Dict[str, Dict[str, str]]] = {}
        self._pack_info: Dict[str, Dict[str, str]] = {}
        # 图库目录指纹：用于热更新检测
        self._sig: tuple = ()
        self._last_check: float = 0.0

    # ---- 加载 ----

    def reload(self) -> int:
        self._libs.clear()
        self._meta.clear()
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
                    self._load_pack_meta(p.name, p)
        if loose:
            self._libs.setdefault(LOOSE_LIBRARY, {}).update(loose)

        self._sig = self._signature()
        self._last_check = time.time()
        _log.info(
            "[FakeAi Stickers] 已加载 %s 套图库、%s 个贴纸名 root=%s",
            len(self._libs),
            len(self.names()),
            self.root,
        )
        return len(self.names())

    # ---- 热更新 ----

    def _signature(self) -> tuple:
        """图库目录的轻量指纹：子目录名 + mtime + 文件数。

        只要有人往 data/fakeai/stickers 里加了新图库或删了图，指纹就变，
        下一次调用会自动 reload，不用重启机器人。
        """
        try:
            entries = []
            with os.scandir(self.root) as it:
                for entry in it:
                    if entry.name.startswith("."):
                        continue
                    try:
                        st = entry.stat()
                    except OSError:
                        continue
                    if entry.is_dir():
                        try:
                            count = sum(
                                1
                                for f in os.scandir(entry.path)
                                if f.is_file() and Path(f.name).suffix.lower() in IMAGE_EXTS
                            )
                        except OSError:
                            count = 0
                        entries.append((entry.name, int(st.st_mtime), count))
                    elif Path(entry.name).suffix.lower() in IMAGE_EXTS:
                        entries.append((entry.name, int(st.st_mtime), 1))
            entries.sort()
            return tuple(entries)
        except OSError:
            return ()

    def maybe_reload(self, min_interval: float = 20.0) -> bool:
        """隔一段时间检查一次图库目录，有变化就重新扫描。"""
        now = time.time()
        if now - self._last_check < min_interval:
            return False
        self._last_check = now
        if self._signature() == self._sig:
            return False
        before = len(self.names())
        self.reload()
        _log.info(
            "[FakeAi Stickers] 图库已热更新：%s → %s 张", before, len(self.names())
        )
        return True

    # ---- 查询 ----

    def _load_pack_meta(self, lib_name: str, path: Path) -> None:
        """读 pack.json：每张图的情绪、描述、关键词。没有就跳过。"""
        meta_file = path / "pack.json"
        if not meta_file.is_file():
            return
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception as e:
            _log.debug("[FakeAi Stickers] 读取 %s/pack.json 失败: %s", lib_name, e)
            return
        images = data.get("images")
        if not isinstance(images, dict):
            return
        cleaned: Dict[str, Dict[str, str]] = {}
        for key, item in images.items():
            if not isinstance(item, dict):
                continue
            cleaned[str(key)] = {
                "mood": str(item.get("mood") or "").lower(),
                "caption": str(item.get("caption") or ""),
                "keywords": str(item.get("keywords") or ""),
            }
        if cleaned:
            self._meta[lib_name] = cleaned
        self._pack_info[lib_name] = {
            "name": str(data.get("name") or lib_name),
            "version": str(data.get("version") or ""),
            "license": str(data.get("license") or ""),
        }

    def _visible_meta(self, name: str) -> Dict[str, str]:
        visible = self._visible()
        for lib, items in self._meta.items():
            if lib in visible and name in items:
                return items[name]
        return {}

    def mood_of(self, name: str) -> str:
        return self._visible_meta(name).get("mood", "")

    def caption(self, name: str) -> str:
        return self._visible_meta(name).get("caption", "")

    def moods(self) -> Dict[str, int]:
        """当前启用图库的情绪分布（按 6 个桶聚合），例如 {"happy": 53}。"""
        counts: Dict[str, int] = {}
        visible = self._visible()
        for lib, items in self._meta.items():
            if lib not in visible:
                continue
            for item in items.values():
                mood = item.get("mood") or ""
                bucket = "other"
                for name, tags in MOOD_BUCKETS.items():
                    if mood in tags:
                        bucket = name
                        break
                counts[bucket] = counts.get(bucket, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    def pack_info(self) -> Dict[str, Dict[str, str]]:
        return {
            lib: info for lib, info in self._pack_info.items() if lib in self._visible()
        }

    def _haystack(self, name: str, lib: str) -> str:
        item = (self._meta.get(lib) or {}).get(name) or {}
        return (
            f"{name} {item.get('mood', '')} {item.get('caption', '')} "
            f"{item.get('keywords', '')} {self._notes.get(name, '')}"
        ).lower()

    def search(
        self, query: str = "", mood: str = "", limit: int = 8
    ) -> List[Dict[str, str]]:
        """按情绪桶 + 关键词找图，返回候选（名字/描述/情绪/图库）。"""
        self.maybe_reload()
        q = str(query or "").strip().lower()
        mood = str(mood or "").strip().lower()
        wanted = None
        if mood:
            wanted = set(MOOD_BUCKETS.get(mood) or [mood])
        tokens = [t for t in re.split(r"[\s,，、;；/|]+", q) if t]
        results: List[Dict[str, object]] = []
        for lib, variants in self._visible().items():
            for name in variants:
                item = (self._meta.get(lib) or {}).get(name) or {}
                if wanted is not None and item.get("mood") not in wanted:
                    continue
                hay = self._haystack(name, lib)
                # 长词命中更值钱：「加班」比「累」更能定位到那张图
                score = sum(len(t) for t in tokens if t and t in hay)
                if tokens and score == 0:
                    continue
                results.append(
                    {
                        "name": name,
                        "mood": item.get("mood", ""),
                        "caption": item.get("caption", ""),
                        "library": lib,
                        "score": score,
                    }
                )
        if tokens and results:
            best = max(int(r["score"]) for r in results)
            results = [r for r in results if int(r["score"]) == best]
        results.sort(
            key=lambda r: (
                -int(self._usage.get(str(r["name"])) or 0),
                str(r["name"]),
            )
        )
        return results[: max(1, min(20, int(limit or 8)))]

    def resolve(self, name: str) -> Optional[str]:
        """把模型给的名字解析成真实贴纸名：先精确，再按描述/关键词模糊匹配。"""
        target = str(name or "").strip()
        if not target:
            return None
        for items in self._visible().values():
            if target in items:
                return target
        hits = self.search(target, limit=5)
        if len(hits) == 1:
            return str(hits[0]["name"])
        # 多个候选时，只有「整串出现在描述里」的唯一一张才算对上
        exact = [
            h
            for h in hits
            if target in str(h.get("caption", "")).lower()
            or target in str(h.get("name", "")).lower()
        ]
        if len(exact) == 1:
            return str(exact[0]["name"])
        return None

    # ---- 使用次数 ----

    def _load_notes(self) -> None:
        try:
            if self._notes_path.is_file():
                data = json.loads(self._notes_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._notes = {
                        str(k): str(v) for k, v in data.items() if str(v).strip()
                    }
        except Exception as e:
            _log.debug("[FakeAi Stickers] 读取表情备注失败: %s", e)

    def _save_notes(self) -> None:
        try:
            self._notes_path.parent.mkdir(parents=True, exist_ok=True)
            self._notes_path.write_text(
                json.dumps(self._notes, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except Exception as e:
            _log.debug("[FakeAi Stickers] 保存表情备注失败: %s", e)

    def note(self, name: str) -> str:
        return self._notes.get(str(name or "").strip(), "")

    def set_note(self, name: str, note: str) -> bool:
        """给表情写一句用途说明；名字必须在图库里，否则拒绝。"""
        key = str(name or "").strip()
        if not key or key not in self.names():
            return False
        text = str(note or "").strip()[:60]
        if text:
            self._notes[key] = text
        else:
            self._notes.pop(key, None)
        self._save_notes()
        return True

    def label(self, name: str) -> str:
        note = self.note(name)
        if note:
            return f"{name}（{note}）"
        caption = self.caption(name)
        if caption:
            short = caption.replace("\n", " ")[:14]
            return f"{name}（{short}）"
        return str(name)

    def find(self, name: str) -> Optional[Path]:
        """按名字找一张图（不记使用次数，供查看/备注用）。"""
        target = str(name or "").strip()
        if not target:
            return None
        for items in self._visible().values():
            paths = items.get(target)
            if paths:
                return paths[0]
        return None

    def _load_usage(self) -> None:
        try:
            if self._usage_path.is_file():
                self._usage = json.loads(self._usage_path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            _log.debug("[FakeAi Stickers] 读取使用次数失败: %s", e)

    def _save_usage(self) -> None:
        try:
            self._usage_path.parent.mkdir(parents=True, exist_ok=True)
            self._usage_path.write_text(
                json.dumps(self._usage, ensure_ascii=False), encoding="utf-8"
            )
            self._usage_dirty = 0
        except Exception as e:
            _log.debug("[FakeAi Stickers] 保存使用次数失败: %s", e)

    def record_use(self, name: str) -> None:
        key = str(name or "").strip()
        if not key:
            return
        self._usage[key] = int(self._usage.get(key) or 0) + 1
        self._usage_dirty += 1
        if self._usage_dirty >= 5:
            self._save_usage()

    def prompt_names(self, limit: int = 30) -> List[str]:
        """按使用次数排序；后半段每小时轮换一批，而不是每轮洗牌。

        每轮洗牌会让 system/user 前缀每次都变，直接打穿 DeepSeek 的前缀缓存，
        成本按全价走。这里用一个「小时桶 + 名单长度」做种子，同一小时内结果稳定。
        """
        names = self.names()
        if not names:
            return []
        limit = max(8, min(60, int(limit or 30)))
        by_use = sorted(
            names, key=lambda n: (-int(self._usage.get(n) or 0), n)
        )
        if len(by_use) <= limit:
            return by_use
        stable = by_use[: max(1, limit // 2)]
        pool = [n for n in by_use if n not in stable]
        bucket = int(time.time() // 3600)
        rng = random.Random(f"{bucket}:{len(pool)}")
        rotated = pool[:]
        rng.shuffle(rotated)
        return stable + rotated[: limit - len(stable)]

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

    def _weighted_choice(
        self, paths: List[Path], name: Optional[str] = None
    ) -> Path:
        """常用的表情更容易被选中（使用次数 +1 做权重）。"""
        if len(paths) <= 1:
            return paths[0]
        weights = [
            1 + int(self._usage.get(name or p.stem) or 0) for p in paths
        ]
        try:
            return random.choices(paths, weights=weights, k=1)[0]
        except (ValueError, IndexError):
            return random.choice(paths)

    def pick(self, name: str) -> Optional[Path]:
        self.maybe_reload()
        target = self.resolve(name)
        if not target:
            return None
        for items in self._visible().values():
            paths = items.get(target)
            if paths:
                picked = self._weighted_choice(paths, target)
                self._remember(picked)
                self.record_use(target)
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
        pool = fresh or chosen
        picked = self._weighted_choice(pool, None)
        self._remember(picked)
        self.record_use(picked.stem)
        return picked

    def save_image(self, name: str, data: bytes) -> Optional[str]:
        """把外部图片收进图库（默认落在根目录的松散库）。

        文件名带内容哈希：同一张图重复收藏不会产生副本。
        返回贴纸名（stem），后续可以直接 send_sticker(name)。
        """
        if not data:
            return None
        head = data[:16]
        if head.startswith(b"\xff\xd8"):
            ext = ".jpg"
        elif head.startswith(b"\x89PNG"):
            ext = ".png"
        elif head[:6] in (b"GIF87a", b"GIF89a"):
            ext = ".gif"
        elif head[:4] == b"RIFF" and data[8:12] == b"WEBP":
            ext = ".webp"
        else:
            return None

        digest = hashlib.md5(data).hexdigest()[:16]
        existing = [p for p in self.root.glob(f"*{digest}*") if p.is_file()]
        if existing:
            return existing[0].stem

        safe = re.sub(r'[\\/:*?"<>|\s]+', "_", str(name or "")).strip("_")[:24]
        stem = f"{safe}-{digest}" if safe else digest
        target = self.root / f"{stem}{ext}"
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except OSError as e:
            _log.warning("[FakeAi Stickers] 收藏失败: %s", e)
            return None
        self.reload()
        return stem

    def prompt_block(self, max_chars: int = 800) -> str:
        names = self.names()
        if not names:
            return ""
        listing = "、".join(self.label(n) for n in names)
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
