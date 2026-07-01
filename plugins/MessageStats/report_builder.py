from typing import Dict, Optional

from common.stats_render import (
    RenderInfo,
    aggregate_word_stats,
    save_daily_trend_chart,
    save_hourly_from_daily_buckets,
    save_pos_chart,
    save_top10_list,
    save_top3_podium,
    save_wordcloud_chart,
)
from common.stats_render.charts import save_hourly_chart
from common.stats_render.helpers import get_date_keys, period_label, rank_users, sum_user_metric
from common.stats_render.report import save_stats_report_file


async def build_group_report(
    group_id: str,
    days: Optional[int],
    stats,
    user_stats: Dict[str, object],
    user_names: Dict[str, str],
    group_label: str = "",
) -> Optional[str]:
    date_keys = get_date_keys(days)
    sections = {}

    if stats.daily_hourly_counts:
        if days == 1 and date_keys:
            today = __import__("datetime").date.today().isoformat()
            hourly = {
                int(h): c
                for h, c in (stats.daily_hourly_counts.get(today) or {}).items()
            }
            sections["小时活跃度"] = save_hourly_chart(hourly)
        elif date_keys:
            sections["小时活跃度"] = save_hourly_from_daily_buckets(
                stats.daily_hourly_counts, date_keys
            )

    if days != 1:
        range_daily = {
            k: v
            for k, v in (stats.daily_counts or {}).items()
            if not date_keys or k in date_keys
        }
        trend = save_daily_trend_chart(range_daily, days)
        if trend:
            sections["发言趋势"] = trend

    msg_counts: Dict[str, int] = {}
    for uid, s in user_stats.items():
        daily = getattr(s, "daily_counts", {}) or {}
        if date_keys:
            val = sum(daily.get(k, 0) for k in date_keys)
        else:
            val = sum(daily.values())
        if val > 0:
            msg_counts[uid] = val
    if msg_counts:
        ranked = await rank_users(group_id, msg_counts, user_names, "条")
        if len(ranked) >= 3:
            sections["话痨排行 TOP3"] = save_top3_podium((ranked[0], ranked[1], ranked[2]))
        sections["话痨排行 TOP10"] = save_top10_list(ranked, "话痨排行 TOP10")

    char_counts = sum_user_metric(user_stats, days, "daily_char_totals")
    if char_counts:
        ranked = await rank_users(group_id, char_counts, user_names, "字")
        sections["字数统计 TOP10"] = save_top10_list(ranked, "字数统计 TOP10")

    long_counts = sum_user_metric(user_stats, days, "daily_max_message", use_max=True)
    if long_counts:
        ranked = await rank_users(group_id, long_counts, user_names, "字")
        sections["长文写手 TOP10"] = save_top10_list(ranked, "单条最长 TOP10")

    words, pos = aggregate_word_stats(
        getattr(stats, "daily_word_counts", None),
        getattr(stats, "daily_pos_counts", None),
        date_keys if date_keys else set((stats.daily_word_counts or {}).keys()),
    )
    if words:
        wc = save_wordcloud_chart(dict(words))
        if wc:
            sections["高频词云"] = wc
    if pos:
        pc = save_pos_chart(dict(pos))
        if pc:
            sections["词性分布"] = pc

    if not sections:
        return None

    info = RenderInfo(
        title="群组发言统计报告",
        period_label=period_label(days),
        group_label=group_label,
    )
    return await save_stats_report_file(info, sections, prefix="message_stats")


async def build_personal_report(
    group_id: str,
    user_id: str,
    days: Optional[int],
    stats,
    nickname: str,
) -> Optional[str]:
    date_keys = get_date_keys(days)
    sections = {}

    if stats.daily_hourly_counts:
        if days == 1 and date_keys:
            today = __import__("datetime").date.today().isoformat()
            hourly = {
                int(h): c
                for h, c in (stats.daily_hourly_counts.get(today) or {}).items()
            }
            sections["个人活跃时段"] = save_hourly_chart(hourly)
        elif date_keys:
            sections["个人活跃时段"] = save_hourly_from_daily_buckets(
                stats.daily_hourly_counts, date_keys
            )

    if days != 1:
        range_daily = {
            k: v
            for k, v in (stats.daily_counts or {}).items()
            if not date_keys or k in date_keys
        }
        trend = save_daily_trend_chart(range_daily, days)
        if trend:
            sections["个人发言趋势"] = trend

    if not sections:
        return None

    info = RenderInfo(
        title="个人发言统计报告",
        period_label=period_label(days),
        group_label=f"用户：{nickname}（{user_id}）",
    )
    return await save_stats_report_file(info, sections, prefix="message_personal")
