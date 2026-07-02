from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from .rankings import RenderUserInfo


def period_label(days: Optional[int]) -> str:
    now = datetime.now()
    if days is None:
        return "全部时间"
    if days == 1:
        return now.strftime("%Y年%m月%d日（今日）")
    if days == 7:
        start = date.today() - timedelta(days=date.today().weekday())
        return f"{start.strftime('%Y-%m-%d')} ~ {now.strftime('%Y-%m-%d')}（本周）"
    if days == 30:
        start = date(date.today().year, date.today().month, 1)
        return f"{start.strftime('%Y-%m-%d')} ~ {now.strftime('%Y-%m-%d')}（本月）"
    end = date.today()
    start = end - timedelta(days=days - 1)
    return f"{start.strftime('%Y-%m-%d')} ~ {end.strftime('%Y-%m-%d')}"


def get_period_start(days: Optional[int], end: Optional[date] = None) -> Optional[date]:
    """与 period_label / get_date_keys 一致的时间范围起点。"""
    if days is None:
        return None
    end = end or date.today()
    if days == 7:
        return end - timedelta(days=end.weekday())
    if days == 30:
        return date(end.year, end.month, 1)
    return end - timedelta(days=days - 1)


def is_date_in_period(date_str: str, days: Optional[int], *, end: Optional[date] = None) -> bool:
    if days is None:
        return True
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return False
    start = get_period_start(days, end=end)
    end = end or date.today()
    if start is None:
        return True
    return start <= d <= end


def filter_daily_by_period(daily: Dict[str, int], days: Optional[int]) -> Dict[str, int]:
    if days is None:
        return dict(daily)
    return {k: int(v) for k, v in daily.items() if is_date_in_period(k, days)}


def sum_daily_by_period(daily: Dict[str, int], days: Optional[int]) -> int:
    return sum(filter_daily_by_period(daily, days).values())


def period_display_label(days: Optional[int]) -> str:
    if days is None:
        return "全部时间"
    if days == 1:
        return "今日"
    if days == 7:
        return "本周"
    if days == 30:
        return "本月"
    return f"最近{days}天"


def get_date_keys(days: Optional[int]) -> set[str]:
    end = date.today()
    if days is None:
        return set()
    if days == 7:
        start = end - timedelta(days=end.weekday())
    elif days == 30:
        start = date(end.year, end.month, 1)
    else:
        start = end - timedelta(days=days - 1)
    keys: set[str] = set()
    d = start
    while d <= end:
        keys.add(d.isoformat())
        d += timedelta(days=1)
    return keys


def sum_user_metric(
    user_stats: Dict[str, object],
    days: Optional[int],
    field_daily: str,
    *,
    use_max: bool = False,
) -> Dict[str, int]:
    keys = get_date_keys(days) if days else None
    result: Dict[str, int] = {}
    for uid, stat in user_stats.items():
        daily: Dict[str, int] = getattr(stat, field_daily, None) or {}
        if keys is None:
            val = max(daily.values()) if use_max and daily else sum(daily.values())
        elif use_max:
            val = max((daily.get(k, 0) for k in keys), default=0)
        else:
            val = sum(daily.get(k, 0) for k in keys)
        if val > 0:
            result[uid] = val
    return result


async def rank_users(
    group_id: str,
    counts: Dict[str, int],
    user_names: Dict[str, str],
    unit: str,
    top_n: int = 10,
    *,
    avatar_type: str = "user",
) -> List[RenderUserInfo]:
    top = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:top_n]
    out = []
    for rank, (uid, cnt) in enumerate(top, 1):
        out.append(
            await RenderUserInfo.create(
                group_id,
                uid,
                rank,
                f"{cnt} {unit}",
                nickname_map=user_names,
                avatar_type=avatar_type,
            )
        )
    return out
