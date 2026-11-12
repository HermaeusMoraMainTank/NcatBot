#!/usr/bin/env python
"""生成开箱插件的数据快照。

用法::

    python scripts/build_case_data.py                # 全量抓取（ByMykel + Liquipedia + Skinport）
    python scripts/build_case_data.py --refresh      # 忽略缓存重新抓
    python scripts/build_case_data.py --skip-prices  # 只重建物品/宝瓶快照

产物写入 ``plugins/common/case_data/data/``（随仓库分发，约 1MB）：

- ``cs2.json.gz``     10 个箱子 + 全部皮肤（供汰换合同）
- ``dota.json.gz``    13 个宝瓶（内容分层 + 官方递增概率表 + 累计奖励）
- ``prices_730.json.gz`` / ``prices_570.json.gz``  价格兜底快照
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins"))

from common.case_data import snapshot as snap
from common.case_data.prices import fetch_skinport
from common.case_data.rules import WEAR_NAMES, market_hash_name

CACHE = ROOT / "temp" / "case_data_cache"
RAW_BASE = "https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/en"
RAW_BASE_ZH = "https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/zh-CN"
LIQUIPEDIA_API = "https://liquipedia.net/dota2/api.php"
UA = "NcatBot-CaseData/1.0 (github.com/huan-yp/NcatBot)"
POLITE_DELAY = 2.5


def _cache_path(*parts: str) -> Path:
    path = CACHE.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def fetch_json(url: str, cache: Path, *, refresh: bool) -> Any:
    if cache.exists() and not refresh:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    response = httpx.get(
        url,
        timeout=600,
        follow_redirects=True,
        headers={"User-Agent": UA},
    )
    response.raise_for_status()
    cache.write_text(json.dumps(response.json(), ensure_ascii=False), encoding="utf-8")
    return response.json()


async def fetch_liquipedia_page(page: str, *, refresh: bool) -> dict[str, str]:
    cache = _cache_path("liquipedia", page.replace("/", "_").replace(" ", "_") + ".json")
    if cache.exists() and not refresh:
        return json.loads(cache.read_text(encoding="utf-8"))
    params = {
        "action": "parse",
        "page": page,
        "prop": "wikitext|text",
        "format": "json",
        "formatversion": "2",
    }
    headers = {"User-Agent": UA, "Accept-Encoding": "gzip"}
    async with httpx.AsyncClient(timeout=60, headers=headers) as client:
        response = await client.get(LIQUIPEDIA_API, params=params)
        response.raise_for_status()
        payload = response.json()
    parse = payload.get("parse") or {}
    data = {
        "page": page,
        "wikitext": (parse.get("wikitext") or ""),
        "text": (parse.get("text") or ""),
    }
    cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    await asyncio.sleep(POLITE_DELAY)
    return data


def load_skinport(app_id: int, *, refresh: bool) -> list[dict[str, Any]]:
    raw_cache = _cache_path("skinport", f"{app_id}.json.gz")
    if raw_cache.exists() and not refresh:
        with gzip.open(raw_cache, "rb") as handle:
            return json.loads(handle.read().decode("utf-8"))
    quotes = asyncio.run(fetch_skinport(app_id))
    payload = [
        {
            "market_hash_name": name,
            "currency": quote.currency,
            "min_price": quote.min_price,
            "median_price": quote.median_price,
            "mean_price": quote.mean_price,
            "quantity": quote.quantity,
            "updated_at": quote.updated_at,
        }
        for name, quote in quotes.items()
    ]
    with gzip.open(raw_cache, "wb") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return payload


def relevant_price_names(payload: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    items = payload["items"]
    for case in payload["cases"]:
        names.add(case["market_hash_name"])
        if case.get("key_name"):
            names.add(case["key_name"])
        souvenir = bool(case.get("tier_odds"))
        ids = [item for ids in case["tiers"].values() for item in ids] + list(case.get("rare") or [])
        for skin_id in ids:
            skin = items.get(skin_id)
            if not skin:
                continue
            base = skin["name"]
            if souvenir:
                for wear in WEAR_NAMES:
                    names.add(market_hash_name(base, wear, souvenir=True))
                continue
            suffixes = [""]
            if skin.get("stattrak"):
                suffixes.append("StatTrak™ ")
            for prefix in suffixes:
                for wear in WEAR_NAMES:
                    names.add(market_hash_name(base, wear, stattrak=prefix.startswith("StatTrak")))
    # 汰换合同会用到的收藏品皮肤也一并留存价格
    for skin in items.values():
        names.add(market_hash_name(skin["name"], "Field-Tested"))
    return names


def write_price_snapshot(raw: list[dict[str, Any]], keep: set[str], path: Path) -> int:
    rows = [row for row in raw if row.get("market_hash_name") in keep]
    payload = {
        "fetched_at": time.time(),
        "quotes": {row["market_hash_name"]: row for row in rows},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="构建开箱插件数据快照")
    parser.add_argument("--refresh", action="store_true", help="忽略本地缓存重新抓取")
    parser.add_argument("--skip-prices", action="store_true", help="不重建价格快照")
    args = parser.parse_args()

    started = time.time()
    print("[1/4] 抓取 ByMykel CSGO-API ...")
    crates = fetch_json(f"{RAW_BASE}/crates.json", _cache_path("bymykel", "crates.json"), refresh=args.refresh)
    skins = fetch_json(f"{RAW_BASE}/skins.json", _cache_path("bymykel", "skins.json"), refresh=args.refresh)
    keys = fetch_json(f"{RAW_BASE}/keys.json", _cache_path("bymykel", "keys.json"), refresh=args.refresh)

    zh_crates = fetch_json(f"{RAW_BASE_ZH}/crates.json", _cache_path("zh", "crates.json"), refresh=args.refresh)
    zh_skins = fetch_json(f"{RAW_BASE_ZH}/skins.json", _cache_path("zh", "skins.json"), refresh=args.refresh)
    payload = snap.build_cs_snapshot(crates, skins, keys, zh_crates=zh_crates, zh_skins=zh_skins)
    snap.save_snapshot(snap.SNAPSHOT_DIR / snap.CS_FILE, payload)
    print(
        f"      -> 箱子 {len(payload['cases'])} 个，皮肤 {len(payload['items'])} 条，"
        f"{_size(snap.SNAPSHOT_DIR / snap.CS_FILE)}"
    )

    print("[2/4] 抓取 Liquipedia 宝瓶页 ...")
    treasures = []
    for index, (page, cn_name, price) in enumerate(snap.DOTA_SELECTED, start=1):
        data = asyncio.run(fetch_liquipedia_page(page, refresh=args.refresh))
        treasure = snap.parse_dota_page(
            page,
            data.get("wikitext") or "",
            html=data.get("text") or "",
            cn_name=cn_name,
            price_cny=price,
        )
        tiers = {tier: len(items) for tier, items in treasure.contents.items()}
        odds_rows = {tier: len(table) for tier, table in treasure.odds.items()}
        print(f"      [{index:02d}] {page}: 内容 {tiers} 概率行 {odds_rows}")
        treasures.append(treasure)

    dota_payload = snap.build_dota_snapshot(treasures)
    snap.save_snapshot(snap.SNAPSHOT_DIR / snap.DOTA_FILE, dota_payload)
    print(f"      -> 宝瓶 {len(treasures)} 个，{_size(snap.SNAPSHOT_DIR / snap.DOTA_FILE)}")

    if args.skip_prices:
        print("[3/4] 跳过价格快照")
        print(f"[4/4] 完成，用时 {time.time() - started:.1f}s")
        return 0

    print("[3/4] 抓取 Skinport 价格 ...")
    raw_730 = load_skinport(730, refresh=args.refresh)
    keep = relevant_price_names(payload)
    kept = write_price_snapshot(raw_730, keep, snap.SNAPSHOT_DIR / snap.PRICES_730)
    print(f"      -> 730 保留 {kept}/{len(raw_730)} 条，{_size(snap.SNAPSHOT_DIR / snap.PRICES_730)}")

    raw_570 = load_skinport(570, refresh=args.refresh)
    dota_names = {
        item.name
        for treasure in treasures
        for items in treasure.contents.values()
        for item in items
    }
    kept_dota = write_price_snapshot(raw_570, dota_names, snap.SNAPSHOT_DIR / snap.PRICES_570)
    print(f"      -> 570 保留 {kept_dota}/{len(raw_570)} 条，{_size(snap.SNAPSHOT_DIR / snap.PRICES_570)}")

    print(f"[4/4] 完成，用时 {time.time() - started:.1f}s")
    return 0


def _size(path: Path) -> str:
    return f"{path.stat().st_size / 1024:.0f} KB" if path.exists() else "missing"


if __name__ == "__main__":
    raise SystemExit(main())
