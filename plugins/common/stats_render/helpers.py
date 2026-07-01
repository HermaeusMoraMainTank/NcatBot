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


def get_date_keys(days: Optional[int]) -> set[str]:
    end = date.today()
    if days is None:
        return set()
    if days == 7:
        start = end - timedelta(days=end.weekday())
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
