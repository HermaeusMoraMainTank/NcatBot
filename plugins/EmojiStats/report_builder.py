from typing import Dict, Optional

from common.stats_render import (
    RenderInfo,
    save_daily_trend_chart,
    save_top10_list,
    save_top3_podium,
)
from common.stats_render.charts import save_top_emojis_chart
from common.stats_render.helpers import get_date_keys, period_label, rank_users
from common.stats_render.report import save_stats_report_file


def _emoji_count_in_range(emoji_stat, days: Optional[int]) -> int:
    if days is None:
        return sum(emoji_stat.daily_counts.values())
    keys = get_date_keys(days)
    return sum(emoji_stat.daily_counts.get(k, 0) for k in keys)


def _user_distinct_emoji_counts(
    user_emoji_stats: Dict[str, Dict[str, object]],
    days: Optional[int],
) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for uid, emojis in user_emoji_stats.items():
        n = sum(
            1 for em in emojis.values() if _emoji_count_in_range(em, days) > 0
        )
        if n > 0:
            out[str(uid)] = n
    return out


def _user_max_daily_sends(
    user_daily_count: Dict[str, Dict[str, int]],
    days: Optional[int],
) -> Dict[str, int]:
    keys = get_date_keys(days) if days is not None else None
    out: Dict[str, int] = {}
    for uid, daily in user_daily_count.items():
        if keys:
            mx = max((daily.get(k, 0) for k in keys), default=0)
        else:
            mx = max(daily.values()) if daily else 0
        if mx > 0:
            out[str(uid)] = mx
    return out


def _emoji_section_scales(days: Optional[int]) -> Dict[str, float]:
    return {
        "使用趋势": 0.82,
        "表情包达人 TOP3": 0.84,
        "表情包使用 TOP10": 0.78,
        "表情种类 TOP10": 0.78,
        "单日狂发 TOP10": 0.78,
        "热门表情 TOP10": 0.80,
    }


async def build_emoji_group_report(
    group_id: str,
    days: Optional[int],
    emoji_stats: Dict[str, object],
    user_counts: Dict[str, int],
    user_names: Dict[str, str],
    *,
    user_emoji_stats: Optional[Dict[str, Dict[str, object]]] = None,
    user_daily_count: Optional[Dict[str, Dict[str, int]]] = None,
) -> Optional[str]:
    sections = {}
    user_emoji_stats = user_emoji_stats or {}
    user_daily_count = user_daily_count or {}

    daily_total: Dict[str, int] = {}
    for em in emoji_stats.values():
        for d, c in (em.daily_counts or {}).items():
            daily_total[d] = daily_total.get(d, 0) + c
    if days != 1:
        keys = get_date_keys(days) if days is not None else None
        filtered = {
            k: v for k, v in daily_total.items() if keys is None or k in keys
        }
        trend = save_daily_trend_chart(
            filtered or daily_total, days, title="使用趋势"
        )
        if trend:
            sections["使用趋势"] = trend

    if user_counts:
        ranked = await rank_users(group_id, user_counts, user_names, "次")
        if len(ranked) >= 3:
            sections["表情包达人 TOP3"] = save_top3_podium(
                (ranked[0], ranked[1], ranked[2]),
                gap=44,
                text_width_reduce=14,
            )
        sections["表情包使用 TOP10"] = save_top10_list(
            ranked, "表情包使用 TOP10", compact=True
        )

    distinct_counts = _user_distinct_emoji_counts(user_emoji_stats, days)
    if distinct_counts:
        ranked = await rank_users(group_id, distinct_counts, user_names, "种")
        sections["表情种类 TOP10"] = save_top10_list(
            ranked, "表情种类 TOP10", compact=True
        )

    max_daily = _user_max_daily_sends(user_daily_count, days)
    if max_daily:
        ranked = await rank_users(group_id, max_daily, user_names, "次/日")
        sections["单日狂发 TOP10"] = save_top10_list(
            ranked, "单日狂发 TOP10", compact=True
        )

    top_emojis = sorted(
        emoji_stats.values(),
        key=lambda e: _emoji_count_in_range(e, days),
        reverse=True,
    )[:10]
    emoji_items = []
    for i, em in enumerate(top_emojis, 1):
        cnt = _emoji_count_in_range(em, days)
        if cnt <= 0:
            continue
        emoji_items.append((em.cache_path, cnt, f"表情{i}"))
    chart = save_top_emojis_chart(emoji_items, "热门表情 TOP10")
    if chart:
        sections["热门表情 TOP10"] = chart

    if not sections:
        return None
    info = RenderInfo(
        title="群组表情包统计报告",
        period_label=period_label(days),
        group_label=f"群号：{group_id}",
    )
    return await save_stats_report_file(
        info,
        sections,
        prefix="emoji_stats",
        section_scales=_emoji_section_scales(days),
        default_scale=0.82,
        page=2,
    )
