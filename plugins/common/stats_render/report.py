import base64
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional

import pillowmd
from PIL import Image

from ncatbot.utils import get_log

from .paths import RESOURCES_PATH, TEMP_PATH, ensure_dirs

_log = get_log("StatsRender")


def _merge_pages(images: List[Image.Image]) -> Image.Image:
    if not images:
        raise ValueError("no images")
    if len(images) == 1:
        return images[0]
    max_w = max(im.width for im in images)
    total_h = sum(im.height for im in images)
    merged = Image.new("RGB", (max_w, total_h), (255, 255, 255))
    y = 0
    for im in images:
        frame = im.convert("RGB") if im.mode != "RGB" else im
        merged.paste(frame, (0, y))
        y += frame.height
    return merged


@dataclass
class RenderInfo:
    title: str
    current_time: datetime = field(default_factory=datetime.now)
    period_label: str = ""
    group_label: str = ""
    extra_lines: List[str] = field(default_factory=list)


async def render_stats_report(
    render_info: RenderInfo,
    sections: Dict[str, Path],
    *,
    section_scales: Optional[Dict[str, float]] = None,
    default_scale: float = 0.85,
    page: int = 2,
) -> Optional[str]:
    """
    使用 pillowmd 将多个子图合成为一张 PNG，返回 base64 字符串。
    sections: {章节标题: 图片路径}
    """
    ensure_dirs()
    if not sections:
        return None
    resources_path = RESOURCES_PATH
    if not (resources_path / "mdstyle").exists():
        _log.error("[StatsRender] mdstyle 资源缺失: %s", resources_path)
        return None

    pillowmd.Setting.QUICK_IMAGE_PATH = TEMP_PATH
    temp_pics: List[Path] = []
    parts = [f"# {render_info.title}"]
    if render_info.period_label:
        parts.append(f"\n统计时段：{render_info.period_label}")
    if render_info.group_label:
        parts.append(f"\n{render_info.group_label}")
    for line in render_info.extra_lines:
        parts.append(f"\n{line}")
    parts.append("\n")

    for name, img_path in sections.items():
        if img_path is None or not Path(img_path).exists():
            continue
        parts.append(f"\n## {name}\n")
        temp_pics.append(Path(img_path))
        scale = (section_scales or {}).get(name, default_scale)
        parts.append(f"!sgm[{Path(img_path).name}|{scale}]")

    markdown_text = "\n".join(parts)
    style_path = resources_path / "mdstyle"
    try:
        style = pillowmd.LoadMarkdownStyles(str(style_path))
        result = await pillowmd.MdToImage(
            text=markdown_text,
            style=style,
            page=page,
            sgm=True,
            sgexter=True,
        )
    except Exception as e:
        _log.error("[StatsRender] pillowmd 渲染失败: %s", e, exc_info=True)
        return None

    if result.imageType == "gif":
        images = result.images
    else:
        images = [result.image]

    for p in temp_pics:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass

    if not images:
        return None
    final_image = _merge_pages(images)
    buffered = BytesIO()
    final_image.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode("utf-8")


async def save_stats_report_file(
    render_info: RenderInfo,
    sections: Dict[str, Path],
    prefix: str = "report",
    *,
    section_scales: Optional[Dict[str, float]] = None,
    default_scale: float = 0.85,
    page: int = 2,
) -> Optional[str]:
    """渲染并保存到 temp 目录，返回文件路径。"""
    b64 = await render_stats_report(
        render_info,
        sections,
        section_scales=section_scales,
        default_scale=default_scale,
        page=page,
    )
    if not b64:
        return None
    ensure_dirs()
    out = TEMP_PATH / f"{prefix}_{datetime.now().strftime('%Y%m%d%H%M%S')}.png"
    data = base64.b64decode(b64)
    with open(out, "wb") as f:
        f.write(data)
    return str(out)
