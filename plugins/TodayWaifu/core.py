"""TodayWaifu 业务辅助。"""

from __future__ import annotations

import random
import time
from datetime import date, datetime
from typing import Any, Protocol


class StoreLike(Protocol):
    def touch_active(
        self, group_id: str, user_id: str, ts: float | None = None
    ) -> None: ...

    def list_active(self, group_id: str, days: int) -> set[str]: ...

    def cleanup_active(self, days: int, max_records: int) -> int: ...

    def add_wife_record(
        self,
        date: str,
        group_id: str,
        user_id: str,
        wife_id: str,
        wife_name: str,
        timestamp: str,
        forced: bool = False,
        daily_limit: int = 1,
    ) -> None: ...

    def get_user_today_records(
        self, group_id: str, user_id: str, date_str: str
    ) -> list[dict[str, Any]]: ...

    def get_force_cd(self, group_id: str, user_id: str) -> float | None: ...

    def get_propose_cd(self, group_id: str, user_id: str) -> dict[str, Any] | None: ...


def today_str() -> str:
    return date.today().isoformat()


def normalize_id_set(raw: Any) -> set[str]:
    if raw is None:
        return set()
    if isinstance(raw, (str, int)):
        s = str(raw).strip()
        return {s} if s else set()
    try:
        return {str(x).strip() for x in raw if str(x).strip()}
    except TypeError:
        return set()


def is_group_allowed(group_id: str, cfg: dict[str, Any]) -> bool:
    gid = str(group_id)
    whitelist = normalize_id_set(cfg.get("whitelist_groups"))
    blacklist = normalize_id_set(cfg.get("blacklist_groups"))
    if whitelist and gid not in whitelist:
        return False
    if gid in blacklist:
        return False
    return True


def get_active_days(cfg: dict[str, Any]) -> int:
    try:
        return min(30, max(1, int(cfg.get("active_user_days", 30))))
    except (TypeError, ValueError):
        return 30


def get_daily_limit(cfg: dict[str, Any]) -> int:
    try:
        return max(1, int(cfg.get("daily_limit", 1)))
    except (TypeError, ValueError):
        return 1


def get_force_cd_days(cfg: dict[str, Any]) -> int:
    try:
        return max(0, int(cfg.get("force_marry_cd", 3)))
    except (TypeError, ValueError):
        return 3


def get_propose_cd_seconds(cfg: dict[str, Any]) -> int:
    try:
        minutes = int(float(cfg.get("propose_cooldown_minutes", 60)))
    except (TypeError, ValueError):
        minutes = 60
    return max(0, minutes) * 60


def is_cd_exempt(user_id: str) -> bool:
    """管理员无抽取次数 / 强娶 / 求婚冷却。"""
    from common.constants.HMMT import HMMT

    return str(user_id) == str(HMMT.HMMT_ID)


def force_cd_remaining(
    store: StoreLike,
    group_id: str,
    user_id: str,
    cfg: dict[str, Any],
) -> float | None:
    if is_cd_exempt(user_id):
        return None
    last = store.get_force_cd(str(group_id), str(user_id))
    if last is None:
        return None
    cd_seconds = get_force_cd_days(cfg) * 86400
    if cd_seconds <= 0:
        return None
    remain = (float(last) + cd_seconds) - time.time()
    return remain if remain > 0 else None


def propose_cd_remaining(
    store: StoreLike,
    group_id: str,
    user_id: str,
) -> float | None:
    if is_cd_exempt(user_id):
        return None
    row = store.get_propose_cd(str(group_id), str(user_id))
    if not row:
        return None
    remain = float(row["expire_at"]) - time.time()
    return remain if remain > 0 else None


def upsert_draw_record(
    store: StoreLike,
    cfg: dict[str, Any],
    group_id: str,
    user_id: str,
    wife_id: str,
    wife_name: str,
    forced: bool = False,
) -> None:
    limit = 9999 if is_cd_exempt(user_id) else get_daily_limit(cfg)
    store.add_wife_record(
        date=today_str(),
        group_id=str(group_id),
        user_id=str(user_id),
        wife_id=str(wife_id),
        wife_name=str(wife_name),
        timestamp=datetime.now().isoformat(timespec="seconds"),
        forced=forced,
        daily_limit=limit,
    )


def record_active(
    store: StoreLike,
    cfg: dict[str, Any],
    group_id: str,
    user_id: str,
    bot_id: str | int | None,
) -> None:
    uid = str(user_id)
    if bot_id is not None and uid == str(bot_id):
        return
    if uid == "0":
        return
    store.touch_active(str(group_id), uid)


def user_draw_count(store: StoreLike, group_id: str, user_id: str) -> int:
    return len(store.get_user_today_records(str(group_id), str(user_id), today_str()))


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分")
    if secs or not parts:
        parts.append(f"{secs}秒")
    return "".join(parts)


def match_keyword(text: str, keyword: str, mode: str) -> bool:
    t = (text or "").strip()
    k = keyword.strip()
    if not t or not k:
        return False
    if mode == "starts_with":
        return t.startswith(k)
    if mode == "contains":
        return k in t
    return t == k


def pick_from_active_members(
    *,
    members: list[Any],
    active_ids: set[str],
    exclude_ids: set[str],
    allow_bot: bool,
    bot_id: str,
) -> Any | None:
    """从活跃成员中随机挑选，排除 exclude_ids。"""
    pool = []
    for m in members:
        uid = str(getattr(m, "user_id", "") or "")
        if not uid or uid == "0":
            continue
        if uid not in active_ids:
            continue
        if uid in exclude_ids:
            continue
        if not allow_bot and (uid == bot_id or bool(getattr(m, "is_robot", False))):
            continue
        pool.append(m)
    if not pool:
        return None
    return random.choice(pool)


def display_name(member: Any | None, fallback_id: str) -> str:
    if member is None:
        return str(fallback_id)
    return (
        str(getattr(member, "card", "") or "")
        or str(getattr(member, "nickname", "") or "")
        or str(fallback_id)
    )
