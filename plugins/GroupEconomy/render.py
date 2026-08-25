from __future__ import annotations

import hashlib
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


def _font(size: int, bold: bool = False):
    candidates = (
        (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc")
        if bold
        else (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf")
    )
    candidates += (
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    ) if bold else (
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    image = image.convert("RGB")
    ratio = max(size[0] / image.width, size[1] / image.height)
    image = image.resize((int(image.width * ratio), int(image.height * ratio)), Image.Resampling.LANCZOS)
    left = (image.width - size[0]) // 2
    top = (image.height - size[1]) // 2
    return image.crop((left, top, left + size[0], top + size[1]))


def _avatar(path: str | None, size: int) -> Image.Image:
    try:
        avatar = Image.open(path).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    except Exception:
        avatar = Image.new("RGBA", (size, size), (210, 220, 235, 255))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    avatar.putalpha(mask)
    return avatar


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> str:
    text = str(text or "群友")
    if _text_width(draw, text, font) <= max_width:
        return text
    while len(text) > 1 and _text_width(draw, text + "…", font) > max_width:
        text = text[:-1]
    return text + "…"


def _make_gradient(size: tuple[int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGBA", size)
    pixels = image.load()
    left = (27, 37, 58)
    right = (196, 99, 91)
    for x in range(width):
        ratio = x / max(1, width - 1)
        color = tuple(int(left[i] * (1 - ratio) + right[i] * ratio) for i in range(3))
        for y in range(height):
            pixels[x, y] = (*color, 255)
    return image


def render_sign_card(
    *, background_path: str | None, avatar_path: str | None, nickname: str,
    reward: int, balance: int, score: int, level: int, rank: int, streak: int,
    output_path: Path,
) -> Path:
    size = (1200, 720)
    try:
        bg = Image.open(background_path) if background_path else _make_gradient(size)
    except Exception:
        bg = _make_gradient(size)
    bg = _cover(bg, size).filter(ImageFilter.GaussianBlur(0.8)).convert("RGBA")
    card = Image.alpha_composite(bg, Image.new("RGBA", size, (12, 19, 32, 92)))
    draw = ImageDraw.Draw(card)
    title = _font(24, True)
    name_font = _font(38, True)
    reward_font = _font(76, True)
    label = _font(18, True)
    value = _font(26, True)
    body = _font(22)
    small = _font(18)

    # Top-left identity block.
    draw.text((72, 54), "每日签到", font=title, fill=(244, 249, 247))
    draw.text((72, 88), "今天的好运已到账", font=small, fill=(201, 224, 220))
    avatar_size = 178
    avatar_x, avatar_y = 72, 148
    draw.ellipse(
        (avatar_x - 9, avatar_y - 9, avatar_x + avatar_size + 9, avatar_y + avatar_size + 9),
        fill=(255, 210, 166, 235),
    )
    card.alpha_composite(_avatar(avatar_path, avatar_size), (avatar_x, avatar_y))
    display_nickname = _fit_text(draw, nickname, name_font, 250)
    draw.text((72, 352), display_nickname, font=name_font, fill=(255, 255, 255))
    draw.text((72, 405), f"连续签到 {streak} 天", font=body, fill=(207, 231, 224))

    # Main reward block.
    main_x = 350
    draw.text((main_x, 60), "今日奖励", font=label, fill=(201, 224, 220))
    draw.text((main_x, 82), f"+{reward}", font=reward_font, fill=(255, 224, 171))
    draw.text((main_x + 240, 132), "钱包", font=body, fill=(244, 249, 247))
    draw.text((main_x, 188), f"当前余额  {balance}", font=body, fill=(255, 255, 255))
    draw.text((main_x, 230), f"Lv.{level}  ·  {score} 经验  ·  跨群排名 #{rank}", font=small, fill=(205, 223, 220))

    # Level progress bar.
    thresholds = (0, 10, 20, 50, 100, 200, 350, 550, 750, 1000, 1200)
    current_threshold = thresholds[min(max(level, 0), len(thresholds) - 1)]
    max_level = len(thresholds) - 1
    at_max_level = level >= max_level
    next_threshold = thresholds[level + 1] if 0 <= level < max_level else current_threshold
    progress = 1.0 if at_max_level else min(1.0, max(0.0, (score - current_threshold) / max(1, next_threshold - current_threshold)))
    bar_x, bar_y, bar_w = main_x, 294, 720
    draw.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + 14), radius=7, fill=(255, 255, 255, 70))
    draw.rounded_rectangle((bar_x, bar_y, bar_x + max(14, int(bar_w * progress)), bar_y + 14), radius=7, fill=(185, 231, 216, 255))
    progress_text = "已达到最高等级" if at_max_level else f"距离 Lv.{level + 1} 还需 {max(0, next_threshold - score)} 经验"
    draw.text((bar_x, bar_y + 27), progress_text, font=small, fill=(210, 229, 225))

    # Bottom stats, kept as a single information band.
    stats = (("钱包余额", str(balance)), ("连续签到", f"{streak} 天"), ("当前等级", f"Lv.{level}"), ("跨群排名", f"#{rank}"))
    stat_y = 470
    stat_w = 240
    for index, (caption, stat_value) in enumerate(stats):
        x = 72 + index * 276
        draw.rounded_rectangle((x, stat_y, x + stat_w, stat_y + 122), radius=18, fill=(13, 27, 43, 118), outline=(255, 255, 255, 48), width=1)
        draw.text((x + 20, stat_y + 18), caption, font=label, fill=(181, 211, 207))
        draw.text((x + 20, stat_y + 52), stat_value, font=value, fill=(255, 245, 224))
    draw.text((72, 655), "愿你今天也有好心情", font=small, fill=(214, 228, 221))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    card.convert("RGB").save(output_path, "PNG")
    return output_path


def render_wallet_card(
    *,
    background_path: str | None,
    avatar_path: str | None,
    nickname: str,
    balance: int,
    score: int,
    level: int,
    rank: int,
    streak: int,
    total_earned: int,
    total_spent: int,
    title: str,
    main_value: str,
    main_label: str,
    notice: str,
    output_path: Path,
) -> Path:
    """Render wallet and check-in status cards with the same visual language."""
    size = (1200, 720)
    try:
        bg = Image.open(background_path) if background_path else _make_gradient(size)
    except Exception:
        bg = _make_gradient(size)
    bg = _cover(bg, size).filter(ImageFilter.GaussianBlur(0.8)).convert("RGBA")
    card = Image.alpha_composite(bg, Image.new("RGBA", size, (12, 19, 32, 92)))
    draw = ImageDraw.Draw(card)
    title_font = _font(30, True)
    name_font = _font(38, True)
    main_font = _font(70, True)
    label_font = _font(18, True)
    value_font = _font(26, True)
    body_font = _font(22)
    small_font = _font(18)

    draw.text((72, 56), title, font=title_font, fill=(244, 249, 247))
    draw.text((72, 98), "账户状态", font=small_font, fill=(201, 224, 220))
    avatar_size = 178
    avatar_x, avatar_y = 72, 166
    draw.ellipse(
        (avatar_x - 9, avatar_y - 9, avatar_x + avatar_size + 9, avatar_y + avatar_size + 9),
        fill=(255, 210, 166, 235),
    )
    card.alpha_composite(_avatar(avatar_path, avatar_size), (avatar_x, avatar_y))
    draw.text((72, 370), _fit_text(draw, nickname, name_font, 250), font=name_font, fill=(255, 255, 255))
    draw.text((72, 424), f"连续签到 {streak} 天", font=body_font, fill=(207, 231, 224))

    main_x = 350
    draw.text((main_x, 64), main_label, font=label_font, fill=(201, 224, 220))
    draw.text((main_x, 92), str(main_value), font=main_font, fill=(255, 224, 171))
    draw.text((main_x, 188), f"Lv.{level}  ·  {score} 经验  ·  跨群排名 #{rank}", font=small_font, fill=(205, 223, 220))
    draw.text((main_x, 236), notice, font=body_font, fill=(255, 255, 255))
    draw.text((main_x, 282), f"当前余额  {balance}", font=body_font, fill=(207, 231, 224))

    stats = (
        ("累计收入", str(total_earned)),
        ("累计支出", str(total_spent)),
        ("当前等级", f"Lv.{level}"),
        ("跨群排名", f"#{rank}"),
    )
    stat_y = 470
    stat_w = 240
    for index, (caption, stat_value) in enumerate(stats):
        x = 72 + index * 276
        draw.rounded_rectangle(
            (x, stat_y, x + stat_w, stat_y + 122),
            radius=18,
            fill=(13, 27, 43, 118),
            outline=(255, 255, 255, 48),
            width=1,
        )
        draw.text((x + 20, stat_y + 18), caption, font=label_font, fill=(181, 211, 207))
        draw.text((x + 20, stat_y + 52), stat_value, font=value_font, fill=(255, 245, 224))
    draw.text((72, 655), "愿你今天也有好心情", font=small_font, fill=(214, 228, 221))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    card.convert("RGB").save(output_path, "PNG")
    return output_path


def render_ranking(
    rows: list[dict],
    output_path: Path,
    *,
    wallet: bool = False,
    title: str | None = None,
) -> Path:
    """Render a distinct level or wallet leaderboard with a top-three section."""
    width = 1200
    row_height = 72
    top_rows = rows[:3]
    list_rows = rows[3:]
    height = 270 + max(1, len(list_rows)) * row_height + 42
    image = Image.new("RGB", (width, height), (241, 245, 247))
    draw = ImageDraw.Draw(image)
    title = title or ("金币财富榜" if wallet else "跨群等级榜")
    subtitle = (
        "按当前钱包余额排名 · 同时展示累计获得金币"
        if wallet
        else "按累计签到经验排名 · 等级与经验跨群累计"
    )
    draw.text((56, 34), title, font=_font(38, True), fill=(31, 52, 68))
    draw.text((58, 84), subtitle, font=_font(20), fill=(93, 116, 126))

    # Highlight the first three entries like the project's other ranking cards.
    podium_y = 132
    card_w = 346
    gap = 18
    medal_colors = ((238, 181, 62), (151, 166, 177), (190, 126, 78))
    for index, row in enumerate(top_rows):
        x = 56 + index * (card_w + gap)
        fill = (255, 250, 231) if index == 0 else (255, 255, 255)
        draw.rounded_rectangle(
            (x, podium_y, x + card_w, podium_y + 112),
            radius=16,
            fill=fill,
            outline=(218, 226, 229),
            width=2,
        )
        draw.ellipse((x + 18, podium_y + 25, x + 62, podium_y + 69), fill=medal_colors[index])
        draw.text((x + 31, podium_y + 31), str(index + 1), font=_font(22, True), fill=(255, 255, 255))
        avatar = _avatar(row.get("avatar_path"), 66)
        image.paste(avatar, (x + 76, podium_y + 23), avatar)
        name = _fit_text(draw, str(row.get("nickname") or row.get("user_id") or "未知"), _font(21, True), 150)
        draw.text((x + 154, podium_y + 20), name, font=_font(21, True), fill=(41, 65, 78))
        if wallet:
            metric = f"余额 {int(row.get('balance', 0))}"
            detail = f"累计获得 {int(row.get('total_earned', 0))}"
        else:
            metric = f"Lv.{int(row.get('level', 0))}  {int(row.get('score', 0))} 经验"
            detail = f"当前余额 {int(row.get('balance', 0))}"
        draw.text((x + 154, podium_y + 53), metric, font=_font(19, True), fill=(56, 112, 145))
        draw.text((x + 154, podium_y + 80), detail, font=_font(16), fill=(111, 132, 140))

    list_y = podium_y + 138
    if not list_rows:
        draw.text((58, list_y), "暂无更多排行数据", font=_font(18), fill=(120, 140, 148))
    else:
        max_metric = max(
            1,
            max(
                (int(row.get("balance", 0)) if wallet else int(row.get("score", 0)))
                for row in rows
            ),
        )
        draw.rounded_rectangle((42, list_y - 12, width - 42, height - 24), radius=16, fill=(255, 255, 255))
        for offset, row in enumerate(list_rows, 4):
            y = list_y + (offset - 4) * row_height
            draw.text((66, y + 21), f"#{offset}", font=_font(22, True), fill=(91, 112, 121))
            avatar = _avatar(row.get("avatar_path"), 48)
            image.paste(avatar, (128, y + 12), avatar)
            name = _fit_text(draw, str(row.get("nickname") or row.get("user_id") or "未知"), _font(19), 190)
            draw.text((194, y + 17), name, font=_font(19), fill=(48, 69, 80))
            metric = int(row.get("balance", 0)) if wallet else int(row.get("score", 0))
            bar_x, bar_y, bar_w = 410, y + 25, 480
            draw.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + 14), radius=7, fill=(229, 236, 238))
            draw.rounded_rectangle((bar_x, bar_y, bar_x + max(12, int(bar_w * metric / max_metric)), bar_y + 14), radius=7, fill=(225, 180, 91) if wallet else (105, 169, 190))
            if wallet:
                value = f"余额 {metric} · 累计获得 {int(row.get('total_earned', 0))}"
            else:
                value = f"Lv.{int(row.get('level', 0))} · {metric} 经验"
            draw.text((910, y + 17), value, font=_font(17, True), fill=(62, 91, 105))
            draw.line((66, y + row_height - 1, width - 66, y + row_height - 1), fill=(233, 238, 240), width=1)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, "PNG")
    return output_path


def cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]


def cache_is_fresh(path: Path, hours: int) -> bool:
    return path.exists() and time.time() - path.stat().st_mtime < max(1, hours) * 3600
