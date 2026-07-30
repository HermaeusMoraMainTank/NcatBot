"""分批构建：明日方舟 / 公主连接 / 原神 / 虚拟主播 卡池。

超时短、只拉 JSON、不验图，避免卡住。
"""
from __future__ import annotations

import json
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "resources"
UA = {"User-Agent": "NcatBot-GuessCard/1.0"}
CURL_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def fetch(url: str, timeout: int = 45) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_json(url: str, timeout: int = 45):
    return json.loads(fetch(url, timeout))


def curl_json(url: str, referer: str, timeout: int = 45) -> dict:
    """wiki.gg / 灰机等对 Python TLS 不友好时用 curl。"""
    cmd = [
        "curl",
        "-sL",
        "-A",
        CURL_UA,
        "-e",
        referer,
        "--connect-timeout",
        "15",
        "--max-time",
        str(timeout),
        url,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0 or not (r.stdout or "").strip():
        return {}
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {}


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
    # Aceship 对新干员经常缺图；prts / GameResource 更全
    img_templates = [
        "https://torappu.prts.wiki/assets/char_portrait/{cid}_1.png",
        "https://cdn.jsdelivr.net/gh/yuanyan3060/ArknightsGameResource@main/portrait/{cid}_1.png",
        "https://raw.githubusercontent.com/yuanyan3060/ArknightsGameResource/main/portrait/{cid}_1.png",
        "https://cdn.jsdelivr.net/gh/Aceship/Arknight-Images@main/portraits/{cid}_1.png",
    ]
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
        urls = [t.format(cid=cid) for t in img_templates]
        cards.append(
            {
                "id": abs(hash(cid)) % (10**9),
                "characterId": cid,
                "cardRarityType": f"rarity_{rarity}",
                "assetbundleName": cid,
                "image_url": urls[0],
                "image_urls": urls,
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
            "source": "Kengxxiao character_table + prts/yuanyan portraits",
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


def _curl_url_ok(url: str, referer: str = "https://limbuscompany.wiki.gg/") -> bool:
    cmd = [
        "curl",
        "-sI",
        "-A",
        CURL_UA,
        "-e",
        referer,
        "--max-time",
        "20",
        url,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    head = r.stdout or ""
    return bool(re.search(r"HTTP/\S+\s+200\b", head))


def _lcb_boss_base_name(title: str) -> str:
    """Matthias/Enemy → Matthias；Rien/Enemy/The Refracted… → Rien。"""
    return (title or "").split("/", 1)[0].strip()


def _lcb_pick_boss_pages(titles: list[str]) -> dict[str, str]:
    """每个 Boss 根名只保留一页：优先 Name/Enemy，其次最短路径。"""
    groups: dict[str, list[str]] = {}
    for t in titles:
        base = _lcb_boss_base_name(t)
        if not base:
            continue
        # 跳过明显非整角色标题
        if base.startswith("Every World") or "Recollected" in t:
            continue
        groups.setdefault(base, []).append(t)

    picked: dict[str, str] = {}
    for base, pages in groups.items():
        prefer = f"{base}/Enemy"
        if prefer in pages:
            picked[base] = prefer
        else:
            pages_sorted = sorted(pages, key=lambda x: (x.count("/"), len(x)))
            picked[base] = pages_sorted[0]
    return picked


def _lcb_huiji_cn_name(en_name: str) -> str:
    """灰机搜索英文名 → 解析 |英文名= 命中的中文页标题。"""
    # 灰机缺页时的兜底译名（常见 Boss）
    fallback = {
        "Aida": "艾达",
        "Erlking Heathcliff": "Erlking希斯克利夫",
        "Gubo": "顾泊",
        "Guido": "圭多",
        "La Manchaland's Don Quixote": "拉曼却领堂吉诃德",
        "Marile": "玛丽勒",
        "Old G Corp. Head Manager": "旧G公司部长",
        "Papa Bongy": "胖胖鸡爸爸",
        "Ricardo": "里卡多",
        "The Barber": "理发师",
        "The Last Knight": "最后的骑士",
        "The Priest": "神父",
        "The Time Ripper": "时间杀人魔",
        "Tingtang Boss": "叮噹帮头目",
        "Öufi Assoc. Director": "欧甫协会部长",
    }
    api = "https://limbuscompany.huijiwiki.com/api.php"
    q = urllib.parse.urlencode(
        {
            "action": "query",
            "list": "search",
            "srsearch": en_name,
            "srlimit": 8,
            "format": "json",
        }
    )
    d = curl_json(api + "?" + q, "https://limbuscompany.huijiwiki.com/")
    candidates = [x.get("title") or "" for x in d.get("query", {}).get("search", [])]
    # 优先短角色页（排除「主线战斗…」）
    candidates = sorted(
        candidates,
        key=lambda t: (
            0 if t == en_name else 1,
            0 if "主线" not in t and "战斗" not in t else 1,
            len(t),
        ),
    )
    for title in candidates:
        if not title:
            continue
        q2 = urllib.parse.urlencode(
            {"action": "parse", "page": title, "prop": "wikitext", "format": "json"}
        )
        page = curl_json(api + "?" + q2, "https://limbuscompany.huijiwiki.com/")
        wt = page.get("parse", {}).get("wikitext", {}).get("*") or ""
        m = re.search(r"\|英文名\s*=\s*([^\n|{]+)", wt)
        if m and m.group(1).strip().lower() == en_name.lower():
            cn_m = re.search(r"\|中文名\s*=\s*([^\n|{]+)", wt)
            if cn_m and cn_m.group(1).strip():
                return cn_m.group(1).strip()
            # 去掉消歧义后缀：克罗默(角色) → 克罗默
            return re.sub(r"[（(][^）)]*[）)]\s*$", "", title).strip() or title
        # 标题本身就是中文且搜索命中较强
        if re.search(r"[\u4e00-\u9fff]", title) and "主线" not in title and "战斗" not in title:
            if en_name.lower() in wt.lower() or f"英文名={en_name}" in wt.replace(" ", ""):
                cn_m = re.search(r"\|中文名\s*=\s*([^\n|{]+)", wt)
                if cn_m and cn_m.group(1).strip():
                    return cn_m.group(1).strip()
    return fallback.get(en_name, "")


def _lcb_boss_storylog_url(en_name: str) -> str:
    fname = f"{en_name} StoryLog.png"
    url = _wiki_gg_image_url(fname)
    # 用 curl 探测（aio/urllib 常被 wiki.gg 拦）
    if _curl_url_ok(url):
        return url.split("?")[0]
    return ""


def build_lcb_bosses() -> tuple[list[dict], list[dict], dict]:
    """从 wiki.gg Category:Boss Enemy 拉故事 Boss 立绘（StoryLog）。"""
    print("[LCB Boss] categorymembers Boss Enemy ...", flush=True)
    api = "https://limbuscompany.wiki.gg/api.php"
    titles: list[str] = []
    cont = None
    while True:
        params: dict = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": "Category:Boss Enemy",
            "cmlimit": "500",
            "format": "json",
        }
        if cont:
            params["cmcontinue"] = cont
        d = curl_json(api + "?" + urllib.parse.urlencode(params), "https://limbuscompany.wiki.gg/")
        titles.extend(
            x["title"] for x in d.get("query", {}).get("categorymembers", []) if x.get("title")
        )
        cont = (d.get("continue") or {}).get("cmcontinue")
        if not cont:
            break
    print(f"  raw boss pages={len(titles)}", flush=True)
    picked = _lcb_pick_boss_pages(titles)
    print(f"  unique bosses={len(picked)}", flush=True)

    characters: list[dict] = []
    cards: list[dict] = []
    skipped_no_img = 0
    skipped_no_cn = 0
    for i, (en, page) in enumerate(sorted(picked.items()), 1):
        image_url = _lcb_boss_storylog_url(en)
        if not image_url:
            skipped_no_img += 1
            if skipped_no_img <= 8:
                print(f"  skip(no StoryLog): {en}", flush=True)
            continue
        cn = _lcb_huiji_cn_name(en)
        if not cn:
            skipped_no_cn += 1
            cn = en  # 无中文时仍收录，可事后猜卡加答案
        cid = f"boss_{re.sub(r'[^0-9A-Za-z]+', '_', en).strip('_')}"
        aliases = [cn, en]
        if cn != en:
            aliases.append(cn.replace("·", ""))
            aliases.append(en.replace(" ", ""))
        # 去掉消歧义后的简称
        short = re.sub(r"[（(][^）)]*[）)]", "", cn).strip()
        if short and short not in aliases:
            aliases.append(short)
        characters.append(
            {
                "characterId": cid,
                "name": cn,
                "fullName": en,
                "fullNameChinese": cn,
                "aliases": list(dict.fromkeys(a for a in aliases if a)),
                "className": "Boss",
                "kind": "boss",
                "wikiPage": page,
            }
        )
        cards.append(
            {
                "id": abs(hash(cid)) % (10**9),
                "characterId": cid,
                "cardRarityType": "rarity_5",
                "assetbundleName": cid,
                "image_url": image_url,
                "image_urls": [image_url],
                "kind": "boss",
            }
        )
        if i % 15 == 0 or i == len(picked):
            print(f"  boss progress {i}/{len(picked)} kept={len(characters)}", flush=True)

    stats = {
        "boss_raw_pages": len(titles),
        "boss_unique": len(picked),
        "boss_kept": len(characters),
        "boss_skipped_no_image": skipped_no_img,
        "boss_fallback_en_name": skipped_no_cn,
    }
    return characters, cards, stats


def build_lcb() -> None:
    """边狱巴士：人格列表 + Boss Enemy + wiki.gg 立绘。"""
    print("[LCB 1/3] personalities.json ...", flush=True)
    items = fetch_json(
        "https://cdn.jsdelivr.net/gh/unacro/limbus-company-helper@main/"
        "data/personalities.json",
        timeout=45,
    )
    print(f"  identities={len(items)}", flush=True)
    characters, cards = [], []
    skipped = 0
    print("[LCB 2/3] resolve identity images (Full 优先) ...", flush=True)
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
                "kind": "identity",
            }
        )
        cards.append(
            {
                "id": abs(hash(pid)) % (10**9),
                "characterId": pid,
                "cardRarityType": "rarity_3",
                "assetbundleName": base.replace(" ", "_"),
                "image_url": image_url,
                "kind": "identity",
            }
        )
        if (i + 1) % 40 == 0:
            print(f"  progress {i+1}/{len(items)}", flush=True)

    print("[LCB 3/3] Boss Enemy ...", flush=True)
    bosses, boss_cards, boss_stats = build_lcb_bosses()
    # 避免与人格同 ID（人格 id 为数字串）；boss_ 前缀已隔离
    characters.extend(bosses)
    cards.extend(boss_cards)

    write_pool(
        "lcb",
        characters,
        cards,
        {
            "source": "unacro personalities + wiki.gg Boss Enemy StoryLog + huiji CN",
            "display": "边狱巴士",
            "skipped_sample": skipped,
            **boss_stats,
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


def _lor_wiki_image_url(filename: str) -> str:
    path = filename.replace(" ", "_")
    path = urllib.parse.quote(path, safe="_.-()")
    return "https://libraryofruina.wiki.gg/images/" + path


def _lor_curl_ok(url: str) -> bool:
    cmd = [
        "curl",
        "-sI",
        "-A",
        CURL_UA,
        "-e",
        "https://libraryofruina.wiki.gg/",
        "--max-time",
        "20",
        url,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return bool(re.search(r"HTTP/\S+\s+200\b", r.stdout or ""))


def _lor_load_cn_map() -> dict[str, str]:
    """灰机《角色译名表》：EN → CN（司书名中文栏常与英文相同）。"""
    api = "https://libraryofruina.huijiwiki.com/api.php"
    q = urllib.parse.urlencode(
        {"action": "parse", "page": "角色译名表", "prop": "wikitext", "format": "json"}
    )
    d = curl_json(api + "?" + q, "https://libraryofruina.huijiwiki.com/", timeout=60)
    wt = d.get("parse", {}).get("wikitext", {}).get("*") or ""
    mapping: dict[str, str] = {}
    rows = re.findall(
        r"\|\s*align=\"center\"\s*\|\s*([^\n|]+)\s*\n"
        r"\|\s*align=\"center\"\s*\|\s*([^\n|]+)\s*\n"
        r"\|\s*align=\"center\"\s*\|\s*([^\n|]+)\s*\n"
        r"\|\s*align=\"center\"\s*\|\s*([^\n|]+)\s*\n"
        r"\|\s*align=\"center\"\s*\|\s*([^\n|]+)",
        wt,
    )
    for _idx, cn, _jp, _kr, en in rows:
        cn_s = re.sub(r"<[^>]+>", "", cn).strip()
        en_s = re.sub(r"<[^>]+>", "", en).strip()
        if not en_s or not cn_s:
            continue
        # 译名表里中文列仍是纯拉丁时，不当作有效中文名
        if not re.search(r"[\u4e00-\u9fff]", cn_s):
            continue
        mapping[en_s] = cn_s
        mapping[en_s.replace(" ", "")] = cn_s
    # 社区/灰机常用中文（译名表缺项或乱码时兜底）
    fallback = {
        "Moirai": "莫伊莱",
        "Carmen": "卡门",
        "Luda": "鲁妲",
        "Merry": "玛丽",
        "Tommy": "汤咪",
        "Yan": "阳",
        "Naimon": "奈蒙",
        "Ogier": "奥吉耶",
        "Renaud": "雷诺",
        "Astolfo": "阿斯托尔福",
        "Ayin": "A",
        "Angelica": "安吉丽卡",
        "Arnold": "阿诺德",
        "Consta": "康斯塔",
        "Mo": "莫",
        "Roland": "罗兰",
        "Angela": "安吉拉",
        # 指定司书官中保留英文：Yesod/Hod/Netzach/Tiphereth/Chesed/Hokma
        # 以及 Malkuth/Gebura/Binah 不用音译；仅社区外号见 nicknames.json
    }
    for en, cn in fallback.items():
        mapping[en] = cn
        mapping[en.replace(" ", "")] = cn
    print(f"  name map entries={len(mapping)}", flush=True)
    return mapping


def _lor_has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _lor_huiji_cn_lookup(en_name: str) -> str:
    """灰机搜索英文名，取带汉字的短角色页标题。"""
    api = "https://libraryofruina.huijiwiki.com/api.php"
    q = urllib.parse.urlencode(
        {
            "action": "query",
            "list": "search",
            "srsearch": en_name,
            "srlimit": 6,
            "format": "json",
        }
    )
    d = curl_json(api + "?" + q, "https://libraryofruina.huijiwiki.com/", timeout=30)
    for hit in d.get("query", {}).get("search", []) or []:
        title = (hit.get("title") or "").strip()
        if not title or not _lor_has_cjk(title):
            continue
        if any(x in title for x in ("游戏剧情", "不速之客", "接待", "小剧场", "震击")):
            continue
        # 去掉消歧义
        title = re.sub(r"[（(][^）)]*[）)]\s*$", "", title).strip()
        if title and _lor_has_cjk(title) and len(title) <= 16:
            return title
    return ""


def _lor_resolve_cn(en_name: str, cn_map: dict[str, str]) -> str:
    cn = cn_map.get(en_name) or cn_map.get(en_name.replace(" ", "")) or ""
    if _lor_has_cjk(cn):
        return cn
    looked = _lor_huiji_cn_lookup(en_name)
    if looked:
        return looked
    return cn or en_name


def _lor_pick_fullbody(wikitext: str, title: str) -> str:
    """从角色页挑人物全身立绘文件名（避开 Hurt/EGO/Combat/卡牌）。"""
    wt = wikitext or ""
    # 单图 image1
    m = re.search(r"\|image1\s*=\s*([^\n|{<]+?\.png)", wt, re.I)
    if m:
        name = m.group(1).strip()
        if re.search(r"fullbody", name, re.I) and not re.search(
            r"Hurt|EGO|Combat|Damaged|Bloody|Mask|Partial", name, re.I
        ):
            return name
    # gallery 内 FullBody
    bodies = re.findall(r"([^\n\|<\]]+[Ff]ull[Bb]ody\.png)", wt)
    clean = [
        b.strip()
        for b in bodies
        if not re.search(r"Hurt|EGO|Combat|Damaged|Bloody|Mask|Partial", b, re.I)
    ]
    if clean:
        return clean[0]
    if bodies:
        return bodies[0].strip()
    # 约定文件名回退
    for cand in (
        f"{title}FullBody.png",
        f"{title} FullBody.png",
        f"{title} fullbody.png",
        f"{title}_FullBody.png",
    ):
        return cand  # 稍后用 HEAD 验证；这里返回候选由调用方探测
    return ""


def build_lor() -> None:
    """废墟图书馆：wiki.gg 人物全身立绘（FullBody），非战斗书页卡面。"""
    print("[LOR 1/3] Category:Characters ...", flush=True)
    api = "https://libraryofruina.wiki.gg/api.php"
    titles: list[str] = []
    cont = None
    while True:
        params: dict = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": "Category:Characters",
            "cmlimit": "500",
            "format": "json",
        }
        if cont:
            params["cmcontinue"] = cont
        d = curl_json(
            api + "?" + urllib.parse.urlencode(params),
            "https://libraryofruina.wiki.gg/",
            timeout=60,
        )
        for x in d.get("query", {}).get("categorymembers", []):
            t = x.get("title") or ""
            if not t or t.startswith("Category:") or t in {"Characters", "Guests", "???"}:
                continue
            if "(Reception)" in t or t.endswith(" (reception)"):
                continue
            titles.append(t)
        cont = (d.get("continue") or {}).get("cmcontinue")
        if not cont:
            break
    titles = list(dict.fromkeys(titles))
    print(f"  character pages={len(titles)}", flush=True)

    print("[LOR 2/3] huiji 角色译名表 ...", flush=True)
    cn_map = _lor_load_cn_map()

    print("[LOR 3/3] resolve FullBody portraits ...", flush=True)
    characters: list[dict] = []
    cards: list[dict] = []
    skipped = 0
    for i, title in enumerate(titles, 1):
        q = urllib.parse.urlencode(
            {"action": "parse", "page": title, "prop": "wikitext", "format": "json"}
        )
        page = curl_json(
            api + "?" + q, "https://libraryofruina.wiki.gg/", timeout=45
        )
        wt = page.get("parse", {}).get("wikitext", {}).get("*") or ""
        if not wt:
            skipped += 1
            continue
        # 优先解析出的文件名，再尝试约定名
        candidates: list[str] = []
        picked = _lor_pick_fullbody(wt, title)
        if picked:
            candidates.append(picked)
        for cand in (
            f"{title}FullBody.png",
            f"{title} FullBody.png",
            f"{title} fullbody.png",
        ):
            if cand not in candidates:
                candidates.append(cand)

        image_url = ""
        used_file = ""
        for fname in candidates:
            url = _lor_wiki_image_url(fname)
            if _lor_curl_ok(url):
                image_url = url.split("?")[0]
                used_file = fname
                break
        if not image_url:
            skipped += 1
            if skipped <= 12:
                print(f"  skip(no FullBody): {title}", flush=True)
            continue

        en = title
        cn = _lor_resolve_cn(en, cn_map)
        cid = f"lor_{re.sub(r'[^0-9A-Za-z]+', '_', en).strip('_')}"
        aliases = [cn, en]
        if cn != en:
            aliases.append(cn.replace("·", "").replace(" ", ""))
            aliases.append(en.replace(" ", ""))
        # 韩文名
        kr = re.search(r"\|korean\s*=\s*([^\n|{]+)", wt, re.I)
        if kr and kr.group(1).strip():
            aliases.append(kr.group(1).strip())

        characters.append(
            {
                "characterId": cid,
                "name": cn,
                "fullName": en,
                "fullNameChinese": cn,
                "aliases": list(dict.fromkeys(a for a in aliases if a)),
                "className": "Characters",
                "kind": "character",
                "portraitFile": used_file,
            }
        )
        cards.append(
            {
                "id": abs(hash(cid)) % (10**9),
                "characterId": cid,
                "cardRarityType": "rarity_4",
                "assetbundleName": cid,
                "image_url": image_url,
                "image_urls": [image_url],
                "kind": "portrait",
            }
        )
        if i % 25 == 0 or i == len(titles):
            print(
                f"  progress {i}/{len(titles)} kept={len(characters)} skipped={skipped}",
                flush=True,
            )

    write_pool(
        "lor",
        characters,
        cards,
        {
            "source": "libraryofruina.wiki.gg FullBody + huiji 角色译名表",
            "display": "废墟图书馆",
            "skipped_no_portrait": skipped,
            "note": "人物全身立绘，非 Combat Page 卡牌图",
        },
    )
    # 人物池写完后并入楼层解放战异想体立绘
    build_lor_abno()


# 楼层解放战：异想体 → Angela/Roland EGO 战斗立绘（wiki.gg Realization Sprite）
# 答案同时接受异想体中文名与解放战战斗单位/EGO 名。
# 宗教层解放战仅有「先知」「失乐园」两阶段，无独立的沉默的代价/碧蓝新星/一罪与百善立绘。
LOR_ABNO_REALIZATION: list[dict] = [
    # 总类层
    {
        "abno": "血浴缸",
        "ego": "割腕",
        "en_abno": "Bloodbath",
        "en_ego": "Wrist Cutter",
        "file": "Wrist Cutter Realization Sprite.png",
        "floor": "总类层",
    },
    {
        "abno": "渴望之心",
        "ego": "热望",
        "en_abno": "Heart of Aspiration",
        "en_ego": "Aspiration",
        "file": "Aspiration Realization Sprite.png",
        "floor": "总类层",
    },
    {
        "abno": "匹诺曹",
        "ego": "提线木偶",
        "en_abno": "Pinocchio",
        "en_ego": "Marionette",
        "file": "Marionette Realization Sprite.png",
        "floor": "总类层",
    },
    {
        "abno": "冰雪女皇",
        "ego": "霜雕",
        "en_abno": "The Snow Queen",
        "en_ego": "Frost Splinter",
        "file": "Frost Splinter Realization Sprite.png",
        "floor": "总类层",
    },
    {
        "abno": "噤默处子",
        "ego": "罪恶感",
        "en_abno": "Silent Girl",
        "en_ego": "Remorse",
        "file": "Remorse Realization Sprite.png",
        "floor": "总类层",
    },
    # 历史层
    {
        "abno": "焦化少女",
        "ego": "终末之光",
        "en_abno": "Scorched Girl",
        "en_ego": "Fourth Match Flame",
        "file": "Fourth Match Flame Realization Sprite.png",
        "floor": "历史层",
    },
    {
        "abno": "快乐泰迪",
        "ego": "忘却",
        "en_abno": "Happy Teddy Bear",
        "en_ego": "The Forgotten",
        "file": "The Forgotten Realization Sprite.png",
        "floor": "历史层",
    },
    {
        "abno": "精灵盛宴",
        "ego": "翅振",
        "en_abno": "Fairy Festival",
        "en_ego": "Wingbeat",
        "file": "Wingbeat Realization Sprite.png",
        "floor": "历史层",
    },
    {
        "abno": "蜂后",
        "ego": "黄蜂",
        "en_abno": "Queen Bee",
        "en_ego": "Hornet",
        "file": "Hornet Realization Sprite.png",
        "floor": "历史层",
    },
    {
        "abno": "白雪公主的苹果",
        "ego": "翠枝",
        "en_abno": "Snow White's Apple",
        "en_ego": "Green Stem",
        "file": "Green Stem Realization Sprite.png",
        "floor": "历史层",
    },
    # 科技层
    {
        "abno": "被遗弃的杀人魔",
        "ego": "悔恨",
        "en_abno": "Forsaken Murderer",
        "en_ego": "Regret",
        "file": "Regret Realization Sprite.png",
        "floor": "科技层",
    },
    {
        "abno": "小帮手",
        "ego": "研削机Mk5-2",
        "en_abno": "All-Around Helper",
        "en_ego": "Grinder Mk. 5-2",
        "file": "Grinder Mk. 5-2 Realization Sprite.png",
        "floor": "科技层",
    },
    {
        "abno": "歌唱机",
        "ego": "和弦",
        "en_abno": "Singing Machine",
        "en_ego": "Harmony",
        "file": "Harmony Realization Sprite.png",
        "floor": "科技层",
    },
    {
        "abno": "亡蝶葬仪",
        "ego": "庄严哀悼",
        "en_abno": "Funeral of the Dead Butterflies",
        "en_ego": "Solemn Lament",
        "file": "Solemn Lament Realization Sprite.png",
        "floor": "科技层",
    },
    {
        "abno": "魔弹射手",
        "ego": "魔弹",
        "en_abno": "Der Freischütz",
        "en_ego": "Magic Bullet",
        "file": "Magic Bullet Realization Sprite.png",
        "floor": "科技层",
    },
    # 文学层
    {
        "abno": "今天也很害羞",
        "ego": "此刻的神情",
        "en_abno": "Today's Shy Look",
        "en_ego": "Today's Expression",
        "file": "Today's Expression Realization Sprite.png",
        "floor": "文学层",
    },
    {
        "abno": "红舞鞋",
        "ego": "血欲",
        "en_abno": "The Red Shoes",
        "en_ego": "Sanguine Desire",
        "file": "Sanguine Desire Realization Sprite.png",
        "floor": "文学层",
    },
    {
        "abno": "蜘蛛巢",
        "ego": "赤瞳",
        "en_abno": "Spider Bud",
        "en_ego": "Red Eyes",
        "file": "Red Eyes Realization Sprite.png",
        "floor": "文学层",
    },
    {
        "abno": "蕾蒂希娅",
        "ego": "Laetitia",
        "en_abno": "Laetitia",
        "en_ego": "Laetitia",
        "file": "Laetitia Realization Sprite.png",
        "floor": "文学层",
    },
    {
        "abno": "黑天鹅之梦",
        "ego": "黑天鹅",
        "en_abno": "Dream of a Black Swan",
        "en_ego": "Black Swan",
        "file": "Black Swan Realization Sprite.png",
        "floor": "文学层",
    },
    # 艺术层
    {
        "abno": "宇宙碎片",
        "ego": "彼方的碎片",
        "en_abno": "Fragment of the Universe",
        "en_ego": "Fragments from Somewhere",
        "file": "Fragments from Somewhere Realization Sprite.png",
        "floor": "艺术层",
    },
    {
        "abno": "银河之子",
        "ego": "我们的小小银河",
        "en_abno": "Child of the Galaxy",
        "en_ego": "Our Galaxy",
        "file": "Our Galaxy Realization Sprite.png",
        "floor": "艺术层",
    },
    {
        "abno": "棘刺公交",
        "ego": "欢愉",
        "en_abno": "Porccubus",
        "en_ego": "Pleasure",
        "file": "Pleasure Realization Sprite.png",
        "floor": "艺术层",
    },
    {
        "abno": "爱娜温",
        "ego": "余香",
        "en_abno": "Alriune",
        "en_ego": "Faint Aroma",
        "file": "Faint Aroma Realization Sprite.png",
        "floor": "艺术层",
    },
    {
        "abno": "沉默乐团",
        "ego": "Da Capo",
        "en_abno": "The Silent Orchestra",
        "en_ego": "Da Capo",
        "file": "Da Capo Realization Sprite.png",
        "floor": "艺术层",
    },
    # 自然层
    {
        "abno": "憎恶皇后",
        "ego": "以爱与憎之名",
        "en_abno": "The Queen of Hatred",
        "en_ego": "In the Name of Love and Hate",
        "file": "In the Name of Love and Hate Realization Sprite.png",
        "floor": "自然层",
    },
    {
        "abno": "绝望骑士",
        "ego": "泪锋之剑",
        "en_abno": "The Knight of Despair",
        "en_ego": "The Sword Sharpened with Tears",
        "file": "The Sword Sharpened by Tears Realization Sprite.png",
        "floor": "自然层",
    },
    {
        "abno": "贪婪国王",
        "ego": "闪金冲锋",
        "en_abno": "The King of Greed",
        "en_ego": "Gold Rush",
        "file": "Gold Rush Realization Sprite.png",
        "floor": "自然层",
    },
    {
        "abno": "愤怒侍从",
        "ego": "盲眼怒火",
        "en_abno": "The Servant of Wrath",
        "en_ego": "Blind Wrath",
        "file": "Blind Wrath Realization Sprite.png",
        "floor": "自然层",
    },
    {
        "abno": "虚无弄臣",
        "ego": "虚无缥缈",
        "en_abno": "The Jester of Nihil",
        "en_ego": "Nihil",
        "file": "Nihil Realization Sprite.png",
        "floor": "自然层",
    },
    # 语言层
    {
        "abno": "小红帽雇佣兵",
        "ego": "猩红创痕",
        "en_abno": "Little Red Riding Hooded Mercenary",
        "en_ego": "Crimson Scar",
        "file": "Crimson Scar Realization Sprite.png",
        "floor": "语言层",
    },
    {
        "abno": "大坏狼",
        "ego": "郁蓝创痕",
        "en_abno": "Big and Will be Bad Wolf",
        "en_ego": "Cobalt Scar",
        "file": "Cobalt Scar Realization Sprite.png",
        "floor": "语言层",
    },
    {
        "abno": "微笑的尸山",
        "ego": "笑靥",
        "en_abno": "Mountain of Smiling Bodies",
        "en_ego": "Smile",
        "file": "Smile Realization Sprite.png",
        "floor": "语言层",
    },
    {
        "abno": "诺斯费拉图",
        "ego": "渴血症",
        "en_abno": "Nosferatu",
        "en_ego": "Dipsia",
        "file": "Dipsia Realization Sprite.png",
        "floor": "语言层",
    },
    {
        "abno": "一无所有",
        "ego": "拟态",
        "en_abno": "Nothing There",
        "en_ego": "Mimicry",
        "file": "Mimicry Final Realization Sprite.png",
        "floor": "语言层",
    },
    # 社会层
    {
        "abno": "求知的稻草人",
        "ego": "丰收",
        "en_abno": "Scarecrow Searching for Wisdom",
        "en_ego": "Harvest",
        "file": "Harvest Realization Sprite.png",
        "floor": "社会层",
    },
    {
        "abno": "热心的樵夫",
        "ego": "滥伐",
        "en_abno": "Warm-hearted Woodsman",
        "en_ego": "Lumber",
        "file": "Lumber Realization Sprite.png",
        "floor": "社会层",
    },
    {
        "abno": "归家的路途与胆小的猫咪",
        "ego": "归巢本能",
        "en_abno": "The Road Home",
        "en_ego": "Homing Instinct",
        "file": "Roland HomingInstinct.png",
        "floor": "社会层",
        "extra_aliases": ["归家的路途", "胆小的猫咪", "Scaredy Cat"],
    },
    {
        "abno": "奥兹玛",
        "ego": "褪色记忆",
        "en_abno": "Ozma",
        "en_ego": "Faded Memories",
        "file": "Faded Memories Realization Sprite.png",
        "floor": "社会层",
    },
    {
        "abno": "说谎的大人",
        "ego": "虚伪王座",
        "en_abno": "The Adult who Tells Lies",
        "en_ego": "False Throne",
        "file": "False Throne Realization Sprite.png",
        "floor": "社会层",
    },
    # 哲学层
    {
        "abno": "大鸟",
        "ego": "目灯",
        "en_abno": "Big Bird",
        "en_ego": "Lamp",
        "file": "Lamp Realization Sprite.png",
        "floor": "哲学层",
    },
    {
        "abno": "惩戒鸟",
        "ego": "尖喙",
        "en_abno": "Punishing Bird",
        "en_ego": "Beak",
        "file": "Beak Realization Sprite.png",
        "floor": "哲学层",
    },
    {
        "abno": "审判鸟",
        "ego": "正义裁决",
        "en_abno": "Judgement Bird",
        "en_ego": "Justitia",
        "file": "Justicia Realization Sprite.png",
        "floor": "哲学层",
    },
    {
        "abno": "终末鸟",
        "ego": "薄暝",
        "en_abno": "Apocalypse Bird",
        "en_ego": "Twilight",
        "file": "Twilight Realization Sprite.png",
        "floor": "哲学层",
    },
    # 宗教层（解放战仅两阶段）
    {
        "abno": "渗透天堂",
        "ego": "先知",
        "en_abno": "Plague Doctor",
        "en_ego": "Prophet",
        "file": "Prophet Realization Sprite V1.png",
        "floor": "宗教层",
        "extra_aliases": ["Heaven"],
    },
    {
        "abno": "白夜",
        "ego": "失乐园",
        "en_abno": "WhiteNight",
        "en_ego": "Paradise Lost",
        "file": "Paradise Lost Realization Sprite.png",
        "floor": "宗教层",
        "extra_aliases": ["White Night"],
    },
]


def _lor_imageinfo_url(filename: str) -> str:
    """经 wiki.gg imageinfo 解析真实图片 URL。"""
    api = "https://libraryofruina.wiki.gg/api.php"
    q = urllib.parse.urlencode(
        {
            "action": "query",
            "titles": f"File:{filename}",
            "prop": "imageinfo",
            "iiprop": "url",
            "format": "json",
        }
    )
    d = curl_json(api + "?" + q, "https://libraryofruina.wiki.gg/", timeout=30)
    for p in (d.get("query") or {}).get("pages", {}).values():
        infos = p.get("imageinfo") or []
        if infos and infos[0].get("url"):
            return infos[0]["url"].split("?")[0]
    # 回退：直接拼路径
    url = _lor_wiki_image_url(filename.replace(" ", "_"))
    if _lor_curl_ok(url):
        return url
    return ""


def build_lor_abno() -> None:
    """并入楼层解放战异想体战斗立绘（Realization Sprite），不重建人物池。"""
    print("[LOR-ABNO] Floor Realization sprites ...", flush=True)
    out = ROOT / "lor"
    chars_path = out / "characters.json"
    cards_path = out / "guess_cards.json"
    meta_path = out / "meta.json"
    if not chars_path.exists() or not cards_path.exists():
        raise SystemExit("lor pool missing; run build_lor first")

    characters: list[dict] = json.loads(chars_path.read_text(encoding="utf-8"))
    cards: list[dict] = json.loads(cards_path.read_text(encoding="utf-8"))
    meta: dict = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

    # 去掉旧异想体条目，便于重复运行
    characters = [c for c in characters if c.get("kind") != "abno_realization"]
    abno_ids = {c["characterId"] for c in characters if str(c.get("characterId", "")).startswith("lor_abno_")}
    # 上面已按 kind 过滤；再按 id 前缀清一次
    characters = [c for c in characters if not str(c.get("characterId", "")).startswith("lor_abno_")]
    cards = [c for c in cards if not str(c.get("characterId", "")).startswith("lor_abno_")]
    del abno_ids

    kept = 0
    skipped = 0
    for row in LOR_ABNO_REALIZATION:
        abno = row["abno"]
        ego = row["ego"]
        fname = row["file"]
        image_url = _lor_imageinfo_url(fname)
        if not image_url:
            skipped += 1
            print(f"  skip(no image): {abno} / {ego} ({fname})", flush=True)
            continue

        slug_en = re.sub(r"[^0-9A-Za-z]+", "_", row["en_abno"]).strip("_")
        cid = f"lor_abno_{slug_en}"
        aliases = [
            abno,
            ego,
            row["en_abno"],
            row["en_ego"],
            abno.replace("的", ""),
            ego.replace("·", ""),
        ]
        for extra in row.get("extra_aliases") or []:
            aliases.append(extra)
        # 研削机别名
        if abno == "小帮手":
            aliases.extend(["研削机", "研削机Mk4", "Grinder"])
        if abno == "蕾蒂希娅":
            aliases.append("蕾提希娅")
        if abno == "棘刺公交":
            aliases.append("Porccubus")
        if abno == "一无所有":
            aliases.extend(["Mimicry", "拟态最终"])
        if abno == "审判鸟":
            aliases.append("Justitia")

        characters.append(
            {
                "characterId": cid,
                "name": abno,
                "fullName": ego,
                "fullNameChinese": abno,
                "aliases": list(dict.fromkeys(a for a in aliases if a)),
                "className": f"Floor Realization ({row['floor']})",
                "kind": "abno_realization",
                "portraitFile": fname,
                "floor": row["floor"],
                "egoName": ego,
            }
        )
        cards.append(
            {
                "id": abs(hash(cid)) % (10**9),
                "characterId": cid,
                "cardRarityType": "rarity_4",
                "assetbundleName": cid,
                "image_url": image_url,
                "image_urls": [image_url],
                "kind": "abno_realization",
            }
        )
        kept += 1
        print(f"  + {row['floor']} {abno} → {ego}", flush=True)

    meta.update(
        {
            "source": (
                "libraryofruina.wiki.gg FullBody + Floor Realization Sprite + huiji 角色译名表"
            ),
            "display": "废墟图书馆",
            "note": "人物全身立绘 + 楼层解放战异想体战斗立绘（非异想体头像）",
            "abno_realization_count": kept,
            "abno_realization_skipped": skipped,
            "character_count": len(characters),
            "card_count": len(cards),
        }
    )
    write_pool("lor", characters, cards, meta)
    print(f"[LOR-ABNO] done kept={kept} skipped={skipped}", flush=True)


def _hbr_normalize_name(text: str) -> str:
    """统一间隔号，去掉不可打印杂质。"""
    text = (text or "").strip()
    for ch in ("‧", "・", "•", "·", "\u2027", "\u30fb"):
        text = text.replace(ch, "·")
    return text


def _hbr_to_hans(text: str) -> str:
    """繁体 → 简体（优先 zhconv，否则用常见字表）。"""
    text = _hbr_normalize_name(text)
    try:
        from zhconv import convert  # type: ignore

        return _hbr_normalize_name(convert(text, "zh-cn"))
    except Exception:
        char_map = {
            "蒼": "苍",
            "繪": "绘",
            "瀨": "濑",
            "聖": "圣",
            "華": "华",
            "東": "东",
            "國": "国",
            "見": "见",
            "來": "来",
            "葉": "叶",
            "櫻": "樱",
            "彌": "弥",
            "裡": "里",
            "裏": "里",
            "亞": "亚",
            "爾": "尔",
            "絲": "丝",
            "瑪": "玛",
            "麗": "丽",
            "達": "达",
            "颯": "飒",
            "歐": "欧",
            "納": "纳",
            "凱": "凯",
            "們": "们",
            "與": "与",
            "為": "为",
            "鈴": "铃",
            "萬": "万",
            "樂": "乐",
            "詩": "诗",
            "紀": "纪",
            "綺": "绮",
            "羅": "罗",
            "澤": "泽",
            "眞": "真",
            "淺": "浅",
            "傑": "杰",
            "‧": "·",
            "・": "·",
            "黒": "黑",
            "齊": "齐",
            "齋": "斋",
            "鄉": "乡",
            "戰": "战",
            "擊": "击",
            "據": "据",
            "臺": "台",
            "灣": "湾",
            "體": "体",
            "餘": "余",
            "黑": "黑",
        }
        out = text.translate(str.maketrans(char_map))
        for a, b in (
            ("伊瓦爾", "伊瓦尔"),
            ("阿黛爾海德", "阿黛尔海德"),
            ("安傑利斯", "安杰利斯"),
        ):
            out = out.replace(a, b)
        return out


# 各部队站立绘序号 → 英文名（与官网 img_stand_0N 顺序一致）
_HBR_EN_BY_SQUAD_STAND: dict[str, dict[int, str]] = {
    "31a": {
        1: "Ruka Kayamori",
        2: "Yuki Izumi",
        3: "Megumi Aikawa",
        4: "Tsukasa Tojo",
        5: "Karen Asakura",
        6: "Tama Kunimi",
    },
    "31b": {
        1: "Erika Aoi",
        2: "Ichigo Minase",
        3: "Sumomo Minase",
        4: "Seika Higuchi",
        5: "Kozue Hiiragi",
        6: "Byakko",
    },
    "31c": {
        1: "Bon Ivar Yamawaki",
        2: "Seira Sakuraba",
        3: "Miko Tenne",
        4: "Yayoi Bungo",
        5: "Adelheid Kanzaki",
        6: "Mari Satsuki",
    },
    "30g": {
        1: "Yuina Shirakawa",
        2: "Monaka Tsukishiro",
        3: "Miya Kiryu",
        4: "Chie Sugawara",
        5: "Hisame Ogasahara",
        6: "Satomi Kura",
    },
    "31d": {
        1: "Misato Nikaido",
        2: "Iroha Ishii",
        3: "Fubuki Mikoto",
        4: "Risa Murofushi",
        5: "Akari Date",
        6: "Aina Mizuhara",
    },
    "31e": {
        1: "Ichiko Ohshima",
        2: "Niina Ohshima",
        3: "Minori Ohshima",
        4: "Yotsuha Ohshima",
        5: "Isuzu Ohshima",
        6: "Muua Ohshima",
    },
    "31f": {
        1: "Mion Yanagi",
        2: "Kanata Maruyama",
        3: "Shiki Hanamura",
        4: "Chiroru Matsuoka",
        5: "Inori Natsume",
        6: "Maki Kurosawa",
    },
    "31x": {
        1: "Carole Reaper",
        2: "Yingxia Li",
        3: "Irene Redmayne",
        4: "Vritika Balakrishnan",
        5: "Maria de Angelis",
        6: "Charlotta Skopovskaya",
    },
    "other": {
        1: "Saki Tezuka",
        2: "Nanami Nanase",
        3: "Makiko Asami",
    },
}


def build_hbr() -> None:
    """炽焰天穹 / 绯染天空：官网繁中站全身立绘。"""
    print("[HBR] official TW character stands ...", flush=True)
    base_tw = "https://tw.heaven-burns-red.com"
    base_jp = "https://heaven-burns-red.com"

    def _curl(url: str) -> str:
        cmd = [
            "curl",
            "-sL",
            "-A",
            CURL_UA,
            "-e",
            base_tw + "/",
            "--connect-timeout",
            "15",
            "--max-time",
            "45",
            url,
        ]
        r = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore"
        )
        return r.stdout or ""

    html_index = _curl(f"{base_jp}/character/")
    links = list(dict.fromkeys(re.findall(r'href="(/character/[^"]+/)"', html_index)))
    print(f"  squad pages={len(links)}", flush=True)

    characters: list[dict] = []
    cards: list[dict] = []
    seen_cn: set[str] = set()

    for link in links:
        html = _curl(base_tw + link)
        if len(html) < 1000:
            html = _curl(base_jp + link)
        pairs = re.findall(
            r'src="[^"]*character/([^/]+)/img_stand_0(\d)\.png"[^>]*alt="([^"]+)"'
            r'|alt="([^"]+)"[^>]*src="[^"]*character/([^/]+)/img_stand_0(\d)\.png"',
            html,
        )
        by_stand: dict[int, tuple[str, str]] = {}
        for g in pairs:
            if g[0] and g[1] and g[2]:
                squad, stand, alt = g[0], int(g[1]), g[2].strip()
            elif g[3] and g[4] and g[5]:
                squad, stand, alt = g[4], int(g[5]), g[3].strip()
            else:
                continue
            if stand not in by_stand and alt and re.search(r"[\u4e00-\u9fff]", alt):
                by_stand[stand] = (squad, alt)

        for stand, (squad, cn_tw) in sorted(by_stand.items()):
            cn_tw = _hbr_normalize_name(cn_tw)
            cn = _hbr_to_hans(cn_tw)
            if cn in seen_cn:
                continue
            seen_cn.add(cn)
            en = (_HBR_EN_BY_SQUAD_STAND.get(squad) or {}).get(stand, "")
            cid = f"hbr_{squad}_{stand:02d}"
            image_url = (
                f"{base_tw}/assets/images/common/character/"
                f"{squad}/img_stand_0{stand}.png"
            )
            aliases = [cn, cn_tw, en]
            for a in (cn, cn_tw):
                aliases.append(a.replace("·", "").replace(" ", ""))
            if en:
                aliases.append(en.replace(" ", ""))
                parts = en.split()
                if len(parts) >= 2:
                    aliases.append(parts[0])
                    aliases.append(parts[-1])

            characters.append(
                {
                    "characterId": cid,
                    "name": cn,
                    "fullName": en or cn,
                    "fullNameChinese": cn,
                    "aliases": list(dict.fromkeys(a for a in aliases if a)),
                    "className": squad.upper(),
                    "kind": "character",
                    "squad": squad,
                    "portraitFile": f"img_stand_0{stand}.png",
                }
            )
            cards.append(
                {
                    "id": abs(hash(cid)) % (10**9),
                    "characterId": cid,
                    "cardRarityType": "rarity_4",
                    "assetbundleName": cid,
                    "image_url": image_url,
                    "image_urls": [image_url],
                    "kind": "portrait",
                    "className": squad.upper(),
                }
            )
            try:
                print(f"  + {squad}#{stand} {cn} / {en or '-'}", flush=True)
            except UnicodeEncodeError:
                print(f"  + {squad}#{stand} / {en or '-'}", flush=True)

    write_pool(
        "hbr",
        characters,
        cards,
        {
            "source": "tw.heaven-burns-red.com character stand portraits",
            "display": "炽焰天穹",
            "note": "官网部队全身立绘（img_stand），非风格卡面",
        },
    )


def main() -> None:
    build_ak()
    build_pcr()
    build_gi()
    build_vt()
    build_lcb()
    build_ww()
    build_lor()
    build_hbr()
    print("ALL DONE", flush=True)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        name = sys.argv[1]
        # 允许 lor_abno 作为独立目标
        if name == "lor_abno":
            build_lor_abno()
        else:
            fn = globals().get(f"build_{name}")
            if not fn:
                raise SystemExit(f"unknown target {name}")
            fn()
    else:
        main()

