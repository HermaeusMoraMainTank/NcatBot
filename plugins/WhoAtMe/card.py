"""QQ 手机端群聊风格卡片渲染。"""

from __future__ import annotations

import platform
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

# 正文/昵称用常规雅黑，徽章用 Bold
if platform.system() == "Windows":
    _FONT_CANDIDATES = (
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\msyhl.ttc"),
        Path(r"C:\Windows\Fonts\segoeui.ttf"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
    )
    _FONT_BOLD_CANDIDATES = (
        Path(r"C:\Windows\Fonts\msyhbd.ttc"),
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\segoeuib.ttf"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
    )
else:
    _FONT_CANDIDATES = (
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
    )
    _FONT_BOLD_CANDIDATES = _FONT_CANDIDATES

_FALLBACK_FONT = (
    Path(__file__).resolve().parent.parent
    / "MediaParser"
    / "core"
    / "resources"
    / "HYSongYunLangHeiW-1.ttf"
)

CARD_WIDTH = 720
HEADER_H = 56
PADDING_X = 12
MSG_GAP = 14
CARD_TOP_PAD = 6
CARD_BOTTOM_PAD = 20
AVATAR_SIZE = 40
AVATAR_GAP = 8
BADGE_H = 18
BADGE_PAD_X = 6
BODY_SIZE = 15
NICK_SIZE = 14
BADGE_SIZE = 10
TIME_SIZE = 11
QUOTE_SIZE = 13
HEADER_TITLE_SIZE = 17
DIVIDER_SIZE = 13
NICK_GAP = 6
HEADER_TO_BUBBLE_GAP = 4

BG = (245, 245, 245)
HEADER_BG = (255, 255, 255)
HEADER_BORDER = (230, 230, 230)
HEADER_TITLE = (30, 30, 30)
ADMIN_BADGE_BG = (38, 194, 201)
MEMBER_BADGE_BG = (160, 174, 192)
BADGE_TEXT = (255, 255, 255)
NICKNAME_COLOR = (120, 120, 120)
BUBBLE_BG = (228, 240, 255)
TEXT_COLOR = (30, 30, 30)
AT_COLOR = (30, 136, 229)
TIME_COLOR = (168, 168, 168)
QUOTE_BG = (240, 240, 240)
QUOTE_BAR = (200, 200, 200)
QUOTE_TEXT = (120, 120, 120)
DIVIDER_COLOR = (0, 122, 255)
BUBBLE_RADIUS = 12
BUBBLE_PAD_X = 12
BUBBLE_PAD_Y = 10
BUBBLE_MAX_W = 480
IMAGE_MAX_W = 240
IMAGE_MAX_H = 160

ROLE_LABEL = {
    "owner": "群主",
    "admin": "管理员",
    "member": "群员",
}


@dataclass
class RenderPart:
    kind: str  # text | at | image | reply_quote
    text: str = ""
    image_bytes: Optional[bytes] = None
    is_gif: bool = False


@dataclass
class ChatRenderMessage:
    user_id: str
    nickname: str
    time_label: str
    parts: List[RenderPart] = field(default_factory=list)
    highlighted: bool = False
    avatar_path: str = ""
    role: str = "member"
    level: str = ""
    title: str = ""
    card: str = ""
    display_name: str = ""


def _load_font(
    size: int, bold: bool = False
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = _FONT_BOLD_CANDIDATES if bold else _FONT_CANDIDATES
    for path in candidates:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    if _FALLBACK_FONT.exists():
        try:
            return ImageFont.truetype(str(_FALLBACK_FONT), size)
        except OSError:
            pass
    return ImageFont.load_default()


def _line_height(font) -> int:
    bbox = font.getbbox("测")
    return int(bbox[3] - bbox[1]) + 3


def _text_top_for_center(y: int, box_h: int, font, sample: str = "测") -> int:
    """在 box_h 区域内垂直居中绘制 text 时的 top-left y。"""
    bbox = font.getbbox(sample)
    text_h = bbox[3] - bbox[1]
    return y + (box_h - text_h) // 2 - bbox[1]


def _header_row_height(badge_font, nick_font) -> int:
    return max(BADGE_H, _line_height(nick_font), _line_height(badge_font)) + 2


def _circle_avatar(path: str, size: int) -> Image.Image:
    try:
        img = (
            Image.open(path)
            .convert("RGBA")
            .resize((size, size), Image.Resampling.LANCZOS)
        )
    except Exception:
        img = Image.new("RGBA", (size, size), (210, 210, 210, 255))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _open_image_bytes(data: bytes) -> Image.Image:
    img = Image.open(BytesIO(data))
    try:
        is_animated = bool(getattr(img, "is_animated", False))
        n_frames = int(getattr(img, "n_frames", 1) or 1)
        if is_animated or n_frames > 1:
            img.seek(0)
            frame = img.copy()
            frame.load()
            if frame.mode in ("P", "PA", "LA"):
                frame = frame.convert("RGBA")
            elif frame.mode != "RGBA":
                frame = frame.convert("RGBA")
            return frame
        return img.convert("RGBA")
    finally:
        img.close()


def _fit_image(img: Image.Image) -> Image.Image:
    w, h = img.size
    if w <= 0 or h <= 0:
        return img
    scale = min(IMAGE_MAX_W / w, IMAGE_MAX_H / h, 1.0)
    if scale >= 1.0:
        return img
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    return img.resize((nw, nh), Image.Resampling.LANCZOS)


def _wrap_text(text: str, font, max_width: int) -> List[str]:
    if not text:
        return []
    lines: List[str] = []
    for paragraph in text.split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        current = ""
        for ch in paragraph:
            trial = current + ch
            if font.getlength(trial) <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = ch
        if current:
            lines.append(current)
    return lines or [""]


def _split_at_segments(line: str) -> List[Tuple[str, bool]]:
    out: List[Tuple[str, bool]] = []
    buf = ""
    i = 0
    while i < len(line):
        if line[i] == "@":
            if buf:
                out.append((buf, False))
                buf = ""
            j = i + 1
            while j < len(line) and line[j] not in " \t\n，。！？,.!?":
                j += 1
            out.append((line[i:j], True))
            i = j
            continue
        buf += line[i]
        i += 1
    if buf:
        out.append((buf, False))
    return out


def _role_badge_text(role: str, level: str) -> str:
    role_key = (role or "member").lower()
    role_label = ROLE_LABEL.get(role_key, "群员")
    lv = (level or "").strip()
    if lv and not lv.upper().startswith("LV"):
        lv = f"LV{lv}"
    if lv:
        return f"{lv} {role_label}"
    return role_label


def _draw_pill(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font,
    *,
    bg: Tuple[int, int, int],
    fg: Tuple[int, int, int],
    box_h: int = BADGE_H,
) -> int:
    tw = font.getlength(text)
    w = int(tw) + BADGE_PAD_X * 2
    pill_y = y + (box_h - BADGE_H) // 2
    draw.rounded_rectangle(
        (x, pill_y, x + w, pill_y + BADGE_H),
        radius=BADGE_H // 2,
        fill=bg,
    )
    ty = _text_top_for_center(pill_y, BADGE_H, font, text)
    draw.text((x + BADGE_PAD_X, ty), text, font=font, fill=fg)
    return w


def _resolve_display_name(msg: ChatRenderMessage) -> str:
    name = (msg.display_name or msg.card or msg.nickname or msg.user_id).strip()
    return name or msg.user_id


def _draw_badges(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    msg: ChatRenderMessage,
    badge_font,
    nick_font,
    row_h: int,
) -> Tuple[int, int]:
    role_key = (msg.role or "member").lower()
    is_admin = role_key in ("owner", "admin")
    badge_bg = ADMIN_BADGE_BG if is_admin else MEMBER_BADGE_BG

    cx = x
    w1 = _draw_pill(
        draw,
        cx,
        y,
        _role_badge_text(msg.role, msg.level),
        badge_font,
        bg=badge_bg,
        fg=BADGE_TEXT,
        box_h=row_h,
    )
    cx += w1 + NICK_GAP

    name = _resolve_display_name(msg)
    ty = _text_top_for_center(y, row_h, nick_font, name)
    draw.text((cx, ty), name, font=nick_font, fill=NICKNAME_COLOR)
    cx += int(nick_font.getlength(name))

    return cx - x, row_h


def _content_blocks(
    parts: Sequence[RenderPart],
) -> List[Tuple[str, object]]:
    blocks: List[Tuple[str, object]] = []
    text_acc = ""
    for part in parts:
        if part.kind == "reply_quote":
            if text_acc:
                blocks.append(("text", text_acc))
                text_acc = ""
            blocks.append(("quote", part.text))
        elif part.kind in ("text", "at"):
            text_acc += part.text
        elif part.kind == "image":
            if text_acc:
                blocks.append(("text", text_acc))
                text_acc = ""
            blocks.append(("image", part.image_bytes))
    if text_acc:
        blocks.append(("text", text_acc))
    return blocks


def _measure_text_width(text: str, font, max_inner: int) -> float:
    lines = _wrap_text(text, font, max_inner)
    return max((font.getlength(ln) for ln in lines), default=0.0)


def _measure_bubble(
    blocks: List[Tuple[str, object]],
    *,
    body_font,
    quote_font,
    image_cache: Dict[bytes, Image.Image],
    max_inner: int,
) -> Tuple[int, int]:
    body_h = _line_height(body_font)
    quote_h = _line_height(quote_font)
    content_w = 0.0
    content_h = 0

    for kind, payload in blocks:
        if kind == "text":
            lines = _wrap_text(str(payload), body_font, max_inner)
            lw = max((body_font.getlength(ln) for ln in lines), default=0.0)
            content_w = max(content_w, lw)
            content_h += len(lines) * body_h + max(len(lines) - 1, 0) * 4
        elif kind == "quote":
            quote_lines = str(payload).split("\n", 1)
            for ql in quote_lines:
                qlines = _wrap_text(ql, quote_font, max_inner - 16)
                lw = max((quote_font.getlength(ln) for ln in qlines), default=0.0)
                content_w = max(content_w, lw + 16)
                content_h += len(qlines) * quote_h + 4
            content_h += 8
        elif kind == "image":
            img = image_cache.get(payload) if payload else None
            if img is not None:
                fitted = _fit_image(img)
                content_w = max(content_w, fitted.width)
                content_h += fitted.height + 8
            else:
                content_h += body_h + 8

    if content_h == 0:
        content_h = body_h

    bubble_w = min(BUBBLE_MAX_W, int(content_w) + BUBBLE_PAD_X * 2)
    bubble_w = max(bubble_w, 48)
    bubble_h = content_h + BUBBLE_PAD_Y * 2
    return bubble_w, bubble_h


def _draw_bubble_content(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    blocks: List[Tuple[str, object]],
    x: int,
    y: int,
    inner_w: int,
    *,
    body_font,
    quote_font,
    image_cache: Dict[bytes, Image.Image],
) -> None:
    body_h = _line_height(body_font)
    quote_h = _line_height(quote_font)
    cy = y + BUBBLE_PAD_Y

    for kind, payload in blocks:
        if kind == "text":
            lines = _wrap_text(str(payload), body_font, inner_w)
            for line in lines:
                cx = x + BUBBLE_PAD_X
                if "@" in line:
                    for seg, is_at in _split_at_segments(line):
                        color = AT_COLOR if is_at else TEXT_COLOR
                        draw.text((cx, cy), seg, font=body_font, fill=color)
                        cx += body_font.getlength(seg)
                else:
                    draw.text(
                        (x + BUBBLE_PAD_X, cy), line, font=body_font, fill=TEXT_COLOR
                    )
                cy += body_h + 4
        elif kind == "quote":
            quote_text = str(payload)
            qlines = quote_text.split("\n")
            qy0 = cy
            qy1 = cy
            for ql in qlines:
                wrapped = _wrap_text(ql, quote_font, inner_w - 16)
                qy1 += len(wrapped) * quote_h + 4
            qy1 += 8
            draw.rounded_rectangle(
                (x + BUBBLE_PAD_X, qy0, x + BUBBLE_PAD_X + inner_w, qy1),
                radius=6,
                fill=QUOTE_BG,
            )
            draw.rectangle(
                (x + BUBBLE_PAD_X, qy0, x + BUBBLE_PAD_X + 3, qy1),
                fill=QUOTE_BAR,
            )
            qcy = qy0 + 6
            for ql in qlines:
                for wln in _wrap_text(ql, quote_font, inner_w - 16):
                    draw.text(
                        (x + BUBBLE_PAD_X + 10, qcy),
                        wln,
                        font=quote_font,
                        fill=QUOTE_TEXT,
                    )
                    qcy += quote_h + 2
            cy = qy1 + 4
        elif kind == "image":
            img = image_cache.get(payload) if payload else None
            if img is not None:
                fitted = _fit_image(img)
                canvas.paste(
                    fitted,
                    (x + BUBBLE_PAD_X, cy),
                    fitted if fitted.mode == "RGBA" else None,
                )
                cy += fitted.height + 8
            else:
                draw.text(
                    (x + BUBBLE_PAD_X, cy),
                    "[图片]",
                    font=body_font,
                    fill=QUOTE_TEXT,
                )
                cy += body_h + 8


def _message_row_height(
    msg: ChatRenderMessage,
    *,
    body_font,
    quote_font,
    badge_font,
    nick_font,
    time_font,
    image_cache: Dict[bytes, Image.Image],
) -> int:
    blocks = _content_blocks(msg.parts)
    max_inner = BUBBLE_MAX_W - BUBBLE_PAD_X * 2
    bubble_w, bubble_h = _measure_bubble(
        blocks,
        body_font=body_font,
        quote_font=quote_font,
        image_cache=image_cache,
        max_inner=max_inner,
    )
    header_h = _header_row_height(badge_font, nick_font)
    time_h = _line_height(time_font)
    row_h = max(
        AVATAR_SIZE,
        header_h + HEADER_TO_BUBBLE_GAP + bubble_h + 4 + time_h,
    )
    return row_h + MSG_GAP


def _measure_message_row(
    msg: ChatRenderMessage,
    *,
    body_font,
    quote_font,
    badge_font,
    nick_font,
    time_font,
    image_cache: Dict[bytes, Image.Image],
) -> Tuple[int, int, int]:
    blocks = _content_blocks(msg.parts)
    max_inner = BUBBLE_MAX_W - BUBBLE_PAD_X * 2
    bubble_w, bubble_h = _measure_bubble(
        blocks,
        body_font=body_font,
        quote_font=quote_font,
        image_cache=image_cache,
        max_inner=max_inner,
    )
    row_h = _message_row_height(
        msg,
        body_font=body_font,
        quote_font=quote_font,
        badge_font=badge_font,
        nick_font=nick_font,
        time_font=time_font,
        image_cache=image_cache,
    )
    return row_h, bubble_w, bubble_h


def _draw_new_message_divider(
    draw: ImageDraw.ImageDraw,
    y: int,
    font,
) -> int:
    label = "新消息"
    lw = font.getlength(label)
    cx = CARD_WIDTH // 2
    ly = y + 8
    gap = 8
    draw.line(
        (PADDING_X, ly + 6, cx - lw / 2 - gap, ly + 6), fill=DIVIDER_COLOR, width=1
    )
    draw.text((cx - lw / 2, ly), label, font=font, fill=DIVIDER_COLOR)
    draw.line(
        (cx + lw / 2 + gap, ly + 6, CARD_WIDTH - PADDING_X, ly + 6),
        fill=DIVIDER_COLOR,
        width=1,
    )
    return 28


def _draw_message_row(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    msg: ChatRenderMessage,
    y: int,
    *,
    body_font,
    quote_font,
    badge_font,
    nick_font,
    time_font,
    image_cache: Dict[bytes, Image.Image],
) -> int:
    content_x = PADDING_X + AVATAR_SIZE + AVATAR_GAP
    avatar = _circle_avatar(msg.avatar_path, AVATAR_SIZE)
    canvas.paste(avatar, (PADDING_X, y), avatar)

    header_h = _header_row_height(badge_font, nick_font)
    _draw_badges(draw, content_x, y, msg, badge_font, nick_font, header_h)

    blocks = _content_blocks(msg.parts)
    max_inner = BUBBLE_MAX_W - BUBBLE_PAD_X * 2
    bubble_w, bubble_h = _measure_bubble(
        blocks,
        body_font=body_font,
        quote_font=quote_font,
        image_cache=image_cache,
        max_inner=max_inner,
    )

    bubble_y = y + header_h + HEADER_TO_BUBBLE_GAP
    bubble_box = (content_x, bubble_y, content_x + bubble_w, bubble_y + bubble_h)
    draw.rounded_rectangle(bubble_box, radius=BUBBLE_RADIUS, fill=BUBBLE_BG)
    _draw_bubble_content(
        canvas,
        draw,
        blocks,
        content_x,
        bubble_y,
        bubble_w - BUBBLE_PAD_X * 2,
        body_font=body_font,
        quote_font=quote_font,
        image_cache=image_cache,
    )

    time_y = bubble_y + bubble_h + 4
    draw.text((content_x, time_y), msg.time_label, font=time_font, fill=TIME_COLOR)

    time_h = _line_height(time_font)
    row_h = max(AVATAR_SIZE, header_h + HEADER_TO_BUBBLE_GAP + bubble_h + 4 + time_h)
    return row_h + MSG_GAP


def render_qq_chat_card(
    *,
    group_title: str,
    messages: Sequence[ChatRenderMessage],
    pending_count: int = 1,
) -> bytes:
    body_font = _load_font(BODY_SIZE)
    quote_font = _load_font(QUOTE_SIZE)
    badge_font = _load_font(BADGE_SIZE, bold=True)
    nick_font = _load_font(NICK_SIZE)
    time_font = _load_font(TIME_SIZE)
    header_font = _load_font(HEADER_TITLE_SIZE, bold=True)
    divider_font = _load_font(DIVIDER_SIZE)

    image_cache: Dict[bytes, Image.Image] = {}
    for msg in messages:
        for part in msg.parts:
            if part.kind == "image" and part.image_bytes:
                if part.image_bytes not in image_cache:
                    try:
                        image_cache[part.image_bytes] = _open_image_bytes(
                            part.image_bytes
                        )
                    except Exception:
                        pass

    row_heights: List[int] = []
    for msg in messages:
        rh, _, _ = _measure_message_row(
            msg,
            body_font=body_font,
            quote_font=quote_font,
            badge_font=badge_font,
            nick_font=nick_font,
            time_font=time_font,
            image_cache=image_cache,
        )
        if msg.highlighted:
            row_heights.append(28)
        row_heights.append(rh)

    body_h = CARD_TOP_PAD + sum(row_heights) + CARD_BOTTOM_PAD
    total_h = HEADER_H + body_h

    canvas = Image.new("RGB", (CARD_WIDTH, total_h), BG)
    draw = ImageDraw.Draw(canvas)

    draw.rectangle((0, 0, CARD_WIDTH, HEADER_H), fill=HEADER_BG)
    draw.line((0, HEADER_H - 1, CARD_WIDTH, HEADER_H - 1), fill=HEADER_BORDER, width=1)

    title = group_title
    if pending_count > 1:
        title = f"{group_title} · {pending_count}条未读@"
    tw = header_font.getlength(title)
    if tw > CARD_WIDTH - 80:
        while title and header_font.getlength(title + "…") > CARD_WIDTH - 80:
            title = title[:-1]
        title += "…"
        tw = header_font.getlength(title)
    draw.text(
        ((CARD_WIDTH - tw) / 2, (HEADER_H - _line_height(header_font)) // 2),
        title,
        font=header_font,
        fill=HEADER_TITLE,
    )

    # 右侧菜单线
    mx = CARD_WIDTH - 28
    my = HEADER_H // 2 - 8
    for i in range(3):
        draw.line((mx, my + i * 7, mx + 18, my + i * 7), fill=(120, 120, 120), width=2)

    y = HEADER_H + CARD_TOP_PAD
    divider_drawn = False
    for msg in messages:
        if msg.highlighted and not divider_drawn:
            y += _draw_new_message_divider(draw, y, divider_font)
            divider_drawn = True
        y += _draw_message_row(
            canvas,
            draw,
            msg,
            y,
            body_font=body_font,
            quote_font=quote_font,
            badge_font=badge_font,
            nick_font=nick_font,
            time_font=time_font,
            image_cache=image_cache,
        )

    buf = BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
