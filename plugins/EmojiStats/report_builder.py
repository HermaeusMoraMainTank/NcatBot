from typing import Dict, Optional

from common.stats_render import RenderInfo, save_daily_trend_chart, save_top10_list, save_top3_podium
from common.stats_render.charts import save_top_emojis_chart
from common.stats_render.helpers import get_date_keys, period_label, rank_users
from common.stats_render.report import save_stats_report_file


def _emoji_count_in_range(emoji_stat, days: Optional[int]) -> int:
    if days is None:
        return sum(emoji_stat.daily_counts.values())
    keys = get_date_keys(days)
    return sum(emoji_stat.daily_counts.get(k, 0) for k in keys)


async def build_emoji_group_report(
    group_id: str,
    days: Optional[int],
    emoji_stats: Dict[str, object],
    user_counts: Dict[str, int],
    user_names: Dict[str, str],
) -> Optional[str]:
    sections = {}
    if user_counts:
        ranked = await rank_users(group_id, user_counts, user_names, "次")
        if len(ranked) >= 3:
            sections["表情包达人 TOP3"] = save_top3_podium((ranked[0], ranked[1], ranked[2]))
        sections["表情包使用 TOP10"] = save_top10_list(ranked, "表情包使用 TOP10")

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

    daily_total: Dict[str, int] = {}
    for em in emoji_stats.values():
        for d, c in (em.daily_counts or {}).items():
            daily_total[d] = daily_total.get(d, 0) + c
    if days != 1 and daily_total:
        keys = get_date_keys(days) if days else None
        filtered = {k: v for k, v in daily_total.items() if not keys or k in keys}
        trend = save_daily_trend_chart(filtered, days)
        if trend:
            sections["使用趋势"] = trend

    if not sections:
        return None
    info = RenderInfo(
        title="群组表情包统计报告",
        period_label=period_label(days),
        group_label=f"群号：{group_id}",
    )
    return await save_stats_report_file(info, sections, prefix="emoji_stats")
