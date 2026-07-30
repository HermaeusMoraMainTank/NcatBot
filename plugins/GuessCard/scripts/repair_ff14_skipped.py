"""Retry skipped FF14 bosses that likely have wiki portraits."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_ff14_pool import (  # noqa: E402
    OUT,
    boss_portrait_and_names,
    slug_id,
    stable_id,
)

# Skip obvious add packs / non-character entries
SKIP_PREFIXES = ("软软", "毛毛", "绒绒", "茸茸", "绵绵", "蓬蓬", "柔柔", "戈耳狄")
SKIP_EXACT = {"万魔殿", "狩猎人偶", "爆破型7号哥布林战车"}


def main() -> None:
    chars = json.loads((OUT / "characters.json").read_text(encoding="utf-8"))
    cards = json.loads((OUT / "guess_cards.json").read_text(encoding="utf-8"))
    nicks = json.loads((OUT / "nicknames.json").read_text(encoding="utf-8"))
    skipped = json.loads((OUT / "_skipped.json").read_text(encoding="utf-8"))
    duties = json.loads((OUT / "_duties_raw.json").read_text(encoding="utf-8"))

    have = {c["fullNameChinese"] for c in chars}
    boss_meta: dict[str, dict] = {}
    for d in duties:
        for idx, cn in enumerate(d.get("bosses") or []):
            cn = str(cn).strip()
            bid = None
            ids = d.get("boss_ids") or []
            if idx < len(ids):
                try:
                    bid = int(ids[idx])
                except (TypeError, ValueError):
                    bid = None
            boss_meta.setdefault(cn, {"boss_id": bid, "tag": d.get("tag"), "sources": []})
            boss_meta[cn]["sources"].append(d.get("name") or d.get("title"))
            if bid and not boss_meta[cn].get("boss_id"):
                boss_meta[cn]["boss_id"] = bid

    still = []
    added = 0
    for item in skipped:
        cn = item["cn"]
        if cn in have:
            continue
        if cn in SKIP_EXACT or any(cn.startswith(p) for p in SKIP_PREFIXES):
            still.append(item)
            continue
        print(f"retry {cn}", flush=True)
        detail = boss_portrait_and_names(cn)
        time.sleep(0.8)
        url = detail.get("image_url") or ""
        if not url:
            print(f"  still no image", flush=True)
            still.append({**item, "detail": detail})
            continue
        info = boss_meta.get(cn) or {}
        cid = slug_id(cn, info.get("boss_id"))
        en = detail.get("en") or ""
        jp = detail.get("jp") or ""
        resolved = detail.get("resolved_page") or ""
        aliases = []
        for a in (cn, resolved, en, jp):
            a = (a or "").strip()
            if a and a not in aliases:
                aliases.append(a)
        tag = info.get("tag") or "trial"
        class_name = {"ult": "绝境战", "savage": "零式", "trial": "讨伐"}.get(tag, tag)
        chars.append(
            {
                "characterId": cid,
                "name": cn,
                "fullName": en or cn,
                "fullNameChinese": cn,
                "fullNameJapanese": jp,
                "aliases": aliases,
                "className": class_name,
                "sources": list(dict.fromkeys(info.get("sources") or [])),
            }
        )
        cards.append(
            {
                "id": stable_id(cid, url),
                "characterId": cid,
                "cardRarityType": "rarity_5",
                "assetbundleName": cid,
                "image_url": url,
                "image_source": "huijiwiki",
                "image_file": detail.get("image_file") or "",
                "className": class_name,
            }
        )
        nicks.setdefault("by_id", {})[cid] = aliases[:]
        have.add(cn)
        added += 1
        print(f"  OK {url[:70]}", flush=True)

    meta = json.loads((OUT / "meta.json").read_text(encoding="utf-8"))
    meta["character_count"] = len(chars)
    meta["card_count"] = len(cards)
    meta["skipped_count"] = len(still)

    (OUT / "characters.json").write_text(
        json.dumps(chars, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "guess_cards.json").write_text(
        json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "nicknames.json").write_text(
        json.dumps(nicks, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "_skipped.json").write_text(
        json.dumps(still, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"added={added} total={len(chars)} still_skipped={len(still)}", flush=True)


if __name__ == "__main__":
    main()
