"""多游戏猜卡面 — NcatBot 插件。

上游 PJSK：https://github.com/yonglanws/astrbot_plugin_pjsk_guess_card
FGO 卡面：Atlas Academy（https://api.atlasacademy.io）
"""
from __future__ import annotations

import asyncio
import io
import itertools
import json
import logging
import os
import random
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Union

import aiohttp
from ncatbot.core import non_self, registrar
from ncatbot.event.qq import GroupMessageEvent
from ncatbot.plugin import NcatBotPlugin
from ncatbot.types import At, Image, MessageArray, PlainText
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont
from pilmoji import Pilmoji

from .effects import ImageEffectProcessor, LRUCache
from .pools import (
    CLASS_CN,
    POOL_DISPLAY,
    CardPool,
    add_custom_aliases,
    build_member_character,
    character_answer_keys,
    display_answer_name,
    display_member_answer,
    find_character,
    format_reveal_details,
    load_custom_nicknames,
    load_pool,
    make_member_pool_stub,
    normalize_answer,
    rebuild_valid_answers,
    reload_character_from_disk,
    resolve_card_image_url,
    resolve_character_alias_query,
    resolve_local_card_path,
    resolve_pool_id,
    suggest_answers,
)
from .sam_item_card import (
    fetch_item_json_curl,
    normalize_wiki_item,
    render_wiki_item_card,
)
from common.utils.CommonUtil import CommonUtil

_log = logging.getLogger("GuessCard")

PLUGIN_DIR = Path(__file__).resolve().parent
RESOURCES_DIR = PLUGIN_DIR / "resources"

DEFAULT_CONFIG: dict[str, Any] = {
    "default_pool": "pjsk",
    "enabled_pools": ["pjsk", "fgo", "e7", "ak", "pcr", "gi", "vt", "lcb", "lor", "ww", "ygo", "uma", "hs", "sv", "gbf", "ba", "ff14", "sam", "hbr", "member"],
    "answer_timeout": 30,
    "daily_play_limit": 999,
    "super_users": ["273421673"],
    "blacklist": [],
    "group_whitelist": [],
    "whitelist_reject_message": (
        "⚠️ 抱歉，当前群聊未在白名单中，无法使用此功能。"
        "如需使用，请联系管理员将本群添加到白名单。"
    ),
    "game_cooldown_seconds": 0,
    "max_guess_attempts": 10,
    "ranking_display_count": 10,
    "reward_valid_time": 5,
    "effects": {},
    # 小图卡池限制可用效果（e7 公开立绘偏小，切片/分块不适读）
    "pool_effects": {
        "e7": ["light_blur", "heavy_blur"],
        "ak": ["light_blur", "heavy_blur"],
        "sam": ["light_blur", "heavy_blur"],
        "member": ["light_blur", "heavy_blur"],
    },
}


def init_db(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id TEXT PRIMARY KEY,
                user_name TEXT,
                custom_name TEXT,
                score INTEGER DEFAULT 0,
                attempts INTEGER DEFAULT 0,
                correct_attempts INTEGER DEFAULT 0,
                last_play_date TEXT,
                daily_plays INTEGER DEFAULT 0
            )
            """
        )
        cursor.execute("PRAGMA table_info(user_stats)")
        columns = [column[1] for column in cursor.fetchall()]
        if "custom_name" not in columns:
            cursor.execute("ALTER TABLE user_stats ADD COLUMN custom_name TEXT")
        conn.commit()


@dataclass
class GameSession:
    guess_attempts_count: int = 0
    game_ended_by_timeout: bool = False
    game_ended_by_attempts: bool = False
    winner_info: Optional[dict] = None
    user_stats_recorded: set = field(default_factory=set)
    game_data: Optional[dict] = None
    winners_list: list = field(default_factory=list)
    first_correct_time: Optional[float] = None
    is_test: bool = False
    pool_id: str = "pjsk"
    # 每局唯一 token：阻止上一局未取消的 timeout 误杀下一局
    token: int = 0
    finishing: bool = False
    done_event: asyncio.Event = field(default_factory=asyncio.Event)
    timeout_task: Optional[asyncio.Task] = None
    reward_stop_task: Optional[asyncio.Task] = None


class GuessCard(NcatBotPlugin):
    name = "GuessCard"
    version = "2.0.0"

    async def on_load(self):
        self.init_defaults(DEFAULT_CONFIG)
        self.plugin_dir = PLUGIN_DIR
        self.resources_dir = RESOURCES_DIR
        self.data_dir = PLUGIN_DIR / "data"
        self.output_dir = self.data_dir / "output"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 兼容旧 PjskGuessCard 积分库
        legacy_db = PLUGIN_DIR.parent / "PjskGuessCard" / "data" / "guess_card_data.db"
        self.db_path = str(self.data_dir / "guess_card_data.db")
        if legacy_db.exists() and not Path(self.db_path).exists():
            import shutil

            shutil.copy2(legacy_db, self.db_path)
            _log.info("已迁移旧版猜卡积分库")
        init_db(self.db_path)

        enabled = list(
            self._cfg(
                "enabled_pools",
                [
                    "pjsk",
                    "fgo",
                    "e7",
                    "ak",
                    "pcr",
                    "gi",
                    "vt",
                    "lcb",
                    "lor",
                    "ww",
                    "ygo",
                    "uma",
                    "hs",
                    "sv",
                    "gbf",
                    "ba",
                    "ff14",
                    "sam",
                    "member",
                ],
            )
            or [
                "pjsk",
                "fgo",
                "e7",
                "ak",
                "pcr",
                "gi",
                "vt",
                "lcb",
                "lor",
                "ww",
                "ygo",
                "uma",
                "hs",
                "sv",
                "gbf",
                "ba",
                "ff14",
                "sam",
                "member",
            ]
        )
        self.custom_nicknames = load_custom_nicknames(self.data_dir)
        self.pools: dict[str, CardPool] = {}
        for pid in enabled:
            if pid == "member":
                self.pools[pid] = make_member_pool_stub()
                continue
            self.pools[pid] = load_pool(
                self.resources_dir, pid, self.custom_nicknames.get(pid)
            )
        # 配置未写 member 时也默认挂上动态池
        if "member" not in self.pools:
            self.pools["member"] = make_member_pool_stub()

        self.last_game_end_time: dict[str, float] = {}
        self.last_round_context: dict[str, dict[str, str]] = {}
        self.custom_nicknames: dict[str, Any] = {}
        self.session_locks: dict[str, asyncio.Lock] = {}
        self.active_game_sessions: set[str] = set()
        self.game_sessions: dict[str, GameSession] = {}
        self._session_token_seq = itertools.count(1)
        self.image_cache = LRUCache(max_size=30)
        self.http_session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task] = set()

        self.effect_processor = ImageEffectProcessor(dict(self.config))
        self._load_fonts()
        self._normalize_lists()
        self._cleanup_output_dir()

        cleanup = asyncio.create_task(self._periodic_cleanup_task())
        self._track_task(cleanup)

        ok_pools = [p for p in self.pools.values() if p.ok]
        if not ok_pools:
            _log.error("没有任何可用卡池，猜卡功能将不可用")
        else:
            detail = ", ".join(
                f"{p.pool_id}:{len(p.cards)}" for p in ok_pools
            )
            _log.info("GuessCard v%s 加载完成 [%s]", self.version, detail)

    async def on_close(self):
        for session in list(self.game_sessions.values()):
            self._end_session(session)
        if self._background_tasks:
            tasks = list(self._background_tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._background_tasks.clear()
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()

    def _cfg(self, key: str, default=None):
        return self.get_config(key, default)

    def _track_task(self, task: asyncio.Task):
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _load_fonts(self):
        font_path = self.resources_dir / "pjsk" / "font.ttf"
        try:
            self.title_font = ImageFont.truetype(str(font_path), 48)
            self.header_font = ImageFont.truetype(str(font_path), 28)
            self.body_font = ImageFont.truetype(str(font_path), 26)
            self.id_font = ImageFont.truetype(str(font_path), 16)
            self.medal_font = ImageFont.truetype(str(font_path), 36)
        except OSError:
            _log.error("字体未找到: %s，使用默认字体", font_path)
            default = ImageFont.load_default()
            self.title_font = default
            self.header_font = default
            self.body_font = default
            self.id_font = default
            self.medal_font = default

    def _default_pool_id(self) -> str:
        return resolve_pool_id(str(self._cfg("default_pool", "pjsk")), default="pjsk") or "pjsk"

    def _pool_allowed_effects(self, pool_id: str) -> Optional[list[str]]:
        """若配置了 pool_effects[pool_id]，则该卡池只能用这些效果；否则不限制。"""
        mapping = self._cfg("pool_effects", {}) or {}
        if not isinstance(mapping, dict):
            return None
        allowed = mapping.get(pool_id)
        if not allowed:
            return None
        return [str(x) for x in allowed]

    def _get_pool(self, pool_id: str) -> Optional[CardPool]:
        pool = self.pools.get(pool_id)
        if pool and pool.ok:
            return pool
        return None

    def _parse_pool_arg(self, raw: str) -> tuple[Optional[str], str]:
        """解析参数。返回 (显式卡池或 None=默认, 剩余文本)。"""
        text = (raw or "").strip()
        if not text:
            return None, ""
        parts = text.split(maxsplit=1)
        first = parts[0].strip().lower()
        if first in POOL_DISPLAY or resolve_pool_id(first) is not None:
            pid = resolve_pool_id(first)
            rest = parts[1].strip() if len(parts) > 1 else ""
            return pid, rest
        return None, text

    def _normalize_lists(self):
        self.group_whitelist = {str(x) for x in (self._cfg("group_whitelist", []) or [])}
        self.blacklist = {str(x) for x in (self._cfg("blacklist", []) or [])}
        self.super_users = {str(x) for x in (self._cfg("super_users", []) or [])}

    def _refresh_acl(self):
        self._normalize_lists()

    def _is_user_blacklisted(self, user_id: str) -> bool:
        self._refresh_acl()
        return str(user_id) in self.blacklist

    def _is_admin(self, user_id: str) -> bool:
        self._refresh_acl()
        return str(user_id) in self.super_users

    def _persist_super_users(self) -> None:
        """把当前管理员集合写回配置并刷新内存。"""
        ordered = sorted(self.super_users, key=lambda x: int(x) if str(x).isdigit() else str(x))
        self.set_config("super_users", ordered)
        self.super_users = {str(x) for x in ordered}

    def _extract_target_user_ids(self, event: GroupMessageEvent) -> list[str]:
        """从 @ 与纯数字 QQ 提取目标用户（排除机器人自己）。"""
        ids: list[str] = []
        seen: set[str] = set()
        bot_id = str(getattr(self, "self_id", None) or getattr(getattr(self, "api", None), "self_id", "") or "")

        def add(uid: object) -> None:
            s = str(uid or "").strip()
            if not s or not s.isdigit() or s == "all" or s == bot_id or s in seen:
                return
            seen.add(s)
            ids.append(s)

        for seg in event.message or []:
            if isinstance(seg, At):
                add(getattr(seg, "user_id", None))

        raw = event.raw_message or ""
        for m in re.finditer(r"\[CQ:at,qq=(\d+)\]", raw):
            add(m.group(1))
        # 去掉 CQ 后再扫裸 QQ，避免把 at 里的数字重复/漏扫
        cleaned = re.sub(r"\[CQ:at,qq=\d+\]", " ", raw)
        cleaned = re.sub(
            r"^(猜卡面|pjsk猜卡面|猜卡|gc)\s*", "", cleaned, flags=re.I
        )
        cleaned = re.sub(
            r"^(添加|删除|移除)管理员|管理员列表|列出管理员|管理员\b",
            " ",
            cleaned,
        )
        for m in re.finditer(r"\b(\d{5,12})\b", cleaned):
            add(m.group(1))
        return ids

    @staticmethod
    def _admin_mgmt_action(text: str) -> Optional[str]:
        """识别管理员子命令：add / remove / list；否则 None。"""
        t = (text or "").strip()
        t = re.sub(r"\[CQ:at,qq=\d+\]", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        if re.match(r"^(添加管理员)\b", t):
            return "add"
        if re.match(r"^(删除管理员|移除管理员)\b", t):
            return "remove"
        if re.fullmatch(r"(管理员列表|列出管理员|管理员)", t):
            return "list"
        return None

    async def _handle_admin_mgmt(self, event: GroupMessageEvent, raw: str) -> None:
        action = self._admin_mgmt_action(raw)
        if not action:
            return
        sender = str(event.user_id)
        if not self._is_admin(sender):
            await self._reply_text(event, "哎呀，管理猜卡管理员只有现有管理员才能操作哦~ 😊")
            return

        if action == "list":
            self._refresh_acl()
            if not self.super_users:
                await self._reply_text(event, "当前还没有配置猜卡管理员。")
                return
            lines = "\n".join(f"- {uid}" for uid in sorted(self.super_users, key=lambda x: int(x) if x.isdigit() else x))
            await self._reply_text(event, f"猜卡管理员列表（{len(self.super_users)}）:\n{lines}")
            return

        targets = self._extract_target_user_ids(event)
        if not targets:
            tip = (
                "请 @ 成员或附上 QQ 号，例如：\n猜卡 添加管理员 @成员"
                if action == "add"
                else "请 @ 成员或附上 QQ 号，例如：\n猜卡 删除管理员 @成员"
            )
            await self._reply_text(event, tip)
            return

        self._refresh_acl()
        if action == "add":
            added, existed = [], []
            for uid in targets:
                if uid in self.super_users:
                    existed.append(uid)
                else:
                    self.super_users.add(uid)
                    added.append(uid)
            if added:
                self._persist_super_users()
            parts = []
            if added:
                parts.append("已添加: " + "、".join(added))
            if existed:
                parts.append("已是管理员: " + "、".join(existed))
            await self._reply_text(event, "\n".join(parts) or "没有变更。")
            return

        # remove
        removed, missing = [], []
        for uid in targets:
            if uid not in self.super_users:
                missing.append(uid)
                continue
            if uid == sender and len(self.super_users) <= 1:
                await self._reply_text(event, "不能删除自己：至少需要保留一名猜卡管理员。")
                return
            self.super_users.discard(uid)
            removed.append(uid)
        if removed:
            # 禁止删光
            if not self.super_users:
                self.super_users.add(sender)
                await self._reply_text(event, "操作取消：不能清空全部管理员。")
                return
            self._persist_super_users()
        parts = []
        if removed:
            parts.append("已删除: " + "、".join(removed))
        if missing:
            parts.append("本就不是管理员: " + "、".join(missing))
        await self._reply_text(event, "\n".join(parts) or "没有变更。")

    def _get_display_name(
        self, user_id: str, original_name: Optional[str] = None
    ) -> str:
        if self._is_user_blacklisted(user_id):
            return "[此用户已被BOT拉黑]"
        return original_name if original_name else "未知用户"

    def _is_group_allowed(self, group_id) -> bool:
        self._refresh_acl()
        if not self.group_whitelist:
            return True
        return str(group_id) in self.group_whitelist

    def _whitelist_reject(self) -> Optional[str]:
        msg = self._cfg("whitelist_reject_message", "") or ""
        return msg.strip() or None

    def _sender_name(self, event: GroupMessageEvent) -> str:
        sender = getattr(event, "sender", None)
        return getattr(sender, "nickname", None) or str(event.user_id)

    async def _reply_text(self, event: GroupMessageEvent, text: str):
        await event.reply(text=text, at_sender=False)

    async def _reply_image(
        self, event: GroupMessageEvent, text: str = "", image_path: str = ""
    ):
        if text and image_path:
            await event.reply(
                rtf=MessageArray([PlainText(text=text), Image(file=image_path)]),
                at_sender=False,
            )
        elif image_path:
            await event.reply(image=image_path, at_sender=False)
        else:
            await self._reply_text(event, text)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.http_session is None or self.http_session.closed:
            async with self._session_lock:
                if self.http_session is None or self.http_session.closed:
                    # wiki.gg 等会拦默认 UA
                    self.http_session = aiohttp.ClientSession(
                        headers={
                            "User-Agent": (
                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/124.0.0.0 Safari/537.36"
                            ),
                            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                        }
                    )
        return self.http_session

    async def _periodic_cleanup_task(self):
        while True:
            await asyncio.sleep(3600)
            try:
                self._cleanup_output_dir()
            except Exception as e:
                _log.error("周期性清理失败: %s", e, exc_info=True)

    def _get_resource_url(self, relative_path: str) -> str:
        from .pools import PJSK_REMOTE_BASE

        return f"{PJSK_REMOTE_BASE}/{'/'.join(Path(relative_path).parts)}"

    @staticmethod
    def _image_url_candidates(url: str) -> list[str]:
        """生成备用图床 URL（Fandom 缩放链经常 503）。"""
        urls: list[str] = []
        seen: set[str] = set()

        def add(u: str) -> None:
            u = (u or "").strip()
            if u and u not in seen:
                seen.add(u)
                urls.append(u)

        add(url)
        # xivapi / cafemaker icon: try hr1 <-> normal, and both hosts
        m = re.match(
            r"^(https://(?:xivapi\.com|cafemaker\.wakingsands\.com)/i/)(\d{6}/\d{6})(_hr1)?(\.png)$",
            url,
            re.I,
        )
        if m:
            base, path, hr, ext = m.groups()
            hosts = [
                "https://cafemaker.wakingsands.com/i/",
                "https://xivapi.com/i/",
            ]
            for host in hosts:
                add(f"{host}{path}_hr1{ext}")
                add(f"{host}{path}{ext}")
        # /revision/latest/scale-to-width-down/621?... -> /revision/latest
        no_scale = re.sub(r"/scale-to-width-down/\d+", "", url)
        add(no_scale)
        # 去掉 cb 查询参数
        if "?" in no_scale:
            add(no_scale.split("?", 1)[0])
        # 再去掉 /revision/latest 直接吃 images 路径（少数镜像可用）
        bare = re.sub(r"/revision/latest/?$", "", no_scale.split("?", 1)[0])
        add(bare)
        return urls

    @staticmethod
    def _image_request_headers(url: str) -> dict[str, str]:
        headers: dict[str, str] = {}
        if "huijistatic.com" in url or "huijiwiki.com" in url:
            headers["Referer"] = "https://ff14.huijiwiki.com/"
            headers["Accept"] = "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"
        elif "umamusume.jp" in url or "microcms-assets.io" in url:
            headers["Referer"] = "https://umamusume.jp/"
        elif "prts.wiki" in url or "torappu.prts" in url:
            headers["Referer"] = "https://prts.wiki/"
        elif "wiki.gg" in url:
            # https://libraryofruina.wiki.gg/... → 对应站 Referer
            try:
                host = url.split("//", 1)[1].split("/", 1)[0]
            except IndexError:
                host = "limbuscompany.wiki.gg"
            headers["Referer"] = f"https://{host}/"
        elif "wikia.nocookie.net" in url:
            # https://static.wikia.nocookie.net/<wiki>/images/...
            m = re.search(r"wikia\.nocookie\.net/([^/]+)/", url)
            wiki = m.group(1) if m else "www"
            headers["Referer"] = f"https://{wiki}.fandom.com/"
        elif "fandom.com" in url:
            headers["Referer"] = "https://www.fandom.com/"
        elif "estertion.win" in url:
            headers["Referer"] = "https://redive.estertion.win/"
        elif "akamaized.net" in url:
            headers["Referer"] = "https://game.granbluefantasy.jp/"
        return headers

    async def _fetch_via_curl(self, url: str) -> Optional[bytes]:
        """huiji 等 CDN 会拦 Python TLS（返回 567）；curl 指纹通常可过。"""
        headers = self._image_request_headers(url)
        cmd = [
            "curl",
            "-sL",
            "--fail",
            "-A",
            (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "--connect-timeout",
            "20",
            "--max-time",
            "45",
        ]
        if headers.get("Referer"):
            cmd.extend(["-e", headers["Referer"]])
        cmd.append(url)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=50)
        except FileNotFoundError:
            _log.warning("curl 不可用，无法回退拉取 %s", url)
            return None
        except (asyncio.TimeoutError, OSError) as e:
            _log.warning("curl 拉取失败 %s: %s", url, e)
            return None
        if proc.returncode != 0 or not stdout or len(stdout) < 100:
            return None
        if len(stdout) > 10 * 1024 * 1024:
            _log.error("curl 图片过大: %s", len(stdout))
            return None
        return stdout

    async def _fetch_remote_image_bytes(self, url: str) -> Optional[bytes]:
        """带重试拉远程图；对 502/503/429/567 退避；huiji 失败时 curl 回退。"""
        session = await self._get_session()
        max_size = 10 * 1024 * 1024
        headers = self._image_request_headers(url)
        last_err: Optional[BaseException] = None
        blocked_tls = False
        for attempt in range(3):
            try:
                async with session.get(
                    url,
                    headers=headers or None,
                    timeout=aiohttp.ClientTimeout(total=25),
                ) as response:
                    if response.status in (429, 502, 503, 504, 567):
                        if response.status == 567:
                            blocked_tls = True
                        last_err = aiohttp.ClientResponseError(
                            response.request_info,
                            response.history,
                            status=response.status,
                            message=response.reason or "",
                            headers=response.headers,
                        )
                        await asyncio.sleep(0.4 * (attempt + 1))
                        continue
                    response.raise_for_status()
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > max_size:
                        _log.error("远程图片过大: %s", content_length)
                        return None
                    image_data = bytearray()
                    async for chunk in response.content.iter_chunked(8192):
                        image_data.extend(chunk)
                        if len(image_data) > max_size:
                            _log.error("远程图片超过大小限制")
                            return None
                    return bytes(image_data)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_err = e
                await asyncio.sleep(0.4 * (attempt + 1))
        # 部分 CDN 对 Python TLS / WAF 不友好：改用 curl
        if (
            blocked_tls
            or "huijistatic.com" in url
            or "huijiwiki.com" in url
            or "umamusume.jp" in url
            or "wiki.gg" in url
            or "wikia.nocookie.net" in url
        ):
            data = await self._fetch_via_curl(url)
            if data:
                return data
        if last_err:
            _log.warning("拉取失败 %s: %s", url, last_err)
        return None

    async def _open_image(
        self, image_source: Union[Path, str], extra_urls: Optional[list] = None
    ) -> Optional[PILImage.Image]:
        if not image_source and not extra_urls:
            return None
        try:
            candidates: list[str] = []
            seen: set[str] = set()

            def add_src(src: Union[Path, str, None]) -> None:
                if not src:
                    return
                s = str(src).strip()
                if not s or s in seen:
                    return
                if s.startswith(("http://", "https://")):
                    # 注意：不要先把 s 放进 seen，否则 candidates 生成的首条
                    # （就是 s 本身）会被跳过，导致 FGO 等无备用链的卡池瞬间失败。
                    for u in self._image_url_candidates(s):
                        if u not in seen:
                            seen.add(u)
                            candidates.append(u)
                else:
                    seen.add(s)
                    candidates.append(s)

            add_src(image_source)
            for u in extra_urls or []:
                add_src(u)

            for src in candidates:
                if src.startswith(("http://", "https://")):
                    image_data = await self._fetch_remote_image_bytes(src)
                    if not image_data:
                        continue
                    try:
                        img = PILImage.open(io.BytesIO(image_data))
                        return ImageEffectProcessor.ensure_processable(img)
                    except Exception as e:
                        _log.warning("解码失败 %s: %s", src, e)
                        continue
                try:
                    img = PILImage.open(src)
                    return ImageEffectProcessor.ensure_processable(img)
                except Exception as e:
                    _log.warning("打开本地图失败 %s: %s", src, e)
                    continue
            _log.error(
                "无法打开图片（已尝试备用 URL）: %s", image_source or (extra_urls or [None])[0]
            )
            return None
        except Exception as e:
            _log.error("无法打开图片 %s: %s", image_source, e)
            return None

    async def _apply_effects(
        self,
        image_source: Union[Path, str],
        effect_names: list,
        extra_urls: Optional[list] = None,
    ) -> Optional[str]:
        # URL 里可能含 @（Atlas CharaGraph），aiohttp 可直接请求
        img = await self._open_image(image_source, extra_urls=extra_urls)
        if not img:
            # FGO 个别灵基图缺失时回退 face_url
            return None
        try:
            processed = await asyncio.to_thread(
                self.effect_processor.apply_effects, img, effect_names
            )
            self.output_dir.mkdir(parents=True, exist_ok=True)
            img_path = self.output_dir / f"processed_{time.time_ns()}.png"
            await asyncio.to_thread(processed.save, img_path)
            return str(img_path)
        finally:
            img.close()

    def _cleanup_output_dir(self, max_age_seconds: int = 3600):
        if not self.output_dir.exists():
            return
        now = time.time()
        prefixes = ("ranking_", "answer_", "blurred_", "processed_")
        files_to_delete = []
        try:
            for filename in os.listdir(self.output_dir):
                file_path = self.output_dir / filename
                if (
                    file_path.is_file()
                    and filename.startswith(prefixes)
                    and filename.endswith((".png", ".jpg"))
                    and (now - file_path.stat().st_mtime) > max_age_seconds
                ):
                    files_to_delete.append(str(file_path))
                    os.remove(file_path)
            if files_to_delete:
                keys_to_remove = [
                    k
                    for k, v in self.image_cache.cache.items()
                    if v in files_to_delete
                ]
                for key in keys_to_remove:
                    del self.image_cache.cache[key]
        except Exception as e:
            _log.error("清理图片出错: %s", e)

    def start_new_game(
        self,
        pool_id: Optional[str] = None,
        force_effect_names: Optional[list] = None,
    ) -> Optional[dict]:
        pool_id = pool_id or self._default_pool_id()
        pool = self._get_pool(pool_id)
        if not pool:
            _log.error("卡池不可用: %s", pool_id)
            return None

        card = random.choice(pool.cards)
        character = pool.characters.get(card["characterId"])
        if not character:
            _log.error("未找到角色 ID %s @%s", card["characterId"], pool_id)
            return None

        if pool_id == "pjsk":
            card_state = random.choice(["normal", "after_training"])
            card = dict(card)
            card["_card_state"] = card_state
            image_source = resolve_card_image_url(pool_id, card)
        else:
            card_state = f"asc{card.get('ascension', 1)}"
            image_source = resolve_card_image_url(pool_id, card)
            # 若构造 URL 失效，下面开局时再回退 face_url

        image_source = resolve_local_card_path(
            self.resources_dir, pool_id, str(image_source)
        )

        # 武士刀等多源图标：把备用 URL 挂到 card，供打开图片时回退
        if card.get("image_urls"):
            card = dict(card)

        allowed = self._pool_allowed_effects(pool_id)
        if force_effect_names:
            effect_names = list(force_effect_names)
            if allowed is not None:
                effect_names = [n for n in effect_names if n in allowed]
                if not effect_names:
                    effect_names = [allowed[0]] if allowed else ["light_blur"]
            effect_name = " + ".join(
                self.effect_processor.EFFECT_NAMES.get(n, n) for n in effect_names
            )
        else:
            effect_names, effect_name = (
                self.effect_processor.random_effect_combination(allowed=allowed)
            )
        difficulty = self.effect_processor.calculate_difficulty(effect_names)
        return {
            "pool_id": pool_id,
            "pool_display": pool.display_name,
            "card": card,
            "card_state": card_state,
            "card_image_source": image_source,
            "face_url": card.get("face_url"),
            "image_urls": list(card.get("image_urls") or []),
            "character": character,
            "score": difficulty,
            "show_rarity_hint": random.choice([True, False]),
            "show_training_hint": random.choice([True, False]),
            "show_class_hint": random.choice([True, False]),
            "effect_names": effect_names,
            "effect_name": effect_name,
            "difficulty": difficulty,
        }

    def _build_intro(self, game_data: dict, timeout: int, test: bool) -> str:
        pool_id = game_data.get("pool_id", "pjsk")
        hints = []
        if game_data.get("show_rarity_hint"):
            if pool_id == "sam":
                rarity_label = game_data["character"].get("rarityLabel")
                if rarity_label:
                    hints.append(f"品质提示: {rarity_label}")
            else:
                rarity = game_data["card"].get("cardRarityType", "")
                if rarity.startswith("rarity_"):
                    n = rarity.split("_")[-1]
                    if n.isdigit():
                        hints.append(f"星级提示: {'⭐' * int(n)}")
                    else:
                        hints.append(f"星级提示: {rarity}")
                else:
                    hints.append(f"星级提示: {rarity}")
        if pool_id == "pjsk" and game_data.get("show_training_hint"):
            state_text = (
                "花后" if game_data["card_state"] == "after_training" else "花前"
            )
            hints.append(f"状态提示: {state_text}")
        if pool_id == "fgo" and game_data.get("show_class_hint"):
            cls = game_data["card"].get("className") or game_data["character"].get(
                "className"
            )
            hints.append(f"职阶提示: {CLASS_CN.get(cls, cls)}")
        if pool_id == "e7" and game_data.get("show_class_hint"):
            role = game_data["card"].get("className") or game_data["character"].get(
                "className"
            )
            attr = game_data["card"].get("attribute") or game_data["character"].get(
                "attribute"
            )
            if role:
                hints.append(f"职业提示: {role}")
            if attr:
                hints.append(f"属性提示: {attr}")

        prefix = "【测试模式】" if test else ""
        pool_label = game_data.get("pool_display") or POOL_DISPLAY.get(pool_id, pool_id)
        ask_what = (
            "刀的名称"
            if pool_id == "sam"
            else "昵称或群名片"
            if pool_id == "member"
            else "角色名称"
        )
        intro = (
            f"{prefix}【{pool_label}】请在{timeout}秒内发送{ask_what}进行回答哦(无需@机器人)\n"
            f"本轮图片效果: {game_data.get('effect_name', '无效果')}\n"
            f"猜对得分: {game_data.get('difficulty', 1)}分\n"
        )
        if hints:
            intro += "\n".join(hints) + "\n"
        return intro

    async def _start_member_game(
        self,
        event: GroupMessageEvent,
        force_effect_names: Optional[list] = None,
    ) -> Optional[dict]:
        """从本群成员中抽一位，下载头像并套用效果。"""
        group_id = str(event.group_id)
        bot_id = str(getattr(event, "self_id", "") or "")
        try:
            members_response = await self.api.qq.query.get_group_member_list(
                group_id=group_id
            )
        except Exception as e:
            _log.error("拉取群成员列表失败: %s", e, exc_info=True)
            return None

        members = CommonUtil.parse_group_member_list(members_response)
        candidates = []
        for m in members:
            uid = str(m.user_id or "")
            if not uid or uid == bot_id:
                continue
            if getattr(m, "is_robot", None):
                continue
            nick = (m.nickname or "").strip()
            card = (m.card or "").strip()
            if not nick and not card:
                continue
            candidates.append((uid, nick, card))

        if len(candidates) < 3:
            return {"_error": "insufficient"}

        random.shuffle(candidates)
        allowed = self._pool_allowed_effects("member") or [
            "light_blur",
            "heavy_blur",
        ]
        if force_effect_names:
            effect_names = list(force_effect_names)
            if allowed is not None:
                effect_names = [n for n in effect_names if n in allowed]
                if not effect_names:
                    effect_names = [allowed[0]] if allowed else ["light_blur"]
            effect_name = " + ".join(
                self.effect_processor.EFFECT_NAMES.get(n, n) for n in effect_names
            )
        else:
            effect_names, effect_name = (
                self.effect_processor.random_effect_combination(allowed=allowed)
            )
        difficulty = self.effect_processor.calculate_difficulty(effect_names)

        for uid, nick, card in candidates[:40]:
            try:
                avatar_path = await CommonUtil.get_avatar_async(uid)
            except Exception as e:
                _log.warning("下载群友头像失败 uid=%s: %s", uid, e)
                continue
            if not avatar_path or str(avatar_path).endswith("default.jpg"):
                continue
            if not Path(avatar_path).is_file():
                continue

            character = build_member_character(
                user_id=uid, nickname=nick, card=card
            )
            keys = set(character.get("_answer_keys") or [])
            if not keys:
                continue

            return {
                "pool_id": "member",
                "pool_display": POOL_DISPLAY.get("member", "群友"),
                "card": {
                    "id": uid,
                    "characterId": uid,
                    "image_url": str(avatar_path),
                },
                "card_state": "avatar",
                "card_image_source": str(avatar_path),
                "face_url": str(avatar_path),
                "image_urls": [str(avatar_path)],
                "character": character,
                "valid_answers": keys,
                "score": difficulty,
                "show_rarity_hint": False,
                "show_training_hint": False,
                "show_class_hint": False,
                "effect_names": effect_names,
                "effect_name": effect_name,
                "difficulty": difficulty,
            }
        return {"_error": "avatar"}

    async def _run_game(
        self,
        event: GroupMessageEvent,
        *,
        pool_id: Optional[str] = None,
        is_test: bool = False,
        force_effect=None,
    ):
        session_id = str(event.group_id)
        pool_id = pool_id or self._default_pool_id()
        if not self._get_pool(pool_id):
            avail = "、".join(p.display_name for p in self.pools.values() if p.ok)
            await self._reply_text(
                event, f"卡池「{pool_id}」不可用。当前可用: {avail or '无'}"
            )
            return

        if session_id not in self.session_locks:
            self.session_locks[session_id] = asyncio.Lock()
        lock = self.session_locks[session_id]

        async with lock:
            if session_id in self.active_game_sessions:
                await self._reply_text(
                    event, "当前已经有一个游戏在进行中啦~ 等它结束后再来玩吧！"
                )
                return
            if not is_test:
                cooldown = int(self._cfg("game_cooldown_seconds", 60))
                last_end = self.last_game_end_time.get(session_id, 0)
                elapsed = time.time() - last_end
                if elapsed < cooldown:
                    remaining = cooldown - elapsed
                    display = (
                        f"{remaining:.3f}" if remaining < 1 else str(int(remaining))
                    )
                    await self._reply_text(
                        event, f"让我们休息一下吧！{display}秒后再来玩哦~ 😊"
                    )
                    return
                if not self._can_play(str(event.user_id)):
                    limit = self._cfg("daily_play_limit", 999)
                    await self._reply_text(
                        event,
                        f"今天的游戏次数已经用完啦~ 明天再来玩吧！"
                        f"每天最多可以玩{limit}次哦~ ✨",
                    )
                    return
            self.active_game_sessions.add(session_id)

        try:
            processed = None
            game_data = None
            if pool_id == "member":
                game_data = await self._start_member_game(
                    event, force_effect_names=force_effect
                )
                if game_data and game_data.get("_error") == "insufficient":
                    await self._reply_text(
                        event, "本群可用群友不足（至少需要 3 人），暂时没法开局哦~"
                    )
                    return
                if game_data and game_data.get("_error") == "avatar":
                    await self._reply_text(
                        event, "群友头像下载失败，请稍后再试一次吧~"
                    )
                    return
                if not game_data:
                    await self._reply_text(
                        event, "拉取群成员失败，请稍后再试一次吧~"
                    )
                    return
                processed = await self._apply_effects(
                    game_data["card_image_source"],
                    game_data.get("effect_names", []),
                    extra_urls=list(game_data.get("image_urls") or []),
                )
            else:
                # 远端图床（Fandom 等）偶发 503：换卡重试
                for attempt in range(4):
                    game_data = self.start_new_game(
                        pool_id=pool_id, force_effect_names=force_effect
                    )
                    if not game_data:
                        break
                    extras = list(game_data.get("image_urls") or [])
                    if game_data.get("face_url"):
                        extras.append(game_data["face_url"])
                    processed = await self._apply_effects(
                        game_data["card_image_source"],
                        game_data.get("effect_names", []),
                        extra_urls=extras,
                    )
                    if processed:
                        break
                    _log.warning(
                        "开局取图失败，换卡重试 %s/%s pool=%s",
                        attempt + 1,
                        4,
                        pool_id,
                    )
            if not game_data:
                await self._reply_text(
                    event,
                    "......开始游戏失败，可能是缺少资源文件或配置错误，请联系管理员。",
                )
                return
            if not processed:
                await self._reply_text(
                    event,
                    "哎呀，图片源暂时不稳定呢~ 请稍后再试一次吧！",
                )
                return

            timeout = int(self._cfg("answer_timeout", 30))
            _log.info(
                "[猜卡%s][%s] 答案=%s 效果=%s",
                "测试" if is_test else "",
                pool_id,
                display_member_answer(game_data["character"])
                if pool_id == "member"
                else display_answer_name(game_data["character"]),
                game_data.get("effect_name"),
            )
            intro = self._build_intro(game_data, timeout, is_test)
            try:
                await self._reply_image(event, intro, processed)
            except Exception as e:
                _log.error("发送问题图片失败: %s", e, exc_info=True)
                await self._reply_text(event, "发送问题图片时出错，游戏中断。")
                return

            if not is_test:
                self._record_game_start(str(event.user_id), self._sender_name(event))

            # 清掉可能残留的上一局定时器，避免跨局误触发
            stale = self.game_sessions.pop(session_id, None)
            if stale:
                self._cancel_session_timers(stale)

            token = next(self._session_token_seq)
            session = GameSession(
                game_data=game_data,
                is_test=is_test,
                pool_id=pool_id,
                token=token,
            )
            self.game_sessions[session_id] = session
            session.timeout_task = asyncio.create_task(
                self._timeout_watcher(session_id, timeout, token)
            )
            self._track_task(session.timeout_task)

            await session.done_event.wait()
            await self._finish_game(event, session_id)
        finally:
            self.active_game_sessions.discard(session_id)
            session = self.game_sessions.pop(session_id, None)
            if session:
                self._cancel_session_timers(session)
                if session.game_data and session.pool_id:
                    character = session.game_data.get("character") or {}
                    cid = character.get("characterId")
                    if cid:
                        self.last_round_context[session_id] = {
                            "pool_id": session.pool_id,
                            "character_id": str(cid),
                        }
            self.last_game_end_time[session_id] = time.time()

    def _cancel_session_timers(self, session: GameSession) -> None:
        for task in (session.timeout_task, session.reward_stop_task):
            if task and not task.done():
                task.cancel()

    def _end_session(
        self,
        session: GameSession,
        *,
        by_timeout: bool = False,
        by_attempts: bool = False,
    ) -> None:
        """安全结束一局：取消计时器 + 唤醒等待方。可重复调用。"""
        if by_timeout:
            session.game_ended_by_timeout = True
        if by_attempts:
            session.game_ended_by_attempts = True
        session.finishing = True
        self._cancel_session_timers(session)
        session.done_event.set()

    async def _timeout_watcher(
        self, session_id: str, timeout: float, token: int
    ):
        try:
            await asyncio.sleep(timeout)
            session = self.game_sessions.get(session_id)
            # token 不一致说明已是下一局，绝不能动
            if not session or session.token != token:
                return
            if session.finishing or session.winner_info:
                return
            self._end_session(session, by_timeout=True)
        except asyncio.CancelledError:
            return

    async def _finish_game(self, event: GroupMessageEvent, session_id: str):
        session = self.game_sessions.get(session_id)
        if not session or not session.game_data:
            return
        game_data = session.game_data
        if game_data.get("pool_id") == "member":
            correct_name = display_member_answer(game_data["character"])
        else:
            correct_name = display_answer_name(game_data["character"])
        text = ""
        if session.winner_info:
            winners = session.winners_list or [
                {
                    "user_id": session.winner_info["id"],
                    "user_name": session.winner_info["name"],
                }
            ]
            if session.is_test:
                if len(winners) == 1:
                    text = (
                        f"【测试模式】{winners[0]['user_name']}答对了呢!（测试模式不计分）\n"
                        f"正确答案是: {correct_name}"
                    )
                else:
                    names = "、".join(w["user_name"] for w in winners)
                    text = (
                        f"🎉 【测试模式】恭喜以下玩家答对！（测试模式不计分）\n"
                        f"{names}\n\n正确答案是: {correct_name}"
                    )
            else:
                score = session.winner_info["score"]
                if len(winners) == 1:
                    self._update_stats(
                        winners[0]["user_id"],
                        winners[0]["user_name"],
                        score,
                        correct=True,
                    )
                    text = (
                        f"{winners[0]['user_name']}答对了呢!获得了{score}分！"
                        f"继续加油哦~\n正确答案是: {correct_name}"
                    )
                else:
                    for w in winners:
                        self._update_stats(
                            w["user_id"], w["user_name"], score, correct=True
                        )
                    names = "、".join(w["user_name"] for w in winners)
                    text = (
                        f"🎉 恭喜以下玩家答对！每人获得{score}分！\n"
                        f"{names}\n\n正确答案是: {correct_name}"
                    )
        elif session.game_ended_by_attempts:
            text = (
                f"哎呀，本轮猜测次数已经用完了呢~ 没关系，下次一定可以的！\n"
                f"正确答案是: {correct_name}\n"
            )
        else:
            text = f"时间到啦~ 大家有没有猜出来呢？\n正确答案是: {correct_name}\n"

        tips = []
        if game_data.get("pool_id") != "member":
            tips = suggest_answers(game_data.get("character") or {}, limit=4)
        if text and tips:
            text = f"{text.rstrip()}\n也可答: {' / '.join(tips)}"

        wiki_info = None
        if game_data.get("pool_id") == "sam":
            # 文案只给名字/品级/等级；完整属性走 wiki 物品卡图片
            wiki_info = await self._get_sam_wiki_info(game_data)
            char = game_data.get("character") or {}
            name = (
                (wiki_info or {}).get("name")
                or char.get("fullNameChinese")
                or char.get("name")
                or correct_name
            )
            ilvl = (wiki_info or {}).get("ilvl") or char.get("itemLevel")
            elvl = (wiki_info or {}).get("elvl") or char.get("equipLevel")
            short = [str(name)]
            if ilvl is not None:
                short.append(f"品级 {ilvl}")
            if elvl is not None:
                short.append(f"{elvl}级")
            text = f"{text.rstrip()}\n\n" + "\n".join(short)
        else:
            details = format_reveal_details(game_data.get("character") or {})
            if text and details:
                text = f"{text.rstrip()}\n{details}"

        if text:
            await self._reply_text(event, text)

        await asyncio.sleep(0.5)
        answer_path = await self._prepare_answer_image(game_data, wiki_info=wiki_info)
        if answer_path:
            try:
                await self._reply_image(event, image_path=answer_path)
            except Exception as e:
                _log.error("发送答案图失败: %s", e)

    async def _get_sam_wiki_info(self, game_data: dict) -> Optional[dict]:
        character = game_data.get("character") or {}
        item_id = character.get("itemId")
        if not item_id:
            return None
        cache_dir = self.data_dir / "sam_wiki"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{int(item_id)}.json"
        raw = None
        if cache_path.exists():
            try:
                raw = json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                raw = None
        if not raw:
            raw = await asyncio.to_thread(fetch_item_json_curl, int(item_id))
            if raw:
                try:
                    cache_path.write_text(
                        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                except Exception as e:
                    _log.warning("缓存武士刀 wiki 数据失败: %s", e)
        if not raw:
            # 退化：用卡池里已有字段拼一份
            return normalize_wiki_item(
                {
                    "中文名": character.get("fullNameChinese") or character.get("name"),
                    "类型": "武士刀",
                    "物理性能": character.get("damagePhys") or 0,
                    "攻击间隔": 2640,
                    "品级": character.get("itemLevel"),
                    "装备等级": character.get("equipLevel"),
                    "品质": character.get("rarity") or 1,
                    "可使用职业显示": "武士",
                },
                fallback=character,
            )
        return normalize_wiki_item(raw, fallback=character)

    async def _prepare_answer_image(
        self, game_data: dict, wiki_info: Optional[dict] = None
    ) -> Optional[str]:
        # 武士刀：灰机 wiki 风格物品卡
        if game_data.get("pool_id") == "sam":
            path = await self._render_sam_answer_card(game_data, wiki_info=wiki_info)
            if path:
                return path
        source = game_data.get("card_image_source")
        if not source:
            return None
        if str(source).startswith(("http://", "https://")):
            img = await self._open_image(source)
            if not img:
                return None
            try:
                optimized = (
                    self.output_dir
                    / f"answer_{game_data['card']['assetbundleName']}_{game_data['card_state']}.png"
                )
                if not optimized.exists():
                    img.thumbnail((800, 800))
                    await asyncio.to_thread(img.save, optimized, "PNG", optimize=True)
                return str(optimized)
            finally:
                img.close()
        return str(source)

    async def _render_sam_answer_card(
        self, game_data: dict, wiki_info: Optional[dict] = None
    ) -> Optional[str]:
        """武士刀揭晓：灰机 wiki 物品卡样式。"""
        card = game_data.get("card") or {}
        info = wiki_info or await self._get_sam_wiki_info(game_data)
        if not info:
            return None

        urls = list(card.get("image_urls") or [])
        primary = game_data.get("card_image_source") or card.get("image_url")
        if primary:
            urls = [str(primary)] + [u for u in urls if u != primary]

        icon = None
        for u in urls:
            icon = await self._open_image(u)
            if icon:
                break

        try:
            out = (
                self.output_dir
                / f"answer_sam_{card.get('assetbundleName', 'x')}_{game_data.get('card_state', '1')}.png"
            )
            font_path = self.resources_dir / "pjsk" / "font.ttf"
            await asyncio.to_thread(
                render_wiki_item_card,
                info,
                icon,
                font_path=font_path if font_path.exists() else None,
                out_path=out,
            )
            return str(out)
        except Exception as e:
            _log.error("生成武士刀 wiki 答案卡失败: %s", e)
            return None
        finally:
            if icon is not None:
                icon.close()

    def _check_answer(self, session: GameSession, answer_key: str) -> bool:
        character = session.game_data["character"]
        keys = character.get("_answer_keys")
        if not keys:
            keys = character_answer_keys(character)
        return answer_key in keys

    def _handle_guess(
        self, session: GameSession, session_id: str, user_id: str, user_name: str, answer: str
    ):
        if session.finishing or not session.game_data:
            return
        answer_key = normalize_answer(answer)
        session_valid = (session.game_data or {}).get("valid_answers")
        if session_valid is not None:
            valid = session_valid
        else:
            pool = self._get_pool(session.pool_id)
            valid = pool.valid_answers if pool else set()
        if not answer_key or answer_key not in valid:
            return

        session.guess_attempts_count += 1
        reward_valid_time = int(self._cfg("reward_valid_time", 5))
        is_correct = self._check_answer(session, answer_key)

        if is_correct:
            now = time.time()
            score = session.game_data["score"]
            if not session.winner_info:
                session.first_correct_time = now
                session.winner_info = {
                    "name": user_name,
                    "id": user_id,
                    "score": score,
                }
                session.winners_list.append(
                    {
                        "user_id": user_id,
                        "user_name": user_name,
                        "answer_time": now,
                        "is_first": True,
                    }
                )
                # 答对后立刻取消本局超时，防止结算拖久时超时任务打到下一局
                if session.timeout_task and not session.timeout_task.done():
                    session.timeout_task.cancel()
                if reward_valid_time > 0:
                    end_token = session.token

                    async def _stop_after():
                        try:
                            await asyncio.sleep(reward_valid_time)
                            cur = self.game_sessions.get(session_id)
                            if (
                                cur
                                and cur.token == end_token
                                and not cur.finishing
                            ):
                                self._end_session(cur)
                        except asyncio.CancelledError:
                            return

                    session.reward_stop_task = asyncio.create_task(_stop_after())
                    self._track_task(session.reward_stop_task)
                else:
                    self._end_session(session)
            else:
                first_t = session.first_correct_time or now
                if (
                    reward_valid_time > 0
                    and (now - first_t) <= reward_valid_time
                    and not any(w["user_id"] == user_id for w in session.winners_list)
                ):
                    session.winners_list.append(
                        {
                            "user_id": user_id,
                            "user_name": user_name,
                            "answer_time": now,
                            "is_first": False,
                        }
                    )
        else:
            if (
                not session.is_test
                and user_id not in session.user_stats_recorded
            ):
                self._update_stats(user_id, user_name, 0, correct=False)
                session.user_stats_recorded.add(user_id)

        max_attempts = int(self._cfg("max_guess_attempts", 10))
        if (
            max_attempts != -1
            and session.guess_attempts_count >= max_attempts
            and not session.finishing
        ):
            self._end_session(session, by_attempts=True)

    # --- 指令 ---

    def _help_text(self, *, hint: str = "") -> str:
        pools = "、".join(
            f"{pid}({p.display_name})" for pid, p in self.pools.items() if p.ok
        )
        head = "✨ 猜卡面指南 ✨\n\n"
        if hint:
            head = f"{hint}\n\n" + head
        return (
            head
            + f"可用卡池: {pools}\n\n"
            "开局（必须指定主题）\n"
            "猜卡 <主题>\n"
            "例如: 猜卡 pjsk / 猜卡 fgo / 猜卡 e7 / 猜卡 vt / "
            "猜卡 ygo / 猜卡 sv / 猜卡 gbf / 猜卡 ba / 猜卡 ff14 / 猜卡 武士刀 / "
            "猜卡 废墟图书馆 / 猜卡 群友\n"
            "（e7/ak/sv/sam/member 仅模糊；sv/szb影之诗 gbf碧蓝幻想 ba蔚蓝档案；"
            "ff14/14/ffxiv 讨伐·零式·绝境 Boss；武士刀/打刀/katana 猜刀名；"
            "lor/ruina/废墟图书馆/废图 人物立绘；群友/群成员/member 猜本群头像）\n\n"
            "数据统计\n"
            "猜卡面排行榜 / 猜卡面分数 / 猜卡面自定义名称\n\n"
            "管理员\n"
            "猜卡 添加管理员 @成员\n"
            "猜卡 删除管理员 @成员\n"
            "猜卡 管理员列表\n"
            "测试猜卡 <主题> 效果名\n"
            "重置猜卡面次数 [QQ]\n"
            "猜卡加答案 <主题> <角色> <别名>\n"
            "猜卡加答案 <别名>（沿用本群上一局角色）\n"
        )

    @non_self
    @registrar.on_group_command("猜卡", "猜卡面", "pjsk猜卡面", "gc")
    async def cmd_start(self, event: GroupMessageEvent, pool: str = ""):
        if not self._is_group_allowed(event.group_id):
            msg = self._whitelist_reject()
            if msg:
                await self._reply_text(event, msg)
            return
        if self._is_user_blacklisted(str(event.user_id)):
            await self._reply_text(event, "抱歉，你已被禁止使用猜卡功能 😔")
            return
        raw = (pool or "").strip()
        if not raw:
            raw_msg = (event.raw_message or "").strip()
            raw = re.sub(
                r"^(猜卡面|pjsk猜卡面|猜卡|gc)\s*", "", raw_msg, flags=re.I
            ).strip()
        # 裸「猜卡」→ 帮助
        if not raw:
            await self._reply_text(
                event, self._help_text(hint="请指定卡池主题，例如：猜卡 fgo")
            )
            return
        # 管理员增删查：猜卡 添加管理员 @成员
        if self._admin_mgmt_action(raw):
            await self._handle_admin_mgmt(event, raw)
            return
        pool_id, _rest = self._parse_pool_arg(raw)
        # 未知主题 → 帮助（不再默认 pjsk）
        if pool_id is None or not self._get_pool(pool_id):
            label = raw.split()[0] if raw else "?"
            await self._reply_text(
                event,
                self._help_text(hint=f"未知卡池「{label}」，请从下方主题中选择："),
            )
            return
        self.effect_processor = ImageEffectProcessor(dict(self.config))
        await self._run_game(event, pool_id=pool_id, is_test=False)

    @non_self
    @registrar.on_group_command("测试猜卡")
    async def cmd_test(self, event: GroupMessageEvent, effect_arg: str = ""):
        if not self._is_group_allowed(event.group_id):
            msg = self._whitelist_reject()
            if msg:
                await self._reply_text(event, msg)
            return
        if self._is_user_blacklisted(str(event.user_id)):
            return
        if not self._is_admin(str(event.user_id)):
            await self._reply_text(event, "哎呀，测试猜卡指令只有管理员才能使用哦~ 😊")
            return
        effect_arg = (effect_arg or "").strip()
        if not effect_arg:
            raw = (event.raw_message or "").strip()
            effect_arg = re.sub(r"^测试猜卡\s*", "", raw).strip()
        pool_id, effect_rest = self._parse_pool_arg(effect_arg)
        if pool_id is None or not self._get_pool(pool_id):
            await self._reply_text(
                event,
                self._help_text(
                    hint="测试猜卡需指定主题，例如：测试猜卡 e7 轻度模糊"
                ),
            )
            return
        allowed = self._pool_allowed_effects(pool_id)
        if allowed is not None:
            name_map = self.effect_processor.EFFECT_NAMES
            available = "、".join(name_map[k] for k in allowed if k in name_map)
        else:
            available = "、".join(self.effect_processor.EFFECT_NAMES.values())
        if not effect_rest:
            await self._reply_text(
                event,
                f"请输入要测试的效果名称哦！\n可用效果: {available}\n"
                f"例如: 测试猜卡 e7 轻度模糊",
            )
            return
        effect_key = self.effect_processor.EFFECT_NAME_TO_KEY.get(effect_rest)
        if not effect_key:
            if effect_rest in self.effect_processor.EFFECT_NAMES:
                effect_key = effect_rest
            else:
                await self._reply_text(
                    event, f"未找到效果 '{effect_rest}' 呢~\n可用效果: {available}"
                )
                return
        if allowed is not None and effect_key not in allowed:
            await self._reply_text(
                event,
                f"卡池 {pool_id} 仅支持模糊效果: {available}",
            )
            return
        self.effect_processor = ImageEffectProcessor(dict(self.config))
        await self._run_game(
            event, pool_id=pool_id, is_test=True, force_effect=[effect_key]
        )

    @non_self
    @registrar.on_group_command("猜卡面帮助")
    async def cmd_help(self, event: GroupMessageEvent):
        if not self._is_group_allowed(event.group_id):
            msg = self._whitelist_reject()
            if msg:
                await self._reply_text(event, msg)
            return
        await self._reply_text(event, self._help_text())

    @non_self
    @registrar.on_group_command("猜卡面分数", "pjsk猜卡面分数", "猜卡分数")
    async def cmd_score(self, event: GroupMessageEvent):
        if not self._is_group_allowed(event.group_id):
            msg = self._whitelist_reject()
            if msg:
                await self._reply_text(event, msg)
            return
        user_id = str(event.user_id)
        if self._is_user_blacklisted(user_id):
            await self._reply_text(event, "抱歉，你已被禁止使用猜卡功能 😔")
            return
        user_name = self._sender_name(event)
        display_name = self._get_display_name(
            user_id, self._get_custom_or_name(user_id, user_name)
        )
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT score, attempts, correct_attempts, last_play_date, daily_plays "
                "FROM user_stats WHERE user_id = ?",
                (user_id,),
            )
            user_data = cursor.fetchone()
            if not user_data:
                await self._reply_text(
                    event, f"{user_name}，你还没有参与过猜卡游戏哦！快来一起玩呀~ 🎮"
                )
                return
            score, attempts, correct_attempts, last_play_date, daily_plays = user_data
            accuracy = (correct_attempts * 100 / attempts) if attempts > 0 else 0
            cursor.execute(
                "SELECT COUNT(*) FROM user_stats WHERE score > ?", (score,)
            )
            rank = cursor.fetchone()[0] + 1
        daily_limit = self._cfg("daily_play_limit", 999)
        today = time.strftime("%Y-%m-%d")
        if daily_limit == -1:
            remaining_plays = "无限次数"
        elif last_play_date == today:
            remaining = daily_limit - daily_plays
            remaining_plays = f"{remaining}次" if remaining > 0 else "0次"
        else:
            remaining_plays = f"{daily_limit}次"
        await self._reply_text(
            event,
            f"✨ {display_name} 的猜卡数据 ✨\n"
            f"🏆 总分: {score} 分\n"
            f"🎯 正确率: {accuracy:.1f}%\n"
            f"🎮 游戏次数: {attempts} 次\n"
            f"✅ 答对次数: {correct_attempts} 次\n"
            f"🏅 当前排名: 第 {rank} 名\n"
            f"📅 今日剩余: {remaining_plays}\n",
        )

    def _get_custom_or_name(self, user_id: str, fallback: str) -> str:
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT custom_name, user_name FROM user_stats WHERE user_id = ?",
                (user_id,),
            )
            row = cursor.fetchone()
            if not row:
                return fallback
            custom_name, user_name = row
            return custom_name or user_name or fallback

    @non_self
    @registrar.on_group_command("猜卡面自定义名称", "自定义名称", "猜卡自定义名称")
    async def cmd_custom_name(self, event: GroupMessageEvent, custom_name: str = ""):
        if not self._is_group_allowed(event.group_id):
            msg = self._whitelist_reject()
            if msg:
                await self._reply_text(event, msg)
            return
        sender_id = str(event.user_id)
        if self._is_user_blacklisted(sender_id):
            await self._reply_text(event, "抱歉，你已被禁止使用猜卡功能 😔")
            return
        if not custom_name:
            raw = (event.raw_message or "").strip()
            parts = raw.split(maxsplit=1)
            custom_name = parts[1].strip() if len(parts) > 1 else ""
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT user_id FROM user_stats WHERE user_id = ?", (sender_id,)
            )
            exists = cursor.fetchone()
            if custom_name:
                if exists:
                    cursor.execute(
                        "UPDATE user_stats SET custom_name = ? WHERE user_id = ?",
                        (custom_name, sender_id),
                    )
                else:
                    today = time.strftime("%Y-%m-%d")
                    cursor.execute(
                        "INSERT INTO user_stats "
                        "(user_id, user_name, custom_name, last_play_date, daily_plays) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            sender_id,
                            self._sender_name(event),
                            custom_name,
                            today,
                            0,
                        ),
                    )
                conn.commit()
                await self._reply_text(
                    event, f"好的！你的猜卡面自定义名称已设置为：{custom_name} ✨"
                )
            else:
                if exists:
                    cursor.execute(
                        "UPDATE user_stats SET custom_name = NULL WHERE user_id = ?",
                        (sender_id,),
                    )
                    conn.commit()
                    await self._reply_text(
                        event, "好的！你的自定义名称已清除，将显示QQ名称 ✨"
                    )
                else:
                    await self._reply_text(
                        event, "你还没有参与过猜卡游戏，暂无自定义名称哦~ 🎮"
                    )

    @non_self
    @registrar.on_group_command("猜卡添加管理员", "猜卡加管理员")
    async def cmd_add_admin(self, event: GroupMessageEvent, _arg: str = ""):
        if not self._is_group_allowed(event.group_id):
            msg = self._whitelist_reject()
            if msg:
                await self._reply_text(event, msg)
            return
        raw = re.sub(
            r"^(猜卡添加管理员|猜卡加管理员)\s*",
            "添加管理员 ",
            (event.raw_message or "").strip(),
        ).strip()
        await self._handle_admin_mgmt(event, raw)

    @non_self
    @registrar.on_group_command("猜卡删除管理员", "猜卡移除管理员")
    async def cmd_remove_admin(self, event: GroupMessageEvent, _arg: str = ""):
        if not self._is_group_allowed(event.group_id):
            msg = self._whitelist_reject()
            if msg:
                await self._reply_text(event, msg)
            return
        raw = re.sub(
            r"^(猜卡删除管理员|猜卡移除管理员)\s*",
            "删除管理员 ",
            (event.raw_message or "").strip(),
        ).strip()
        await self._handle_admin_mgmt(event, raw)

    @non_self
    @registrar.on_group_command("猜卡管理员", "猜卡管理员列表")
    async def cmd_list_admin(self, event: GroupMessageEvent, _arg: str = ""):
        if not self._is_group_allowed(event.group_id):
            msg = self._whitelist_reject()
            if msg:
                await self._reply_text(event, msg)
            return
        await self._handle_admin_mgmt(event, "管理员列表")

    @non_self
    @registrar.on_group_command("猜卡加答案", "猜卡添加答案")
    async def cmd_add_answer(self, event: GroupMessageEvent, arg: str = ""):
        if not self._is_group_allowed(event.group_id):
            msg = self._whitelist_reject()
            if msg:
                await self._reply_text(event, msg)
            return
        if not self._is_admin(str(event.user_id)):
            await self._reply_text(event, "哎呀，这个指令只有管理员才能使用哦~ 😊")
            return

        raw = (arg or "").strip()
        if not raw:
            raw = re.sub(
                r"^(猜卡加答案|猜卡添加答案)\s*",
                "",
                (event.raw_message or "").strip(),
                flags=re.I,
            ).strip()
        if not raw:
            await self._reply_text(
                event,
                "用法:\n"
                "猜卡加答案 e7 洁若米亚 夏娜\n"
                "猜卡加答案 e7 洁若米亚 夏娜/火夏娜\n"
                "猜卡加答案 边狱巴士 环指 点彩派 学徒 李箱 环指箱\n"
                "猜卡加答案 夏娜（沿用本群上一局角色）",
            )
            return

        pool_id, rest = self._parse_pool_arg(raw)
        character = None
        ambiguous: list = []
        alias_text = ""
        char_query = ""
        if pool_id == "member":
            await self._reply_text(event, "群友卡池不支持手动加答案哦~")
            return
        if pool_id:
            if not rest.strip():
                await self._reply_text(
                    event,
                    "请同时指定角色和别名，例如：猜卡加答案 e7 洁若米亚 夏娜",
                )
                return
            pool = self._get_pool(pool_id)
            if not pool:
                await self._reply_text(event, f"卡池「{pool_id}」不可用。")
                return
            # 角色名可含空格：取最长唯一匹配前缀，剩余当别名
            character, alias_text, ambiguous, char_query = resolve_character_alias_query(
                pool.characters, rest
            )
        else:
            ctx = self.last_round_context.get(str(event.group_id))
            if not ctx:
                await self._reply_text(
                    event,
                    "未找到本群上一局角色，请使用完整格式："
                    "猜卡加答案 e7 洁若米亚 夏娜",
                )
                return
            pool_id = ctx["pool_id"]
            if pool_id == "member":
                await self._reply_text(event, "群友卡池不支持手动加答案哦~")
                return
            char_query = ctx["character_id"]
            alias_text = raw
            pool = self._get_pool(pool_id)
            if not pool:
                await self._reply_text(event, f"卡池「{pool_id}」不可用。")
                return
            character, ambiguous = find_character(pool.characters, char_query)

        pool = self._get_pool(pool_id)
        if not pool:
            await self._reply_text(event, f"卡池「{pool_id}」不可用。")
            return

        if ambiguous:
            names = "、".join(display_answer_name(c) for c in ambiguous[:5])
            await self._reply_text(
                event, f"角色「{char_query}」有多个匹配：{names}，请写得更具体一些。"
            )
            return
        if not character:
            await self._reply_text(event, f"在 {pool.display_name} 中未找到角色「{char_query}」。")
            return
        if not (alias_text or "").strip():
            await self._reply_text(event, "请至少提供一个别名。")
            return

        aliases = [
            item.strip()
            for item in re.split(r"[/／|｜、,，]", alias_text)
            if item.strip()
        ]
        if not aliases:
            await self._reply_text(event, "请至少提供一个别名。")
            return

        cid = str(character["characterId"])
        added, skipped = add_custom_aliases(
            self.data_dir,
            pool_id,
            character,
            aliases,
            all_custom=self.custom_nicknames,
        )
        character = reload_character_from_disk(
            pool,
            self.resources_dir / pool_id,
            cid,
            self.custom_nicknames.get(pool_id),
        )
        rebuild_valid_answers(pool)

        answer_name = display_answer_name(character)
        if added:
            msg = (
                f"已为【{pool.display_name}】{answer_name} 添加答案: "
                f"{' / '.join(added)}"
            )
            if skipped:
                msg += f"\n已存在跳过: {' / '.join(skipped)}"
            await self._reply_text(event, msg)
        else:
            await self._reply_text(
                event,
                f"这些别名本来就可用：{' / '.join(skipped)}",
            )

    @non_self
    @registrar.on_group_command("重置猜卡面次数", "resetgl")
    async def cmd_reset(self, event: GroupMessageEvent, target: str = ""):
        if not self._is_group_allowed(event.group_id):
            msg = self._whitelist_reject()
            if msg:
                await self._reply_text(event, msg)
            return
        sender_id = str(event.user_id)
        if not self._is_admin(sender_id):
            await self._reply_text(event, "哎呀，这个指令只有管理员才能使用哦~ 😊")
            return
        if not target:
            raw = (event.raw_message or "").strip()
            parts = raw.split()
            target = parts[1] if len(parts) > 1 and parts[1].isdigit() else sender_id
        elif not target.isdigit():
            target = sender_id
        target_id = str(target)
        if self._reset_user_limit(target_id):
            if target_id == sender_id:
                await self._reply_text(
                    event, "好的！你的猜卡次数已经重置啦~ 可以继续玩了哦！✨"
                )
            else:
                await self._reply_text(
                    event, f"好的！用户 {target_id} 的猜卡次数已经重置啦~ ✨"
                )
        else:
            await self._reply_text(
                event,
                f"哎呀，没有找到用户 {target_id} 的游戏记录呢~ 是不是ID输入错了呀？",
            )

    @non_self
    @registrar.on_group_command("猜卡面排行榜", "猜卡排行榜", "本地猜卡排行榜")
    async def cmd_ranking(self, event: GroupMessageEvent):
        if not self._is_group_allowed(event.group_id):
            msg = self._whitelist_reject()
            if msg:
                await self._reply_text(event, msg)
            return
        self._cleanup_output_dir()
        ranking_count = int(self._cfg("ranking_display_count", 10))
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT user_id, user_name, custom_name, score, attempts, correct_attempts "
                "FROM user_stats ORDER BY score DESC LIMIT ?",
                (ranking_count,),
            )
            rows = cursor.fetchall()
        if not rows:
            await self._reply_text(
                event, "还没有人参与过猜卡游戏呢~ 快来成为第一个玩家吧！✨"
            )
            return
        try:
            img_path = await asyncio.to_thread(
                self._render_ranking_image, rows, ranking_count
            )
            if img_path:
                await self._reply_image(event, image_path=img_path)
            else:
                await self._reply_text(event, "生成排行榜图片时出错，请联系管理员。")
        except Exception as e:
            _log.error("生成排行榜失败: %s", e, exc_info=True)
            await self._reply_text(event, "生成排行榜图片时出错，请联系管理员。")

    @non_self
    @registrar.on_group_message()
    async def on_guess_answer(self, event: GroupMessageEvent):
        session_id = str(event.group_id)
        session = self.game_sessions.get(session_id)
        if not session or session.finishing:
            return
        raw = (event.raw_message or "").strip()
        if not raw:
            return
        # 不把开局等指令当答案重复处理
        if raw.split()[0] in {
            "猜卡",
            "猜卡面",
            "pjsk猜卡面",
            "gc",
            "测试猜卡",
            "猜卡面帮助",
            "猜卡面分数",
            "猜卡面排行榜",
            "猜卡面自定义名称",
            "猜卡加答案",
            "猜卡添加答案",
            "猜卡添加管理员",
            "猜卡加管理员",
            "猜卡删除管理员",
            "猜卡移除管理员",
            "猜卡管理员",
            "猜卡管理员列表",
            "重置猜卡面次数",
        }:
            return
        self._handle_guess(
            session,
            session_id,
            str(event.user_id),
            self._sender_name(event),
            raw,
        )

    def _render_ranking_image(
        self, rows: list, ranking_count: int = 10
    ) -> Optional[str]:
        try:
            width = 850
            base_height = 250
            item_height = 70
            height = base_height + len(rows) * item_height
            bg_start = (230, 240, 255)
            bg_end = (200, 210, 240)
            img = PILImage.new("RGB", (width, height), bg_start)
            draw_bg = ImageDraw.Draw(img)
            for y in range(height):
                r = int(bg_start[0] + (bg_end[0] - bg_start[0]) * y / height)
                g = int(bg_start[1] + (bg_end[1] - bg_start[1]) * y / height)
                b = int(bg_start[2] + (bg_end[2] - bg_start[2]) * y / height)
                draw_bg.line([(0, y), (width, y)], fill=(r, g, b))
            img = img.convert("RGBA")
            white_overlay = PILImage.new("RGBA", img.size, (255, 255, 255, 100))
            img = PILImage.alpha_composite(img, white_overlay)

            font_color = (30, 30, 50)
            shadow_color = (180, 180, 190, 128)
            header_color = (80, 90, 120)
            score_color = (235, 120, 20)
            accuracy_color = (0, 128, 128)

            with Pilmoji(img) as pilmoji:
                center_x, title_y = int(width / 2), 80
                title_text = "猜卡面排行榜"
                pilmoji.text(
                    (center_x + 2, title_y + 2),
                    title_text,
                    font=self.title_font,
                    fill=shadow_color,
                    anchor="mm",
                    emoji_position_offset=(0, 6),
                )
                pilmoji.text(
                    (center_x, title_y),
                    title_text,
                    font=self.title_font,
                    fill=font_color,
                    anchor="mm",
                    emoji_position_offset=(0, 6),
                )
                headers = ["排名", "玩家", "总分", "正确率", "总次数"]
                col_positions_header = [40, 150, 500, 610, 720]
                title_height = pilmoji.getsize(title_text, font=self.title_font)[1]
                current_y = title_y + int(title_height / 2) + 45
                for header in headers:
                    pilmoji.text(
                        (col_positions_header.pop(0), current_y),
                        header,
                        font=self.header_font,
                        fill=header_color,
                    )
                current_y += 55
                rank_icons = ["🥇", "🥈", "🥉"]
                for i, row in enumerate(rows):
                    user_id = str(row[0])
                    user_name = row[1]
                    custom_name = row[2]
                    score = str(row[3])
                    attempts = str(row[4])
                    correct_attempts = row[5]
                    display_name = self._get_display_name(
                        user_id, custom_name if custom_name else user_name
                    )
                    accuracy = (
                        f"{(correct_attempts * 100 / int(attempts) if int(attempts) > 0 else 0):.1f}%"
                    )
                    col_positions = [40, 150, 500, 610, 720]
                    pilmoji.text(
                        (130, current_y),
                        str(i + 1),
                        font=self.body_font,
                        fill=font_color,
                        anchor="ra",
                    )
                    if i < 3:
                        pilmoji.text(
                            (col_positions[0], current_y - 30),
                            rank_icons[i],
                            font=self.medal_font,
                            fill=font_color,
                        )
                    max_name_width = col_positions[2] - col_positions[1] - 20
                    if self.body_font.getbbox(display_name)[2] > max_name_width:
                        while (
                            self.body_font.getbbox(display_name + "...")[2]
                            > max_name_width
                            and display_name
                        ):
                            display_name = display_name[:-1]
                        display_name += "..."
                    pilmoji.text(
                        (col_positions[1], current_y),
                        display_name,
                        font=self.body_font,
                        fill=font_color,
                    )
                    id_text = f"{user_name} ID: {user_id}"
                    max_id_width = col_positions[2] - col_positions[1] - 20
                    if self.id_font.getbbox(id_text)[2] > max_id_width:
                        while (
                            self.id_font.getbbox(id_text + "...")[2] > max_id_width
                            and id_text
                        ):
                            id_text = id_text[:-1]
                        id_text += "..."
                    pilmoji.text(
                        (col_positions[1], current_y + 32),
                        id_text,
                        font=self.id_font,
                        fill=header_color,
                    )
                    pilmoji.text(
                        (col_positions[2], current_y),
                        score,
                        font=self.body_font,
                        fill=score_color,
                    )
                    pilmoji.text(
                        (col_positions[3], current_y),
                        accuracy,
                        font=self.body_font,
                        fill=accuracy_color,
                    )
                    pilmoji.text(
                        (col_positions[4], current_y),
                        attempts,
                        font=self.body_font,
                        fill=font_color,
                    )
                    if i < len(rows) - 1:
                        draw = ImageDraw.Draw(img)
                        draw.line(
                            [(30, current_y + 60), (width - 30, current_y + 60)],
                            fill=(200, 200, 210, 128),
                            width=1,
                        )
                    current_y += 70
                footer = f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                pilmoji.text(
                    (center_x, height - 25),
                    footer,
                    font=self.id_font,
                    fill=header_color,
                    anchor="ms",
                )

            self.output_dir.mkdir(parents=True, exist_ok=True)
            img_path = self.output_dir / f"ranking_{time.time_ns()}.png"
            img.save(img_path)
            return str(img_path)
        except Exception as e:
            _log.error("渲染排行榜失败: %s", e, exc_info=True)
            return None

    def _record_game_start(self, user_id: str, user_name: str):
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            cursor = conn.cursor()
            today = time.strftime("%Y-%m-%d")
            cursor.execute(
                "SELECT last_play_date, daily_plays FROM user_stats WHERE user_id = ?",
                (user_id,),
            )
            user_data = cursor.fetchone()
            if user_data:
                last_play_date, daily_plays = user_data
                new_daily = daily_plays + 1 if last_play_date == today else 1
                cursor.execute(
                    "UPDATE user_stats SET user_name = ?, last_play_date = ?, "
                    "daily_plays = ? WHERE user_id = ?",
                    (user_name, today, new_daily, user_id),
                )
            else:
                cursor.execute(
                    "INSERT INTO user_stats "
                    "(user_id, user_name, last_play_date, daily_plays) VALUES (?, ?, ?, ?)",
                    (user_id, user_name, today, 1),
                )
            conn.commit()

    def _update_stats(
        self, user_id: str, user_name: str, score: int, correct: bool
    ):
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT score, attempts, correct_attempts FROM user_stats WHERE user_id = ?",
                (user_id,),
            )
            user_data = cursor.fetchone()
            if user_data:
                cursor.execute(
                    "UPDATE user_stats SET score = ?, attempts = ?, "
                    "correct_attempts = ?, user_name = ? WHERE user_id = ?",
                    (
                        user_data[0] + score,
                        user_data[1] + 1,
                        user_data[2] + (1 if correct else 0),
                        user_name,
                        user_id,
                    ),
                )
            else:
                today = time.strftime("%Y-%m-%d")
                cursor.execute(
                    "INSERT INTO user_stats "
                    "(user_id, user_name, score, attempts, correct_attempts, "
                    "last_play_date, daily_plays) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        user_id,
                        user_name,
                        score,
                        1,
                        1 if correct else 0,
                        today,
                        0,
                    ),
                )
            conn.commit()

    def _can_play(self, user_id: str) -> bool:
        daily_limit = self._cfg("daily_play_limit", 999)
        if daily_limit == -1:
            return True
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            cursor = conn.cursor()
            today = time.strftime("%Y-%m-%d")
            cursor.execute(
                "SELECT daily_plays, last_play_date FROM user_stats WHERE user_id = ?",
                (user_id,),
            )
            user_data = cursor.fetchone()
            if user_data and user_data[1] == today:
                return user_data[0] < daily_limit
            return True

    def _reset_user_limit(self, user_id: str) -> bool:
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT user_id FROM user_stats WHERE user_id = ?", (user_id,)
            )
            if cursor.fetchone():
                cursor.execute(
                    "UPDATE user_stats SET daily_plays = 0 WHERE user_id = ?",
                    (user_id,),
                )
                conn.commit()
                return True
            return False
