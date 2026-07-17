"""原 FF14LogsInfo 风格卡片绘制，数据源改为塔塔露 zone / records。"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from io import BytesIO
from typing import Any

import httpx
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

from .engine import (
    FFLOGS_CHARACTER_GROUPS,
    find_logs_job,
    fflogs_character_encounter_label,
)

_log = logging.getLogger("Tataru.logs_card")

FONT_PATH = "data/font/FZMiaoWuK.TTF"
BOSS_ICON_CACHE_DIR = "data/image/ff14/boss_icons"
JOB_ICON_CACHE_DIR = "data/image/ff14/job_icons"


def _boss_icon_url(encounter_id: int) -> str:
    if encounter_id == 1068:
        return "https://assets.rpglogs.com/img/ff/bosses/1068-icon.jpg?v=2"
    return f"https://assets.rpglogs.com/img/ff/bosses/{encounter_id}-icon.jpg?v=2"


def _job_icon_url(job_cn_or_en: str | None) -> str | None:
    if not job_cn_or_en:
        return None
    job = find_logs_job(job_cn_or_en)
    # FFLogs 图标文件名用英文职业名（无空格）
    name = (job or {}).get("name") or job_cn_or_en
    name = str(name).replace(" ", "")
    return f"https://assets.rpglogs.com/img/ff/icons/{name}.png"


def _get_rank_color(rank: float | None) -> tuple:
    if rank is None or rank < 0:
        return (128, 128, 128)
    if rank == 100:
        return (229, 204, 128)
    if rank >= 99:
        return (226, 104, 168)
    if rank >= 95:
        return (255, 128, 0)
    if rank >= 75:
        return (163, 53, 238)
    if rank >= 50:
        return (0, 112, 255)
    if rank >= 25:
        return (30, 255, 0)
    return (128, 128, 128)


def _get_cached_image(url: str, cache_dir: str) -> PILImage.Image:
    file_name = url.split("/")[-1].split("?")[0]
    cache_path = os.path.normpath(os.path.join(cache_dir, file_name))
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    if os.path.exists(cache_path):
        try:
            return PILImage.open(cache_path)
        except Exception as e:
            _log.warning("加载缓存图失败: %s", e)
    response = httpx.get(url, timeout=20)
    response.raise_for_status()
    image = PILImage.open(BytesIO(response.content))
    image.save(cache_path)
    return image


def _ordered_sections(records: dict[str, dict]) -> list[tuple[str, list[dict]]]:
    sections: list[tuple[str, list[dict]]] = []
    for title, encounter_ids in FFLOGS_CHARACTER_GROUPS:
        rows: list[dict] = []
        seen_labels: set[str] = set()
        for encounter_id in encounter_ids:
            label = fflogs_character_encounter_label(encounter_id)
            if label in seen_labels:
                continue
            seen_labels.add(label)
            record = records.get(label)
            if record:
                rows.append(record)
        sections.append((title, rows))
    return sections


def render_character_logs_card(
    username: str,
    server: str,
    records: dict[str, dict[str, Any]],
) -> str:
    """用塔塔露收集到的 records 绘制原风格黑底卡片。"""
    sections = _ordered_sections(records)
    row_count = sum(len(rows) for _, rows in sections)
    if row_count == 0:
        return ""

    width = 780
    # 标题区 + 每节标题 + 每行
    height = 150
    for _, rows in sections:
        height += 50  # section header
        height += max(len(rows), 1) * 80

    image = PILImage.new("RGB", (width, height), "black")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype(FONT_PATH, 26)
        title_font = ImageFont.truetype(FONT_PATH, 28)
        section_font = ImageFont.truetype(FONT_PATH, 24)
    except Exception as e:
        _log.warning("加载字体失败: %s", e)
        return ""

    draw.text((10, 40), f"{username} - {server}", fill="white", font=title_font)
    y = 100

    for title, rows in sections:
        draw.text((16, y), f"【{title}】", fill=(255, 214, 120), font=section_font)
        y += 40
        # 列头
        draw.text((20, y), "Boss", fill=(180, 189, 255), font=font)
        draw.text((360, y), "Best", fill=(180, 189, 255), font=font)
        draw.text((470, y), "rDPS", fill=(180, 189, 255), font=font)
        draw.text((640, y), "Parses", fill=(180, 189, 255), font=font)
        y += 36

        if not rows:
            draw.text((20, y), "暂无记录", fill=(128, 128, 128), font=font)
            y += 60
            continue

        for record in rows:
            encounter_id = int(record.get("encounter_id") or 0)
            label = str(record.get("label") or "")
            percent = record.get("percent")
            amount = record.get("amount")
            total_parses = record.get("total_parses")
            job = str(record.get("job") or "")

            # Boss 图标 + 名
            try:
                if encounter_id:
                    boss_icon = _get_cached_image(
                        _boss_icon_url(encounter_id), BOSS_ICON_CACHE_DIR
                    ).resize((56, 56))
                    image.paste(boss_icon, (12, y + 4))
            except Exception as e:
                _log.warning("boss 图标失败: %s", e)
            draw.text((78, y + 14), label, fill=(180, 189, 255), font=font)

            # 分位
            percent_value = float(percent) if isinstance(percent, (int, float)) else -1
            draw.text(
                (360, y + 14),
                f"{percent_value:.0f}" if percent_value >= 0 else "--",
                fill=_get_rank_color(percent_value if percent_value >= 0 else None),
                font=font,
            )

            # 职业图标
            try:
                job_url = _job_icon_url(job)
                if job_url:
                    job_icon = _get_cached_image(job_url, JOB_ICON_CACHE_DIR).resize(
                        (28, 28)
                    )
                    if job_icon.mode != "RGBA":
                        job_icon = job_icon.convert("RGBA")
                    transparent = PILImage.new("RGBA", job_icon.size, (0, 0, 0, 0))
                    transparent.paste(job_icon, (0, 0), job_icon)
                    image.paste(transparent, (410, y + 16), transparent)
            except Exception as e:
                _log.warning("职业图标失败: %s", e)

            # rDPS
            if isinstance(amount, (int, float)) and amount > 0:
                amount_text = f"{amount:,.0f}"
            else:
                amount_text = "--"
            draw.text((470, y + 14), amount_text, fill=(180, 189, 255), font=font)

            # Parses
            parses_text = str(total_parses) if total_parses is not None else "--"
            draw.text((650, y + 14), parses_text, fill=(225, 242, 245), font=font)

            # 副标题：职业 / 版本（绝本）
            detail_bits = [job] if job else []
            if record.get("category") == "ultimate" and record.get("version"):
                detail_bits.append(f"{record['version']}记录")
            if detail_bits:
                draw.text(
                    (78, y + 42),
                    " / ".join(detail_bits),
                    fill=(140, 140, 160),
                    font=section_font,
                )

            y += 80

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    file_path = f"data/image/ff14/logs/image_{timestamp}.png"
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    image.save(file_path)
    return file_path
