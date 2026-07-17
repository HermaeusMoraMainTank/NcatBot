"""构建 影之诗 / 碧蓝幻想 / 蔚蓝档案 卡池。"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "resources"
UA = {"User-Agent": "Mozilla/5.0 NcatBot-GuessCard/1.0"}

GBF_ELEM_CN = {
    "Fire": "火",
    "Water": "水",
    "Earth": "土",
    "Wind": "风",
    "Light": "光",
    "Dark": "暗",
}


def fetch(url: str, timeout: int = 90) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_json(url: str, timeout: int = 90):
    return json.loads(fetch(url, timeout))


def write_pool(pool_id: str, characters: list, cards: list, meta: dict) -> None:
    out = ROOT / pool_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "characters.json").write_text(
        json.dumps(characters, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "guess_cards.json").write_text(
        json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    meta = {
        **meta,
        "character_count": len(characters),
        "card_count": len(cards),
    }
    (out / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  WRITE {pool_id}: chars={len(characters)} cards={len(cards)}", flush=True)


def build_ba() -> None:
    """蔚蓝档案：SchaleDB 学生立绘。"""
    print("[BA] SchaleDB students ...", flush=True)
    cn_list = fetch_json(
        "https://cdn.jsdelivr.net/gh/lonqie/SchaleDB@main/data/cn/students.json",
        120,
    )
    en_list = fetch_json(
        "https://cdn.jsdelivr.net/gh/lonqie/SchaleDB@main/data/en/students.json",
        120,
    )
    enmap = {int(s["Id"]): s for s in en_list if s.get("Id") is not None}

    characters, cards = [], []
    for s in cn_list:
        sid = s.get("Id")
        if sid is None:
            continue
        # 任一服已实装即可
        released = s.get("IsReleased") or []
        if not any(released):
            continue
        en = enmap.get(int(sid), {})
        cn_name = (s.get("Name") or "").strip()
        personal = (s.get("PersonalName") or "").strip()
        family = (s.get("FamilyName") or "").strip()
        en_name = (en.get("Name") or "").strip()
        path = (en.get("PathName") or s.get("PathName") or "").strip()
        if not cn_name and not en_name:
            continue

        aliases = []
        for a in (cn_name, personal, family, en_name, path):
            if a and a not in aliases:
                aliases.append(a)
        # 去括号皮肤后缀的本体名：爱露（正月）→ 爱露
        for src in (cn_name, en_name):
            m = re.match(r"^(.+?)[（(]", src or "")
            if m:
                base = m.group(1).strip()
                if base and base not in aliases:
                    aliases.append(base)

        display = cn_name or en_name
        cid = str(sid)
        characters.append(
            {
                "characterId": cid,
                "name": display,
                "fullName": display,
                "fullNameChinese": cn_name,
                "aliases": aliases,
                "className": (s.get("School") or ""),
                "rarity": int(s.get("StarGrade") or 0),
            }
        )
        cards.append(
            {
                "id": int(sid),
                "characterId": cid,
                "cardRarityType": f"star_{s.get('StarGrade') or 0}",
                "assetbundleName": path or cid,
                "image_url": (
                    "https://cdn.jsdelivr.net/gh/lonqie/SchaleDB@main/"
                    f"images/student/portrait/{sid}.webp"
                ),
                "className": s.get("School") or "",
            }
        )

    write_pool(
        "ba",
        characters,
        cards,
        {
            "source": "SchaleDB students + portrait webp",
            "display": "蔚蓝档案",
        },
    )


def build_sv() -> None:
    """影之诗：Portal API 随从（金+传说），卡面图。"""
    print("[SV] shadowverse-portal cards ...", flush=True)
    tw = fetch_json(
        "https://shadowverse-portal.com/api/v1/cards?format=json&lang=zh-tw", 150
    )
    en = fetch_json(
        "https://shadowverse-portal.com/api/v1/cards?format=json&lang=en", 150
    )
    ja = fetch_json(
        "https://shadowverse-portal.com/api/v1/cards?format=json&lang=ja", 150
    )
    twc = (tw.get("data") or {}).get("cards") or []
    enmap = {c["card_id"]: c for c in ((en.get("data") or {}).get("cards") or [])}
    jamap = {c["card_id"]: c for c in ((ja.get("data") or {}).get("cards") or [])}

    # char_type 1=随从；rarity 3=金 4=传说
    candidates = [
        c
        for c in twc
        if c.get("char_type") == 1 and int(c.get("rarity") or 0) >= 3
    ]
    seen_base: set = set()
    characters, cards = [], []
    for c in candidates:
        base = c.get("base_card_id") or c.get("card_id")
        if base in seen_base:
            continue
        seen_base.add(base)
        cid = int(c["card_id"])
        tw_name = (c.get("card_name") or "").strip()
        en_name = (enmap.get(cid, {}).get("card_name") or "").strip()
        ja_name = (jamap.get(cid, {}).get("card_name") or "").strip()
        if not tw_name and not en_name:
            continue

        aliases = []
        for a in (tw_name, en_name, ja_name):
            if a and a not in aliases:
                aliases.append(a)
        if en_name:
            compact = re.sub(r"[^A-Za-z0-9]", "", en_name)
            if compact and compact not in aliases:
                aliases.append(compact)

        display = tw_name or en_name
        sid = str(cid)
        characters.append(
            {
                "characterId": sid,
                "name": display,
                "fullName": display,
                "fullNameChinese": tw_name,
                "fullNameJapanese": ja_name,
                "aliases": aliases,
                "className": str(c.get("clan") or ""),
                "rarity": int(c.get("rarity") or 0),
            }
        )
        cards.append(
            {
                "id": cid,
                "characterId": sid,
                "cardRarityType": f"rarity_{c.get('rarity')}",
                "assetbundleName": sid,
                # 卡面含卡名 → 配置里对该池仅开模糊
                "image_url": (
                    "https://shadowverse-portal.com/image/card/phase2/common/"
                    f"C/C_{cid}.png"
                ),
                "className": str(c.get("clan") or ""),
            }
        )
        if len(characters) % 300 == 0:
            print(f"  progress {len(characters)}", flush=True)

    write_pool(
        "sv",
        characters,
        cards,
        {
            "source": "shadowverse-portal API (zh-tw/en/ja) followers rarity>=3",
            "display": "影之诗",
            "note": "card art includes name; use blur-only effects",
        },
    )


def _gbf_cargo_all() -> list[dict]:
    """分页拉齐 gbf.wiki Cargo 角色表。"""
    fields = "id,name,jpname,element,rarity"
    rows: list[dict] = []
    offset = 0
    while True:
        url = (
            "https://gbf.wiki/Special:CargoExport?"
            + urllib.parse.urlencode(
                {
                    "tables": "characters",
                    "fields": fields,
                    "format": "json",
                    "limit": "500",
                    "offset": str(offset),
                }
            )
        )
        chunk = fetch_json(url, 90)
        if not chunk:
            break
        rows.extend(chunk)
        print(f"  cargo offset={offset} +{len(chunk)} total={len(rows)}", flush=True)
        if len(chunk) < 500:
            break
        offset += 500
        if offset > 5000:
            break
    return rows


def build_gbf() -> None:
    """碧蓝幻想：wiki Cargo + 官方 CDN 立绘。"""
    print("[GBF] cargo characters ...", flush=True)
    rows = _gbf_cargo_all()
    characters, cards = [], []
    seen_id: set = set()
    for r in rows:
        rarity = str(r.get("rarity") or "").upper()
        if rarity not in {"SSR", "SR"}:
            continue
        cid_raw = r.get("id")
        if cid_raw is None:
            continue
        cid = str(int(cid_raw)) if str(cid_raw).isdigit() else str(cid_raw)
        if cid in seen_id:
            continue
        seen_id.add(cid)

        en = (r.get("name") or "").strip()
        jp = (r.get("jpname") or "").strip()
        elem = (r.get("element") or "").strip()
        elem_cn = GBF_ELEM_CN.get(elem, "")
        if not en:
            continue

        aliases = [en]
        if jp and jp not in aliases:
            aliases.append(jp)
        compact = en.replace(" ", "")
        if compact not in aliases:
            aliases.append(compact)
        if elem_cn:
            aliases.append(f"{elem_cn}{en}")
            aliases.append(f"{en}({elem_cn})")

        display = en
        characters.append(
            {
                "characterId": cid,
                "name": display,
                "fullName": display,
                "fullNameChinese": "",
                "fullNameJapanese": jp,
                "aliases": aliases,
                "className": elem,
                "attribute": elem.lower() if elem else "",
                "rarity": 5 if rarity == "SSR" else 4,
            }
        )
        cards.append(
            {
                "id": abs(hash(cid)) % (10**9),
                "characterId": cid,
                "cardRarityType": rarity.lower(),
                "assetbundleName": cid,
                # f/ 全身立绘，无卡名文字
                "image_url": (
                    "https://prd-game-a-granbluefantasy.akamaized.net/"
                    f"assets/img/sp/assets/npc/f/{cid}_01.jpg"
                ),
                "className": elem,
            }
        )

    write_pool(
        "gbf",
        characters,
        cards,
        {
            "source": "gbf.wiki Cargo + akamaized npc/f art",
            "display": "碧蓝幻想",
            "rarity": "SSR+SR",
        },
    )


def main() -> None:
    import sys

    targets = sys.argv[1:] or ["ba", "sv", "gbf"]
    for t in targets:
        if t == "ba":
            build_ba()
        elif t == "sv":
            build_sv()
        elif t == "gbf":
            build_gbf()
        else:
            raise SystemExit(f"unknown {t}")


if __name__ == "__main__":
    main()
