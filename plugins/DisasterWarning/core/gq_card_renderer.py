"""
Global Quake 卡片：Jinja2 + Playwright 截图（模板见 resources/card_templates/）。
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from ncatbot.utils import get_log

_log = get_log()

_TEMPLATE_ALIASES = {
    "aurora": "Aurora",
    "darknight": "DarkNight",
    "暗夜": "DarkNight",
    "极光": "Aurora",
}


def _normalize_template(name: str) -> str:
    key = name.strip()
    lower = key.lower()
    return _TEMPLATE_ALIASES.get(lower, key)


def _template_rel_path(template_name: str) -> str:
    folder = _normalize_template(template_name)
    if folder not in ("Aurora", "DarkNight"):
        folder = "Aurora"
    return f"{folder}/global_quake.html"


async def render_global_quake_card_png(
    *,
    context: dict[str, Any],
    card_templates_root: Path,
    template_name: str,
    out_dir: Path,
    playwright_mode: str = "local",
    playwright_server_url: str = "",
) -> Path | None:
    """
    将 Global Quake HTML 模板渲染为 PNG。失败返回 None。
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        _log.warning(
            "[灾害预警] 未安装 playwright，无法生成 Global Quake 卡片图（pip install playwright && playwright install chromium）"
        )
        return None

    rel = _template_rel_path(template_name)
    tpl_path = card_templates_root / rel
    if not tpl_path.is_file():
        _log.error(f"[灾害预警] Global Quake 模板不存在: {tpl_path}")
        return None

    env = Environment(
        loader=FileSystemLoader(str(card_templates_root)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template(rel)
    html = template.render(**context)

    out_dir.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^\w\-.]+", "_", str(context.get("event_id", "gq")))[:80]
    out_path = out_dir / f"gq_card_{safe_id}_{uuid.uuid4().hex[:8]}.png"

    async with async_playwright() as p:
        try:
            if playwright_mode == "remote" and playwright_server_url.strip():
                browser = await p.chromium.connect(playwright_server_url.strip())
            else:
                browser = await p.chromium.launch(headless=True)
        except Exception as e:
            _log.warning(f"[灾害预警] Playwright 启动/连接失败，跳过 GQ 卡片: {e}")
            return None

        try:
            page = await browser.new_page(viewport={"width": 900, "height": 1400})
            await page.set_content(html, wait_until="domcontentloaded", timeout=120_000)
            try:
                await page.wait_for_selector("body.d3-ready", timeout=90_000)
            except Exception:
                _log.debug("[灾害预警] 等待 d3-ready 超时，仍尝试截图")
            await page.screenshot(path=str(out_path), full_page=True)
        finally:
            await browser.close()

    if out_path.is_file() and out_path.stat().st_size > 0:
        return out_path
    return None
