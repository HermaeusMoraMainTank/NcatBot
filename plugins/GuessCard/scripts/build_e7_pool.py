"""Build Epic Seven (e7) pool — Fribbels herodata + best available portraits.

Sources (batched, short timeouts):
1. Fribbels herodata + zh locale (jsDelivr)
2. Portrait preference:
   - epic-seven.fandom.com 「Name big.png」 (~1500px, old roster only)
   - else Fribbels `{code}_su.png` (~200–430×206)
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "resources" / "e7"
OUT.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent": "NcatBot-GuessCard/1.0"}
BASE = "https://cdn.jsdelivr.net/gh/fribbels/Fribbels-Epic-7-Optimizer@main"
HERO_URL = f"{BASE}/data/cache/herodata.json"
ZH_URL = f"{BASE}/data/locales/zh/translation.json"
IMG_SU = f"{BASE}/data/cachedimages/{{code}}_su.png"
FANDOM_API = "https://epic-seven.fandom.com/api.php"


def fetch_json(url: str, timeout: int = 45):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fandom_list_big_titles() -> list[str]:
    titles: list[str] = []
    offset = 0
    for page in range(8):
        api = FANDOM_API + "?" + urllib.parse.urlencode(
            {
                "action": "query",
                "list": "search",
                "srnamespace": "6",
                "srsearch": "big.png",
                "srlimit": "50",
                "sroffset": str(offset),
                "format": "json",
            }
        )
        j = fetch_json(api, timeout=20)
        hits = j.get("query", {}).get("search", [])
        if not hits:
            break
        for it in hits:
            t = it.get("title") or ""
            low = t.lower()
            if low.endswith(" big.png") or low.endswith("_big.png"):
                titles.append(t)
        print(f"  fandom search page {page+1}: hits={len(hits)}", flush=True)
        if "continue" not in j:
            break
        offset = int(j["continue"].get("sroffset", offset + 50))
    return sorted(set(titles))


def fandom_title_to_en(title: str) -> str:
    # File:Ras big.png / File:Vivian_big.png -> Ras / Vivian
    name = title
    if name.startswith("File:"):
        name = name[5:]
    name = re.sub(r"[ _]big\.png$", "", name, flags=re.I)
    return name.strip()


def fandom_resolve_urls(titles: list[str]) -> dict[str, str]:
    """Map English hero name -> image URL."""
    out: dict[str, str] = {}
    batch = 40
    for i in range(0, len(titles), batch):
        chunk = titles[i : i + batch]
        api = FANDOM_API + "?" + urllib.parse.urlencode(
            {
                "action": "query",
                "titles": "|".join(chunk),
                "prop": "imageinfo",
                "iiprop": "url",
                "format": "json",
            }
        )
        j = fetch_json(api, timeout=25)
        pages = j.get("query", {}).get("pages", {})
        for _, p in pages.items():
            t = p.get("title") or ""
            ii = (p.get("imageinfo") or [None])[0]
            if not ii or not ii.get("url"):
                continue
            en = fandom_title_to_en(t)
            out[en] = ii["url"]
        print(
            f"  fandom imageinfo {min(i + batch, len(titles))}/{len(titles)} "
            f"mapped={len(out)}",
            flush=True,
        )
    return out


def main() -> None:
    print("[1/4] fetch Fribbels herodata ...", flush=True)
    hero = fetch_json(HERO_URL, timeout=45)
    if not isinstance(hero, dict):
        raise RuntimeError(f"unexpected herodata type: {type(hero)}")
    print(f"  heroes raw={len(hero)}", flush=True)

    print("[2/4] fetch Fribbels zh names ...", flush=True)
    zh = fetch_json(ZH_URL, timeout=45)
    print(f"  zh keys={len(zh)}", flush=True)

    print("[3/4] index epic-seven.fandom * big.png ...", flush=True)
    big_titles = fandom_list_big_titles()
    fandom_urls = fandom_resolve_urls(big_titles) if big_titles else {}
    print(f"  fandom big urls={len(fandom_urls)}", flush=True)

    playable = []
    for key, h in hero.items():
        if not isinstance(h, dict):
            continue
        code = str(h.get("code") or "").strip()
        en = str(h.get("name") or key).strip()
        rarity = int(h.get("rarity") or 0)
        if not code or not en or rarity < 3:
            continue
        playable.append((code, en, rarity, h.get("role") or "", h.get("attribute") or ""))
    playable.sort(key=lambda x: x[1].lower())
    print(f"  rarity>=3: {len(playable)}", flush=True)

    print("[4/4] write pool JSON ...", flush=True)
    characters = []
    cards = []
    fandom_hits = 0
    batch = 80

    for i in range(0, len(playable), batch):
        chunk = playable[i : i + batch]
        for code, en, rarity, role, attr in chunk:
            cn = (zh.get(en) or en).strip()
            aliases = [en]
            compact = re.sub(r"[^A-Za-z0-9]", "", en)
            if compact and compact.lower() != en.lower():
                aliases.append(compact)
            if cn and cn != en:
                aliases.append(cn)

            image_url = fandom_urls.get(en) or IMG_SU.format(code=code)
            source = "fandom_big" if en in fandom_urls else "fribbels_su"
            if source == "fandom_big":
                fandom_hits += 1

            characters.append(
                {
                    "characterId": code,
                    "name": en.lower().replace(" ", ""),
                    "fullName": en,
                    "fullNameChinese": cn,
                    "aliases": aliases,
                    "className": role,
                    "attribute": attr,
                    "rarity": rarity,
                }
            )
            cards.append(
                {
                    "id": abs(hash(code)) % (10**9),
                    "characterId": code,
                    "cardRarityType": f"rarity_{rarity}",
                    "assetbundleName": code,
                    "image_url": image_url,
                    "image_source": source,
                    "className": role,
                    "attribute": attr,
                }
            )
        done = min(i + batch, len(playable))
        print(f"  progress {done}/{len(playable)} chars={len(characters)}", flush=True)

    seen: set[str] = set()
    uniq_chars = []
    for c in characters:
        if c["characterId"] in seen:
            continue
        seen.add(c["characterId"])
        uniq_chars.append(c)
    uniq_cards = [c for c in cards if c["characterId"] in seen]

    meta = {
        "source": "Fribbels herodata/zh + fandom big (fallback Fribbels _su)",
        "character_count": len(uniq_chars),
        "card_count": len(uniq_cards),
        "fandom_big_count": fandom_hits,
        "fribbels_su_count": len(uniq_cards) - fandom_hits,
        "rarity_min": 3,
        "recent_samples": [
            c["fullNameChinese"]
            for c in uniq_chars
            if c["fullName"]
            in (
                "Notos",
                "Ruiza",
                "Monarch of the Sword Iseria",
                "Abigail",
                "Young Senya",
                "Ras",
            )
        ],
    }
    (OUT / "characters.json").write_text(
        json.dumps(uniq_chars, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "guess_cards.json").write_text(
        json.dumps(uniq_cards, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("DONE", meta, flush=True)


if __name__ == "__main__":
    main()
