"""Build FGO guess-card pool from Atlas Academy + Chaldea (+ optional Mooncell aliases)."""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "resources" / "fgo"
OUT.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent": "NcatBot-GuessCard/1.0"}


def fetch_json(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fetch_text(url: str, timeout: int = 60) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")


def main() -> None:
    print("fetch atlas basic_servant...")
    servants = fetch_json(
        "https://api.atlasacademy.io/export/JP/basic_servant.json"
    )
    print("fetch chaldea svt_names...")
    name_map = fetch_json(
        "https://raw.githubusercontent.com/chaldea-center/chaldea-data/main/mappings/svt_names.json"
    )

    mooncell_aliases: dict[int, dict] = {}
    print("fetch mooncell list for aliases...")
    try:
        html = fetch_text(
            "https://fgo.wiki/w/%E5%BE%AE%E4%BB%B6:ServantsList/data"
        )
        rows = re.findall(
            r"(\d+),(\d+),([^,\n]+),([^,\n]+),([^,\n]+),([^,\n]*),([^,\n]*)",
            html,
        )
        print("mooncell regex rows", len(rows))
        for row in rows:
            col_no, _stars, name_cn, _name_jp, name_en, _link, name_other = row
            aliases = []
            for part in re.split(r"[&/、,，]", name_other or ""):
                part = part.strip()
                if part and part not in (name_cn, name_en):
                    aliases.append(part)
            mooncell_aliases[int(col_no)] = {
                "cn": name_cn.strip(),
                "en": name_en.strip(),
                "aliases": aliases,
            }
    except Exception as e:
        print("mooncell failed", type(e).__name__, e)

    characters = []
    cards = []
    seen_chars: set[int] = set()
    valid_types = {"normal", "heroine"}

    for svt in servants:
        if svt.get("type") not in valid_types:
            continue
        col = int(svt.get("collectionNo") or 0)
        if col <= 0:
            continue
        rarity = int(svt.get("rarity") or 0)
        if rarity < 3:
            continue

        sid = int(svt["id"])
        jp = svt.get("originalName") or svt.get("name") or ""
        mapped = name_map.get(jp) or name_map.get(svt.get("name") or "") or {}
        cn = (mapped.get("CN") if isinstance(mapped, dict) else None) or ""
        en = (mapped.get("NA") if isinstance(mapped, dict) else None) or ""
        mc = mooncell_aliases.get(col) or {}
        if not cn:
            cn = mc.get("cn") or en or jp
        if not en:
            en = mc.get("en") or ""

        aliases: list[str] = []
        if en:
            aliases.append(en)
            short = re.sub(r"[^A-Za-z0-9]", "", en)
            if short and short.lower() != en.lower():
                aliases.append(short)
        for a in mc.get("aliases") or []:
            if a and a not in aliases:
                aliases.append(a)

        if sid not in seen_chars:
            seen_chars.add(sid)
            name_key = (en or jp or str(col)).lower().replace(" ", "")
            characters.append(
                {
                    "characterId": sid,
                    "collectionNo": col,
                    "name": name_key,
                    "fullName": jp,
                    "fullNameChinese": cn,
                    "aliases": aliases,
                    "className": svt.get("className"),
                    "rarity": rarity,
                }
            )

        ascension_urls = [
            f"https://static.atlasacademy.io/JP/CharaGraph/{sid}/{sid}a@1.png",
            f"https://static.atlasacademy.io/JP/CharaGraph/{sid}/{sid}a@2.png",
            f"https://static.atlasacademy.io/JP/CharaGraph/{sid}/{sid}b@1.png",
        ]
        for i, url in enumerate(ascension_urls, start=1):
            cards.append(
                {
                    "id": sid * 10 + i,
                    "characterId": sid,
                    "cardRarityType": f"rarity_{rarity}",
                    "assetbundleName": f"fgo_{sid}_asc{i}",
                    "image_url": url,
                    "face_url": svt.get("face"),
                    "ascension": i,
                    "className": svt.get("className"),
                }
            )

    (OUT / "characters.json").write_text(
        json.dumps(characters, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "guess_cards.json").write_text(
        json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    meta = {
        "source": "Atlas Academy + Chaldea + Mooncell",
        "character_count": len(characters),
        "card_count": len(cards),
        "rarity_min": 3,
    }
    (OUT / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "done chars",
        len(characters),
        "cards",
        len(cards),
        "mooncell",
        len(mooncell_aliases),
    )
    if characters:
        print("sample", characters[0]["fullNameChinese"], characters[0]["aliases"][:5])
        if len(characters) > 1:
            print("sample2", characters[1]["fullNameChinese"])


if __name__ == "__main__":
    main()
