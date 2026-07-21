"""HTML → PNG 渲染（关系图 / RBQ 排行）。"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from jinja2 import Environment, BaseLoader, select_autoescape
from ncatbot.utils import get_log

_log = get_log()

PLUGIN_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = PLUGIN_DIR / "template"
VENDOR_DIR = PLUGIN_DIR / "vendor"


def _load_vis_js() -> str:
    path = VENDOR_DIR / "vis-network.min.js"
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


def _render_template(name: str, context: dict[str, Any]) -> str:
    path = TEMPLATE_DIR / name
    raw = path.read_text(encoding="utf-8")
    env = Environment(
        loader=BaseLoader(),
        autoescape=select_autoescape(["html", "xml"]),
    )
    return env.from_string(raw).render(**context)


async def _screenshot_html(
    html: str,
    *,
    out_path: Path,
    width: int,
    height: int,
    wait_selector: str | None = None,
    wait_ms: int = 1500,
) -> Path | None:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        _log.warning(
            "[TodayWaifu] 未安装 playwright，无法渲染图片"
            "（pip install playwright && playwright install chromium）"
        )
        return None

    out_path.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception as e:
            _log.warning("[TodayWaifu] Playwright 启动失败: %s", e)
            return None
        try:
            page = await browser.new_page(
                viewport={"width": width, "height": height},
                device_scale_factor=2,
            )
            await page.set_content(html, wait_until="domcontentloaded", timeout=120_000)
            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=90_000)
                except Exception:
                    _log.debug("[TodayWaifu] 等待选择器超时: %s", wait_selector)
            if wait_ms > 0:
                await page.wait_for_timeout(wait_ms)
            await page.screenshot(path=str(out_path), full_page=False)
        finally:
            await browser.close()

    if out_path.is_file() and out_path.stat().st_size > 0:
        return out_path
    return None


async def render_relation_graph(
    *,
    group_id: str,
    group_name: str,
    records: list[dict[str, Any]],
    user_map: dict[str, str],
    iterations: int,
    out_dir: Path,
) -> Path | None:
    unique_nodes: set[str] = set()
    for r in records:
        unique_nodes.add(str(r.get("user_id")))
        unique_nodes.add(str(r.get("wife_id")))
    node_count = len(unique_nodes)
    # 离散簇多时纵向更易铺开；给标签/边留余量，避免截断
    width = 1920
    height = 1200 + max(0, node_count - 8) * 80

    html = _render_template(
        "graph_template.html",
        {
            "vis_js_content": _load_vis_js(),
            "group_id": group_id,
            "group_name": group_name,
            "user_map_json": json.dumps(user_map, ensure_ascii=False),
            "records_json": json.dumps(records, ensure_ascii=False),
            "iterations": int(iterations),
        },
    )
    safe = re.sub(r"[^\w\-]+", "_", str(group_id))[:40]
    out_path = out_dir / f"waifu_graph_{safe}_{uuid.uuid4().hex[:8]}.png"
    return await _screenshot_html(
        html,
        out_path=out_path,
        width=width,
        height=height,
        wait_selector="body.graph-ready",
        wait_ms=1200,
    )


async def render_rbq_ranking(
    *,
    title: str,
    ranking: list[dict[str, Any]],
    out_dir: Path,
) -> Path | None:
    html = _render_template(
        "rbq_ranking.html",
        {"title": title, "ranking": ranking},
    )
    height = 90 + max(1, len(ranking)) * 62 + 40
    out_path = out_dir / f"waifu_rbq_{uuid.uuid4().hex[:8]}.png"
    return await _screenshot_html(
        html,
        out_path=out_path,
        width=400,
        height=height,
        wait_ms=300,
    )
