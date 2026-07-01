import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

from common.utils.CommonUtil import CommonUtil
from .fonts import load_font
from .paths import RESOURCES_PATH, TEMP_PATH, ensure_dirs


@dataclass
class RenderUserInfo:
    group_id: str
    user_id: str
    rank: int
    count: str = "Unknown"
    nickname: str = ""
    avatar_path: str = ""

    @classmethod
    async def create(
        cls,
        group_id: str,
        user_id: str,
        rank: int,
        count: str = "Unknown",
        api=None,
        nickname_map: Optional[Dict[str, str]] = None,
        avatar_type: str = "user",
    ) -> "RenderUserInfo":
        nickname = (nickname_map or {}).get(user_id, str(user_id))
        if avatar_type == "group":
            avatar_path = CommonUtil.get_group_avatar(user_id)
        else:
            avatar_path = CommonUtil.get_avatar(user_id)
        return cls(
            group_id=group_id,
            user_id=user_id,
            rank=rank,
            count=count,
            nickname=nickname,
            avatar_path=avatar_path,
        )

    @classmethod
    def placeholder(cls, rank: int) -> "RenderUserInfo":
        return cls(group_id="0", user_id="0", rank=rank, nickname="暂无", count="—")


def _circle_avatar(path: str, size: int) -> Image.Image:
    try:
        img = (
            Image.open(path)
            .convert("RGBA")
            .resize((size, size), Image.Resampling.LANCZOS)
        )
    except Exception:
        img = Image.new("RGBA", (size, size), (220, 220, 220, 255))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    img.putalpha(mask)
    return img


def create_top3_podium(
    top3: Tuple[RenderUserInfo, RenderUserInfo, RenderUserInfo],
    resources_path: Path = RESOURCES_PATH,
    gap: int = 56,
    text_width_reduce: int = 20,
) -> Image.Image:
    img_1st = Image.open(resources_path / "1st.png").convert("RGBA")
    img_2nd = Image.open(resources_path / "2nd.png").convert("RGBA")
    img_3rd = Image.open(resources_path / "3rd.png").convert("RGBA")
    w1, h1 = img_1st.size
    w2, h2 = int(w1 * 0.95), int(h1 * 0.95)
    img_2nd = img_2nd.resize((w2, h2), Image.Resampling.LANCZOS)
    img_3rd = img_3rd.resize((w2, h2), Image.Resampling.LANCZOS)
    text_gap = 12
    font_size, count_font_size = 24, 18
    text_h = font_size + count_font_size + text_gap * 2 + 14
    total_w = w2 + gap + w1 + gap + w2
    total_h = h1 + text_h
    canvas = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    x2, y2 = 0, total_h - h2 - text_h
    x1, y1 = w2 + gap, total_h - h1 - text_h
    x3, y3 = w2 + gap + w1 + gap, total_h - h2 - text_h
    frames = [
        (top3[1], img_2nd, x2, y2, w2, h2),
        (top3[0], img_1st, x1, y1, w1, h1),
        (top3[2], img_3rd, x3, y3, w2, h2),
    ]
    for info, frame, x, y, fw, fh in frames:
        inner = int(min(fw, fh) * 0.8)
        av = _circle_avatar(info.avatar_path, inner)
        canvas.paste(av, (x + (fw - inner) // 2, y + (fh - inner) // 2), av)
        canvas.paste(frame, (x, y), frame)
    draw = ImageDraw.Draw(canvas)
    font = load_font(font_size)
    cf = load_font(count_font_size)
    for info, _, x, y, fw, fh in frames:
        ny = y + fh + text_gap
        max_text_w = max(40, fw - text_width_reduce)
        nick = _truncate_to_width(draw, info.nickname, font, max_text_w)
        nb = draw.textbbox((0, 0), nick, font=font)
        nx = x + (fw - (nb[2] - nb[0])) // 2
        draw.text((nx, ny), nick, font=font, fill=(0, 0, 0, 255))
        cb = draw.textbbox((0, 0), info.count, font=cf)
        cx = x + (fw - (cb[2] - cb[0])) // 2
        draw.text((cx, ny + font_size + 2), info.count, font=cf, fill=(80, 80, 80, 255))
    return canvas


def _truncate_to_width(
    draw: ImageDraw.ImageDraw, text: str, font, max_width: int
) -> str:
    if not text:
        return ""
    bbox = draw.textbbox((0, 0), text, font=font)
    if bbox[2] - bbox[0] <= max_width:
        return text
    ellipsis = "…"
    for end in range(len(text), 0, -1):
        candidate = text[:end] + ellipsis
        bb = draw.textbbox((0, 0), candidate, font=font)
        if bb[2] - bb[0] <= max_width:
            return candidate
    return ellipsis


def _draw_text_on_bg(
    draw: ImageDraw.ImageDraw,
    xy: tuple,
    text: str,
    font,
    *,
    fill=(50, 50, 50, 255),
    bg=(255, 255, 255, 240),
    padding: int = 2,
):
    """带底色绘制文字，避免被蜡笔条遮挡。"""
    x, y = xy
    bbox = draw.textbbox((x, y), text, font=font)
    draw.rectangle(
        [bbox[0] - padding, bbox[1] - padding, bbox[2] + padding, bbox[3] + padding],
        fill=bg,
    )
    draw.text((x, y), text, font=font, fill=fill)


def create_top10_list(
    users: List[RenderUserInfo],
    title: str = "排行榜",
    resources_path: Path = RESOURCES_PATH,
    compact: bool = False,
) -> Image.Image:
    """TOP10 竖向列表（含前三名时可单独用 podium，此函数用于完整 TOP10 或 4-10）。"""
    if not users:
        users = [RenderUserInfo.placeholder(i) for i in range(1, 4)]
    row_h = 46 if compact else 52
    width = 920
    header_h = 36
    height = header_h + len(users) * row_h + 20
    img = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    title_font = load_font(18, bold=True)
    name_font = load_font(14)
    count_font = load_font(13)
    rank_font = load_font(14, bold=True)
    draw.text((20, 8), title, font=title_font, fill=(60, 60, 60, 255))
    max_count = 1
    for u in users:
        try:
            max_count = max(max_count, int(u.count.split()[0]))
        except ValueError:
            pass
    nick_x = 96
    bar_x = 280
    bar_max_w = width - bar_x - 100
    nick_max_w = bar_x - nick_x - 16
    rank_colors = [(255, 215, 0), (192, 192, 192), (205, 127, 50)] + [
        (180, 180, 180)
    ] * 7

    from .crayon_utils import draw_crayon_rectangle

    row_layout = []
    for i, u in enumerate(users):
        y = header_h + i * row_h
        try:
            cnt = int(u.count.split()[0])
        except ValueError:
            cnt = 0
        bw = max(4, (cnt / max_count) * bar_max_w) if max_count else 4
        draw_crayon_rectangle(
            draw, bar_x, y + 18, bw, 18, (135, 180, 255), "horizontal"
        )
        row_layout.append((i, u, y))

    draw = ImageDraw.Draw(img)
    avatar_pastes = []
    for i, u, y in row_layout:
        rc = rank_colors[i] if i < len(rank_colors) else (180, 180, 180)
        draw.ellipse((16, y + 10, 44, y + 38), fill=rc + (220,))
        draw.text((22, y + 14), str(u.rank), font=rank_font, fill=(255, 255, 255, 255))
        avatar_pastes.append((u.avatar_path, 52, y + 8))
        nick = _truncate_to_width(draw, u.nickname, name_font, nick_max_w)
        _draw_text_on_bg(draw, (nick_x, y + 16), nick, name_font)
        _draw_text_on_bg(
            draw,
            (bar_x + bar_max_w + 8, y + 16),
            u.count,
            count_font,
            fill=(100, 100, 100, 255),
        )
    for avatar_path, ax, ay in avatar_pastes:
        av = _circle_avatar(avatar_path, 36)
        img.paste(av, (ax, ay), av)
    return img


def save_top3_podium(
    top3: Tuple[RenderUserInfo, RenderUserInfo, RenderUserInfo],
    *,
    gap: int = 56,
    text_width_reduce: int = 20,
) -> Path:
    ensure_dirs()
    out = TEMP_PATH / f"podium_{uuid.uuid4().hex}.png"
    create_top3_podium(top3, gap=gap, text_width_reduce=text_width_reduce).save(
        out, "PNG"
    )
    return out


def save_top10_list(
    users: List[RenderUserInfo],
    title: str = "排行榜 TOP10",
    *,
    compact: bool = False,
) -> Path:
    ensure_dirs()
    out = TEMP_PATH / f"top10_{uuid.uuid4().hex}.png"
    create_top10_list(users, title=title, compact=compact).save(out, "PNG")
    return out
