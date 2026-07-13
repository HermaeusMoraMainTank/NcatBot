import base64
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional

import pillowmd

from ncatbot.utils import get_log

from .mdstyle_title import save_composed_section
from .paths import RESOURCES_PATH, TEMP_PATH, ensure_dirs

_log = get_log("StatsRender")


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
    使用 pillowmd 将多个 section 合成为一张双栏长图（单次 MdToImage，纵向随内容增高）。
    每个 section 的 mdstyle H2 标题与图表内容预先合成，避免标题与内容被分页拆开。
    """
    ensure_dirs()
    if not sections:
        return None
    resources_path = RESOURCES_PATH
    if not (resources_path / "mdstyle").exists():
        _log.error("[StatsRender] mdstyle 资源缺失: %s", resources_path)
        return None

    pillowmd.Setting.QUICK_IMAGE_PATH = TEMP_PATH
    composed_paths: List[Path] = []
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
        composed = save_composed_section(Path(img_path), name)
        composed_paths.append(composed)
        scale = (section_scales or {}).get(name, default_scale)
        parts.append(f"!sgm[{composed.name}|{scale}]")

    markdown_text = "\n".join(parts)
    style_path = resources_path / "mdstyle"
    try:
        style = pillowmd.LoadMarkdownStyles(str(style_path))
        result = await pillowmd.MdToImage(
            text=markdown_text,
            style=style,
            page=page,
            autoPage=False,
            sgm=True,
            sgexter=True,
        )
    except Exception as e:
        _log.error("[StatsRender] pillowmd 渲染失败: %s", e, exc_info=True)
        return None

    for p in composed_paths:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    for name, img_path in sections.items():
        if img_path is None:
            continue
        try:
            Path(img_path).unlink(missing_ok=True)
        except OSError:
            pass

    if not result.image:
        return None

    final_image = result.image
    if result.imageType == "gif" and result.images:
        final_image = result.images[0]

    buffered = BytesIO()
    final_image.convert("RGB").save(buffered, format="PNG")
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
