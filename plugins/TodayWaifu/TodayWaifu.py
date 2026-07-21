"""今日群友老婆 — 活跃池抽取 + 强娶/求婚 + 关系图。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from common.constants.HMMT import HMMT
from common.utils.CommonUtil import CommonUtil
from ncatbot.core import registrar
from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.plugin import NcatBotPlugin
from ncatbot.utils import get_log

from .commands import handlers
from .config import DEFAULT_CONFIG, KEYWORD_ROUTES
from .core import (
    get_active_days,
    is_group_allowed,
    match_keyword,
    normalize_id_set,
    pick_from_active_members,
    record_active,
    today_str,
)
from .store import WaifuStore

_log = get_log()

PLUGIN_DIR = Path(__file__).resolve().parent


class TodayWaifu(NcatBotPlugin):
    name = "TodayWaifu"
    version = "2.0.0"

    async def on_load(self):
        self.init_defaults(DEFAULT_CONFIG)
        self.data_dir = PLUGIN_DIR / "data"
        self.output_dir = self.data_dir / "output"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.store = WaifuStore(self.data_dir / "today_waifu.db")
        self.bot_id = str(HMMT.BOT_ID)
        self.admin_ids = {str(HMMT.HMMT_ID)}
        # 内存态：求婚 / 强娶确认
        self._propose: dict[str, dict[str, dict]] = {}
        self._force_confirm: dict[str, dict[str, dict]] = {}
        self._last_cleanup = 0.0
        days = get_active_days(self.cfg_dict())
        removed = self.store.cleanup_active(
            days, int(self.cfg_dict().get("max_records", 500) or 500)
        )
        self.store.cleanup_rbq(30)
        cleared = self._cleanup_output_dir()
        # 每小时清理一次关系图渲染缓存
        try:
            self.add_scheduled_task(
                "todaywaifu_output_cleanup",
                "60m",
                callback=self._scheduled_output_cleanup,
            )
        except Exception as e:
            _log.debug("[TodayWaifu] 注册输出清理任务失败: %s", e)
        _log.info(
            "TodayWaifu v%s 加载完成，清理过期活跃 %s 条，输出缓存 %s 个",
            self.version,
            removed,
            cleared,
        )

    async def on_close(self):
        try:
            self.store.close()
        except Exception:
            pass

    def cfg_dict(self) -> dict[str, Any]:
        out = dict(DEFAULT_CONFIG)
        for key, default in DEFAULT_CONFIG.items():
            out[key] = self.get_config(key, default)
        return out

    def cfg_bool(self, key: str) -> bool:
        return bool(self.cfg_dict().get(key))

    def is_admin(self, user_id: str) -> bool:
        return str(user_id) in self.admin_ids

    def _maybe_cleanup(self) -> None:
        now = time.time()
        if now - self._last_cleanup < 3600:
            return
        self._last_cleanup = now
        cfg = self.cfg_dict()
        self.store.cleanup_active(
            get_active_days(cfg), int(cfg.get("max_records", 500) or 500)
        )
        self.store.cleanup_rbq(30)
        self._cleanup_output_dir()

    def _cleanup_output_dir(self, max_age_seconds: int = 3600) -> int:
        """清理关系图/排行渲染缓存，默认保留 1 小时内文件。"""
        if not self.output_dir.exists():
            return 0
        now = time.time()
        removed = 0
        try:
            for path in self.output_dir.iterdir():
                if not path.is_file():
                    continue
                if path.name == ".gitignore":
                    continue
                if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                    continue
                try:
                    if now - path.stat().st_mtime > max_age_seconds:
                        path.unlink(missing_ok=True)
                        removed += 1
                except OSError as e:
                    _log.warning("[TodayWaifu] 删除缓存失败 %s: %s", path, e)
        except Exception as e:
            _log.warning("[TodayWaifu] 清理输出目录失败: %s", e)
        if removed:
            _log.info("[TodayWaifu] 已清理 %s 个过期渲染缓存", removed)
        return removed

    async def _scheduled_output_cleanup(self) -> None:
        self._cleanup_output_dir()

    # ── 求婚状态 ──────────────────────────────────────────────

    def create_propose(
        self, group_id: str, proposer_id: str, target_id: str, timeout: int
    ) -> None:
        g = self._propose.setdefault(str(group_id), {})
        # 清理同发起人旧请求
        for tid in list(g.keys()):
            if g[tid].get("proposer_id") == proposer_id:
                g.pop(tid, None)
        g[str(target_id)] = {
            "proposer_id": str(proposer_id),
            "expire": time.time() + max(5, int(timeout)),
        }

    def get_propose_for_target(self, group_id: str, target_id: str) -> Optional[dict]:
        g = self._propose.get(str(group_id), {})
        req = g.get(str(target_id))
        if not req:
            return None
        if float(req.get("expire", 0)) <= time.time():
            g.pop(str(target_id), None)
            return None
        return req

    def clear_propose(self, group_id: str, target_id: str) -> None:
        g = self._propose.get(str(group_id))
        if g:
            g.pop(str(target_id), None)

    def create_force_confirm(
        self, group_id: str, proposer_id: str, target_id: str, timeout: int
    ) -> None:
        g = self._force_confirm.setdefault(str(group_id), {})
        g[str(proposer_id)] = {
            "target_id": str(target_id),
            "expire": time.time() + max(5, int(timeout)),
        }

    def pop_force_confirm(self, group_id: str, proposer_id: str) -> Optional[dict]:
        g = self._force_confirm.get(str(group_id), {})
        req = g.get(str(proposer_id))
        if not req:
            return None
        if float(req.get("expire", 0)) <= time.time():
            g.pop(str(proposer_id), None)
            return None
        g.pop(str(proposer_id), None)
        return req

    # ── 抽选 ──────────────────────────────────────────────────

    async def pick_wife(self, event: GroupMessage, exclude_ids: set[str]):
        group_id = str(event.group_id)
        cfg = self.cfg_dict()
        self._maybe_cleanup()
        days = get_active_days(cfg)
        active = self.store.list_active(group_id, days)
        # 确保发起人也在活跃池
        active.add(str(event.sender.user_id))

        members_response = await self.api.qq.query.get_group_member_list(
            group_id=group_id
        )
        members = CommonUtil.parse_group_member_list(members_response)
        excluded = set(exclude_ids) | normalize_id_set(cfg.get("excluded_users"))

        # 仅在开启一夫一妻时，才排除今日已被抽中的人
        if bool(cfg.get("exclusive_allocation")):
            for rec in self.store.get_today_records(group_id, today_str()):
                wid = str(rec.get("wife_id") or "")
                if wid:
                    excluded.add(wid)

        return pick_from_active_members(
            members=members,
            active_ids=active,
            exclude_ids=excluded,
            allow_bot=bool(cfg.get("allow_marry_bot")),
            bot_id=self.bot_id,
        )

    def _resolve_action(self, text: str) -> Optional[str]:
        cfg = self.cfg_dict()
        mode = str(cfg.get("keyword_trigger_mode") or "exact")
        enabled = bool(cfg.get("keyword_trigger_enabled", True))
        if not enabled:
            # 仍允许完整中文主指令
            for kw in ("今日老婆", "我的老婆", "关系图", "抽老婆帮助", "rbq排行"):
                if match_keyword(text, kw, "exact"):
                    return KEYWORD_ROUTES[kw]
            if text.startswith("强娶") or text.startswith("求婚"):
                return KEYWORD_ROUTES.get(text.split()[0], None)
            return None

        # 前缀类：强娶 / 求婚
        for prefix, action in (("强娶", "force_marry"), ("求婚", "propose"), ("qiangqu", "force_marry"), ("qh", "propose")):
            if text == prefix or text.startswith(prefix + " ") or text.startswith(prefix + "@") or text.startswith(prefix + "＠"):
                return action

        for keyword, action in KEYWORD_ROUTES.items():
            if match_keyword(text, keyword, mode):
                return action
        return None

    @registrar.qq.on_group_message(priority=40)
    async def handle_message(self, event: GroupMessage) -> None:
        group_id = str(event.group_id)
        cfg = self.cfg_dict()
        if not is_group_allowed(group_id, cfg):
            return

        user_id = str(event.sender.user_id)
        # 活跃记录（查询指令本身也算活跃）
        record_active(self.store, cfg, group_id, user_id, self.bot_id)

        text = ""
        for seg in event.message or []:
            if hasattr(seg, "text") and seg.text:
                text += seg.text
        text = text.strip()
        if not text:
            text = (event.raw_message or "").strip()
            # 去掉 CQ 码便于匹配前缀
            import re

            text = re.sub(r"\[CQ:[^\]]+\]", "", text).strip()

        # 求婚回复优先（「强娶」仅在无 @ 时视为确认，避免挡住「强娶 @人」）
        has_at = handlers.extract_at_user_id(event) is not None
        if text in ("同意", "拒绝", "确认强娶", "是") or (
            text == "强娶" and not has_at
        ):
            if await handlers.handle_propose_reply(self, event, text):
                return

        action = self._resolve_action(text)
        if not action:
            return

        dispatch = {
            "draw": handlers.cmd_draw,
            "history": handlers.cmd_history,
            "force_marry": handlers.cmd_force_marry,
            "propose": handlers.cmd_propose,
            "graph": handlers.cmd_graph,
            "rbq": handlers.cmd_rbq,
            "help": handlers.cmd_help,
            "reset_records": handlers.cmd_reset_records,
            "reset_force_cd": handlers.cmd_reset_force_cd,
            "reset_propose_cd": handlers.cmd_reset_propose_cd,
        }
        handler = dispatch.get(action)
        if handler:
            await handler(self, event)
