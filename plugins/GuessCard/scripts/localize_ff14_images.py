"""Download FF14 boss portraits via curl (huiji CDN blocks Python TLS).

Rewrites guess_cards.json image_url to local relative paths: images/<file>.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse

OUT = Path(__file__).resolve().parent.parent / "resources" / "ff14"
IMG_DIR = OUT / "images"
CARDS = OUT / "guess_cards.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
REFERER = "https://ff14.huijiwiki.com/"


def safe_name(url: str, fallback: str) -> str:
    path = unquote(urlparse(url).path)
    name = Path(path).name or fallback
    name = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", name)
    if not re.search(r"\.(png|jpg|jpeg|webp)$", name, re.I):
        name += ".png"
    return name


def curl_download(url: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    cmd = [
        "curl",
        "-sL",
        "--fail",
        "-A",
        UA,
        "-e",
        REFERER,
        "--connect-timeout",
        "20",
        "--max-time",
        "60",
        "-o",
        str(tmp),
        url,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except FileNotFoundError:
        print("curl not found", flush=True)
        return False
    except subprocess.TimeoutExpired:
        print(f"timeout {url}", flush=True)
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        return False
    if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 100:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        print(f"fail [{r.returncode}] {url}", flush=True)
        return False
    tmp.replace(dest)
    return True


def main() -> None:
    cards = json.loads(CARDS.read_text(encoding="utf-8"))
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    fail = 0
    for i, card in enumerate(cards):
        url = (card.get("image_url") or "").strip()
        # already local
        if url and not url.startswith(("http://", "https://")):
            local = OUT / url
            if local.exists():
                ok += 1
                continue
        if not url.startswith(("http://", "https://")):
            fail += 1
            continue
        fname = safe_name(url, f"card_{card.get('id')}")
        stored = (card.get("image_file") or "").strip()
        if stored:
            fname = safe_name(
                stored if "/" in stored or "\\" in stored else f"x/{stored}", fname
            )
        dest = IMG_DIR / fname
        rel = f"images/{fname}"
        if dest.exists() and dest.stat().st_size > 100:
            card["image_url"] = rel
            card["image_remote"] = url
            ok += 1
            continue
        print(f"[{i + 1}/{len(cards)}] {fname}", flush=True)
        if curl_download(url, dest):
            card["image_url"] = rel
            card["image_remote"] = url
            ok += 1
            print(f"  ok {dest.stat().st_size}", flush=True)
        else:
            fail += 1
    CARDS.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"done ok={ok} fail={fail}", flush=True)


if __name__ == "__main__":
    main()
