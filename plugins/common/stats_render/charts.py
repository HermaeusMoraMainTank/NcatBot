import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
from PIL import Image, ImageDraw

from .crayon_utils import draw_crayon_rectangle
from .fonts import load_font
from .paths import TEMP_PATH, ensure_dirs


def _truncate_label(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if not text:
        return ""
    bbox = draw.textbbox((0, 0), text, font=font)
    if bbox[2] - bbox[0] <= max_width:
        return text
    for end in range(len(text), 0, -1):
        candidate = text[:end] + "…"
        bb = draw.textbbox((0, 0), candidate, font=font)
        if bb[2] - bb[0] <= max_width:
            return candidate
    return "…"


def _draw_text_on_bg(
    draw: ImageDraw.ImageDraw,
    xy: tuple,
    text: str,
    font,
    *,
    fill=(60, 60, 60, 255),
    bg=(255, 255, 255, 240),
    padding: int = 2,
):
    x, y = xy
    bbox = draw.textbbox((x, y), text, font=font)
    draw.rectangle(
        [bbox[0] - padding, bbox[1] - padding, bbox[2] + padding, bbox[3] + padding],
        fill=bg,
    )
    draw.text((x, y), text, font=font, fill=fill)


CRAYON_COLORS = [
    (255, 138, 128),
    (255, 179, 128),
    (255, 218, 128),
    (189, 252, 201),
    (135, 206, 235),
    (173, 216, 230),
    (221, 160, 221),
    (240, 230, 140),
    (144, 238, 144),
    (176, 196, 222),
]


def _hour_color(count: int, max_count: int) -> tuple:
    if count == 0:
        return (230, 230, 230)
    ratio = count / max_count if max_count else 0
    if ratio >= 0.8:
        return (30, 80, 200)
    if ratio >= 0.6:
        return (50, 110, 230)
    if ratio >= 0.4:
        return (100, 150, 255)
    if ratio >= 0.2:
        return (160, 190, 255)
    return (200, 220, 255)


def save_hourly_chart(hourly_data: Dict[int, int], title: str = "小时活跃度") -> Path:
    """24 小时蜡笔色块图。hourly_data: hour(0-23) -> count"""
    ensure_dirs()
    width, height = 960, 120
    padding_x, padding_y, bar_height = 80, 30, 48
    img = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    font = load_font(12)

    data = [(h, hourly_data.get(h, 0)) for h in range(24)]
    sum(c for _, c in data) or 1
    max_count = max((c for _, c in data), default=1) or 1

    bar_y = padding_y
    total_bar_width = width - 2 * padding_x
    block_width = total_bar_width / 24
    current_x = padding_x
    block_positions = []

    for hour, count in data:
        draw_crayon_rectangle(
            draw,
            current_x,
            bar_y,
            block_width,
            bar_height,
            _hour_color(count, max_count),
            orientation="vertical",
        )
        block_positions.append((current_x + block_width / 2, hour))
        current_x += block_width

    label_y = bar_y + bar_height + 8
    for i in range(0, 24, 2):
        if i < len(block_positions):
            x_pos, hour = block_positions[i]
            hour_text = f"{hour:02d}"
            bbox = draw.textbbox((0, 0), hour_text, font=font)
            tw = bbox[2] - bbox[0]
            lx = max(5, min(x_pos - tw // 2, width - tw - 5))
            draw.text((lx, label_y), hour_text, font=font, fill=(100, 100, 100, 255))

    out = TEMP_PATH / f"hourly_{uuid.uuid4().hex}.png"
    img.save(out, "PNG")
    return out


def save_daily_trend_chart(
    daily_counts: Dict[str, int],
    days: Optional[int],
    title: str = "发言趋势",
) -> Optional[Path]:
    """按日/周/月生成蜡笔柱状趋势图。"""
    if not daily_counts:
        return None
    ensure_dirs()
    end_date = date.today()
    labels: List[str] = []
    counts: List[int] = []

    if days == 1:
        end_date.isoformat()
        hourly = (
            daily_counts
            if all(k.isdigit() or len(k) <= 2 for k in daily_counts)
            else {}
        )
        if hourly:
            labels = [f"{h:02d}" for h in range(24)]
            counts = [hourly.get(str(h), hourly.get(h, 0)) for h in range(24)]
        else:
            return None
    elif days == 7:
        start = end_date - timedelta(days=end_date.weekday())
        week_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        for i in range(7):
            d = (start + timedelta(days=i)).isoformat()
            labels.append(week_names[i])
            counts.append(daily_counts.get(d, 0))
    elif days == 30:
        start = date(end_date.year, end_date.month, 1)
        d = start
        while d <= end_date:
            labels.append(d.strftime("%d"))
            counts.append(daily_counts.get(d.isoformat(), 0))
            d += timedelta(days=1)
    elif days is None:
        monthly: Dict[str, int] = {}
        for ds, c in daily_counts.items():
            try:
                m = date.fromisoformat(ds).strftime("%Y-%m")
            except ValueError:
                continue
            monthly[m] = monthly.get(m, 0) + c
        labels = sorted(monthly.keys())
        counts = [monthly[m] for m in labels]
    else:
        start = end_date - timedelta(days=days - 1)
        d = start
        while d <= end_date:
            labels.append(d.strftime("%m-%d"))
            counts.append(daily_counts.get(d.isoformat(), 0))
            d += timedelta(days=1)

    if not counts or max(counts) == 0:
        return None

    n = len(counts)
    width = 960
    height = 232 if days in (7, 30) else 260
    padding_x, padding_y = 48, 42
    img = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    font = load_font(10 if days == 30 and n > 20 else 11)
    max_c = max(counts) or 1
    chart_h = height - padding_y - 48
    slot_w = (width - 2 * padding_x) / n
    bar_w = max(4, slot_w - 4)

    for i, (label, count) in enumerate(zip(labels, counts)):
        bar_h = (count / max_c) * chart_h if max_c else 0
        x = padding_x + i * slot_w + (slot_w - bar_w) / 2
        y = padding_y + chart_h - bar_h
        color = CRAYON_COLORS[i % len(CRAYON_COLORS)]
        if bar_h > 1:
            draw_crayon_rectangle(draw, x, y, bar_w, bar_h, color, "vertical")
        if days == 30 and n > 20 and i % 2 == 1:
            continue
        bbox = draw.textbbox((0, 0), label, font=font)
        tw = bbox[2] - bbox[0]
        draw.text(
            (x + bar_w / 2 - tw / 2, padding_y + chart_h + 6),
            label,
            font=font,
            fill=(100, 100, 100, 255),
        )

    out = TEMP_PATH / f"trend_{uuid.uuid4().hex}.png"
    img.save(out, "PNG")
    return out


def save_hourly_from_daily_buckets(
    daily_hourly: Dict[str, Dict[str, int]],
    date_keys: set[str],
) -> Path:
    """合并多日的 hourly 桶为 24 小时分布。"""
    merged: Dict[int, int] = {h: 0 for h in range(24)}
    for d in date_keys:
        bucket = daily_hourly.get(d, {})
        for h_str, c in bucket.items():
            try:
                merged[int(h_str)] += c
            except ValueError:
                pass
    return save_hourly_chart(merged)


def save_pos_chart(pos_counter: Dict[str, int], top_n: int = 3) -> Optional[Path]:
    top = sorted(pos_counter.items(), key=lambda x: x[1], reverse=True)[:top_n]
    if not top:
        return None
    ensure_dirs()
    width, height = 960, max(180, 60 + len(top) * 55)
    label_col_w = 88
    padding_x, padding_y, bar_spacing = label_col_w + 24, 40, 15
    img = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    font = load_font(14)
    font_large = load_font(16)
    max_count = max(c for _, c in top) or 1
    chart_height = height - 2 * padding_y
    bar_height = (chart_height - (len(top) - 1) * bar_spacing) / len(top)
    max_bar_width = width - padding_x - 80
    y = padding_y
    rows = []
    for idx, (pos_name, count) in enumerate(top):
        bar_width = (count / max_count) * max_bar_width
        color = CRAYON_COLORS[idx % len(CRAYON_COLORS)]
        if bar_width > 1:
            draw_crayon_rectangle(
                draw, padding_x, y, bar_width, bar_height, color, "horizontal"
            )
        rows.append((pos_name, count, y, bar_width))
        y += bar_height + bar_spacing
    draw = ImageDraw.Draw(img)
    for pos_name, count, row_y, bar_width in rows:
        label = _truncate_label(draw, pos_name, font_large, label_col_w)
        label_x = padding_x - label_col_w - 8
        _draw_text_on_bg(
            draw,
            (label_x, row_y + bar_height / 2 - 8),
            label,
            font_large,
            fill=(80, 80, 80, 255),
        )
        ct = str(count)
        cx = (
            padding_x + bar_width + 8
            if bar_width <= max_bar_width * 0.6
            else padding_x + bar_width - 30
        )
        _draw_text_on_bg(
            draw, (cx, row_y + bar_height / 2 - 6), ct, font, fill=(80, 80, 80, 255)
        )
    out = TEMP_PATH / f"pos_{uuid.uuid4().hex}.png"
    img.save(out, "PNG")
    return out


def save_top_emojis_chart(
    items: List[tuple],
    title: str = "热门表情 TOP10",
    *,
    show_title: bool = False,
) -> Optional[Path]:
    """items: [(cache_path, count, label), ...]"""
    if not items:
        return None
    ensure_dirs()
    row_h = 48
    width = 920
    header_h = 36 if show_title else 10
    height = header_h + len(items) * row_h + 12
    img = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    title_font = load_font(18, bold=True)
    count_font = load_font(13)
    if show_title:
        draw.text((20, 8), title, font=title_font, fill=(60, 60, 60, 255))
    max_count = max(c for _, c, _ in items) or 1
    bar_x = 140
    bar_max_w = width - bar_x - 120
    label_max_w = bar_x - 72
    rows = []
    for i, (path, count, label) in enumerate(items):
        y = header_h + i * row_h
        bw = max(4, (count / max_count) * bar_max_w)
        draw_crayon_rectangle(
            draw,
            bar_x,
            y + 20,
            bw,
            16,
            CRAYON_COLORS[i % len(CRAYON_COLORS)],
            "horizontal",
        )
        rows.append((path, count, label, y))
    draw = ImageDraw.Draw(img)
    emoji_pastes = []
    for path, count, label, y in rows:
        emoji_pastes.append((path, 20, y + 8))
        label_text = _truncate_label(draw, str(label), count_font, label_max_w)
        _draw_text_on_bg(
            draw, (68, y + 18), label_text, count_font, fill=(80, 80, 80, 255)
        )
        _draw_text_on_bg(
            draw,
            (bar_x + bar_max_w + 8, y + 16),
            f"{count} 次",
            count_font,
            fill=(100, 100, 100, 255),
        )
    for path, ex, ey in emoji_pastes:
        try:
            em = (
                Image.open(path)
                .convert("RGBA")
                .resize((40, 40), Image.Resampling.LANCZOS)
            )
        except Exception:
            em = Image.new("RGBA", (40, 40), (220, 220, 220, 255))
        img.paste(em, (ex, ey), em)
    out = TEMP_PATH / f"emoji_top_{uuid.uuid4().hex}.png"
    img.save(out, "PNG")
    return out


def save_labeled_bar_chart(
    items: List[tuple],
    title: str = "统计",
    unit: str = "",
    *,
    show_title: bool = False,
) -> Optional[Path]:
    if not items:
        return None
    ensure_dirs()
    width = 920
    row_h = 48
    header_h = 36 if show_title else 10
    height = header_h + len(items) * row_h + 16
    img = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    font = load_font(14)
    title_font = load_font(18, bold=True)
    if show_title:
        draw.text((20, 8), title, font=title_font, fill=(60, 60, 60, 255))
    max_v = max(float(v) for _, v in items) or 1
    bar_x = 260
    bar_max_w = width - bar_x - 120
    label_max_w = bar_x - 36
    bar_rows = []
    for i, (label, value) in enumerate(items):
        y = header_h + i * row_h
        bw = max(4, (float(value) / max_v) * bar_max_w)
        draw_crayon_rectangle(
            draw,
            bar_x,
            y + 14,
            bw,
            20,
            CRAYON_COLORS[i % len(CRAYON_COLORS)],
            "horizontal",
        )
        bar_rows.append((label, value, y))
    draw = ImageDraw.Draw(img)
    for label, value, y in bar_rows:
        text = _truncate_label(draw, str(label), font, label_max_w)
        _draw_text_on_bg(draw, (20, y + 14), text, font)
        suffix = f" {unit}" if unit else ""
        _draw_text_on_bg(
            draw,
            (bar_x + bar_max_w + 8, y + 14),
            f"{value}{suffix}",
            font,
            fill=(100, 100, 100, 255),
        )
    out = TEMP_PATH / f"bars_{uuid.uuid4().hex}.png"
    img.save(out, "PNG")
    return out


SOURCE_PANEL_COLORS = {
    "active": (108, 87, 245),
    "passive": (72, 149, 239),
    "summary": (46, 184, 134),
    "impression": (255, 159, 67),
}


def _format_cost(cost: float) -> str:
    return f"¥{cost:.4f}" if cost else "¥0.0000"


def save_source_breakdown_chart(
    source_rows: List[dict],
    title: str = "来源消耗明细",
    *,
    show_title: bool = False,
) -> Optional[Path]:
    """2x2 来源面板：次数 / Token / 输入输出 / 费用。"""
    if not source_rows:
        return None
    try:
        from common.utils.AiStatsRecorder import SOURCE_ROLLUP, SOURCE_ROLLUP_ORDER
    except ImportError:
        return None

    ensure_dirs()
    width = 920
    gap = 12
    cols = 2
    panel_w = (width - 40 - gap) // cols
    panel_h = 96
    header_h = 36 if show_title else 8
    rows_n = (len(SOURCE_ROLLUP_ORDER) + cols - 1) // cols
    height = header_h + rows_n * (panel_h + gap) + 20

    img = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    title_font = load_font(18, bold=True)
    label_font = load_font(15, bold=True)
    detail_font = load_font(13)
    small_font = load_font(12)

    if show_title:
        draw.text((20, 8), title, font=title_font, fill=(60, 60, 60, 255))
    y0 = header_h

    row_map = {r.get("key"): r for r in source_rows if r.get("key")}
    for idx, key in enumerate(SOURCE_ROLLUP_ORDER):
        row = row_map.get(key)
        if not row:
            label = SOURCE_ROLLUP.get(key, (key, []))[0]
            row = {
                "key": key,
                "label": label,
                "count": 0,
                "tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost": 0.0,
            }

        col = idx % cols
        row_i = idx // cols
        px = 20 + col * (panel_w + gap)
        py = y0 + row_i * (panel_h + gap)
        color = SOURCE_PANEL_COLORS.get(key, (120, 120, 140))

        draw.rounded_rectangle(
            [px, py, px + panel_w, py + panel_h],
            radius=10,
            fill=(248, 248, 252, 255),
        )
        draw.rounded_rectangle(
            [px, py, px + 5, py + panel_h],
            radius=2,
            fill=(*color, 255),
        )
        draw.text((px + 14, py + 10), row["label"], font=label_font, fill=(*color, 255))
        draw.text(
            (px + 14, py + 34),
            f"{row.get('count', 0)} 次",
            font=detail_font,
            fill=(80, 80, 90, 255),
        )
        tokens = row.get("tokens", 0)
        prompt_t = row.get("prompt_tokens", 0)
        completion_t = row.get("completion_tokens", 0)
        draw.text(
            (px + 14, py + 54),
            f"{tokens:,} tok",
            font=detail_font,
            fill=(80, 80, 90, 255),
        )
        draw.text(
            (px + 14, py + 72),
            f"输入 {prompt_t:,} / 输出 {completion_t:,}  ·  {_format_cost(row.get('cost', 0))}",
            font=small_font,
            fill=(110, 110, 120, 255),
        )

    out = TEMP_PATH / f"source_breakdown_{uuid.uuid4().hex}.png"
    img.save(out, "PNG")
    return out


def save_wordcloud_chart(word_freq: Dict[str, int]) -> Optional[Path]:
    if not word_freq:
        return None
    try:
        from wordcloud import WordCloud
    except ImportError:
        return None
    ensure_dirs()
    font_paths = [
        "C:/Windows/Fonts/simkai.ttf",
        "C:/Windows/Fonts/msyh.ttc",
    ]
    font_path = next((p for p in font_paths if Path(p).exists()), None)
    wc = WordCloud(
        font_path=font_path,
        width=800,
        height=500,
        max_words=80,
        min_font_size=10,
        colormap="viridis",
        background_color=None,
        mode="RGBA",
    )
    wc.generate_from_frequencies(dict(word_freq))
    wordcloud_image = wc.to_image().convert("RGBA")
    data = np.array(wordcloud_image)
    r, g, b = data[:, :, 0], data[:, :, 1], data[:, :, 2]
    mask = (r < 12) & (g < 12) & (b < 12)
    data[mask, 3] = 0
    wordcloud_image = Image.fromarray(data, "RGBA")
    out = TEMP_PATH / f"wordcloud_{uuid.uuid4().hex}.png"
    wordcloud_image.save(out, "PNG")
    return out
