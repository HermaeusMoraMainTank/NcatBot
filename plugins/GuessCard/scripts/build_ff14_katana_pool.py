"""Build FF14 Samurai katana guess-card pool from CN Item.csv.

Source: thewakingsands/ffxiv-datamining-cn Item.csv (ItemUICategory=96 武士刀)
Icons: xivapi / cafemaker icon CDN (hr1 preferred)
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "resources" / "sam"
OUT.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent": "NcatBot-GuessCard/1.0 (FF14 katana pool)"}
ITEM_CSV_URL = (
    "https://raw.githubusercontent.com/thewakingsands/"
    "ffxiv-datamining-cn/master/Item.csv"
)
ICON_BASES = [
    "https://cafemaker.wakingsands.com/i",
    "https://xivapi.com/i",
]

RARITY_CN = {
    1: "普通",
    2: "稀有",
    3: "上品",
    4: "极品",
    7: "绝品",
}


def fetch(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def icon_path(icon_id: int) -> str:
    """XIVAPI icon folder/filename from numeric Icon id."""
    # pad to 6 digits with leading 0
    padded = f"{int(icon_id):06d}"
    folder = padded[:3] + "000"
    return f"{folder}/{padded}"


def icon_urls(icon_id: int) -> list[str]:
    rel = icon_path(icon_id)
    urls: list[str] = []
    for base in ICON_BASES:
        urls.append(f"{base}/{rel}_hr1.png")
        urls.append(f"{base}/{rel}.png")
    return urls


def stable_id(*parts: str) -> int:
    h = hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def should_skip(name: str, ilvl: int, elvl: int) -> bool:
    if not name or not name.strip():
        return True
    if "复制品" in name:
        return True
    if elvl <= 1 and ilvl <= 1:
        return True
    return False


def parse_item_csv(text: str) -> list[dict]:
    lines = text.splitlines()
    # SaintCoinach: row0 keys, row1 headers, row2 types, data from 3
    headers = next(csv.reader([lines[1]]))
    ui_idx = headers.index("ItemUICategory")
    name_idx = headers.index("Name")
    icon_idx = headers.index("Icon")
    ilvl_idx = headers.index("Level{Item}")
    elvl_idx = headers.index("Level{Equip}")
    rarity_idx = headers.index("Rarity")
    # optional combat stats
    dphys_idx = headers.index("Damage{Phys}") if "Damage{Phys}" in headers else None
    dmag_idx = headers.index("Damage{Mag}") if "Damage{Mag}" in headers else None

    rows: list[dict] = []
    for row in csv.reader(lines[3:]):
        if len(row) <= max(ui_idx, name_idx, icon_idx, ilvl_idx, elvl_idx, rarity_idx):
            continue
        if row[ui_idx] != "96":
            continue
        try:
            iid = int(row[0])
            ilvl = int(row[ilvl_idx] or 0)
            elvl = int(row[elvl_idx] or 0)
            rarity = int(row[rarity_idx] or 1)
            icon = int(float(row[icon_idx])) if row[icon_idx] else 0
        except (TypeError, ValueError):
            continue
        name = (row[name_idx] or "").strip()
        if should_skip(name, ilvl, elvl):
            continue
        dphys = 0
        dmag = 0
        if dphys_idx is not None and len(row) > dphys_idx:
            try:
                dphys = int(row[dphys_idx] or 0)
            except ValueError:
                dphys = 0
        if dmag_idx is not None and len(row) > dmag_idx:
            try:
                dmag = int(row[dmag_idx] or 0)
            except ValueError:
                dmag = 0
        rows.append(
            {
                "id": iid,
                "name": name,
                "ilvl": ilvl,
                "elvl": elvl,
                "rarity": rarity,
                "icon": icon,
                "damage_phys": dphys,
                "damage_mag": dmag,
            }
        )
    # sort by ilvl desc then name
    rows.sort(key=lambda x: (-x["ilvl"], -x["elvl"], x["name"]))
    return rows


def main() -> None:
    cache = OUT / "_Item_cn.csv"
    if cache.exists() and cache.stat().st_size > 1_000_000:
        print("use cached Item.csv", flush=True)
        text = cache.read_text(encoding="utf-8-sig")
    else:
        print("download Item.csv…", flush=True)
        raw = fetch(ITEM_CSV_URL)
        cache.write_bytes(raw)
        text = raw.decode("utf-8-sig")

    items = parse_item_csv(text)
    print(f"katana items: {len(items)}", flush=True)

    characters: list[dict] = []
    cards: list[dict] = []
    nick_by_id: dict[str, list[str]] = {}

    for it in items:
        cid = f"i{it['id']}"
        cn = it["name"]
        rarity = it["rarity"]
        rarity_label = RARITY_CN.get(rarity, f"品质{rarity}")
        aliases = [cn]
        # short alias without · separators
        compact = re.sub(r"[\s·・‧•]+", "", cn)
        if compact != cn:
            aliases.append(compact)

        characters.append(
            {
                "characterId": cid,
                "name": cn,
                "fullName": cn,
                "fullNameChinese": cn,
                "aliases": aliases,
                "className": "武士刀",
                "rarity": rarity,
                "rarityLabel": rarity_label,
                "itemLevel": it["ilvl"],
                "equipLevel": it["elvl"],
                "itemId": it["id"],
                "damagePhys": it["damage_phys"],
                "damageMag": it["damage_mag"],
                "iconId": it["icon"],
            }
        )
        urls = icon_urls(it["icon"]) if it["icon"] else []
        cards.append(
            {
                "id": stable_id(cid, str(it["icon"])),
                "characterId": cid,
                "cardRarityType": f"rarity_{min(max(rarity, 1), 5)}",
                "assetbundleName": cid,
                "image_url": urls[0] if urls else "",
                "image_urls": urls,
                "image_source": "xivapi_icon",
                "className": "武士刀",
                "itemLevel": it["ilvl"],
                "equipLevel": it["elvl"],
            }
        )
        nick_by_id[cid] = aliases[:]

    # drop entries without icon
    keep_ids = {c["characterId"] for c in cards if c.get("image_url")}
    characters = [c for c in characters if c["characterId"] in keep_ids]
    cards = [c for c in cards if c["characterId"] in keep_ids]
    nick_by_id = {k: v for k, v in nick_by_id.items() if k in keep_ids}

    meta = {
        "source": "thewakingsands/ffxiv-datamining-cn Item.csv + xivapi icons",
        "display": "FF14武士刀",
        "character_count": len(characters),
        "card_count": len(cards),
        "item_ui_category": 96,
        "notes": "猜刀名；揭晓时附品级/装等/品质；图标为游戏 Icon",
    }
    (OUT / "characters.json").write_text(
        json.dumps(characters, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "guess_cards.json").write_text(
        json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "nicknames.json").write_text(
        json.dumps({"by_id": nick_by_id, "by_name": {}}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUT / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"done chars={len(characters)} cards={len(cards)} "
        f"sample={characters[0]['fullNameChinese'] if characters else None}",
        flush=True,
    )


if __name__ == "__main__":
    main()
