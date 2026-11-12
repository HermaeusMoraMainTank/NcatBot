"""快照构建与加载。

CS2：ByMykel/CSGO-API 的 crates/skins/keys 归一化。
Dota 2：Liquipedia 宝瓶页的 infobox + 内容分层 + 官方递增概率表。

快照以 gzip JSON 存放，插件运行时只读快照，不再依赖网络。
"""

from __future__ import annotations

import gzip
import json
import re
from collections.abc import Iterable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import CrateInfo, SkinItem, Treasure, TreasureItem
from .rules import RARE_TIER_NAME, TIER_ORDER

SNAPSHOT_DIR = Path(__file__).resolve().parent / "data"
CS_FILE = "cs2.json.gz"
DOTA_FILE = "dota.json.gz"
PRICES_730 = "prices_730.json.gz"
PRICES_570 = "prices_570.json.gz"

#: 首批上架的 10 个箱子（见设计文档 §5.0）
#: 箱子中文显示名（社区通用叫法，用于列表与搜索）
CASE_CN: dict[str, str] = {
    "Kilowatt Case": "千瓦武器箱",
    "Fever Case": "狂热武器箱",
    "Gallery Case": "画廊武器箱",
    "Fracture Case": "裂空武器箱",
    "Dreams & Nightmares Case": "梦境与噩梦武器箱",
    "Recoil Case": "反冲武器箱",
    "Prisma 2 Case": "棱彩 2 号武器箱",
    "Danger Zone Case": "危险地带武器箱",
    "Snakebite Case": "蛇噬武器箱",
    "Revolution Case": "革命武器箱",
    # 高价值箱（老箱，价格从几十到几百元）
    "CS:GO Weapon Case": "CS:GO 武器箱",
    "Operation Bravo Case": "英勇大行动武器箱",
    "eSports 2013 Case": "电竞 2013 武器箱",
    "Operation Hydra Case": "九头蛇大行动武器箱",
    "Operation Breakout Weapon Case": "突围大行动武器箱",
    "Huntsman Weapon Case": "猎杀者武器箱",
    "Operation Riptide Case": "激流大行动武器箱",
    "Operation Broken Fang Case": "狂牙大行动武器箱",
    "Chroma Case": "幻彩武器箱",
    "Operation Phoenix Weapon Case": "凤凰大行动武器箱",
    "Prisma Case": "棱彩武器箱",
    "Spectrum Case": "光谱武器箱",
    "Spectrum 2 Case": "光谱 2 号武器箱",
    "Horizon Case": "地平线武器箱",
    "CS20 Case": "CS20 武器箱",
    "Clutch Case": "命悬一线武器箱",
    "Falchion Case": "弯刀武器箱",
    "Chroma 2 Case": "幻彩 2 号武器箱",
    "Chroma 3 Case": "幻彩 3 号武器箱",
    "Shadow Case": "暗影武器箱",
    "Gamma Case": "伽马武器箱",
    "Gamma 2 Case": "伽马 2 号武器箱",
    "Revolver Case": "左轮武器箱",
    "Operation Wildfire Case": "野火大行动武器箱",
    "Shattered Web Case": "裂网大行动武器箱",
    "Operation Vanguard Weapon Case": "先锋大行动武器箱",
    "eSports 2014 Summer Case": "电竞 2014 夏季武器箱",
    "eSports 2013 Winter Case": "电竞 2013 冬季武器箱",
    "Winter Offensive Weapon Case": "冬季攻势武器箱",
    "CS:GO Weapon Case 2": "CS:GO 武器箱 2 号",
    "CS:GO Weapon Case 3": "CS:GO 武器箱 3 号",
    "Glove Case": "手套武器箱",
}

SELECTED_CASES: tuple[str, ...] = (
    "Kilowatt Case",
    "Fever Case",
    "Gallery Case",
    "Fracture Case",
    "Dreams & Nightmares Case",
    "Recoil Case",
    "Prisma 2 Case",
    "Danger Zone Case",
    "Snakebite Case",
    "Revolution Case",
    # 高价值箱
    "CS:GO Weapon Case",
    "Operation Bravo Case",
    "eSports 2013 Case",
    "Operation Hydra Case",
    "Operation Breakout Weapon Case",
    "Huntsman Weapon Case",
    "Operation Riptide Case",
    "Operation Broken Fang Case",
    "Chroma Case",
    "Operation Phoenix Weapon Case",
    # 其余常见箱体，凑齐全部 42 个 Case
    "Prisma Case",
    "Spectrum Case",
    "Spectrum 2 Case",
    "Horizon Case",
    "CS20 Case",
    "Clutch Case",
    "Falchion Case",
    "Chroma 2 Case",
    "Chroma 3 Case",
    "Shadow Case",
    "Gamma Case",
    "Gamma 2 Case",
    "Revolver Case",
    "Operation Wildfire Case",
    "Shattered Web Case",
    "Operation Vanguard Weapon Case",
    "eSports 2014 Summer Case",
    "eSports 2013 Winter Case",
    "Winter Offensive Weapon Case",
    "CS:GO Weapon Case 2",
    "CS:GO Weapon Case 3",
    "Glove Case",
    # 古堡激战纪念包（含 AWP | 龙狙），无钥匙、按纪念包概率表
    "ESL One Cologne 2014 Cobblestone Souvenir Package",
    "DreamHack 2014 Cobblestone Souvenir Package",
    "ESL One Katowice 2015 Cobblestone Souvenir Package",
    "ESL One Cologne 2015 Cobblestone Souvenir Package",
    "DreamHack Cluj-Napoca 2015 Cobblestone Souvenir Package",
    "MLG Columbus 2016 Cobblestone Souvenir Package",
    "Cologne 2016 Cobblestone Souvenir Package",
    "Atlanta 2017 Cobblestone Souvenir Package",
    "Krakow 2017 Cobblestone Souvenir Package",
    "Boston 2018 Cobblestone Souvenir Package",
)

#: 古堡纪念包（唯一含 AWP | 龙狙 的容器）的官方品质概率模型
#: 由运营从 Valve 公示数据抄录：六档合计 100%
SOUVENIR_TIER_ODDS: dict[str, float] = {
    "Consumer Grade": 0.7987,
    "Industrial Grade": 0.1597,
    "Mil-Spec Grade": 0.0333,
    "Restricted": 0.0067,
    "Classified": 0.0013,
    "Covert": 0.0003,
}

#: 含龙狙的纪念包关键字
SOUVENIR_KEYWORD = "Cobblestone"

#: 2024–2026 可游戏内购买、且已核对国服售价的 13 个宝瓶（§6.0）
DOTA_SELECTED: tuple[tuple[str, str, float], ...] = (
    ("Dragon's Hoard", "金龙宝藏", 17.0),
    ("Crownfall Treasure I", "倾天之战珍藏", 21.0),
    ("Crownfall Treasure II", "倾天之战珍藏（第二套）", 21.0),
    ("Crownfall Treasure III", "倾天之战珍藏（第二幕）", 21.0),
    ("Crownfall 2024 Collector's Cache", "倾天之战典藏宝瓶", 21.0),
    ("Crownfall 2024 Collector's Cache II", "倾天之战典藏宝瓶 II", 21.0),
    ("Winter 2024 Heroes' Hoard", "2024年隆冬英雄宝藏", 21.0),
    ("The Charms of the Snake", "蛇年珍藏", 17.0),
    ("Spring 2025 Heroes' Hoard", "2025年新春英雄宝藏", 21.0),
    ("Cosmic 2025 Heroes' Hoard", "2025年寰宇英雄宝藏", 21.0),
    ("Winter 2025 Collector's Cache", "2025年冬季典藏宝瓶", 21.0),
    ("Treasure of Wonders", "灵幻奇观的珍藏", 17.0),
    ("Trove of Terror", "惊世奇珍", 35.0),
)

DOTA_SOURCE = "https://liquipedia.net/dota2/{page}"

# --------------------------------------------------------------------------- CS2


def build_cs_snapshot(
    crates: Iterable[dict[str, Any]],
    skins: Iterable[dict[str, Any]],
    keys: Iterable[dict[str, Any]],
    selected: Iterable[str] = SELECTED_CASES,
    zh_crates: Iterable[dict[str, Any]] | None = None,
    zh_skins: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    wanted = {name for name in selected}
    crate_rows = [
        row
        for row in crates
        if row.get("name") in wanted
        and (row.get("type") == "Case" or row.get("type") == "Souvenir")
    ]
    if not crate_rows:
        raise ValueError("没有匹配到任何箱子，检查 SELECTED_CASES")
    order = {name: index for index, name in enumerate(selected)}
    crate_rows.sort(key=lambda row: order.get(row.get("name"), 999))

    key_by_crate: dict[str, str] = {}
    for key in keys:
        key_name = str(key.get("name") or "")
        for crate in key.get("crates") or []:
            crate_id = str(crate.get("id") or "")
            if crate_id and crate_id not in key_by_crate:
                key_by_crate[crate_id] = key_name

    zh_crate_names = {str(row.get("id")): row.get("name") or "" for row in (zh_crates or [])}
    zh_skin_names = {str(row.get("id")): row.get("name") or "" for row in (zh_skins or [])}
    zh_collection_names: dict[str, str] = {}
    for row in zh_skins or []:
        collections = row.get("collections") or []
        if collections and collections[0].get("id"):
            zh_collection_names[str(collections[0]["id"])] = collections[0].get("name") or ""

    items: dict[str, dict[str, Any]] = {}
    for skin in skins:
        rarity = skin.get("rarity") or {}
        collections = skin.get("collections") or []
        collection = collections[0] if collections else {}
        items[str(skin.get("id"))] = {
            "id": str(skin.get("id")),
            "name": skin.get("name") or "",
            "name_cn": zh_skin_names.get(str(skin.get("id"))) or "",
            "weapon": (skin.get("weapon") or {}).get("name") or "",
            "category": (skin.get("category") or {}).get("name") or "",
            "rarity_id": rarity.get("id") or "",
            "rarity_name": rarity.get("name") or "",
            "color": rarity.get("color") or "#b0c3d9",
            "min_float": skin.get("min_float"),
            "max_float": skin.get("max_float"),
            "stattrak": bool(skin.get("stattrak")),
            "souvenir": bool(skin.get("souvenir")),
            "paint_index": skin.get("paint_index"),
            "image": skin.get("image"),
            "collection_id": collection.get("id") or "",
            "collection_name": collection.get("name") or "",
            "collection_name_cn": zh_collection_names.get(collection.get("id") or "", ""),
        }

    cases: list[dict[str, Any]] = []
    for row in crate_rows:
        is_souvenir = row.get("type") == "Souvenir"
        tier_names = list(SOUVENIR_TIER_ODDS) if is_souvenir else list(TIER_ORDER)
        tiers: dict[str, list[str]] = {tier: [] for tier in tier_names}
        for entry in row.get("contains") or []:
            tier_name = (entry.get("rarity") or {}).get("name")
            if tier_name in tiers:
                tiers[tier_name].append(str(entry.get("id")))
        rare = [] if is_souvenir else [str(entry.get("id")) for entry in row.get("contains_rare") or []]
        crate_id = str(row.get("id"))
        cases.append(
            {
                "id": crate_id,
                "name": row.get("name") or "",
                "cn_name": zh_crate_names.get(crate_id)
                or CASE_CN.get(row.get("name") or "", row.get("name") or ""),
                "type": row.get("type"),
                "market_hash_name": row.get("market_hash_name") or row.get("name") or "",
                "first_sale_date": str(row.get("first_sale_date") or ""),
                "image": row.get("image"),
                "key_name": None if is_souvenir else key_by_crate.get(crate_id),
                "tier_odds": dict(SOUVENIR_TIER_ODDS) if is_souvenir else {},
                "tiers": {tier: ids for tier, ids in tiers.items() if ids},
                "rare": rare,
            }
        )

    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": {
            "items": "https://github.com/ByMykel/CSGO-API (MIT)",
            "prices": "https://api.skinport.com/v1/items",
        },
        "cases": cases,
        "items": items,
        "rare_tier": RARE_TIER_NAME,
    }


def load_cs_snapshot(path: Path | None = None):
    path = path or SNAPSHOT_DIR / CS_FILE
    payload = _read_gz(path)
    cases = {
        row["name"]: CrateInfo(
            id=row["id"],
            name=row["name"],
            type=row.get("type"),
            market_hash_name=row["market_hash_name"],
            first_sale_date=row.get("first_sale_date") or "",
            image=row.get("image"),
            key_name=row.get("key_name"),
            cn_name=row.get("cn_name") or row.get("name") or "",
            tier_odds={tier: float(odds) for tier, odds in (row.get("tier_odds") or {}).items()},
            tiers={tier: list(ids) for tier, ids in (row.get("tiers") or {}).items()},
            rare=list(row.get("rare") or []),
        )
        for row in payload["cases"]
    }
    items = {
        key: SkinItem(
            id=row["id"],
            name=row["name"],
            weapon=row["weapon"],
            category=row["category"],
            rarity_id=row["rarity_id"],
            rarity_name=row["rarity_name"],
            color=row["color"],
            min_float=row.get("min_float"),
            max_float=row.get("max_float"),
            stattrak=bool(row.get("stattrak")),
            souvenir=bool(row.get("souvenir")),
            paint_index=row.get("paint_index"),
            image=row.get("image"),
            collection_id=row.get("collection_id") or "",
            collection_name=row.get("collection_name") or "",
            name_cn=row.get("name_cn") or "",
            collection_name_cn=row.get("collection_name_cn") or "",
        )
        for key, row in payload["items"].items()
    }
    return cases, items, payload


# --------------------------------------------------------------------------- Dota


def parse_dota_page(
    page: str,
    wikitext: str,
    *,
    html: str = "",
    cn_name: str = "",
    price_cny: float | None = None,
) -> Treasure:
    info = _infobox(wikitext)
    icons = _icon_map(html)
    contents = _contents(wikitext, icons)
    odds = _odds_table(wikitext)
    cosmic = _cosmic_odds(wikitext)
    if cosmic:
        odds["Cosmically Rare"] = [cosmic]
    return Treasure(
        name=page,
        cn_name=cn_name or page,
        defindex=int(info.get("defindex") or 0),
        releasedate=str(info.get("releasedate") or ""),
        availability=_clean_links(info.get("availability") or ""),
        purchasable=str(info.get("purchasable") or "").lower() == "yes",
        marketable=str(info.get("marketable") or "").lower() == "yes",
        tradeable=str(info.get("tradeable") or "").lower() == "yes",
        price_cny=price_cny,
        contents=contents,
        odds=odds,
        bonus_rewards=_bonus_rewards(wikitext),
        recycle_rule=_recycle_rule(wikitext),
        icon=_treasure_icon(html, page),
        source=DOTA_SOURCE.format(page=page.replace(" ", "_")),
        fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def build_dota_snapshot(treasures: Iterable[Treasure]) -> dict[str, Any]:
    rows = []
    for treasure in treasures:
        row = asdict(treasure)
        row["contents"] = {
            tier: [asdict(item) for item in items] for tier, items in treasure.contents.items()
        }
        rows.append(row)
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": {
            "contents": "https://liquipedia.net/dota2 (CC-BY-SA 3.0)",
            "prices": "国服商城人工核对（2026-11-06）",
        },
        "treasures": rows,
    }


def load_dota_snapshot(path: Path | None = None) -> tuple[dict[str, Treasure], dict[str, Any]]:
    path = path or SNAPSHOT_DIR / DOTA_FILE
    payload = _read_gz(path)
    treasures: dict[str, Treasure] = {}
    for row in payload["treasures"]:
        contents = {
            tier: [TreasureItem(**item) for item in items]
            for tier, items in (row.get("contents") or {}).items()
        }
        treasures[row["name"]] = Treasure(
            name=row["name"],
            cn_name=row.get("cn_name") or row["name"],
            defindex=int(row.get("defindex") or 0),
            releasedate=row.get("releasedate") or "",
            availability=list(row.get("availability") or []),
            purchasable=bool(row.get("purchasable")),
            marketable=bool(row.get("marketable")),
            tradeable=bool(row.get("tradeable")),
            price_cny=row.get("price_cny"),
            contents=contents,
            odds={tier: [float(v) for v in table] for tier, table in (row.get("odds") or {}).items()},
            bonus_rewards=list(row.get("bonus_rewards") or []),
            recycle_rule=row.get("recycle_rule"),
            icon=row.get("icon"),
            source=row.get("source") or "",
            fetched_at=row.get("fetched_at") or "",
        )
    return treasures, payload


# --------------------------------------------------------------------------- 内部


def _read_gz(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rb") as handle:
        return json.loads(handle.read().decode("utf-8"))


def save_snapshot(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.open(path, "wb") as handle:
        handle.write(raw)


def _infobox(wikitext: str) -> dict[str, str]:
    match = re.search(r"\{\{Infobox cosmetic(.*?)\n\}\}", wikitext, re.DOTALL)
    block = match.group(1) if match else wikitext[:2000]
    fields: dict[str, str] = {}
    for line in block.split("\n"):
        if "=" not in line:
            continue
        key, value = line.lstrip("| ").split("=", 1)
        fields[key.strip()] = value.strip()
    return fields


def _clean_links(value: str) -> list[str]:
    value = re.sub(r"<br\s*/?>", " / ", value)
    parts = re.split(r"\s*/\s*|,\s*", value)
    cleaned = [re.sub(r"[\[\]]", "", part).strip() for part in parts]
    return [part for part in cleaned if part]


_SECTION_RE = re.compile(r"^===\s*(.*?)\s*===\s*$", re.MULTILINE)
_COSMETIC_RE = re.compile(r"\{\{Cosmetic\|([^|}]+)")


def _contents(wikitext: str, icons: dict[str, str]) -> dict[str, list[TreasureItem]]:
    result: dict[str, list[TreasureItem]] = {}
    matches = list(_SECTION_RE.finditer(wikitext))
    for index, match in enumerate(matches):
        heading = _strip_markup(match.group(1))
        if heading in ("Drop Rates", "Bonus Reward", "See also", "Gallery"):
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(wikitext)
        body = wikitext[start:end]
        names = [name.strip() for name in _COSMETIC_RE.findall(body)]
        if not names:
            continue
        tier = "Regular" if "Regular" in heading else heading
        result.setdefault(tier, []).extend(
            TreasureItem(name=name, tier=tier, icon=icons.get(name)) for name in names
        )
    return result


def _strip_markup(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\{\{[^}]*\}\}", "", text)
    return text.strip()


def _icon_map(html: str) -> dict[str, str]:
    icons: dict[str, str] = {}
    if not html:
        return icons
    for alt, src in re.findall(r'<img[^>]+alt="([^"]*)"[^>]*src="([^"]+)"', html):
        if not alt or not src.startswith("/commons/"):
            continue
        name = re.sub(r"&#39;", "'", alt)
        icons.setdefault(name, "https://liquipedia.net" + src)
    return icons


def _treasure_icon(html: str, page: str) -> str | None:
    """从宝瓶页 HTML 里找宝瓶自身的图标（infobox 那张）。"""
    if not html:
        return None
    slug = page.replace(" ", "_")
    for pattern in (f"Cosmetic_icon_{slug}", f"Cosmetic_Item_{slug}"):
        match = re.search(r'src="(/commons/images/[^"]*' + re.escape(pattern) + r'[^"]*)"', html)
        if match:
            return "https://liquipedia.net" + match.group(1)
    match = re.search(r'<img[^>]+src="(/commons/images/[^"]+)"', html)
    return "https://liquipedia.net" + match.group(1) if match else None


_HEADER_RE = re.compile(r"^!\s*(?:class=\"header\"[^|]*\|)?(.*)$", re.MULTILINE)
_ROW_RE = re.compile(r"^\|\s*(\d+)(?:st|nd|rd|th)\s*\|\|(.*)$", re.MULTILINE)


def _odds_table(wikitext: str) -> dict[str, list[float]]:
    start = wikitext.find("Drop Rates")
    if start < 0:
        return {}
    table_start = wikitext.find("{|", start)
    table_end = wikitext.find("|}", table_start if table_start >= 0 else start)
    if table_start < 0 or table_end < 0:
        return {}
    table = wikitext[table_start:table_end]
    headers = [_strip_markup(m.group(1)) for m in _HEADER_RE.finditer(table)]
    tiers = [h for h in headers if h in ("Rare", "Very Rare", "Ultra Rare")]
    if not tiers:
        return {}
    rows: dict[str, list[float]] = {tier: [] for tier in tiers}
    for match in _ROW_RE.finditer(table):
        cells = [cell.strip() for cell in match.group(2).split("||")]
        for tier, cell in zip(tiers, cells):
            rows[tier].append(_cell_probability(cell))
    return {tier: values for tier, values in rows.items() if values}


def _cell_probability(cell: str) -> float:
    percent = re.search(r"\(([\d.]+)%\)", cell)
    if percent:
        return float(percent.group(1)) / 100
    ratio = re.search(r"1:([\d.]+)", cell)
    if ratio:
        return 1 / float(ratio.group(1))
    return 0.0


def _cosmic_odds(wikitext: str) -> float:
    match = re.search(r"Cosmically Rare[^\n]*?1\s*/\s*([\d,]+)", wikitext)
    if match:
        return 1 / float(match.group(1).replace(",", ""))
    percent = re.search(r"Cosmically Rare[^\n]*?\(([\d.]+)%\)", wikitext)
    if percent:
        return float(percent.group(1)) / 100
    return 0.0


_BONUS_RE = re.compile(
    r"Open\s+'''(\d+)'''\s+treasures.*?receive[^\n]*\n(.*?)(?=\n\s*(?:Open|==|\|}|$))",
    re.DOTALL,
)


def _bonus_rewards(wikitext: str) -> list[dict[str, Any]]:
    rewards: list[dict[str, Any]] = []
    for match in _BONUS_RE.finditer(wikitext):
        opens = int(match.group(1))
        items = [name.strip() for name in _COSMETIC_RE.findall(match.group(2))]
        if items:
            rewards.append({"opens": opens, "items": items})
    return rewards


_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def _recycle_rule(wikitext: str) -> dict[str, Any] | None:
    match = re.search(
        r"recycl\w*\s+(?:any\s+)?(\w+)\s+(?:sets|items)", wikitext, re.IGNORECASE
    )
    if not match:
        return None
    token = match.group(1).lower()
    inputs = int(token) if token.isdigit() else _NUMBER_WORDS.get(token)
    if not inputs:
        return None
    output = 1
    gain = re.search(r"gain\s+(?:an?\s+)?(\d+)\s+more\s+copy", wikitext, re.IGNORECASE)
    if gain:
        output = int(gain.group(1))
    return {"inputs": inputs, "output": output}
