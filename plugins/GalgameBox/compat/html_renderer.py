"""Jinja2 + Playwright HTML → 图片，替代 AstrBot html_renderer。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from jinja2 import Environment, BaseLoader
from ncatbot.utils import get_log

_log = get_log()

_OUTPUT_DIR: Path | None = None


def set_output_dir(path: Path) -> None:
    global _OUTPUT_DIR
    _OUTPUT_DIR = path
    path.mkdir(parents=True, exist_ok=True)


async def render_custom_template(
    tmpl: str,
    data: dict[str, Any],
    return_url: bool = True,
    options: dict | None = None,
) -> str:
    """渲染 HTML 模板为本地图片路径（与 AstrBot 返回 URL 的用法兼容）。"""
    options = options or {}
    out_dir = _OUTPUT_DIR or Path(__file__).resolve().parent.parent / "data" / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    env = Environment(
        loader=BaseLoader(),
        autoescape=False,  # 模板依赖 join("<br>") / 简介中的 HTML 换行
    )
    html = env.from_string(tmpl).render(**data)

    img_type = str(options.get("type", "jpeg")).lower()
    suffix = ".jpg" if img_type in ("jpeg", "jpg") else f".{img_type}"
    out_path = out_dir / f"gal_{uuid.uuid4().hex}{suffix}"

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        _log.warning(
            "[GalgameBox] 未安装 playwright，无法渲染"
            "（pip install playwright && playwright install chromium）"
        )
        raise RuntimeError("endpoints failed: playwright missing")

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception as e:
            _log.warning("[GalgameBox] Playwright 启动失败: %s", e)
            raise RuntimeError(f"endpoints failed: {e}") from e
        try:
            page = await browser.new_page(
                viewport={"width": 900, "height": 1200},
                device_scale_factor=2,
            )
            await page.set_content(html, wait_until="networkidle", timeout=120_000)
            # 按内容高度截全页
            await page.screenshot(
                path=str(out_path), full_page=True, type="jpeg", quality=90
            )
        finally:
            await browser.close()

    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise RuntimeError("endpoints failed: empty screenshot")
    return str(out_path)
