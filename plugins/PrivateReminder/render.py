from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

WIDTH = 1240
MARGIN = 52
HEADER_HEIGHT = 150
BG = (244, 248, 249, 255)
HEADER = (33, 68, 82, 255)
CARD = (255, 255, 255, 255)
LINE = (222, 231, 233, 255)
TEXT = (40, 61, 68, 255)
BODY = (70, 83, 88, 255)
MUTED = (92, 112, 117, 255)
ACCENT = (38, 144, 164, 255)
GOOD = (46, 140, 110, 255)
BAD = (190, 76, 76, 255)
ROW = (247, 250, 251, 255)

_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)
_BOLD_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyhbd.ttc",
    *_FONT_CANDIDATES,
)


def _rgba(color, alpha: int = 255) -> tuple[int, int, int, int]:
    if len(color) == 4:
        return color
    r, g, b = color[:3]
    return r, g, b, alpha


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = _BOLD_FONT_CANDIDATES if bold else _FONT_CANDIDATES
    for path in candidates:
        if Path(path).is_file():
            try:
                engine = getattr(ImageFont, "Layout", None)
                if engine is not None:
                    return ImageFont.truetype(path, size, layout_engine=engine.BASIC)
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    result: list[str] = []
    current = ""
    closing = "。，、；：！？）》」』】…%,.!?;:)]}"
    opening = "（《「『【([{"
    for char in str(text):
        if char == "\n":
            result.append(current)
            current = ""
            continue
        candidate = current + char
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            if char in closing and len(current) > 1:
                result.append(current[:-1])
                current = current[-1] + char
                continue
            if current[-1] in opening:
                result.append(current[:-1])
                current = current[-1] + char
                continue
            result.append(current)
            current = char
        else:
            current = candidate
    if current:
        result.append(current)
    return result or [""]


def _card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], *, fill=CARD, radius: int = 16) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=LINE, width=2)


def _draw_header(draw: ImageDraw.ImageDraw, title: str, subtitle: str, accent=ACCENT) -> None:
    draw.rounded_rectangle((0, 0, WIDTH, HEADER_HEIGHT), radius=24, fill=HEADER)
    draw.rectangle((0, HEADER_HEIGHT - 18, WIDTH, HEADER_HEIGHT), fill=HEADER)
    draw.text((MARGIN, 28), title, font=_font(50, bold=True), fill=(250, 252, 252, 255))
    draw.text((MARGIN, 96), subtitle, font=_font(22), fill=(190, 222, 225, 255))
    draw.rounded_rectangle((MARGIN, HEADER_HEIGHT - 6, MARGIN + 88, HEADER_HEIGHT - 2), radius=2, fill=_rgba(accent))


def _draw_footer(draw: ImageDraw.ImageDraw, y: int, text: str) -> None:
    draw.line((MARGIN, y, WIDTH - MARGIN, y), fill=(184, 202, 205, 255), width=2)
    draw.text((MARGIN, y + 14), text, font=_font(18), fill=MUTED)


def render_notice(path: Path, title: str, lines: list[str], *, accent=ACCENT) -> None:
    draw_probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    body_font = _font(24)
    wrapped: list[str] = []
    for line in lines or [""]:
        wrapped.extend(_wrap(draw_probe, line, body_font, WIDTH - MARGIN * 2 - 64))
    line_height = 36
    height = HEADER_HEIGHT + 24 + 28 + max(1, len(wrapped)) * line_height + 26 + 54
    image = Image.new("RGBA", (WIDTH, height), BG)
    draw = ImageDraw.Draw(image)
    _draw_header(draw, title, "PrivateReminder · 预约提醒", accent)
    _card(draw, (MARGIN, HEADER_HEIGHT + 24, WIDTH - MARGIN, height - 54))
    draw.rounded_rectangle(
        (MARGIN, HEADER_HEIGHT + 24, MARGIN + 10, height - 54), radius=4, fill=_rgba(accent)
    )
    y = HEADER_HEIGHT + 50
    for line in wrapped:
        draw.text((MARGIN + 34, y), line, font=body_font, fill=BODY)
        y += line_height
    _draw_footer(draw, height - 44, "发送“预约帮助”查看支持的日期与时间写法")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")


def render_help(path: Path) -> None:
    sections = [
        (
            "创建预约",
            [
                "预约 21:30 收鱼",
                "预约 明天9点 起床",
                "预约 11月5日 9点30分 收鱼",
                "预约 11月5号9点03",
                "预约 2026年11月5日9点30分",
                "预约 30分钟 喝水 / 半小时 休息",
                "提醒内容可省略，默认发送“时间到了”。",
            ],
            ACCENT,
        ),
        (
            "查看与取消",
            [
                "预约列表 / 我的预约：查看自己的全部预约",
                "取消预约 3：取消指定编号",
                "取消预约 全部：清空自己的预约",
            ],
            (176, 119, 64),
        ),
        (
            "规则",
            [
                "时间按北京时间计算；日期、时间、数字与单位之间的空格都可以省略。",
                "支持：今天/明天/后天、11月5日/11月5号、2026年11月5日、9点/9点30分/9:00。",
                "群聊中创建的预约也会私聊提醒；到时发送图片提醒卡。",
                "最多同时保留 20 条，时间范围最多 365 天；私聊失败会自动重试 3 次。",
            ],
            (112, 96, 164),
        ),
    ]
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    body_font = _font(23)
    prepared = []
    total_body = 0
    for title, lines, accent in sections:
        wrapped = []
        for line in lines:
            wrapped.extend(_wrap(probe, line, body_font, WIDTH - MARGIN * 2 - 86))
        section_height = 72 + sum(28 for _ in wrapped) + 18
        prepared.append((title, wrapped, accent, section_height))
        total_body += section_height + 18
    height = HEADER_HEIGHT + 24 + total_body + 48
    image = Image.new("RGBA", (WIDTH, height), BG)
    draw = ImageDraw.Draw(image)
    _draw_header(draw, "预约提醒", "时间预约 · 私聊提醒 · 命令说明")
    y = HEADER_HEIGHT + 24
    for title, wrapped, accent, section_height in prepared:
        _card(draw, (MARGIN, y, WIDTH - MARGIN, y + section_height))
        draw.rounded_rectangle((MARGIN, y, MARGIN + 10, y + section_height), radius=4, fill=_rgba(accent))
        draw.text((MARGIN + 30, y + 20), title, font=_font(27, bold=True), fill=TEXT)
        draw.line((MARGIN + 30, y + 60, WIDTH - MARGIN - 28, y + 60), fill=_rgba(accent, 100), width=2)
        line_y = y + 76
        for line in wrapped:
            draw.text((MARGIN + 30, line_y), line, font=body_font, fill=BODY)
            line_y += 28
        y += section_height + 18
    _draw_footer(draw, height - 44, "发送“我的预约”查看预约列表")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")


def render_list(path: Path, items: list[dict[str, Any]], max_pending: int) -> None:
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    content_font = _font(20)
    time_font = _font(19)
    status_font = _font(19)
    content_width = WIDTH - MARGIN * 2 - 620
    prepared: list[tuple[list[str], list[str], list[str], int]] = []
    for item in items:
        content_lines = _wrap(probe, str(item.get("content", "")), content_font, content_width)
        status_lines = [str(item.get("status_text", "待发送"))]
        detail = str(item.get("status_detail", ""))
        if detail:
            status_lines.append(detail)
        row_height = max(len(content_lines), len(status_lines)) * 26 + 18
        prepared.append((content_lines, status_lines, item, row_height))
    table_height = 20 + 40 + sum(row[3] for row in prepared) + 18
    height = HEADER_HEIGHT + 24 + 150 + 18 + table_height + 54
    image = Image.new("RGBA", (WIDTH, height), BG)
    draw = ImageDraw.Draw(image)
    _draw_header(draw, "我的预约", f"共 {len(items)} 条 · 到点后私聊提醒", (176, 119, 64))

    summary_y = HEADER_HEIGHT + 24
    _card(draw, (MARGIN, summary_y, WIDTH - MARGIN, summary_y + 150))
    draw.text((MARGIN + 30, summary_y + 22), "预约列表", font=_font(28, bold=True), fill=TEXT)
    draw.text(
        (MARGIN + 30, summary_y + 66),
        f"最多保留 {max_pending} 条；发送“取消预约 编号”可取消单条。",
        font=_font(20),
        fill=MUTED,
    )
    draw.text(
        (MARGIN + 30, summary_y + 104),
        "时间按北京时间显示，群聊中创建的预约也会私聊提醒。",
        font=_font(20),
        fill=MUTED,
    )

    table_y = summary_y + 168
    _card(draw, (MARGIN, table_y, WIDTH - MARGIN, table_y + table_height))
    header_y = table_y + 12
    draw.rounded_rectangle(
        (MARGIN + 18, header_y, WIDTH - MARGIN - 18, header_y + 40),
        radius=8,
        fill=(232, 241, 242, 255),
    )
    headers = (("编号", 26), ("预约时间", 112), ("状态", 340), ("提醒内容", 606))
    for label, offset in headers:
        draw.text((MARGIN + offset, header_y + 9), label, font=_font(19, bold=True), fill=TEXT)

    row_y = header_y + 40
    for index, (content_lines, status_lines, item, row_height) in enumerate(prepared):
        if index % 2 == 0:
            draw.rounded_rectangle(
                (MARGIN + 18, row_y + 2, WIDTH - MARGIN - 18, row_y + row_height - 2),
                radius=6,
                fill=ROW,
            )
        draw.text((MARGIN + 26, row_y + 9), f"#{item.get('id', '?')}", font=_font(19, bold=True), fill=ACCENT)
        draw.text((MARGIN + 112, row_y + 9), str(item.get("due_text", "")), font=time_font, fill=BODY)
        color = BAD if item.get("status_kind") == "failed" else GOOD
        status_y = row_y + 9
        for line_index, line in enumerate(status_lines):
            draw.text(
                (MARGIN + 340, status_y),
                line,
                font=status_font,
                fill=color if line_index == 0 else MUTED,
            )
            status_y += 26
        content_y = row_y + 9
        for line in content_lines:
            draw.text((MARGIN + 606, content_y), line, font=content_font, fill=BODY)
            content_y += 26
        row_y += row_height

    if not prepared:
        draw.text(
            (MARGIN + 30, table_y + 62),
            "你还没有预约。发送“预约 明天9点 收鱼”创建第一条。",
            font=_font(23),
            fill=MUTED,
        )
    _draw_footer(draw, height - 44, "发送“取消预约 编号”取消；“预约帮助”查看全部写法")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")


def render_reminder(path: Path, content: str, due_text: str, *, late: bool = False) -> None:
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    content_font = _font(32, bold=True)
    lines = _wrap(probe, content, content_font, WIDTH - MARGIN * 2 - 80)
    line_height = 48
    height = HEADER_HEIGHT + 24 + 70 + max(1, len(lines)) * line_height + 82 + 54
    image = Image.new("RGBA", (WIDTH, height), BG)
    draw = ImageDraw.Draw(image)
    _draw_header(draw, "预约提醒", "补发 · 已到预约时间" if late else "到点了", BAD if late else GOOD)
    _card(draw, (MARGIN, HEADER_HEIGHT + 24, WIDTH - MARGIN, height - 54))
    draw.rounded_rectangle(
        (MARGIN, HEADER_HEIGHT + 24, MARGIN + 10, height - 54),
        radius=4,
        fill=_rgba(BAD if late else GOOD),
    )
    draw.text((MARGIN + 34, HEADER_HEIGHT + 52), "提醒内容", font=_font(20), fill=MUTED)
    y = HEADER_HEIGHT + 92
    for line in lines:
        draw.text((MARGIN + 34, y), line, font=content_font, fill=TEXT)
        y += line_height
    draw.text((MARGIN + 34, y + 20), f"预约时间：{due_text}", font=_font(20), fill=MUTED)
    _draw_footer(draw, height - 44, "PrivateReminder · 私聊提醒")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")
