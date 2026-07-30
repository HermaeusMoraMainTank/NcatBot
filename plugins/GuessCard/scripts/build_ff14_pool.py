"""Build FF14 GuessCard pool from 灰机 wiki (trials / savage / ultimate bosses).

Rules (product):
- Trials: keep 歼殛战; keep 歼灭战 only when no matching 歼殛战 (same boss).
- Savage: all floors (零式).
- Ultimate: all phases listed in BOSS.中文名.
- Dedupe characters by Chinese boss name; one portrait card each.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, unquote

OUT = Path(__file__).resolve().parent.parent / "resources" / "ff14"
OUT.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent": "NcatBot-GuessCard/1.0 (FF14 pool builder)"}
API = "https://ff14.huijiwiki.com/api.php"


def fetch(url: str, timeout: int = 40) -> bytes:
    last: Exception | None = None
    for i in range(6):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (403, 429, 503, 500):
                wait = 1.5 * (i + 1)
                print(f"  http {e.code}, sleep {wait:.1f}s", flush=True)
                time.sleep(wait)
                continue
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.2 * (i + 1))
    raise RuntimeError(f"fetch failed: {url}") from last


def jget(url: str):
    return json.loads(fetch(url))


def parse_page(page: str, prop: str = "wikitext") -> dict:
    url = f"{API}?action=parse&page={quote(page)}&prop={prop}&format=json"
    d = jget(url)
    if "error" in d:
        raise RuntimeError(d["error"])
    return d["parse"]


def hub_duty_titles(hub: str) -> list[str]:
    html = parse_page(hub, prop="text")["text"]["*"]
    pattern = re.compile(r'href="/wiki/([^"#?]+)"')
    titles = [unquote(t).replace("_", " ") for t in pattern.findall(html)]
    return list(dict.fromkeys(titles))


def pick_trial_titles(titles: list[str]) -> list[str]:
    """Prefer 歼殛战; keep 歼灭战 only if no extreme twin. Skip 幻巧战."""
    extremes: list[str] = []
    normals: list[str] = []
    for t in titles:
        if "幻巧" in t:
            continue
        if t.endswith("歼殛战"):
            extremes.append(t)
        elif t.endswith("歼灭战"):
            normals.append(t)
    extreme_bases = {t[: -len("歼殛战")] for t in extremes}
    picked = list(extremes)
    for t in normals:
        if t[: -len("歼灭战")] in extreme_bases:
            continue
        picked.append(t)
    return picked


def pick_savage_titles(titles: list[str]) -> list[str]:
    return [t for t in titles if "零式" in t]


def pick_ultimate_titles(titles: list[str]) -> list[str]:
    out = []
    for t in titles:
        if "绝境" not in t:
            continue
        if t in ("绝境战", "绝境战武器"):
            continue
        out.append(t)
    return out


def resolve_instance_id(title: str) -> int | None:
    try:
        wt = parse_page(title, prop="wikitext")["wikitext"]["*"]
    except Exception as e:  # noqa: BLE001
        print(f"  resolve fail {title}: {e}", flush=True)
        return None
    m = re.search(r"\{\{副本页\|id=(\d+)", wt)
    return int(m.group(1)) if m else None


def fetch_instance(pid: int) -> dict | None:
    page = f"Data:Instance/{pid}.json"
    try:
        wt = parse_page(page, prop="wikitext")["wikitext"]["*"].strip()
        return json.loads(wt)
    except Exception as e:  # noqa: BLE001
        print(f"  instance fail {pid}: {e}", flush=True)
        return None


def image_url(filename: str) -> str | None:
    fn = filename.strip()
    if not re.search(r"\.(png|jpg|jpeg|webp)$", fn, re.I):
        fn = fn + ".png"
    url = (
        f"{API}?action=query&titles={quote('File:' + fn)}"
        "&prop=imageinfo&iiprop=url|size&format=json"
    )
    d = jget(url)
    for p in d.get("query", {}).get("pages", {}).values():
        ii = p.get("imageinfo")
        if ii and ii[0].get("url"):
            return ii[0]["url"]
    return None


def _is_junk_image(fn: str) -> bool:
    low = fn.lower()
    if low.endswith(".ogg") or low.endswith(".mp3"):
        return True
    if low.startswith("060") or low.startswith("061") or low.startswith("065"):
        return True
    if "icon" in low or low.startswith("职业") or "lodestone" in low or "garland" in low:
        return True
    if low.startswith("112") and low.endswith(".png"):  # duty journal icons often
        return True
    return False


def boss_portrait_and_names(boss_cn: str) -> dict:
    """Return {en, jp, image_url, image_file} from monster page (follow redirects)."""
    out: dict = {"cn": boss_cn, "en": "", "jp": "", "image_url": "", "image_file": ""}
    page = boss_cn
    wt = ""
    imgs: list[str] = []
    for _ in range(4):
        try:
            p = parse_page(page, prop="wikitext|images")
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e)
            return out
        wt = p.get("wikitext", {}).get("*", "")
        imgs = p.get("images") or []
        m = re.match(r"^#重定向\s*\[\[([^\|\]]+)", wt.strip())
        if not m:
            m = re.match(r"^#redirect\s*\[\[([^\|\]]+)", wt.strip(), re.I)
        if m:
            page = m.group(1).strip()
            time.sleep(0.25)
            continue
        break
    out["resolved_page"] = page

    def field(key: str) -> str:
        m = re.search(rf"\|\s*{key}\s*=\s*([^\|\}}\n]+)", wt)
        return (m.group(1).strip() if m else "") or ""

    out["en"] = field("ENname") or field("enname")
    out["jp"] = field("JPname") or field("jpname")
    img_field = field("img")

    candidates: list[str] = []
    if img_field:
        candidates.append(img_field)
    for fn in imgs:
        if _is_junk_image(fn):
            continue
        candidates.append(fn)

    seen: set[str] = set()
    for c in candidates:
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            u = image_url(c)
        except Exception as e:  # noqa: BLE001
            print(f"    img fail {c}: {e}", flush=True)
            time.sleep(0.4)
            continue
        time.sleep(0.25)
        if u:
            out["image_url"] = u
            out["image_file"] = (
                c if re.search(r"\.(png|jpg|jpeg|webp)$", c, re.I) else c + ".png"
            )
            break
    return out


def stable_id(*parts: str) -> int:
    h = hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def slug_id(cn: str, boss_id: int | None) -> str:
    if boss_id:
        return f"b{boss_id}"
    return f"n{stable_id(cn)}"


def main() -> None:
    print("1) hub titles…", flush=True)
    trial_hub = hub_duty_titles("讨伐歼灭战")
    time.sleep(0.8)
    raid_hub = hub_duty_titles("大型任务")
    time.sleep(0.8)
    ult_hub = hub_duty_titles("绝境战")

    trials = pick_trial_titles(trial_hub)
    savages = pick_savage_titles(raid_hub)
    ultimates = pick_ultimate_titles(ult_hub)
    print(
        f"  trials={len(trials)} savages={len(savages)} ultimates={len(ultimates)}",
        flush=True,
    )

    duties: list[dict] = []
    for tag, titles in (
        ("trial", trials),
        ("savage", savages),
        ("ult", ultimates),
    ):
        for i, title in enumerate(titles):
            print(f"2) resolve [{tag}] {i+1}/{len(titles)} {title}", flush=True)
            pid = resolve_instance_id(title)
            time.sleep(0.55)
            if not pid:
                continue
            data = fetch_instance(pid)
            time.sleep(0.55)
            if not data:
                continue
            bosses = (data.get("BOSS") or {}).get("中文名") or []
            boss_ids = (data.get("BOSS") or {}).get("中文名id") or []
            if not bosses:
                print(f"  skip no boss: {title}", flush=True)
                continue
            duties.append(
                {
                    "instance_id": pid,
                    "title": title,
                    "name": data.get("中文名") or title,
                    "type": data.get("类型"),
                    "tag": tag,
                    "version": data.get("版本"),
                    "bosses": list(bosses),
                    "boss_ids": list(boss_ids),
                }
            )

    (OUT / "_duties_raw.json").write_text(
        json.dumps(duties, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"duties resolved: {len(duties)}", flush=True)

    # Unique bosses: first-seen wins; prefer ult > savage > trial for tag label
    tag_rank = {"ult": 3, "savage": 2, "trial": 1}
    boss_map: dict[str, dict] = {}
    for d in duties:
        for idx, cn in enumerate(d["bosses"]):
            cn = str(cn).strip()
            if not cn:
                continue
            bid = None
            if idx < len(d["boss_ids"]):
                try:
                    bid = int(d["boss_ids"][idx])
                except (TypeError, ValueError):
                    bid = None
            cur = boss_map.get(cn)
            entry = {
                "cn": cn,
                "boss_id": bid,
                "tag": d["tag"],
                "sources": [d["name"]],
            }
            if not cur:
                boss_map[cn] = entry
                continue
            cur["sources"].append(d["name"])
            if bid and not cur.get("boss_id"):
                cur["boss_id"] = bid
            if tag_rank.get(d["tag"], 0) > tag_rank.get(cur.get("tag"), 0):
                cur["tag"] = d["tag"]

    print(f"unique bosses: {len(boss_map)}", flush=True)

    characters: list[dict] = []
    cards: list[dict] = []
    nick_by_id: dict[str, list[str]] = {}
    skipped: list[dict] = []

    for i, (cn, info) in enumerate(boss_map.items()):
        print(f"3) portrait {i+1}/{len(boss_map)} {cn}", flush=True)
        detail = boss_portrait_and_names(cn)
        time.sleep(0.7)
        url = detail.get("image_url") or ""
        if not url:
            skipped.append({"cn": cn, "reason": "no_image", "detail": detail})
            print(f"  SKIP no image: {cn}", flush=True)
            continue

        cid = slug_id(cn, info.get("boss_id"))
        en = detail.get("en") or ""
        jp = detail.get("jp") or ""
        resolved = (detail.get("resolved_page") or "").strip()
        aliases: list[str] = []
        for a in (cn, resolved, en, jp):
            a = (a or "").strip()
            if a and a not in aliases:
                aliases.append(a)
        # strip spaces variant for EN
        if en:
            compact = re.sub(r"[\s\-·・]+", "", en)
            if compact and compact not in aliases:
                aliases.append(compact)

        tag = info.get("tag") or "trial"
        class_name = {"ult": "绝境战", "savage": "零式", "trial": "讨伐"}.get(tag, tag)

        characters.append(
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
        nick_by_id[cid] = aliases[:]

    nicknames = {"by_id": nick_by_id, "by_name": {}}
    meta = {
        "source": "ff14.huijiwiki.com Data:Instance + monster pages",
        "display": "FF14",
        "character_count": len(characters),
        "card_count": len(cards),
        "duty_count": len(duties),
        "skipped_count": len(skipped),
        "rules": {
            "trial": "歼殛优先，无极则保留歼灭",
            "savage": "全部零式楼层",
            "ultimate": "绝境战全阶段 Boss",
            "dedupe": "按中文 Boss 名去重",
        },
    }

    (OUT / "characters.json").write_text(
        json.dumps(characters, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "guess_cards.json").write_text(
        json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "nicknames.json").write_text(
        json.dumps(nicknames, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "_skipped.json").write_text(
        json.dumps(skipped, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        f"done chars={len(characters)} cards={len(cards)} skipped={len(skipped)}",
        flush=True,
    )
    if characters:
        print("sample", characters[0]["fullNameChinese"], characters[0]["aliases"], flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
