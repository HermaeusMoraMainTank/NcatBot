"""分批构建：明日方舟 / 公主连接 / 原神 / 虚拟主播 卡池。

超时短、只拉 JSON、不验图，避免卡住。
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "resources"
UA = {"User-Agent": "NcatBot-GuessCard/1.0"}


def fetch(url: str, timeout: int = 45) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_json(url: str, timeout: int = 45):
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


def build_ak() -> None:
    print("[AK 1/2] character_table ...", flush=True)
    table = fetch_json(
        "https://cdn.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master/"
        "zh_CN/gamedata/excel/character_table.json",
        timeout=90,
    )
    rarity_map = {
        "TIER_1": 1,
        "TIER_2": 2,
        "TIER_3": 3,
        "TIER_4": 4,
        "TIER_5": 5,
        "TIER_6": 6,
    }
    img = (
        "https://cdn.jsdelivr.net/gh/Aceship/Arknight-Images@main/"
        "portraits/{cid}_1.png"
    )
    characters, cards = [], []
    print("[AK 2/2] filter ops rarity>=3 ...", flush=True)
    for cid, v in table.items():
        if not isinstance(v, dict):
            continue
        if not str(cid).startswith("char_"):
            continue
        if v.get("profession") in ("TOKEN", "TRAP"):
            continue
        if v.get("isNotObtainable") is True:
            continue
        rarity = rarity_map.get(str(v.get("rarity") or ""), 0)
        if rarity < 3:
            continue
        cn = (v.get("name") or "").strip()
        en = (v.get("appellation") or "").strip()
        if not cn:
            continue
        aliases = [a for a in (cn, en) if a]
        characters.append(
            {
                "characterId": cid,
                "name": cn,
                "fullName": cn,
                "fullNameChinese": cn,
                "aliases": aliases,
                "className": v.get("profession") or "",
                "rarity": rarity,
            }
        )
        cards.append(
            {
                "id": abs(hash(cid)) % (10**9),
                "characterId": cid,
                "cardRarityType": f"rarity_{rarity}",
                "assetbundleName": cid,
                "image_url": img.format(cid=cid),
                "className": v.get("profession") or "",
            }
        )
        if len(characters) % 80 == 0:
            print(f"  progress {len(characters)}", flush=True)
    write_pool(
        "ak",
        characters,
        cards,
        {
            "source": "Kengxxiao character_table + Aceship portraits",
            "display": "明日方舟",
        },
    )


def build_pcr() -> None:
    print("[PCR 1/2] Hoshino _pcr_data.py ...", flush=True)
    text = fetch(
        "https://cdn.jsdelivr.net/gh/Ice-Cirno/HoshinoBot@master/"
        "hoshino/modules/priconne/_pcr_data.py",
        timeout=45,
    ).decode("utf-8", "ignore")
    m = re.search(r"UnavailableChara\s*=\s*\{([^}]+)\}", text, re.S)
    unavail = {int(x) for x in re.findall(r"\d+", m.group(1))} if m else set()
    # CHARA_NAME = { 1001: ["日和", ...], ...}
    entries = re.findall(
        r"(\d+)\s*:\s*\[([^\]]+)\]",
        text,
    )
    print(f"  parsed entries={len(entries)} unavail={len(unavail)}", flush=True)
    characters, cards = [], []
    print("[PCR 2/2] build card urls ...", flush=True)
    for sid, arr in entries:
        cid = int(sid)
        if cid <= 1000 or cid in unavail:
            continue
        names = re.findall(r"[\"']([^\"']+)[\"']", arr)
        if not names:
            continue
        cn = names[0]
        if "未知" in cn:
            continue
        aliases = list(dict.fromkeys(names))  # unique keep order
        # 默认 3★ 卡面 id = {cid}31
        bundle = f"{cid}31"
        characters.append(
            {
                "characterId": str(cid),
                "name": cn,
                "fullName": cn,
                "fullNameChinese": cn,
                "aliases": aliases,
                "rarity": 3,
            }
        )
        cards.append(
            {
                "id": abs(hash(bundle)) % (10**9),
                "characterId": str(cid),
                "cardRarityType": "rarity_3",
                "assetbundleName": bundle,
                "image_url": f"https://redive.estertion.win/card/full/{bundle}.webp",
            }
        )
        if len(characters) % 80 == 0:
            print(f"  progress {len(characters)}", flush=True)
    write_pool(
        "pcr",
        characters,
        cards,
        {
            "source": "HoshinoBot CHARA_NAME + estertion card/full",
            "display": "公主连接",
        },
    )


def build_gi() -> None:
    print("[GI 1/2] yatta avatar list ...", flush=True)
    payload = fetch_json("https://gi.yatta.moe/api/v2/chs/avatar", timeout=45)
    items = (payload.get("data") or {}).get("items") or {}
    print(f"  items={len(items)}", flush=True)
    characters, cards = [], []
    print("[GI 2/2] splash urls ...", flush=True)
    for aid, v in items.items():
        if not isinstance(v, dict):
            continue
        cn = (v.get("name") or "").strip()
        icon = (v.get("icon") or "").strip()  # UI_AvatarIcon_Ayaka
        if not cn or not icon:
            continue
        # 旅行者多个形态容易重复混淆，跳过
        if "旅行者" in cn or cn in {"空", "荧"}:
            continue
        rarity = int(v.get("rank") or 4)
        en = (v.get("route") or "").strip()
        splash = icon.replace("UI_AvatarIcon_", "UI_Gacha_AvatarImg_")
        aliases = [a for a in (cn, en) if a]
        characters.append(
            {
                "characterId": str(aid),
                "name": cn,
                "fullName": cn,
                "fullNameChinese": cn,
                "aliases": aliases,
                "className": v.get("element") or "",
                "rarity": rarity,
            }
        )
        cards.append(
            {
                "id": abs(hash(str(aid))) % (10**9),
                "characterId": str(aid),
                "cardRarityType": f"rarity_{rarity}",
                "assetbundleName": splash,
                "image_url": f"https://enka.network/ui/{splash}.png",
                "className": v.get("element") or "",
            }
        )
    write_pool(
        "gi",
        characters,
        cards,
        {
            "source": "yatta.moe avatar API + enka UI_Gacha_AvatarImg",
            "display": "原神",
        },
    )


def _fandom_category_titles(category: str, limit_pages: int = 8) -> list[str]:
    titles: list[str] = []
    cont = None
    for page in range(limit_pages):
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": category,
            "cmnamespace": "0",
            "cmlimit": "50",
            "format": "json",
        }
        if cont:
            params["cmcontinue"] = cont
        api = "https://virtualyoutuber.fandom.com/api.php?" + urllib.parse.urlencode(
            params
        )
        j = fetch_json(api, timeout=20)
        for it in j.get("query", {}).get("categorymembers", []):
            t = it.get("title") or ""
            if t and not t.startswith("Category:"):
                titles.append(t)
        print(f"  {category} page {page+1}: total={len(titles)}", flush=True)
        cont = (j.get("continue") or {}).get("cmcontinue")
        if not cont:
            break
    return titles


def _fandom_pageimages(titles: list[str]) -> dict[str, tuple[str, str]]:
    """title -> (thumb_url, pageimage_name)"""
    out: dict[str, tuple[str, str]] = {}
    batch = 40
    for i in range(0, len(titles), batch):
        chunk = titles[i : i + batch]
        api = "https://virtualyoutuber.fandom.com/api.php?" + urllib.parse.urlencode(
            {
                "action": "query",
                "titles": "|".join(chunk),
                "prop": "pageimages",
                "pithumbsize": "800",
                "pilicense": "any",
                "format": "json",
            }
        )
        j = fetch_json(api, timeout=25)
        for _, p in (j.get("query") or {}).get("pages", {}).items():
            t = p.get("title") or ""
            thumb = (p.get("thumbnail") or {}).get("source")
            if t and thumb:
                out[t] = (thumb, p.get("pageimage") or "")
        print(
            f"  pageimages {min(i+batch, len(titles))}/{len(titles)} mapped={len(out)}",
            flush=True,
        )
    return out


def build_vt() -> None:
    print("[VT 1/3] list fandom categories ...", flush=True)
    # 不含任何 Hololive 分类 / 成员
    cats = [
        "Category:Nijisanji",
        "Category:VShojo",
    ]
    titles: list[str] = []
    for cat in cats:
        titles.extend(_fandom_category_titles(cat, limit_pages=6))
    titles = sorted(set(titles))
    print(f"  unique titles={len(titles)}", flush=True)
    print("[VT 2/3] pageimages ...", flush=True)
    images = _fandom_pageimages(titles)
    print("[VT 3/3] write ...", flush=True)
    characters, cards = [], []
    for title, (url, _) in images.items():
        # Fandom 缩略图链易 503，落盘用原图路径
        url = re.sub(r"/scale-to-width-down/\d+", "", url or "")
        url = url.split("?", 1)[0]
        low = title.lower()
        # 排除公司/列表页，以及任何 Hololive 相关条目
        if "hololive" in low or "ホロライブ" in title:
            continue
        if any(
            k in low
            for k in (
                "list of",
                "nijisanji",
                "category",
                "generation",
            )
        ):
            continue
        if title in {"NIJISANJI", "VShojo", "Nijisanji"}:
            continue
        cid = re.sub(r"[^0-9A-Za-z\u3400-\u9fff]+", "_", title).strip("_")
        aliases = [title]
        compact = title.replace(" ", "")
        if compact != title:
            aliases.append(compact)
        characters.append(
            {
                "characterId": cid,
                "name": title,
                "fullName": title,
                "fullNameChinese": title,
                "aliases": aliases,
            }
        )
        cards.append(
            {
                "id": abs(hash(cid)) % (10**9),
                "characterId": cid,
                "cardRarityType": "rarity_5",
                "assetbundleName": cid,
                "image_url": url,
            }
        )
    write_pool(
        "vt",
        characters,
        cards,
        {
            "source": "virtualyoutuber.fandom Nijisanji/VShojo pageimages",
            "display": "虚拟主播",
        },
    )


def _wiki_gg_image_url(filename: str) -> str:
    # File title spaces -> underscores；非 ASCII（如 Ryōshū）需百分号编码
    path = filename.replace(" ", "_")
    path = urllib.parse.quote(path, safe="_.-()")
    return "https://limbuscompany.wiki.gg/images/" + path


def _url_ok(url: str, timeout: int = 8) -> bool:
    try:
        req = urllib.request.Request(
            url, headers={**UA, "Range": "bytes=0-64"}, method="GET"
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code = getattr(r, "status", 200)
            return 200 <= code < 400
    except Exception:
        return False


def build_lcb() -> None:
    """边狱巴士：人格列表 + wiki.gg 立绘。"""
    print("[LCB 1/2] personalities.json ...", flush=True)
    items = fetch_json(
        "https://cdn.jsdelivr.net/gh/unacro/limbus-company-helper@main/"
        "data/personalities.json",
        timeout=45,
    )
    print(f"  identities={len(items)}", flush=True)
    characters, cards = [], []
    skipped = 0
    print("[LCB 2/2] resolve images (Full 优先) ...", flush=True)
    for i, p in enumerate(items):
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or "")
        cn = (p.get("name") or "").strip()
        en = (p.get("nameRaw") or "").strip()
        title_en = (p.get("titleRaw") or "").replace("\n", " ").strip()
        title_cn = (p.get("title") or "").replace("\n", " ").strip()
        if not pid or not cn or not en or not title_en:
            continue
        base = f"{title_en} {en}".strip()
        full_name = f"{base} Full.png"
        normal = f"{base}.png"
        # 少量校验模式，多数直接拼 URL（wiki.gg 规则稳定）
        image_url = _wiki_gg_image_url(full_name)
        if i < 12:
            if not _url_ok(image_url):
                image_url = _wiki_gg_image_url(normal)
                if not _url_ok(image_url):
                    skipped += 1
                    print(f"  skip {base}", flush=True)
                    continue
        aliases = []
        for a in (
            cn,
            en,
            title_cn,
            title_en,
            p.get("nameWithTitle"),
            f"{title_cn}{cn}",
            f"{title_cn} {cn}",
            f"{title_en} {en}",
            f"{title_en}{en}",
        ):
            if a and str(a).strip() and str(a).strip() not in aliases:
                aliases.append(str(a).strip())
        # 称号末段 + 角色名（神父格里高尔）
        for title in (title_cn, title_en):
            tokens = [t for t in re.split(r"[\s·・]+", title or "") if t]
            if tokens and cn:
                last = tokens[-1]
                if len(last) >= 2:
                    for combo in (f"{last}{cn}", f"{last} {cn}", f"{last}{en}"):
                        if combo not in aliases:
                            aliases.append(combo)
        characters.append(
            {
                "characterId": pid,
                "name": cn,
                "fullName": f"{title_cn} {cn}".strip() or cn,
                "fullNameChinese": cn,
                "aliases": aliases,
                "className": title_cn or title_en,
            }
        )
        cards.append(
            {
                "id": abs(hash(pid)) % (10**9),
                "characterId": pid,
                "cardRarityType": "rarity_3",
                "assetbundleName": base.replace(" ", "_"),
                "image_url": image_url,
            }
        )
        if (i + 1) % 40 == 0:
            print(f"  progress {i+1}/{len(items)}", flush=True)
    write_pool(
        "lcb",
        characters,
        cards,
        {
            "source": "unacro personalities + limbuscompany.wiki.gg images",
            "display": "边狱巴士",
            "skipped_sample": skipped,
        },
    )


def _fandom_category_members(
    api_host: str, category: str, limit_pages: int = 8
) -> list[str]:
    titles: list[str] = []
    cont = None
    for page in range(limit_pages):
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": category,
            "cmnamespace": "0",
            "cmlimit": "50",
            "format": "json",
        }
        if cont:
            params["cmcontinue"] = cont
        j = fetch_json(api_host + "?" + urllib.parse.urlencode(params), timeout=20)
        for it in j.get("query", {}).get("categorymembers", []):
            t = it.get("title") or ""
            if t and not t.startswith("Category:"):
                titles.append(t)
        print(f"  {category} page {page+1}: total={len(titles)}", flush=True)
        cont = (j.get("continue") or {}).get("cmcontinue")
        if not cont:
            break
    return titles


def build_ww() -> None:
    """鸣潮：Fandom Resonators + Card 立绘 + 中文 langlinks。"""
    host = "https://wutheringwaves.fandom.com/api.php"
    print("[WW 1/3] Category:Resonators ...", flush=True)
    titles = sorted(set(_fandom_category_members(host, "Category:Resonators", 6)))
    # 去掉总览页 / 空名
    titles = [
        t
        for t in titles
        if t
        not in {"Resonator", "Resonators"}
        and not t.startswith('"')
        and "List of" not in t
    ]
    print(f"  playable-ish={len(titles)}", flush=True)

    print("[WW 2/3] pageimages + zh langlinks ...", flush=True)
    meta: dict[str, dict] = {}
    batch = 40
    for i in range(0, len(titles), batch):
        chunk = titles[i : i + batch]
        api = host + "?" + urllib.parse.urlencode(
            {
                "action": "query",
                "titles": "|".join(chunk),
                "prop": "pageimages|langlinks",
                "pithumbsize": "800",
                "piprop": "thumbnail",
                "lllang": "zh",
                "lllimit": "max",
                "format": "json",
            }
        )
        j = fetch_json(api, timeout=30)
        for _, p in (j.get("query") or {}).get("pages", {}).items():
            t = p.get("title") or ""
            thumb = (p.get("thumbnail") or {}).get("source")
            if not t or not thumb:
                continue
            # 用原图而非缩小版
            thumb = re.sub(r"/scale-to-width-down/\d+", "", thumb)
            thumb = thumb.split("?", 1)[0]
            zh = ""
            for ll in p.get("langlinks") or []:
                if ll.get("lang") == "zh" and ll.get("*"):
                    zh = ll["*"]
                    break
            meta[t] = {"url": thumb, "zh": zh}
        print(
            f"  meta {min(i+batch, len(titles))}/{len(titles)} mapped={len(meta)}",
            flush=True,
        )

    print("[WW 3/3] write ...", flush=True)
    characters, cards = [], []
    for en, info in sorted(meta.items()):
        cn = (info.get("zh") or en).strip()
        cid = re.sub(r"[^0-9A-Za-z\u3400-\u9fff]+", "_", en).strip("_")
        aliases = [en]
        if cn and cn != en:
            aliases.append(cn)
        compact = en.replace(" ", "")
        if compact not in aliases:
            aliases.append(compact)
        characters.append(
            {
                "characterId": cid,
                "name": cn,
                "fullName": en,
                "fullNameChinese": cn,
                "aliases": aliases,
            }
        )
        cards.append(
            {
                "id": abs(hash(cid)) % (10**9),
                "characterId": cid,
                "cardRarityType": "rarity_5",
                "assetbundleName": cid,
                "image_url": info["url"],
            }
        )
    write_pool(
        "ww",
        characters,
        cards,
        {
            "source": "wutheringwaves.fandom Resonators pageimages + zh langlinks",
            "display": "鸣潮",
        },
    )


def main() -> None:
    build_ak()
    build_pcr()
    build_gi()
    build_vt()
    build_lcb()
    build_ww()
    print("ALL DONE", flush=True)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        name = sys.argv[1]
        fn = globals().get(f"build_{name}")
        if not fn:
            raise SystemExit(f"unknown target {name}")
        fn()
    else:
        main()

