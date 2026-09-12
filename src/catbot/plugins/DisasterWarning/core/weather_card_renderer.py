"""
气象预警 PIL 卡片图（无需 Playwright）。
依赖 Pillow；未安装时返回 None，由推送层回退为纯文本。
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any

from ncatbot.utils import get_log

from ..models.models import WeatherAlarmData
from ..utils.formatters.weather import (
    SORTED_WEATHER_TYPES,
    WEATHER_EMOJI_MAP,
    WeatherFormatter,
)

_log = get_log()

_LEVEL_ACCENT: dict[str, tuple[int, int, int]] = {
    "红色": (220, 55, 55),
    "橙色": (230, 125, 45),
    "黄色": (210, 175, 55),
    "蓝色": (55, 130, 220),
    "白色": (180, 190, 205),
}


def _accent_from_headline(headline: str) -> tuple[int, int, int]:
    for level in ("红色", "橙色", "黄色", "蓝色", "白色"):
        if level in headline:
            return _LEVEL_ACCENT[level]
    return (70, 130, 210)


def _weather_emoji(headline: str) -> str:
    for name in SORTED_WEATHER_TYPES:
        if name in headline:
            return WEATHER_EMOJI_MAP.get(name, "⛈️")
    return "⛈️"


def _font_candidates(cfg: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    raw = cfg.get("font_paths") or []
    if isinstance(raw, str) and raw.strip():
        paths.append(raw.strip())
    elif isinstance(raw, list):
        paths.extend(str(p) for p in raw if p and str(p).strip())
    windir = os.environ.get("WINDIR", r"C:\Windows")
    paths.extend(
        [
            os.path.join(windir, "Fonts", "msyh.ttc"),
            os.path.join(windir, "Fonts", "msyhl.ttc"),
            os.path.join(windir, "Fonts", "simhei.ttf"),
            os.path.join(windir, "Fonts", "msjhbd.ttc"),
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        ]
    )
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _load_font(path: str, size: int):
    from PIL import ImageFont

    try:
        return ImageFont.truetype(path, size=size)
    except OSError:
        return None


def _resolve_fonts(
    cfg: dict[str, Any], sizes: tuple[int, int, int]
) -> tuple[Any, Any, Any]:
    from PIL import ImageFont

    title_s, body_s, label_s = sizes
    for path in _font_candidates(cfg):
        if not os.path.isfile(path):
            continue
        title_f = _load_font(path, title_s)
        body_f = _load_font(path, body_s)
        label_f = _load_font(path, label_s)
        if title_f and body_f and label_f:
            return title_f, body_f, label_f
    _log.warning(
        "[灾害预警] 未找到可用的中文字体文件，气象卡片可能显示为方块。"
        "可在 weather_config.font_paths 中指定字体路径。"
    )
    return (
        ImageFont.load_default(),
        ImageFont.load_default(),
        ImageFont.load_default(),
    )


def _text_width(draw, text: str, font) -> int:
    if not text:
        return 0
    bbox = draw.textbbox((0, 0), text, font=font)
    return int(bbox[2] - bbox[0])


def _wrap_by_char(draw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.replace("\r\n", "\n").split("\n"):
        if not paragraph:
            lines.append("")
            continue
        line = ""
        for ch in paragraph:
            test = line + ch
            if _text_width(draw, test, font) <= max_width:
                line = test
            else:
                if line:
                    lines.append(line)
                line = ch
        if line:
            lines.append(line)
    return lines


def _line_step(draw, sample: str, font) -> int:
    bbox = draw.textbbox((0, 0), sample or "国", font=font)
    return int(bbox[3] - bbox[1]) + 6


def render_weather_card_png(
    weather: WeatherAlarmData,
    out_dir: Path,
    weather_config: dict[str, Any] | None = None,
) -> Path | None:
    """
    生成气象预警 PNG，成功返回文件路径；失败（无 Pillow、无字体等）返回 None。
    """
    try:
        from PIL import Image as PILImage
        from PIL import ImageDraw
    except ImportError:
        _log.warning("[灾害预警] 未安装 Pillow，跳过气象卡片: pip install pillow")
        return None

    cfg = weather_config or {}
    width = max(480, min(1200, int(cfg.get("card_width", 880))))
    padding = max(12, min(48, int(cfg.get("card_padding", 28))))
    title_size = max(16, min(40, int(cfg.get("font_title_size", 26))))
    body_size = max(12, min(32, int(cfg.get("font_body_size", 20))))
    label_size = max(11, min(22, int(cfg.get("font_label_size", 17))))
    max_desc = int(cfg.get("max_description_length", 384))
    max_body_lines = max(4, min(40, int(cfg.get("card_max_body_lines", 16))))

    headline = (weather.headline or weather.title or "气象预警").strip()
    desc = (weather.description or "").strip()
    if max_desc > 0 and len(desc) > max_desc:
        desc = desc[: max_desc - 3] + "..."

    title_font, body_font, label_font = _resolve_fonts(
        cfg, (title_size, body_size, label_size)
    )

    accent = _accent_from_headline(headline)

    inner_w = width - padding * 2
    # 预估高度：动态累加
    probe = PILImage.new("RGB", (width, 120), (26, 29, 36))
    probe_draw = ImageDraw.Draw(probe)

    headline_lines = _wrap_by_char(probe_draw, headline, title_font, inner_w)[:3]
    body_lines = _wrap_by_char(probe_draw, desc, body_font, inner_w) if desc else []
    if len(body_lines) > max_body_lines:
        body_lines = body_lines[:max_body_lines]
        if body_lines:
            body_lines[-1] = body_lines[-1][: max(0, len(body_lines[-1]) - 1)] + "…"

    time_line = ""
    if weather.issue_time:
        time_line = f"生效时间 · {WeatherFormatter.format_time(weather.issue_time)}"

    bar_h = 6
    label_h = _line_step(probe_draw, "气象", label_font)
    title_h = sum(
        _line_step(probe_draw, ln or " ", title_font) for ln in headline_lines
    )
    body_h = (
        sum(_line_step(probe_draw, ln or " ", body_font) for ln in body_lines)
        if body_lines
        else 0
    )
    footer_h = (
        _line_step(probe_draw, time_line or " ", label_font)
        if time_line
        else label_h // 2
    )

    height = (
        bar_h
        + padding
        + label_h
        + 8
        + title_h
        + 16
        + (body_h + 16 if body_lines else 8)
        + footer_h
        + padding
    )
    height = max(220, min(2600, height))

    img = PILImage.new("RGB", (width, height), color=(26, 29, 36))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, width, bar_h), fill=accent)

    y = bar_h + padding
    draw.text((padding, y), "气象预警", font=label_font, fill=(175, 182, 195))
    y += label_h + 8

    for ln in headline_lines:
        draw.text((padding, y), ln, font=title_font, fill=(248, 249, 252))
        y += _line_step(probe_draw, ln or " ", title_font)

    y += 8
    draw.line((padding, y, width - padding, y), fill=(55, 60, 72), width=1)
    y += 14

    for ln in body_lines:
        draw.text((padding, y), ln, font=body_font, fill=(205, 210, 220))
        y += _line_step(probe_draw, ln or " ", body_font)

    if time_line:
        y += 10
        draw.text((padding, y), time_line, font=label_font, fill=(130, 138, 155))

    out_dir.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^\w\-.]+", "_", weather.id)[:72] or "weather"
    out_path = out_dir / f"weather_card_{safe_id}_{uuid.uuid4().hex[:10]}.png"
    try:
        img.save(out_path, format="PNG", optimize=True)
    except OSError as e:
        _log.warning(f"[灾害预警] 气象卡片保存失败: {e}")
        return None
    if out_path.is_file() and out_path.stat().st_size > 0:
        return out_path
    return None
