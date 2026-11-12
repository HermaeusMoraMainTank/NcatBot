"""群友语感样本：从本群真实发言里挑几句，作为「这个群怎么说话」的参考。

不是让人设背语料，而是每轮按当前话题挑 3~5 条短句注入，
把「作者想象中的女大学生」拉回「这个群里的普通人」。
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

DEFAULT_PATH = Path("data/FakeAi/style_corpus.json")
MAX_ITEMS = 5
_PUNCT = re.compile(r"[\s，,。.!！?？~～…、·—\-]+")
_TAG_RULES = (
    ("game", ("游戏", "打", "零式", "ff14", "lol", "瓦", "steam", "开黑", "上分", "队友", "副本")),
    ("food", ("吃", "外卖", "饭", "火锅", "奶茶", "夜宵", "饿", "辣", "肠粉", "烧烤")),
    ("work", ("课", "作业", "毕设", "导师", "考试", "上班", "加班", "代码", "bug", "改")),
    ("sleep", ("睡", "困", "熬夜", "早八", "晚安", "醒了", "通宵")),
    ("mood", ("累", "烦", "难受", "开心", "笑死", "无语", "离谱", "草", "典", "啊")),
    ("money", ("钱", "工资", "穷", "氪", "买", "贵", "便宜", "红包")),
    ("media", ("番", "剧", "电影", "歌", "视频", "直播", "b站", "up")),
)


def normalize(text: str) -> str:
    return _PUNCT.sub("", str(text or "")).strip()


def tags_for(text: str) -> list[str]:
    body = str(text or "").lower()
    hits = [name for name, keys in _TAG_RULES if any(k in body for k in keys)]
    return hits or ["daily"]


class StyleCorpus:
    def __init__(self, path: Path | str = DEFAULT_PATH):
        self.path = Path(path)
        self.items: list[dict] = []
        self.mtime = 0.0

    def reload(self, force: bool = False) -> int:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            self.items = []
            self.mtime = 0.0
            return 0
        if not force and mtime == self.mtime:
            return len(self.items)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.items = []
            return 0
        items = data.get("items") if isinstance(data, dict) else data
        self.items = [item for item in (items or []) if isinstance(item, dict) and item.get("text")]
        self.mtime = mtime
        return len(self.items)

    def _score(self, item: dict, query: str, query_tags: set[str]) -> float:
        text = str(item.get("text") or "")
        item_tags = set(item.get("tags") or [])
        score = 3.0 * len(item_tags & query_tags)
        if not query:
            return score + random.random()
        q = normalize(query)
        t = normalize(text)
        if not q or not t:
            return score + random.random()
        # 字符二元组重合：不需要分词，也能抓到「零式/毕设/外卖」这类词
        q_bi = {q[i : i + 2] for i in range(max(0, len(q) - 1))}
        t_bi = {t[i : i + 2] for i in range(max(0, len(t) - 1))}
        overlap = len(q_bi & t_bi)
        score += min(6.0, overlap * 1.5)
        if abs(len(t) - len(q)) <= 4:
            score += 0.6
        return score + random.random() * 0.8

    def pick(self, query: str = "", limit: int = 4) -> list[str]:
        # 重新生成语料后不用重启：mtime 没变时这里只做一次 stat
        self.reload()
        if not self.items:
            return []
        limit = max(1, min(MAX_ITEMS, int(limit or 4)))
        query_tags = set(tags_for(query)) if query else set()
        ranked = sorted(
            self.items,
            key=lambda item: self._score(item, query, query_tags),
            reverse=True,
        )
        picked: list[str] = []
        seen: set[str] = set()
        for item in ranked:
            text = str(item["text"]).strip()
            key = normalize(text)
            if not text or key in seen:
                continue
            seen.add(key)
            picked.append(text)
            if len(picked) >= limit:
                break
        return picked

    def prompt_block(self, query: str = "", limit: int = 4) -> str:
        picked = self.pick(query, limit)
        if not picked:
            return ""
        lines = "\n".join(f"- {text}" for text in picked)
        return (
            "【群里平时怎么说话 · 只看语感和长度，别原样复述，也别冒充这些群友】\n"
            f"{lines}\n"
            "（照这个松紧程度说自己的话，不要照抄。）"
        )


style_corpus = StyleCorpus()
