import asyncio
import random
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

PIGHUB_API_URLS = (
    "https://pighub.top/api/images?sort=2",
    "https://pighub.top/api/images?sort=3",
)
PIGHUB_BASE_URL = "https://pighub.top"
# 长命令优先匹配，避免「小猪搜索」被「猪搜索」误切前缀
SEARCH_COMMANDS = ("小猪搜索", "猪搜索", "找猪", "搜猪")
RANDOM_COMMANDS = ("随机猪", "随机小猪")
MAX_RESULTS = 5
MAX_RANDOM = 5
MIN_SCORE = 30.0
CACHE_TTL_SEC = 3600
FETCH_RETRIES = 3
FETCH_RETRY_DELAY_SEC = 1.5

_HELP = (
    "猪搜索用法：猪搜索 <关键词>\n"
    "示例：猪搜索 开心\n"
    "也可用：小猪搜索 / 找猪 / 搜猪\n"
    "随机：随机猪 / 随机小猪 [数量]\n"
    "数据来源：PigHub (https://pighub.top/)"
)


@dataclass(frozen=True)
class PigEntry:
    id: str
    title: str
    image_url: str


@dataclass(frozen=True)
class SearchHit:
    pig: PigEntry
    score: float


class PigSearch(NcatBotPlugin):
    name = "PigSearch"
    version = "1.0.0"

    timeout = 30

    async def on_load(self):
        self._pigs: List[PigEntry] = []
        self._cache_loaded_at = 0.0
        _log.info(f"开始加载 {self.name} 插件 v{self.version}")
        try:
            await self._ensure_pigs()
            _log.info(f"{self.name} 插件加载完成，共 {len(self._pigs)} 条猪猪")
        except Exception as e:
            _log.warning(f"{self.name} 预加载失败，将在首次请求时重试: {e}")

    @registrar.qq.on_group_message()
    async def handle_group_message(self, input: GroupMessage):
        message_text = self._get_message_text(input)
        if not message_text:
            return

        random_count = self._parse_random(message_text)
        if random_count is not None:
            await self._handle_random(input, random_count)
            return

        query = self._parse_search_query(message_text)
        if query is None:
            return

        if query == "":
            await self.api.qq.post_group_msg(group_id=input.group_id, text=_HELP)
            return

        try:
            await self._ensure_pigs()
        except Exception as e:
            _log.error(f"加载猪猪数据失败: {e}", exc_info=True)
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                text=f"[Pig] 猪猪数据加载失败，请稍后再试: {e}",
            )
            return

        hits = self._search(query)
        if not hits:
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                text=f"[Pig] 未找到与「{query}」匹配的猪猪",
            )
            return

        shown = hits[:MAX_RESULTS]
        header = f"[Pig] 共找到 {len(hits)} 条，显示前 {len(shown)} 条："
        chain: List[PlainText | Image] = [PlainText(text=header + "\n")]

        for idx, hit in enumerate(shown, start=1):
            chain.append(
                PlainText(text=f"{idx}. {hit.pig.title} 相似度 {hit.score:.0f}%\n")
            )
            chain.append(Image(file=self._image_url(hit.pig.image_url)))

        await self.api.qq.post_group_msg(
            group_id=input.group_id,
            rtf=MessageChain(chain),
        )

    async def _handle_random(self, input: GroupMessage, count: int) -> None:
        if count <= 0:
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                text="[Pig] 数量需为正整数，例如：随机猪 3",
            )
            return
        if count > MAX_RANDOM:
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                text=f"[Pig] 一次最多抽取 {MAX_RANDOM} 只猪猪",
            )
            return

        try:
            await self._ensure_pigs()
        except Exception as e:
            _log.error(f"加载猪猪数据失败: {e}", exc_info=True)
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                text=f"[Pig] 猪猪数据加载失败，请稍后再试: {e}",
            )
            return

        if not self._pigs:
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                text="[Pig] 猪圈空荡荡，暂时没有可用图片",
            )
            return

        picked = random.sample(self._pigs, k=min(count, len(self._pigs)))
        if len(picked) == 1:
            pig = picked[0]
            chain: List[PlainText | Image] = [
                PlainText(text=f"[Pig] 随机猪猪：{pig.title}\n"),
                Image(file=self._image_url(pig.image_url)),
            ]
        else:
            chain = [PlainText(text=f"[Pig] 随机抽取 {len(picked)} 只猪猪：\n")]
            for idx, pig in enumerate(picked, start=1):
                chain.append(PlainText(text=f"{idx}. {pig.title}\n"))
                chain.append(Image(file=self._image_url(pig.image_url)))

        await self.api.qq.post_group_msg(
            group_id=input.group_id,
            rtf=MessageChain(chain),
        )

    async def _ensure_pigs(self) -> None:
        now = time.monotonic()
        if self._pigs and now - self._cache_loaded_at < CACHE_TTL_SEC:
            return
        try:
            pigs = await asyncio.to_thread(self._fetch_pigs)
            self._pigs = pigs
            self._cache_loaded_at = now
        except Exception as e:
            # 刷新失败时继续用过期缓存，避免偶发断连直接不可用
            if self._pigs:
                _log.warning(f"刷新猪猪数据失败，沿用缓存 {len(self._pigs)} 条: {e}")
                self._cache_loaded_at = now
                return
            raise

    def _fetch_pigs(self) -> List[PigEntry]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://pighub.top/",
            "Connection": "close",
        }
        last_error: Exception | None = None
        for api_url in PIGHUB_API_URLS:
            for attempt in range(1, FETCH_RETRIES + 1):
                try:
                    # 每次新建 Session，避免 keep-alive 被对端掐断
                    with requests.Session() as session:
                        response = session.get(
                            api_url, headers=headers, timeout=self.timeout
                        )
                        response.raise_for_status()
                        response.encoding = "utf-8"
                        payload = response.json()
                    if not isinstance(payload, dict) or payload.get("code") not in (
                        0,
                        None,
                    ):
                        raise RuntimeError(
                            f"PigHub 返回异常: "
                            f"{payload.get('message') if isinstance(payload, dict) else payload}"
                        )

                    entries: List[PigEntry] = []
                    for item in payload.get("data") or []:
                        title = str(item.get("title", "")).strip()
                        image_url = str(item.get("image_url", "")).strip()
                        pig_id = item.get("id")
                        if not title or not image_url or pig_id is None:
                            continue
                        entries.append(
                            PigEntry(
                                id=str(pig_id), title=title, image_url=image_url
                            )
                        )
                    if not entries:
                        raise RuntimeError("PigHub 返回了空图片列表")
                    _log.info(f"猪猪数据已从 {api_url} 加载，共 {len(entries)} 条")
                    return entries
                except Exception as e:
                    last_error = e
                    _log.warning(
                        f"PigHub 请求失败 {api_url} ({attempt}/{FETCH_RETRIES}): {e}"
                    )
                    if attempt < FETCH_RETRIES:
                        time.sleep(FETCH_RETRY_DELAY_SEC * attempt)
        raise last_error or RuntimeError("无可用 PigHub 数据源")

    def _search(self, query: str) -> List[SearchHit]:
        hits: List[SearchHit] = []
        for pig in self._pigs:
            score = self._similarity(query, pig.title)
            if score >= MIN_SCORE:
                hits.append(SearchHit(pig=pig, score=score))
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
        if path.startswith(("http://", "https://")):
            return path
        parts = path.lstrip("/").split("/")
        encoded = "/".join(quote(part, safe="") for part in parts)
        return f"{PIGHUB_BASE_URL.rstrip('/')}/{encoded}"

    @staticmethod
    def _get_message_text(input: GroupMessage) -> str:
        for segment in input.message:
            if hasattr(segment, "text") and segment.text.strip():
                return segment.text.strip()
        return re.sub(r"\[CQ:[^\]]+\]", "", input.raw_message).strip()

    @staticmethod
    def _parse_search_query(message_text: str) -> Optional[str]:
        text = message_text.strip()
        for cmd in SEARCH_COMMANDS:
            if text.startswith(cmd):
                query = text[len(cmd) :].strip()
                if query.lower() in ("help", "?", "帮助"):
                    return ""
                return query
        return None

    @staticmethod
    def _parse_random(message_text: str) -> Optional[int]:
        text = message_text.strip()
        for cmd in RANDOM_COMMANDS:
            if text == cmd:
                return 1
            if text.startswith(cmd + " ") or text.startswith(cmd + "\u3000"):
                rest = text[len(cmd) :].strip()
                if not rest:
                    return 1
                if rest.isdigit():
                    return int(rest)
                return 0
        return None
