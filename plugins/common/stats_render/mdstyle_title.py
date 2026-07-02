"""将 pillowmd mdstyle 的 H2 标题与图表内容合成为单块图片，避免双栏排版时标题与内容分离。"""

from __future__ import annotations

import json
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Tuple

from PIL import Image, ImageDraw, ImageFont

from .paths import RESOURCES_PATH, TEMP_PATH, ensure_dirs

_MDSTYLE_DIR = RESOURCES_PATH / "mdstyle"


@lru_cache(maxsize=1)
def _load_mdstyle_settings() -> dict:
    setting_path = _MDSTYLE_DIR / "setting.json"
    if not setting_path.exists():
        return {}
    with open(setting_path, encoding="utf-8") as f:
        return json.load(f)


def load_mdstyle_h2_font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    settings = _load_mdstyle_settings()
    font_name = settings.get("titleFont", "OPPOSans-Regular.ttf")
    size = int(settings.get("title2FontSize", 55))
    font_path = _MDSTYLE_DIR / "fonts" / font_name
    if not font_path.exists():
        fonts_dir = _MDSTYLE_DIR / "fonts"
        if fonts_dir.is_dir():
            for candidate in fonts_dir.glob("*.ttf"):
                if candidate.name != settings.get("codeFont"):
                    font_path = candidate
                    break
    try:
        return ImageFont.truetype(str(font_path), size)
    except OSError:
        from .fonts import load_font

        return load_font(size, bold=True)


def mdstyle_h2_color() -> Tuple[int, int, int, int]:
    settings = _load_mdstyle_settings()
    rgb = settings.get("textColor", [86, 96, 108])
    if isinstance(rgb, list) and len(rgb) >= 3:
        return int(rgb[0]), int(rgb[1]), int(rgb[2]), 255
    return 86, 96, 108, 255


def compose_section_block(content: Image.Image, title: str) -> Image.Image:
    """在图表上方绘制 mdstyle H2 标题，使每个 section 成为 pillowmd 中的单一 !sgm 块。"""
    title_font = load_mdstyle_h2_font()
    color = mdstyle_h2_color()
    settings = _load_mdstyle_settings()
    line_gap = int(settings.get("lineDistance", 10))
    header_h = int(getattr(title_font, "size", 55)) + line_gap + 12

    content = content.convert("RGBA")
    width = max(content.width, 920)
    canvas = Image.new("RGBA", (width, content.height + header_h), (255, 255, 255, 0))
    draw = ImageDraw.Draw(canvas)
    draw.text((0, 4), title, font=title_font, fill=color)
    x_offset = max(0, (width - content.width) // 2)
    canvas.paste(content, (x_offset, header_h), content)
    return canvas


def save_composed_section(content_path: Path, title: str) -> Path:
    ensure_dirs()
    with Image.open(content_path) as img:
        composed = compose_section_block(img, title)
    out = TEMP_PATH / f"section_{uuid.uuid4().hex}.png"
    composed.save(out, "PNG")
    return out
