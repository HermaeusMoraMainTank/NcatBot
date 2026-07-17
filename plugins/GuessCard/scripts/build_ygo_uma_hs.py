"""构建 游戏王 / 赛马娘 / 炉石传说 卡池。"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "resources"
TMP = ROOT / "_tmp"
UA = {"User-Agent": "Mozilla/5.0 NcatBot-GuessCard/1.0"}

# YGO type flags (ygopro)
TYPE_MONSTER = 0x1
TYPE_TOKEN = 0x400000
TYPE_FUSION = 0x40
TYPE_SYNCHRO = 0x2000
TYPE_XYZ = 0x800000
TYPE_LINK = 0x4000000
TYPE_EXTRA = TYPE_FUSION | TYPE_SYNCHRO | TYPE_XYZ | TYPE_LINK


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


def _download_cdb(locale: str, filename: str) -> Path:
    TMP.mkdir(parents=True, exist_ok=True)
    path = TMP / filename
    if path.exists() and path.stat().st_size > 1_000_000:
        print(f"  reuse {filename}", flush=True)
        return path
    url = (
        "https://raw.githubusercontent.com/mycard/ygopro-database/master/"
        f"locales/{locale}/cards.cdb"
    )
    print(f"  download {locale} cdb ...", flush=True)
    path.write_bytes(fetch(url, timeout=180))
    return path


def _read_cdb_names(path: Path) -> dict[int, str]:
    con = sqlite3.connect(path)
    cur = con.cursor()
    rows = cur.execute("SELECT id, name FROM texts").fetchall()
    con.close()
    return {int(i): (n or "").strip() for i, n in rows if n}


def _read_cdb_datas(path: Path) -> dict[int, dict]:
    con = sqlite3.connect(path)
    cur = con.cursor()
    rows = cur.execute(
        "SELECT id, alias, type, atk, def, level FROM datas"
    ).fetchall()
    con.close()
    out = {}
    for cid, alias, typ, atk, deff, level in rows:
        out[int(cid)] = {
            "alias": int(alias or 0),
            "type": int(typ or 0),
            "atk": int(atk or 0),
            "def": int(deff or 0),
            "level": int(level or 0) & 0xFF,  # pendulum nibble
        }
    return out


def build_ygo() -> None:
    print("[YGO] load cdb ...", flush=True)
    zh_path = _download_cdb("zh-CN", "ygo_zh.cdb")
    en_path = _download_cdb("en-US", "ygo_en.cdb")
    try:
        jp_path = _download_cdb("ja-JP", "ygo_jp.cdb")
        jp_names = _read_cdb_names(jp_path)
    except Exception as e:
        print(f"  jp cdb skip: {e}", flush=True)
        jp_names = {}

    zh = _read_cdb_names(zh_path)
    en = _read_cdb_names(en_path)
    datas = _read_cdb_datas(zh_path)

    # 低攻但仍常被叫外号的名卡（手坑/环境卡/常用魔陷）
    staple_cn = {
        "灰流丽",
        "效果遮蒙者",
        "增殖的G",
        "浮幽樱",
        "幽鬼兔",
        "屋敷童",
        "抹杀之指名者",
        "无限泡影",
        "神之宣告",
        "神之通告",
        "强制脱出装置",
        "死者苏生",
        "黑洞",
        "融合",
        "强欲之壶",
        "贪欲之壶",
        "天使的施舍",
        "愚蠢的埋葬",
        "增援",
        "旋风",
        "鹰身女妖的羽毛扫",
        "激流葬",
        "圣盾空灵剑士",
        "求救共鸣者",
        "原始生命态尼比鲁",
        "超级量子妖精阿尔方",
        "PSY框架装备·γ",
        "PSY骨架装备·γ",
        "幻崩·独角兽",
        "电光千鸟",
    }

    characters, cards = [], []
    seen_name = set()
    for cid, d in datas.items():
        typ = d["type"]
        is_monster = bool(typ & TYPE_MONSTER)
        cn = zh.get(cid) or ""
        en_name = en.get(cid) or ""
        jp = jp_names.get(cid) or ""
        if not cn and not en_name:
            continue
        # 跳过异画
        if d["alias"] and d["alias"] in datas:
            continue
        if typ & TYPE_TOKEN:
            continue
        # 排除易串台的套皮/衍化（「罪 青眼白龙」拆词会变成青眼白龙）
        skip_prefixes = ("罪 ", "罪・", "白色幻兽", "白斗气")
        if any(cn.startswith(p) for p in skip_prefixes):
            continue
        is_extra = bool(typ & TYPE_EXTRA)
        is_staple = cn in staple_cn or any(
            k in cn for k in ("灰流丽", "增殖的G", "效果遮蒙", "尼比鲁", "屋敷童")
        )
        # 魔陷只收一小撮 staple；怪兽：额外 或 攻>=2000 或 等级>=5 或 名卡
        is_spell_trap = bool(typ & (0x2 | 0x4)) and not is_monster
        if is_monster:
            if not is_extra and d["atk"] < 2000 and d["level"] < 5 and not is_staple:
                continue
        elif is_spell_trap:
            if cn not in staple_cn and not is_staple:
                continue
        else:
            continue

        # 同中文名去重（预改/繁体复卡）
        key = (cn or en_name).lower()
        if key in seen_name:
            continue
        seen_name.add(key)

        aliases = []
        for a in (cn, en_name, jp):
            if a and a not in aliases:
                aliases.append(a)
        if en_name:
            compact = en_name.replace("-", " ").replace("  ", " ")
            if compact not in aliases:
                aliases.append(compact)

        kind = "Extra" if is_extra else ("SpellTrap" if is_spell_trap else "Main")
        characters.append(
            {
                "characterId": str(cid),
                "name": cn or en_name,
                "fullName": cn or en_name,
                "fullNameChinese": cn,
                "fullNameJapanese": jp,
                "aliases": aliases,
                "className": kind,
                "rarity": 1 if is_extra else 0,
            }
        )
        cards.append(
            {
                "id": cid,
                "characterId": str(cid),
                "cardRarityType": kind.lower(),
                "assetbundleName": str(cid),
                # cropped 无卡名边框，避免直接读字
                "image_url": (
                    f"https://images.ygoprodeck.com/images/cards_cropped/{cid}.jpg"
                ),
                "className": kind,
            }
        )
        if len(characters) % 200 == 0:
            print(f"  progress {len(characters)}", flush=True)

    write_pool(
        "ygo",
        characters,
        cards,
        {
            "source": "mycard ygopro-database + ygoprodeck images",
            "display": "游戏王",
            "filter": "monster; extra OR atk>=2500 OR level>=7; no token/alias",
        },
    )


def _parse_uma_cn_map(wikitext: str) -> dict[str, str]:
    """从 biligame 模块:翻译数据库 text_data_6 提取 game_id→简中名。"""
    m = re.search(r"p\.text_data_6\s*=\s*\{", wikitext)
    if not m:
        return {}
    # 截到下一个 p.text_data_
    start = m.end()
    m2 = re.search(r"\np\.text_data_\d+\s*=", wikitext[start:])
    chunk = wikitext[start : start + m2.start()] if m2 else wikitext[start:]
    out: dict[str, str] = {}
    for row in re.finditer(
        r'index="(\d+)"\s*,\s*text_JP="([^"]*)"\s*,\s*text_TW="([^"]*)"\s*,\s*text_CN="([^"]*)"',
        chunk,
    ):
        gid, _jp, _tw, cn = row.groups()
        if cn:
            out[gid] = cn
    return out


def build_uma() -> None:
    print("[UMA] list + CN map ...", flush=True)
    lst = fetch_json("https://umapyoi.net/api/v1/character", timeout=45)
    api = "https://wiki.biligame.com/umamusume/api.php?" + urllib.parse.urlencode(
        {
            "action": "parse",
            "page": "模块:翻译数据库",
            "prop": "wikitext",
            "format": "json",
        }
    )
    wt_json = fetch_json(api, timeout=90)
    wt = (wt_json.get("parse") or {}).get("wikitext") or {}
    text = wt.get("*") if isinstance(wt, dict) else str(wt or "")
    cn_map = _parse_uma_cn_map(text)
    print(f"  characters={len(lst)} cn_map={len(cn_map)}", flush=True)

    characters, cards = [], []
    for i, item in enumerate(lst):
        gid = int(item.get("game_id") or 0)
        if not gid:
            continue
        try:
            d = fetch_json(f"https://umapyoi.net/api/v1/character/{gid}", timeout=30)
        except Exception as e:
            print(f"  FAIL {gid}: {e}", flush=True)
            time.sleep(0.2)
            continue
        en = (d.get("name_en") or "").strip()
        jp = (d.get("name_jp") or "").strip()
        cn = cn_map.get(str(gid), "").strip()
        if not en and not jp and not cn:
            continue
        img = (
            (d.get("detail_img_pc") or "").strip()
            or (d.get("thumb_img") or "").strip()
            or (d.get("sns_icon") or "").strip()
        )
        if not img:
            continue
        aliases = []
        for a in (cn, en, jp, (d.get("name_en_internal") or "").strip()):
            if a and a not in aliases:
                aliases.append(a)
        if en:
            compact = en.replace(" ", "")
            if compact not in aliases:
                aliases.append(compact)

        cid = str(gid)
        display = cn or en or jp
        characters.append(
            {
                "characterId": cid,
                "name": display,
                "fullName": display,
                "fullNameChinese": cn,
                "fullNameJapanese": jp,
                "aliases": aliases,
                "className": d.get("grade") or "",
                "rarity": 0,
            }
        )
        cards.append(
            {
                "id": gid,
                "characterId": cid,
                "cardRarityType": "uma",
                "assetbundleName": d.get("name_en_internal") or cid,
                "image_url": img,
                "className": d.get("grade") or "",
            }
        )
        if (i + 1) % 20 == 0:
            print(f"  progress {i+1}/{len(lst)} last={display}", flush=True)
            time.sleep(0.1)
        else:
            time.sleep(0.05)

    write_pool(
        "uma",
        characters,
        cards,
        {
            "source": "umapyoi.net portraits + biligame 翻译数据库 CN",
            "display": "赛马娘",
        },
    )


def build_hs() -> None:
    print("[HS] hearthstonejson ...", flush=True)
    zh = fetch_json(
        "https://api.hearthstonejson.com/v1/latest/zhCN/cards.collectible.json",
        timeout=120,
    )
    en = fetch_json(
        "https://api.hearthstonejson.com/v1/latest/enUS/cards.collectible.json",
        timeout=120,
    )
    enmap = {c["id"]: (c.get("name") or "").strip() for c in en}

    skip_sets = {
        "HERO_SKINS",
        "TB",
        "CREDITS",
        "MISSIONS",
        "Taverns of Time",
        "VANILLA",  # 经典重做重复可保留；若太多重可再滤
    }
    characters, cards = [], []
    seen = set()
    for c in zh:
        if c.get("type") != "MINION":
            continue
        if c.get("rarity") != "LEGENDARY":
            continue
        if c.get("set") in skip_sets:
            continue
        if not c.get("collectible"):
            continue
        cid = c.get("id") or ""
        cn = (c.get("name") or "").strip()
        en_name = enmap.get(cid, "")
        if not cid or not cn:
            continue
        # 去重同名（核心年重印）
        key = normalize_key(cn)
        if key in seen:
            continue
        seen.add(key)

        aliases = [a for a in (cn, en_name) if a]
        if en_name:
            compact = en_name.replace("'", "").replace(" ", "")
            if compact not in aliases:
                aliases.append(compact)

        characters.append(
            {
                "characterId": cid,
                "name": cn,
                "fullName": cn,
                "fullNameChinese": cn,
                "aliases": aliases,
                "className": c.get("cardClass") or "",
                "rarity": 5,
            }
        )
        cards.append(
            {
                "id": abs(hash(cid)) % (10**9),
                "characterId": cid,
                "cardRarityType": "legendary",
                "assetbundleName": cid,
                # 512x 纯插画（非 render 卡面），避免卡名外泄
                "image_url": f"https://art.hearthstonejson.com/v1/512x/{cid}.jpg",
                "className": c.get("cardClass") or "",
            }
        )

    write_pool(
        "hs",
        characters,
        cards,
        {
            "source": "hearthstonejson collectible legendary minions",
            "display": "炉石传说",
        },
    )


def normalize_key(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").lower())


def main() -> None:
    import sys

    targets = sys.argv[1:] or ["ygo", "uma", "hs"]
    for t in targets:
        if t == "ygo":
            build_ygo()
        elif t == "uma":
            build_uma()
        elif t == "hs":
            build_hs()
        else:
            raise SystemExit(f"unknown {t}")


if __name__ == "__main__":
    main()
