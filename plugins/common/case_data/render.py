"""开箱卡片渲染（Pillow），两个插件共用。

只保证：稀有度色条 + 物品图 + 磨损/float + 现价 + 分组标题，
图片下载失败时自动退化为色块，不阻塞开箱。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

_log = logging.getLogger(__name__)

WIDTH = 980
PADDING = 26
HEADER_H = 104
HEADER_IMG_H = 122
ROW_H = 92
SECTION_H = 46
FOOTER_H = 74
BG = (18, 24, 36)
PANEL = (28, 37, 52)
TEXT = (232, 238, 248)
MUTED = (150, 163, 184)
ACCENT = (232, 180, 88)

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)


def _font(size: int) -> ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _hex(value: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    try:
        value = value.lstrip("#")
        return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except Exception:  # noqa: BLE001 - 颜色异常就用默认
        return fallback


def _wrap(
    draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int = 2
) -> list[str]:
    """按宽度折行，超出部分用省略号收尾。"""
    text = str(text or "")
    lines: list[str] = []
    while text and len(lines) < max_lines:
        if draw.textlength(text, font=font) <= max_width:
            lines.append(text)
            return lines
        cut = len(text)
        while cut > 1 and draw.textlength(text[:cut] + "…", font=font) > max_width:
            cut -= 1
        space = text.rfind(" ", 0, cut)
        if space > max(4, cut // 2):
            cut = space
        while cut > 1 and text[cut - 1] == " ":
            cut -= 1
        lines.append(text[:cut] + ("…" if len(lines) == max_lines - 1 or cut == len(text) else ""))
        text = text[cut:]
    return lines


def _fit(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    text = str(text or "")
    if draw.textlength(text, font=font) <= max_width:
        return text
    while len(text) > 1 and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text + "…"


def _thumbnail(path: Path | None, size: tuple[int, int]) -> Image.Image:
    if path and Path(path).exists():
        try:
            image = Image.open(path).convert("RGBA")
            ratio = min(size[0] / image.width, size[1] / image.height)
            image = image.resize(
                (max(1, int(image.width * ratio)), max(1, int(image.height * ratio))),
                Image.Resampling.LANCZOS,
            )
            canvas = Image.new("RGBA", size, (0, 0, 0, 0))
            canvas.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2), image)
            return canvas
        except Exception as exc:  # noqa: BLE001 - 坏图退化为占位
            _log.debug("thumbnail failed for %s: %s", path, exc)
    return Image.new("RGBA", size, (44, 54, 72, 255))


def render_panel(
    path: Path,
    *,
    title: str,
    subtitle: str = "",
    rows: Sequence[dict[str, Any]],
    footer: str = "",
    empty_text: str = "暂无内容",
    header_image: Path | None = None,
) -> Path:
    """渲染列表卡。

    ``rows`` 支持两种元素：
    - 普通行：``title`` / ``meta`` / ``right`` / ``color`` / ``image``
    - 分组行：``{"section": "标题"}``
    """
    rows = list(rows)
    header_h = HEADER_IMG_H if header_image else HEADER_H
    body_h = sum(SECTION_H if row.get("section") else ROW_H for row in rows) or ROW_H
    height = header_h + body_h + FOOTER_H
    image = Image.new("RGB", (WIDTH, height), BG)
    draw = ImageDraw.Draw(image)

    title_font = _font(33)
    sub_font = _font(18)
    row_font = _font(25)
    meta_font = _font(17)
    right_font = _font(23)
    section_font = _font(19)
    footer_font = _font(18)

    title_width = WIDTH - PADDING * 2 - (150 if header_image else 0)
    draw.text(
        (PADDING, 16),
        _fit(draw, title, title_font, title_width),
        font=title_font,
        fill=TEXT,
    )
    if subtitle:
        draw.text(
            (PADDING, 62),
            _fit(draw, subtitle, sub_font, title_width),
            font=sub_font,
            fill=MUTED,
        )
    if header_image:
        thumb = _thumbnail(header_image, (128, 96))
        image.paste(thumb, (WIDTH - PADDING - 128, 14), thumb)
    draw.line(
        [(PADDING, header_h - 10), (WIDTH - PADDING, header_h - 10)],
        fill=(56, 68, 90),
        width=2,
    )

    if not rows:
        draw.text((PADDING, header_h + 26), empty_text, font=row_font, fill=MUTED)

    top = header_h
    for row in rows:
        if row.get("section"):
            draw.text((PADDING + 4, top + 12), str(row["section"]), font=section_font, fill=ACCENT)
            draw.line(
                [(PADDING + 4, top + 40), (WIDTH - PADDING, top + 40)],
                fill=(56, 68, 90),
                width=1,
            )
            top += SECTION_H
            continue

        color = _hex(str(row.get("color") or ""), (120, 132, 156))
        draw.rounded_rectangle(
            [(PADDING, top + 6), (WIDTH - PADDING, top + ROW_H - 6)],
            radius=12,
            fill=PANEL,
        )
        draw.rounded_rectangle(
            [(PADDING, top + 6), (PADDING + 7, top + ROW_H - 6)], radius=4, fill=color
        )
        compact = bool(row.get("compact"))
        if compact:
            text_left = PADDING + 26
        else:
            thumb = _thumbnail(row.get("image"), (96, 68))
            image.paste(thumb, (PADDING + 20, top + 16), thumb)
            text_left = PADDING + 134
        draw.text(
            (text_left, top + 14),
            _fit(draw, row.get("title") or "", row_font, WIDTH - text_left - 230),
            font=row_font,
            fill=TEXT,
        )
        draw.text(
            (text_left, top + 50),
            _fit(draw, row.get("meta") or "", meta_font, WIDTH - text_left - 230),
            font=meta_font,
            fill=MUTED,
        )
        right = str(row.get("right") or "")
        if right:
            width = draw.textlength(right, font=right_font)
            draw.text((WIDTH - PADDING - 22 - width, top + 30), right, font=right_font, fill=ACCENT)
        top += ROW_H

    if footer:
        draw.line(
            [(PADDING, height - FOOTER_H + 8), (WIDTH - PADDING, height - FOOTER_H + 8)],
            fill=(56, 68, 90),
            width=1,
        )
        draw.text(
            (PADDING, height - FOOTER_H + 24),
            _fit(draw, footer, footer_font, WIDTH - PADDING * 2),
            font=footer_font,
            fill=MUTED,
        )

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")
    return path


GRID_GAP = 16
CELL_H = 292


def render_grid(
    path: Path,
    *,
    title: str,
    subtitle: str = "",
    items: Sequence[dict[str, Any]],
    footer: str = "",
    cols: int = 3,
    empty_text: str = "暂无物品",
) -> Path:
    """卡片网格：一行 ``cols`` 个，每个卡片带图、编号、名称与稀有度。"""
    items = list(items)
    inner = WIDTH - PADDING * 2
    cell_w = (inner - GRID_GAP * (cols - 1)) // cols
    rows = max(1, (len(items) + cols - 1) // cols)
    header_h = HEADER_H
    height = header_h + rows * CELL_H + (rows - 1) * GRID_GAP + FOOTER_H
    image = Image.new("RGB", (WIDTH, height), BG)
    draw = ImageDraw.Draw(image)

    title_font = _font(33)
    sub_font = _font(18)
    name_font = _font(20)
    meta_font = _font(15)
    badge_font = _font(15)
    footer_font = _font(18)

    draw.text((PADDING, 16), _fit(draw, title, title_font, inner), font=title_font, fill=TEXT)
    if subtitle:
        draw.text((PADDING, 62), _fit(draw, subtitle, sub_font, inner), font=sub_font, fill=MUTED)
    draw.line([(PADDING, header_h - 10), (WIDTH - PADDING, header_h - 10)], fill=(56, 68, 90), width=2)

    if not items:
        draw.text((PADDING, header_h + 26), empty_text, font=name_font, fill=MUTED)

    for index, item in enumerate(items):
        row, col = divmod(index, cols)
        left = PADDING + col * (cell_w + GRID_GAP)
        top = header_h + row * (CELL_H + GRID_GAP)
        color = _hex(str(item.get("color") or ""), (120, 132, 156))
        draw.rounded_rectangle(
            [(left, top), (left + cell_w, top + CELL_H)],
            radius=14,
            fill=PANEL,
            outline=(44, 55, 74),
            width=1,
        )
        draw.rounded_rectangle(
            [(left, top), (left + cell_w, top + 6)], radius=3, fill=color
        )

        img_w, img_h = cell_w - 28, 142
        thumb = _thumbnail(item.get("image"), (img_w, img_h))
        image.paste(thumb, (left + (cell_w - img_w) // 2, top + 18), thumb)

        badge = str(item.get("badge") or "")
        if badge:
            draw.text((left + 12, top + 12), badge, font=badge_font, fill=color)

        name_lines = _wrap(draw, item.get("title") or "", name_font, cell_w - 24, max_lines=2)
        for line_index, line in enumerate(name_lines):
            draw.text((left + 12, top + 170 + line_index * 25), line, font=name_font, fill=TEXT)
        meta = _fit(draw, item.get("meta") or "", meta_font, cell_w - 24)
        draw.text((left + 12, top + 224), meta, font=meta_font, fill=MUTED)
        extra = _fit(draw, item.get("right") or "", meta_font, cell_w - 24)
        if extra:
            draw.text((left + 12, top + 250), extra, font=meta_font, fill=ACCENT)

    if footer:
        draw.line(
            [(PADDING, height - FOOTER_H + 8), (WIDTH - PADDING, height - FOOTER_H + 8)],
            fill=(56, 68, 90),
            width=1,
        )
        draw.text(
            (PADDING, height - FOOTER_H + 24),
            _fit(draw, footer, footer_font, inner),
            font=footer_font,
            fill=MUTED,
        )

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")
    return path
