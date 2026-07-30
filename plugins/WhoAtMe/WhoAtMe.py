"""谁艾特我 — 查询最近被艾特时的群聊上下文。"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple

from common.utils.CommonUtil import CommonUtil
from common.utils.async_io import http_get_bytes
from ncatbot.core import registrar
from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.event.qq import GroupRecallEvent
from ncatbot.plugin import NcatBotPlugin
from ncatbot.types import At, Image, PlainText, Reply, Record, Video
from ncatbot.types import MessageArray as MessageChain
from ncatbot.utils import get_log

from .card import ChatRenderMessage, RenderPart, render_qq_chat_card
from .models import MessagePart, PendingAt, StoredMessage
from .store import AtStore

_log = get_log()

PLUGIN_DIR = Path(__file__).resolve().parent

DEFAULT_CONFIG = {
    # 空列表 = 全群启用
    "group_whitelist": [],
    # 未读记录保留秒数
    "record_expire_seconds": 86400,
    # @ 消息前后各保留条数
    "context_size": 10,
    # 群滚动消息窗口
    "context_window": 50,
    # 单次查询最多发送几张未读卡片
    "max_cards_per_query": 3,
}

_SELF_QUERY_RE = re.compile(r"^(?:谁艾特我|谁@我)[?？]?$")
_TARGET_QUERY_RE = re.compile(r"^(?:谁艾特|谁@)\s*")
_EMPTY_SELF = "最近没有未查看的艾特哦~"
_EMPTY_OTHER = "最近没有人艾特 TA 哦~"

PendingKey = Tuple[int, str]


class WhoAtMe(NcatBotPlugin):
    name = "WhoAtMe"
    version = "1.3.0"

    _context_ring: Dict[int, Deque[StoredMessage]] = {}
    _pending: Dict[PendingKey, List[PendingAt]] = {}
    _image_bytes_cache: Dict[str, bytes] = {}
    _member_cache: Dict[Tuple[int, str], Tuple[float, dict]] = {}
    _quoted_msg_cache: Dict[str, StoredMessage] = {}
    _MEMBER_CACHE_TTL = 600

    async def on_load(self):
        self.init_defaults(DEFAULT_CONFIG)
        self.data_dir = PLUGIN_DIR / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = AtStore(self.data_dir / "who_at_me.db")
        self._reload_pending_from_store()
        _log.info(
            "开始加载 %s 插件 v%s（pending=%s）",
            self.name,
            self.version,
            sum(len(v) for v in self._pending.values()),
        )

    def _cfg(self, key: str, default=None):
        return self.get_config(key, DEFAULT_CONFIG.get(key, default))

    def _context_size(self) -> int:
        try:
            return max(1, int(self._cfg("context_size", 10)))
        except (TypeError, ValueError):
            return 10

    def _context_window(self) -> int:
        try:
            return max(
                self._context_size() * 2 + 1, int(self._cfg("context_window", 50))
            )
        except (TypeError, ValueError):
            return 50

    def _expire_seconds(self) -> int:
        try:
            v = int(self._cfg("record_expire_seconds", 86400))
            return v if v > 0 else 86400
        except (TypeError, ValueError):
            return 86400

    def _max_cards(self) -> int:
        try:
            return max(1, min(10, int(self._cfg("max_cards_per_query", 3))))
        except (TypeError, ValueError):
            return 3

    def _is_group_allowed(self, group_id) -> bool:
        raw = self._cfg("group_whitelist", []) or []
        if not raw:
            return True
        try:
            gid = int(group_id)
        except (TypeError, ValueError):
            return False
        allowed = set()
        for x in raw:
            try:
                allowed.add(int(x))
            except (TypeError, ValueError):
                continue
        return gid in allowed

    def _reload_pending_from_store(self) -> None:
        self._pending.clear()
        for pending in self.store.load_all_alive(self._expire_seconds()):
            key = (int(pending.group_id), str(pending.target_user_id))
            self._pending.setdefault(key, []).append(pending)

    @registrar.qq.on_group_message(priority=50)
    async def handle_group_message(self, event: GroupMessage) -> None:
        if not self._is_group_allowed(event.group_id):
            return

        query_target = self._parse_query_target(event)
        if query_target is not None:
            await self._reply_who_at(event, query_target)
            return

        self._record_message(event)

    @registrar.qq.on_group_recall()
    async def handle_group_recall(self, event: GroupRecallEvent) -> None:
        try:
            group_id = int(event.group_id)
            message_id = str(event.message_id)
        except (TypeError, ValueError):
            return
        if not self._is_group_allowed(group_id):
            return
        affected = self.store.handle_recall(group_id, message_id)
        if affected:
            self._apply_recall_in_memory(group_id, message_id)
            _log.info(
                "[WhoAtMe] 撤回同步: 群 %s 消息 %s 影响 %s",
                group_id,
                message_id,
                affected,
            )

    def _apply_recall_in_memory(self, group_id: int, message_id: str) -> None:
        mid = str(message_id)
        for key in list(self._pending.keys()):
            if key[0] != group_id:
                continue
            bucket = self._pending.get(key) or []
            kept: List[PendingAt] = []
            for p in bucket:
                if p.message_id == mid:
                    continue
                if p.context:
                    p.context = [m for m in p.context if m.message_id != mid]
                kept.append(p)
            if kept:
                self._pending[key] = kept
            else:
                self._pending.pop(key, None)

        ring = self._context_ring.get(group_id)
        if ring:
            filtered = deque(
                (m for m in ring if m.message_id != mid),
                maxlen=ring.maxlen,
            )
            self._context_ring[group_id] = filtered

    def _parse_query_target(self, event: GroupMessage) -> Optional[str]:
        """解析查询指令。返回目标 QQ；非查询返回 None。"""
        text = self._extract_plain_text(event)
        if _SELF_QUERY_RE.fullmatch(text):
            return str(event.sender.user_id)

        if not _TARGET_QUERY_RE.match(text):
            return None

        # 「谁艾特@用户」/「谁@@用户」
        self_id = str(getattr(event, "self_id", "") or "")
        for seg in event.message:
            if isinstance(seg, At):
                uid = str(seg.user_id)
                if uid and uid != "all" and uid != self_id:
                    return uid

        # 纯文本兜底：谁艾特123456
        rest = _TARGET_QUERY_RE.sub("", text).strip()
        rest = rest.lstrip("@").strip()
        if rest.isdigit():
            return rest
        # 以「谁艾特」开头但未指定目标：不当作查询，避免吞消息
        return None

    def _record_message(self, event: GroupMessage) -> None:
        group_id = event.group_id
        if group_id is None:
            return
        stored = self._build_stored_message(event)
        window = self._context_window()
        ring = self._context_ring.get(group_id)
        if ring is None or ring.maxlen != window:
            old = list(ring) if ring else []
            ring = deque(old, maxlen=window)
            self._context_ring[group_id] = ring
        ring.append(stored)
        self._extend_pending_after_context(group_id, stored)
        if stored.at_user_ids:
            self._register_pending_ats(group_id, stored, list(ring))

    def _resolve_context_for_pending(
        self, group_id: int, pending: PendingAt
    ) -> List[StoredMessage]:
        size = self._context_size()
        ring = list(self._context_ring.get(group_id, []))
        hit_index = next(
            (i for i, m in enumerate(ring) if m.message_id == pending.message_id),
            None,
        )
        if hit_index is not None:
            start = max(0, hit_index - size)
            end = min(len(ring), hit_index + size + 1)
            return list(ring[start:end])
        if pending.context:
            return list(pending.context)
        return [
            StoredMessage(
                message_id=pending.message_id,
                user_id=pending.atter_user_id,
                nickname=pending.atter_nickname,
                parts=[MessagePart(kind="text", text="[艾特消息]")],
                at_user_ids=[pending.target_user_id],
                timestamp=pending.timestamp,
            )
        ]

    def _extend_pending_after_context(
        self, group_id: int, stored: StoredMessage
    ) -> None:
        size = self._context_size()
        for key, bucket in self._pending.items():
            if key[0] != group_id:
                continue
            for pending in bucket:
                if stored.message_id == pending.message_id:
                    continue
                ctx = pending.context
                if not ctx:
                    continue
                if any(m.message_id == stored.message_id for m in ctx):
                    continue
                try:
                    at_idx = next(
                        i
                        for i, m in enumerate(ctx)
                        if m.message_id == pending.message_id
                    )
                except StopIteration:
                    continue
                after_count = len(ctx) - at_idx - 1
                if after_count >= size:
                    continue
                ctx.append(stored)
                if pending.id is not None:
                    self.store.update_context(pending.id, ctx)

    def _register_pending_ats(
        self,
        group_id: int,
        stored: StoredMessage,
        ring_snapshot: List[StoredMessage],
    ) -> None:
        size = self._context_size()
        hit_index = next(
            (
                i
                for i, m in enumerate(ring_snapshot)
                if m.message_id == stored.message_id
            ),
            None,
        )
        if hit_index is None:
            context = [stored]
        else:
            start = max(0, hit_index - size)
            end = min(len(ring_snapshot), hit_index + size + 1)
            context = ring_snapshot[start:end]

        now = time.time()
        for target_id in stored.at_user_ids:
            pending = PendingAt(
                id=None,
                group_id=int(group_id),
                message_id=stored.message_id,
                target_user_id=str(target_id),
                atter_user_id=stored.user_id,
                atter_nickname=stored.display_name,
                timestamp=stored.timestamp,
                created_at=now,
                context=list(context),
            )
            pending = self.store.insert(pending)
            key = (int(group_id), str(target_id))
            self._pending.setdefault(key, []).append(pending)
            self._purge_expired_pending(int(group_id), str(target_id))

    def _purge_expired_pending(self, group_id: int, user_id: str) -> None:
        self.store.cleanup_expired(self._expire_seconds())
        key = (group_id, user_id)
        bucket = self._pending.get(key)
        if not bucket:
            return
        cutoff = time.time() - self._expire_seconds()
        alive = [p for p in bucket if p.created_at >= cutoff]
        if alive:
            self._pending[key] = alive
        else:
            self._pending.pop(key, None)

    def _clear_pending_ids(self, group_id: int, user_id: str, ids: List[int]) -> None:
        self.store.delete_ids(ids)
        key = (group_id, user_id)
        bucket = self._pending.get(key) or []
        id_set = set(ids)
        kept = [p for p in bucket if p.id not in id_set]
        if kept:
            self._pending[key] = kept
        else:
            self._pending.pop(key, None)

    def _get_pending_list(self, group_id: int, user_id: str) -> List[PendingAt]:
        # 以 DB 为准，保证重启后一致
        rows = self.store.list_for_target(
            int(group_id), str(user_id), expire_seconds=self._expire_seconds()
        )
        self._pending[(int(group_id), str(user_id))] = list(rows)
        return rows

    def _build_stored_message(self, event: GroupMessage) -> StoredMessage:
        parts, at_ids = self._parse_message_parts(event)
        sender = event.sender
        return StoredMessage(
            message_id=str(event.message_id),
            user_id=str(sender.user_id),
            nickname=str(getattr(sender, "nickname", None) or sender.user_id),
            card=str(getattr(sender, "card", None) or ""),
            role=str(getattr(sender, "role", None) or "member"),
            level=str(getattr(sender, "level", None) or ""),
            title=str(getattr(sender, "title", None) or ""),
            parts=parts,
            at_user_ids=at_ids,
            timestamp=float(getattr(event, "time", 0) or 0),
        )

    async def _fetch_group_title(self, group_id: int) -> str:
        try:
            info = await self.api.qq.query.get_group_info(group_id)
            name = getattr(info, "group_name", None) or str(group_id)
            count = getattr(info, "member_count", None)
            if count is not None:
                return f"{name}({count})"
            return name
        except Exception as e:
            _log.debug("[WhoAtMe] 获取群信息失败: %s", e)
            return str(group_id)

    async def _reply_who_at(self, event: GroupMessage, target_user_id: str) -> None:
        group_id = int(event.group_id)
        viewer_id = str(event.sender.user_id)
        is_self = target_user_id == viewer_id

        pending_list = self._get_pending_list(group_id, target_user_id)
        if not pending_list:
            await self.api.qq.post_group_msg(
                group_id=group_id,
                text=_EMPTY_SELF if is_self else _EMPTY_OTHER,
            )
            return

        # 从旧到新展示最近若干条未读
        max_cards = self._max_cards()
        to_show = pending_list[-max_cards:]
        remaining = len(pending_list) - len(to_show)

        try:
            group_title = await self._fetch_group_title(group_id)
            for idx, pending in enumerate(to_show):
                context = self._resolve_context_for_pending(group_id, pending)
                render_msgs = await self._build_render_messages(
                    group_id,
                    context,
                    highlight_message_id=pending.message_id,
                )
                # 多条时用「第 i/n 条未读」提示
                pending_count_label = len(pending_list) if idx == 0 else 0
                png_bytes = await asyncio.to_thread(
                    render_qq_chat_card,
                    group_title=group_title,
                    messages=render_msgs,
                    pending_count=pending_count_label
                    if len(to_show) == 1
                    else len(to_show),
                )
                await self._send_card_image(group_id, png_bytes)
                if idx + 1 < len(to_show):
                    await asyncio.sleep(0.4)
        except Exception as e:
            _log.error("绘制谁艾特我卡片失败: %s", e, exc_info=True)
            await self.api.qq.post_group_msg(
                group_id=group_id,
                text=f"生成图片失败: {e}",
            )
            return

        clear_ids = [p.id for p in to_show if p.id is not None]
        self._clear_pending_ids(group_id, target_user_id, clear_ids)

        if remaining > 0:
            tip = (
                f"已展示最近 {len(to_show)} 条，还有 {remaining} 条未读，"
                f"再发一次「{'谁艾特我' if is_self else '谁艾特@TA'}」继续查看"
            )
            await self.api.qq.post_group_msg(group_id=group_id, text=tip)

        _log.info(
            "[WhoAtMe] 群 %s 查看者 %s 目标 %s 展示 %s/%s",
            group_id,
            viewer_id,
            target_user_id,
            len(to_show),
            len(pending_list),
        )

    async def _send_card_image(self, group_id: int, png_bytes: bytes) -> None:
        fd, path = tempfile.mkstemp(suffix=".png", prefix="who_at_me_")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(png_bytes)
            from ncatbot.types import Image as ImageSeg

            await self.api.qq.post_group_msg(
                group_id=group_id,
                rtf=MessageChain([ImageSeg(file=path)]),
            )
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    async def _get_member_profile(self, group_id: int, user_id: str) -> dict:
        key = (group_id, user_id)
        now = time.time()
        cached = self._member_cache.get(key)
        if cached and now - cached[0] < self._MEMBER_CACHE_TTL:
            return cached[1]

        profile: dict = {}
        try:
            info = await self.api.qq.query.get_group_member_info(
                group_id=group_id,
                user_id=user_id,
            )
            if hasattr(info, "user_id"):
                profile = {
                    "nickname": str(info.nickname or ""),
                    "card": str(info.card or ""),
                    "role": str(info.role or "member"),
                    "level": str(info.level or ""),
                    "title": str(info.title or ""),
                }
            elif isinstance(info, dict) and info.get("status") == "ok":
                data = info.get("data") or {}
                profile = {
                    "nickname": str(data.get("nickname") or ""),
                    "card": str(data.get("card") or ""),
                    "role": str(data.get("role") or "member"),
                    "level": str(data.get("level") or ""),
                    "title": str(data.get("title") or ""),
                }
        except Exception as e:
            _log.debug("[WhoAtMe] 获取群成员 %s 信息失败: %s", user_id, e)

        self._member_cache[key] = (now, profile)
        if len(self._member_cache) > 500:
            oldest = min(self._member_cache, key=lambda k: self._member_cache[k][0])
            self._member_cache.pop(oldest, None)
        return profile

    @staticmethod
    def _merge_profile(stored: StoredMessage, profile: dict) -> dict:
        return {
            "nickname": profile.get("nickname") or stored.nickname,
            "card": profile.get("card") or stored.card,
            "role": profile.get("role") or stored.role,
            "level": profile.get("level") or stored.level,
            "title": profile.get("title") or stored.title,
        }

    @staticmethod
    def _profile_display_name(profile: dict, user_id: str) -> str:
        return (profile.get("card") or profile.get("nickname") or user_id).strip()

    async def _build_render_messages(
        self,
        group_id: int,
        context: List[StoredMessage],
        highlight_message_id: str,
    ) -> List[ChatRenderMessage]:
        user_ids: set[str] = {m.user_id for m in context}
        for m in context:
            for p in m.parts:
                if p.kind == "at" and p.user_id:
                    user_ids.add(p.user_id)

        profiles: Dict[str, dict] = {}
        fetch_results = await asyncio.gather(
            *[self._get_member_profile(group_id, uid) for uid in user_ids],
            return_exceptions=True,
        )
        for uid, result in zip(user_ids, fetch_results):
            if isinstance(result, dict):
                profiles[uid] = result

        stored_by_uid = {m.user_id: m for m in context}

        def merged_for(uid: str) -> dict:
            stored = stored_by_uid.get(uid)
            if stored:
                return self._merge_profile(stored, profiles.get(uid, {}))
            return profiles.get(uid, {})

        uid_to_name = {
            uid: self._profile_display_name(merged_for(uid), uid) for uid in user_ids
        }
        msg_by_id = {m.message_id: m for m in context}
        quoted_by_id = dict(msg_by_id)
        reply_ids = {
            p.reply_id
            for m in context
            for p in m.parts
            if p.kind == "reply" and p.reply_id
        }
        for reply_id in reply_ids:
            if reply_id in quoted_by_id:
                continue
            quoted = await self._fetch_stored_message_by_id(reply_id, group_id)
            if quoted:
                quoted_by_id[reply_id] = quoted
                user_ids.add(quoted.user_id)
                prof = await self._get_member_profile(group_id, quoted.user_id)
                profiles[quoted.user_id] = prof
                uid_to_name[quoted.user_id] = self._profile_display_name(
                    self._merge_profile(quoted, prof), quoted.user_id
                )

        avatar_paths: Dict[str, str] = {}
        for uid in {m.user_id for m in context}:
            avatar_paths[uid] = await CommonUtil.get_avatar_async(uid)

        image_sources = {
            (p.url, p.file)
            for m in context
            for p in m.parts
            if p.kind == "image" and (p.url or p.file)
        }
        url_to_bytes: Dict[Tuple[str, str], bytes] = {}
        for url, file in image_sources:
            data = await self._fetch_image_bytes(url, file)
            if data:
                url_to_bytes[(url, file)] = data

        out: List[ChatRenderMessage] = []
        for msg in context:
            render_parts: List[RenderPart] = []
            for p in msg.parts:
                if p.kind == "text":
                    if p.text:
                        render_parts.append(RenderPart(kind="text", text=p.text))
                elif p.kind == "at":
                    name = uid_to_name.get(p.user_id, p.user_id)
                    render_parts.append(RenderPart(kind="at", text=f"@{name}"))
                elif p.kind == "image":
                    blob = url_to_bytes.get((p.url, p.file))
                    is_gif = bool(
                        blob and len(blob) >= 3 and blob[:3] == b"GIF"
                    ) or bool(
                        p.url
                        and "gif" in p.url.lower()
                        or p.file
                        and p.file.lower().endswith(".gif")
                    )
                    render_parts.append(
                        RenderPart(kind="image", image_bytes=blob, is_gif=is_gif)
                    )
                elif p.kind == "reply" and p.reply_id:
                    quoted = quoted_by_id.get(p.reply_id)
                    if quoted:
                        qtime = self._format_full_time(quoted.timestamp)
                        preview = quoted.plain_preview
                        if len(preview) > 100:
                            preview = preview[:100] + "…"
                        qprof = self._merge_profile(
                            quoted, profiles.get(quoted.user_id, {})
                        )
                        qname = self._profile_display_name(qprof, quoted.user_id)
                        quote = f"{qname}  {qtime}\n{preview}"
                        render_parts.append(RenderPart(kind="reply_quote", text=quote))
                    else:
                        render_parts.append(
                            RenderPart(kind="reply_quote", text="[无法加载引用消息]")
                        )
                elif p.kind in ("video", "record", "other"):
                    render_parts.append(
                        RenderPart(kind="text", text=p.text or f"[{p.kind}]")
                    )

            if not render_parts:
                render_parts.append(RenderPart(kind="text", text="[空消息]"))

            prof = self._merge_profile(msg, profiles.get(msg.user_id, {}))
            display_name = self._profile_display_name(prof, msg.user_id)

            out.append(
                ChatRenderMessage(
                    user_id=msg.user_id,
                    nickname=prof.get("nickname") or msg.nickname,
                    time_label=self._format_full_time(msg.timestamp),
                    parts=render_parts,
                    highlighted=msg.message_id == highlight_message_id,
                    avatar_path=avatar_paths.get(msg.user_id, ""),
                    role=prof.get("role") or msg.role,
                    level=prof.get("level") or msg.level,
                    title=prof.get("title") or msg.title,
                    card=prof.get("card") or msg.card,
                    display_name=display_name,
                )
            )
        return out

    async def _fetch_stored_message_by_id(
        self, message_id: str, group_id: int
    ) -> Optional[StoredMessage]:
        if message_id in self._quoted_msg_cache:
            return self._quoted_msg_cache[message_id]
        try:
            msg_data = await self.api.qq.query.get_msg(message_id)
        except Exception as e:
            _log.debug("[WhoAtMe] get_msg(%s) 失败: %s", message_id, e)
            return None

        stored = self._stored_from_msg_data(msg_data, group_id)
        if stored:
            self._quoted_msg_cache[message_id] = stored
            if len(self._quoted_msg_cache) > 200:
                self._quoted_msg_cache.pop(next(iter(self._quoted_msg_cache)))
        return stored

    @staticmethod
    def _stored_from_msg_data(msg_data, group_id: int) -> Optional[StoredMessage]:
        raw = getattr(msg_data, "raw_message", None) or ""
        segments = getattr(msg_data, "message", None) or []
        if raw:
            parts, at_ids = WhoAtMe._parse_raw_message(raw)
        elif segments:
            parts, at_ids = WhoAtMe._parse_message_dicts(segments)
        else:
            return None

        sender = getattr(msg_data, "sender", None)
        user_id = str(getattr(sender, "user_id", None) or "")
        mid = str(getattr(msg_data, "message_id", None) or "")
        if not mid:
            return None
        return StoredMessage(
            message_id=mid,
            user_id=user_id,
            nickname=str(getattr(sender, "nickname", None) or user_id),
            card=str(getattr(sender, "card", None) or ""),
            role=str(getattr(sender, "role", None) or "member"),
            parts=parts,
            at_user_ids=at_ids,
            timestamp=float(getattr(msg_data, "time", 0) or 0),
        )

    @staticmethod
    def _parse_message_dicts(segments) -> tuple[List[MessagePart], List[str]]:
        parts: List[MessagePart] = []
        at_ids: List[str] = []
        for seg in segments:
            if isinstance(seg, dict):
                typ = seg.get("type", "")
                data = seg.get("data") or {}
            else:
                typ = getattr(seg, "type", "")
                data = {}
                if hasattr(seg, "model_dump"):
                    dumped = seg.model_dump()
                    typ = dumped.get("type", typ)
                    data = dumped.get("data") or {}
            if typ == "text":
                text = str(data.get("text", ""))
                if text:
                    parts.append(MessagePart(kind="text", text=text))
            elif typ == "at":
                uid = str(data.get("qq", ""))
                if uid:
                    at_ids.append(uid)
                    parts.append(MessagePart(kind="at", user_id=uid, text=f"@{uid}"))
            elif typ == "image":
                url = str(data.get("url") or "").strip()
                file = str(data.get("file") or "").strip()
                if not url and file:
                    url = file
                parts.append(
                    MessagePart(kind="image", url=url, file=file, text="[图片]")
                )
            elif typ == "reply":
                rid = str(data.get("id", ""))
                if rid:
                    parts.append(MessagePart(kind="reply", reply_id=rid))
            elif typ == "video":
                parts.append(MessagePart(kind="video", text="[视频]"))
            elif typ == "record":
                parts.append(MessagePart(kind="record", text="[语音]"))
        return parts, at_ids

    async def _fetch_image_bytes(self, url: str, file: str = "") -> Optional[bytes]:
        cache_key = url or file
        if not cache_key:
            return None
        if cache_key in self._image_bytes_cache:
            return self._image_bytes_cache[cache_key]

        data: Optional[bytes] = None
        if url.startswith(("http://", "https://")):
            try:
                status, body = await http_get_bytes(url, timeout=20)
                if status == 200 and body:
                    data = body
            except Exception as e:
                _log.debug("[WhoAtMe] 下载图片失败 %s: %s", url[:80], e)

        if data is None:
            for candidate in (url, file):
                if candidate and os.path.isfile(candidate):
                    try:
                        with open(candidate, "rb") as f:
                            data = f.read()
                        break
                    except OSError:
                        pass

        if data is None and (url or file):
            try:
                result = await self.api.qq.download_file(url=url or "", file=file or "")
                local_path = getattr(result, "file", "") or ""
                if local_path and os.path.isfile(local_path):
                    with open(local_path, "rb") as f:
                        data = f.read()
            except Exception as e:
                _log.debug("[WhoAtMe] NapCat 下载图片失败: %s", e)

        if data:
            self._image_bytes_cache[cache_key] = data
            if len(self._image_bytes_cache) > 200:
                self._image_bytes_cache.pop(next(iter(self._image_bytes_cache)))
            return data
        return None

    @staticmethod
    def _parse_message_parts(
        event: GroupMessage,
    ) -> tuple[List[MessagePart], List[str]]:
        parts: List[MessagePart] = []
        at_ids: List[str] = []

        for seg in event.message:
            if isinstance(seg, PlainText):
                if seg.text:
                    parts.append(MessagePart(kind="text", text=seg.text))
            elif isinstance(seg, At):
                uid = str(seg.user_id)
                at_ids.append(uid)
                parts.append(MessagePart(kind="at", user_id=uid, text=f"@{uid}"))
            elif isinstance(seg, Reply):
                parts.append(MessagePart(kind="reply", reply_id=str(seg.id)))
            elif isinstance(seg, Image):
                url = (
                    getattr(seg, "url", None) or getattr(seg, "file", None) or ""
                ).strip()
                file = str(getattr(seg, "file", None) or "").strip()
                parts.append(
                    MessagePart(kind="image", url=url, file=file, text="[图片]")
                )
            elif isinstance(seg, Video):
                parts.append(MessagePart(kind="video", text="[视频]"))
            elif isinstance(seg, Record):
                parts.append(MessagePart(kind="record", text="[语音]"))
            else:
                parts.append(
                    MessagePart(
                        kind="other",
                        text=f"[{getattr(seg, 'type', type(seg).__name__)}]",
                    )
                )

        if not parts:
            parts, at_ids = WhoAtMe._parse_raw_message(event.raw_message)

        WhoAtMe._enrich_image_urls_from_raw(event.raw_message, parts)
        WhoAtMe._ensure_reply_from_raw(event.raw_message, parts)
        return parts, at_ids

    @staticmethod
    def _parse_raw_message(raw: str) -> tuple[List[MessagePart], List[str]]:
        parts: List[MessagePart] = []
        at_ids: List[str] = []
        pos = 0
        for m in re.finditer(r"\[CQ:([^\]]+)\]", raw):
            if m.start() > pos:
                text = raw[pos : m.start()]
                if text:
                    parts.append(MessagePart(kind="text", text=text))
            cq = m.group(1)
            if cq.startswith("at,"):
                uid_m = re.search(r"qq=(\d+)", cq)
                if uid_m:
                    uid = uid_m.group(1)
                    at_ids.append(uid)
                    parts.append(MessagePart(kind="at", user_id=uid, text=f"@{uid}"))
            elif cq.startswith("image,"):
                url_m = re.search(r"url=([^,\]]+)", cq)
                file_m = re.search(r"file=([^,\]]+)", cq)
                url = ""
                file = ""
                if url_m:
                    url = url_m.group(1).replace("&amp;", "&")
                if file_m:
                    file = file_m.group(1)
                if not url and file:
                    url = file
                parts.append(
                    MessagePart(kind="image", url=url, file=file, text="[图片]")
                )
            elif cq.startswith("reply,"):
                id_m = re.search(r"id=(\d+)", cq)
                if id_m:
                    parts.append(MessagePart(kind="reply", reply_id=id_m.group(1)))
            pos = m.end()
        if pos < len(raw):
            tail = raw[pos:]
            if tail:
                parts.append(MessagePart(kind="text", text=tail))
        return parts, at_ids

    @staticmethod
    def _ensure_reply_from_raw(raw: str, parts: List[MessagePart]) -> None:
        if any(p.kind == "reply" for p in parts):
            return
        match = re.search(r"\[CQ:reply,id=(\d+)\]", raw)
        if match:
            parts.insert(0, MessagePart(kind="reply", reply_id=match.group(1)))

    @staticmethod
    def _enrich_image_urls_from_raw(raw: str, parts: List[MessagePart]) -> None:
        cq_images: List[tuple[str, str]] = []
        for m in re.finditer(r"\[CQ:image,([^\]]+)\]", raw):
            body = m.group(1)
            url_m = re.search(r"url=([^,\]]+)", body)
            file_m = re.search(r"file=([^,\]]+)", body)
            url = url_m.group(1).replace("&amp;", "&") if url_m else ""
            file = file_m.group(1) if file_m else ""
            cq_images.append((url, file))
        img_idx = 0
        for p in parts:
            if p.kind != "image":
                continue
            if img_idx < len(cq_images):
                url, file = cq_images[img_idx]
                if not p.url and url:
                    p.url = url
                if not p.file and file:
                    p.file = file
            img_idx += 1

    @staticmethod
    def _extract_plain_text(event: GroupMessage) -> str:
        chunks: List[str] = []
        for seg in event.message:
            if isinstance(seg, PlainText) and seg.text:
                chunks.append(seg.text)
        if chunks:
            return "".join(chunks).strip()
        return re.sub(r"\[CQ:[^\]]+\]", "", event.raw_message).strip()

    @staticmethod
    def _format_full_time(ts: float) -> str:
        if ts <= 0:
            return "--"
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
