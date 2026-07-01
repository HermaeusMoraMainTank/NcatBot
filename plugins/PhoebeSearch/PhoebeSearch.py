import asyncio
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import List, Optional
from urllib.parse import quote

import requests

from ncatbot.core import registrar
from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.plugin import NcatBotPlugin
from ncatbot.types import Image, MessageArray as MessageChain, PlainText
from ncatbot.utils import get_log

_log = get_log()

MEMES_JSON_URL = "https://phoebehub.top/data/memes.json"
BASE_URL = "https://phoebehub.top/"
COMMAND = "菲比搜索"
MAX_RESULTS = 5
MIN_SCORE = 30.0
CACHE_TTL_SEC = 3600

_HELP = (
    "菲比搜索用法：菲比搜索 <关键词>\n"
    "示例：菲比搜索 2000元烧鸡哈哈\n"
    "数据来源：Phoebe Hub (https://phoebehub.top/)"
)


@dataclass(frozen=True)
class MemeEntry:
    title: str
    url: str
    is_gif: bool


@dataclass(frozen=True)
class SearchHit:
    meme: MemeEntry
    score: float


class PhoebeSearch(NcatBotPlugin):
    name = "PhoebeSearch"
    version = "1.0.0"

    timeout = 30
    session = requests.Session()

    async def on_load(self):
        self._memes: List[MemeEntry] = []
        self._cache_loaded_at = 0.0
        _log.info(f"开始加载 {self.name} 插件 v{self.version}")
        try:
            await self._ensure_memes()
            _log.info(f"{self.name} 插件加载完成，共 {len(self._memes)} 条菲比")
        except Exception as e:
            _log.warning(f"{self.name} 预加载失败，将在首次搜索时重试: {e}")

    @registrar.qq.on_group_message()
    async def handle_phoebe_search(self, input: GroupMessage):
        message_text = self._get_message_text(input)
        if not message_text:
            return

        query = self._parse_query(message_text)
        if query is None:
            return

        if query == "":
            await self.api.qq.post_group_msg(group_id=input.group_id, text=_HELP)
            return

        try:
            await self._ensure_memes()
        except Exception as e:
            _log.error(f"加载菲比数据失败: {e}", exc_info=True)
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                text=f"[Phoebe] 菲比数据加载失败，请稍后再试: {e}",
            )
            return

        hits = self._search(query)
        if not hits:
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                text=f"[Phoebe] 未找到与「{query}」匹配的菲比",
            )
            return

        shown = hits[:MAX_RESULTS]
        header = f"[Phoebe] 共找到 {len(hits)} 条，显示前 {len(shown)} 条："
        chain: List[PlainText | Image] = [PlainText(text=header + "\n")]

        for idx, hit in enumerate(shown, start=1):
            chain.append(
                PlainText(text=f"{idx}. {hit.meme.title} 相似度 {hit.score:.0f}%\n")
            )
            chain.append(Image(file=self._image_url(hit.meme.url)))

        await self.api.qq.post_group_msg(
            group_id=input.group_id,
            rtf=MessageChain(chain),
        )

    async def _ensure_memes(self) -> None:
        now = time.monotonic()
        if self._memes and now - self._cache_loaded_at < CACHE_TTL_SEC:
            return
        memes = await asyncio.to_thread(self._fetch_memes)
        self._memes = memes
        self._cache_loaded_at = now

    def _fetch_memes(self) -> List[MemeEntry]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/114.0.0.0 Safari/537.36"
            ),
        }
        response = self.session.get(
            MEMES_JSON_URL, headers=headers, timeout=self.timeout
        )
        response.raise_for_status()
        response.encoding = "utf-8"
        payload = response.json()

        entries: List[MemeEntry] = []
        for item in payload.get("memes", []):
            title = str(item.get("title", "")).strip()
            url = str(item.get("url", "")).strip()
            if not title or not url:
                continue
            entries.append(
                MemeEntry(
                    title=title,
                    url=url,
                    is_gif=bool(item.get("isGif", False)),
                )
            )
        return entries

    def _search(self, query: str) -> List[SearchHit]:
        hits: List[SearchHit] = []
        for meme in self._memes:
            score = self._similarity(query, meme.title)
            if score >= MIN_SCORE:
                hits.append(SearchHit(meme=meme, score=score))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits

    @staticmethod
    def _similarity(query: str, title: str) -> float:
        q, t = query.strip(), title.strip()
        if not q or not t:
            return 0.0
        if q == t:
            return 100.0
        if q in t:
            return min(100.0, 80.0 + 20.0 * len(q) / len(t))
        if t in q:
            return min(95.0, 70.0 + 25.0 * len(t) / len(q))
        return SequenceMatcher(None, q, t).ratio() * 100.0

    @staticmethod
    def _image_url(path: str) -> str:
        encoded = "/".join(quote(part, safe="") for part in path.split("/"))
        return f"{BASE_URL}{encoded}"

    @staticmethod
    def _get_message_text(input: GroupMessage) -> str:
        for segment in input.message:
            if hasattr(segment, "text") and segment.text.strip():
                return segment.text.strip()
        return re.sub(r"\[CQ:[^\]]+\]", "", input.raw_message).strip()

    @staticmethod
    def _parse_query(message_text: str) -> Optional[str]:
        text = message_text.strip()
        if not text.startswith(COMMAND):
            return None
        query = text[len(COMMAND) :].strip()
        if query.lower() in ("help", "?", "帮助"):
            return ""
        return query
