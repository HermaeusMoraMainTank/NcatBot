from typing import Dict, List, Optional

from common.utils.CommonUtil import CommonUtil
from common.stats_render import RenderInfo, save_top10_list, save_top3_podium
from common.stats_render.charts import (
    save_labeled_bar_chart,
    save_source_breakdown_chart,
)
from common.stats_render.helpers import period_label, rank_users
from common.stats_render.rankings import RenderUserInfo
from common.stats_render.report import save_stats_report_file


def _format_cost(cost: float) -> str:
    return f"¥{cost:.4f}" if cost else "¥0.0000"


def _rank_groups(
    counts: Dict[str, int],
    group_names: Dict[str, str],
    unit: str,
    top_n: int = 10,
) -> List[RenderUserInfo]:
    """群组排行（使用群头像，不依赖 helpers.rank_users 以免热重载缓存旧版）。"""
    top = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:top_n]
    out: List[RenderUserInfo] = []
    for rank, (gid, cnt) in enumerate(top, 1):
        out.append(
            RenderUserInfo(
                group_id=gid,
                user_id=gid,
                rank=rank,
                count=f"{cnt} {unit}",
                nickname=group_names.get(gid, gid),
                avatar_path=CommonUtil.get_group_avatar(gid),
            )
        )
    return out


def _source_extra_lines(source_rows: List[dict]) -> List[str]:
    lines = ["—— 各来源汇总 ——"]
    for row in source_rows:
        if not row.get("count") and not row.get("tokens") and not row.get("cost"):
            continue
        label = row.get("label", "?")
        lines.append(
            f"{label}：{row.get('count', 0)} 次 · "
            f"{row.get('tokens', 0):,} tok · {_format_cost(row.get('cost', 0))}"
        )
    return lines


async def build_ai_group_report(
    group_id: str,
    days: Optional[int],
    stats,
    user_counts: Dict[str, int],
    user_names: Dict[str, str],
    source_rows: List[dict],
) -> Optional[str]:
    sections = {}
    daily = stats.daily_counts or {}
    if days is not None:
        from common.stats_render.helpers import get_date_keys

        keys = get_date_keys(days)
        daily = {k: v for k, v in daily.items() if k in keys}

    if days != 1 and daily:
        from common.stats_render import save_daily_trend_chart

        trend = save_daily_trend_chart(daily, days)
        if trend:
            sections["AI 调用趋势"] = trend

    if user_counts:
        ranked = await rank_users(group_id, user_counts, user_names, "次")
        if len(ranked) >= 3:
            sections["使用排行 TOP3"] = save_top3_podium((ranked[0], ranked[1], ranked[2]))
        sections["使用排行 TOP10"] = save_top10_list(ranked, "AI 使用 TOP10")

    if source_rows:
        breakdown = save_source_breakdown_chart(source_rows, "来源消耗明细")
        if breakdown:
            sections["来源明细"] = breakdown
        items = [
            (r.get("label", r.get("source", "?")), r.get("count", 0))
            for r in source_rows
            if r.get("count", 0) > 0
        ]
        chart = save_labeled_bar_chart(items[:10], "来源分布", "次")
        if chart:
            sections["来源分布"] = chart

    if not sections:
        return None
    info = RenderInfo(
        title="群组 AI 统计报告",
        period_label=period_label(days),
        group_label=f"群号：{group_id}",
    )
    return await save_stats_report_file(info, sections, prefix="ai_group")


async def build_ai_overview_report(
    days: Optional[int],
    group_ranking: List[dict],
    group_names: Dict[str, str],
    source_rows: List[dict],
    totals: dict,
) -> List[str]:
    """生成两页总览：第 1 页来源明细，第 2 页群组排行。"""
    total_cost = totals.get("total_cost", 0)
    summary_lines = [
        f"总调用：{totals.get('total_count', 0)} 次",
        f"总 Token：{totals.get('total_tokens', 0):,}",
        f"总费用：{_format_cost(total_cost)}",
        f"活跃群数：{totals.get('active_groups', 0)}",
    ]
    summary_lines.extend(_source_extra_lines(source_rows))

    pages: List[str] = []

    # —— 第 1 页：汇总 + 来源明细 ——
    page1_sections = {}
    page1_scales = {}
    if source_rows:
        breakdown = save_source_breakdown_chart(source_rows, "全群来源消耗明细")
        if breakdown:
            page1_sections["来源消耗明细"] = breakdown
            page1_scales["来源消耗明细"] = 0.9

        token_items = [
            (r.get("label", "?"), r.get("tokens", 0))
            for r in source_rows
            if r.get("tokens", 0) > 0
        ]
        if token_items:
            chart = save_labeled_bar_chart(token_items, "各来源 Token", "tok")
            if chart:
                page1_sections["各来源 Token"] = chart
                page1_scales["各来源 Token"] = 0.82

        cost_items = [
            (r.get("label", "?"), round(r.get("cost", 0), 4))
            for r in source_rows
            if r.get("cost", 0) > 0
        ]
        if cost_items:
            chart = save_labeled_bar_chart(cost_items, "各来源费用", "元")
            if chart:
                page1_sections["各来源费用"] = chart
                page1_scales["各来源费用"] = 0.82

        count_items = [
            (r.get("label", "?"), r.get("count", 0))
            for r in source_rows
            if r.get("count", 0) > 0
        ]
        if count_items:
            chart = save_labeled_bar_chart(count_items, "各来源调用次数", "次")
            if chart:
                page1_sections["各来源调用次数"] = chart
                page1_scales["各来源调用次数"] = 0.82

    if page1_sections:
        info1 = RenderInfo(
            title="全群 AI 使用总览（1/2）",
            period_label=period_label(days),
            group_label="跨群汇总 · 来源明细",
            extra_lines=summary_lines,
        )
        path1 = await save_stats_report_file(
            info1,
            page1_sections,
            prefix="ai_overview_p1",
            section_scales=page1_scales,
            default_scale=0.82,
            page=2,
        )
        if path1:
            pages.append(path1)

    # —— 第 2 页：群组排行 ——
    page2_sections = {}
    page2_scales = {}
    if group_ranking:
        counts = {item["group_id"]: item.get("count", 0) for item in group_ranking}
        names = {gid: group_names.get(gid, gid) for gid in counts}
        ranked = _rank_groups(counts, names, "次")
        if len(ranked) >= 3:
            page2_sections["群组 TOP3"] = save_top3_podium(
                (ranked[0], ranked[1], ranked[2]),
                gap=72,
                text_width_reduce=12,
            )
            page2_scales["群组 TOP3"] = 0.88
        page2_sections["群组 TOP10"] = save_top10_list(
            ranked, "全群 AI 使用 TOP10", compact=True
        )
        page2_scales["群组 TOP10"] = 0.72

        cost_ranked = sorted(
            group_ranking,
            key=lambda x: (x.get("cost", 0), x.get("count", 0)),
            reverse=True,
        )[:10]
        cost_items = [
            (group_names.get(item["group_id"], item["group_id"]), round(item.get("cost", 0), 4))
            for item in cost_ranked
            if item.get("cost", 0) > 0
        ]
        if cost_items:
            chart = save_labeled_bar_chart(cost_items, "群组费用 TOP10", "元")
            if chart:
                page2_sections["群组费用 TOP10"] = chart
                page2_scales["群组费用 TOP10"] = 0.78

    if page2_sections:
        info2 = RenderInfo(
            title="全群 AI 使用总览（2/2）",
            period_label=period_label(days),
            group_label="跨群汇总 · 群组排行",
            extra_lines=summary_lines[:4],
        )
        path2 = await save_stats_report_file(
            info2,
            page2_sections,
            prefix="ai_overview_p2",
            section_scales=page2_scales,
            default_scale=0.8,
            page=2,
        )
        if path2:
            pages.append(path2)

    return pages


async def build_ai_personal_report(
    group_id: str,
    user_id: str,
    days: Optional[int],
    stats,
    nickname: str,
    source_rows: List[dict],
) -> Optional[str]:
    sections = {}
    daily = stats.daily_counts or {}
    if days is not None:
        from common.stats_render.helpers import get_date_keys

        keys = get_date_keys(days)
        daily = {k: v for k, v in daily.items() if k in keys}

    if days != 1 and daily:
        from common.stats_render import save_daily_trend_chart

        trend = save_daily_trend_chart(daily, days)
        if trend:
            sections["个人 AI 趋势"] = trend
    if source_rows:
        breakdown = save_source_breakdown_chart(source_rows, "个人来源明细")
        if breakdown:
            sections["来源明细"] = breakdown
        items = [(r.get("label", "?"), r.get("count", 0)) for r in source_rows if r.get("count", 0) > 0]
        chart = save_labeled_bar_chart(items, "个人来源分布", "次")
        if chart:
            sections["来源分布"] = chart
    if not sections:
        return None
    info = RenderInfo(
        title="个人 AI 统计报告",
        period_label=period_label(days),
        group_label=f"用户：{nickname}（{user_id}）",
    )
    return await save_stats_report_file(info, sections, prefix="ai_personal")
