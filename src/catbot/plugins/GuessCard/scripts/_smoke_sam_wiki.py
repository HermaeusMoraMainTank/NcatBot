"""Smoke: render wiki-style card for 改良型月使武士刀."""

from __future__ import annotations

import json
import sys
import urllib.request
from io import BytesIO
from pathlib import Path

from PIL import Image as PILImage

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
sys.path.insert(0, str(ROOT))

from sam_item_card import (  # noqa: E402
    format_wiki_text,
    normalize_wiki_item,
    render_wiki_item_card,
)

sample = REPO / "_sam_item_sample.json"
if not sample.exists():
    sample = Path("_sam_item_sample.json")
raw = json.loads(sample.read_text(encoding="utf-8"))
info = normalize_wiki_item(raw)
print(format_wiki_text(info))
print("---")

icon_id = int(raw.get("图标ID") or 36536)
padded = f"{icon_id:06d}"
url = f"https://cafemaker.wakingsands.com/i/{padded[:3]}000/{padded}_hr1.png"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=20) as r:
    icon = PILImage.open(BytesIO(r.read()))
out = ROOT / "data/output/_sam_wiki_smoke.png"
render_wiki_item_card(
    info,
    icon,
    font_path=ROOT / "resources/pjsk/font.ttf",
    out_path=out,
)
print("wrote", out, out.stat().st_size)
