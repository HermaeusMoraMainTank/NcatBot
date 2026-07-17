from __future__ import annotations

import html
import re
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from .models import RegionPrice, SteamGameDetails, SteamPrice

STEAM_STORE_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
STEAM_APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"
XIAOHEIHE_GLOBAL_PRICE_URL = "https://api.xiaoheihe.cn/game/get_game_global_prices/"
XIAOHEIHE_PRICE_HISTORY_URL = "https://api.xiaoheihe.cn/game/get_game_prices/history/v2"
XIAOHEIHE_WEB_BASE_URL = "https://www.xiaoheihe.cn"


class ApiResponseError(RuntimeError):
    pass


class SteamStoreClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def search(self, query: str, country: str, language: str) -> list[dict[str, Any]]:
        data = await self._get_json(
            STEAM_STORE_SEARCH_URL,
            {"term": query, "cc": country, "l": language},
        )
        items = data.get("items") if isinstance(data, dict) else []
        results = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            appid = item.get("id") or item.get("appid")
            name = str(item.get("name") or "").strip()
            if appid and name:
                results.append({"appid": int(appid), "name": name})
        return results

    async def details(self, appid: int, country: str, language: str) -> SteamGameDetails:
        payload = await self._get_json(
            STEAM_APP_DETAILS_URL,
            {"appids": appid, "cc": country, "l": language},
        )
        entry = payload.get(str(appid)) if isinstance(payload, dict) else None
        if not isinstance(entry, dict) or not entry.get("success"):
            raise ApiResponseError(f"Steam 商店没有返回 appid={appid} 的游戏资料。")
        data = entry.get("data")
        if not isinstance(data, dict):
            raise ApiResponseError(f"Steam 商店返回了无效的游戏资料：appid={appid}")
        return parse_steam_details(appid, data)

    async def _get_json(self, url: str, params: dict[str, Any]) -> Any:
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        return response.json()


class HeyboxClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def price_history(self, appid: int, country: str, days: int) -> dict[str, Any]:
        response = await self.client.get(
            XIAOHEIHE_PRICE_HISTORY_URL,
            params={"appid": appid, "platf": "steam", "cc": country, "days": days},
            headers={"Referer": f"{XIAOHEIHE_WEB_BASE_URL}/app/topic/game/pc/{appid}"},
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get("status") != "ok":
            raise ApiResponseError(f"小黑盒历史价格接口返回异常：{data}")
        result = data.get("result")
        return result if isinstance(result, dict) else {}

    async def global_prices(self, appid: int) -> list[RegionPrice]:
        response = await self.client.get(
            XIAOHEIHE_GLOBAL_PRICE_URL,
            params={
                "lang": "zh-cn",
                "os_type": "web",
                "offset": 0,
                "limit": 100,
                "steam_appid": appid,
            },
            headers={"Referer": f"{XIAOHEIHE_WEB_BASE_URL}/app/topic/game/pc/{appid}"},
        )
        response.raise_for_status()
        data = response.json()
        prices = data.get("result", {}).get("prices", []) if isinstance(data, dict) else []
        return parse_region_prices(prices)


def parse_steam_details(appid: int, data: dict[str, Any]) -> SteamGameDetails:
    release = data.get("release_date") if isinstance(data.get("release_date"), dict) else {}
    metacritic = data.get("metacritic") if isinstance(data.get("metacritic"), dict) else {}
    recommendations = (
        data.get("recommendations") if isinstance(data.get("recommendations"), dict) else {}
    )
    achievements = data.get("achievements") if isinstance(data.get("achievements"), dict) else {}
    descriptors = (
        data.get("content_descriptors") if isinstance(data.get("content_descriptors"), dict) else {}
    )
    platforms = data.get("platforms") if isinstance(data.get("platforms"), dict) else {}

    return SteamGameDetails(
        appid=appid,
        name=str(data.get("name") or f"appid={appid}").strip(),
        game_type=str(data.get("type") or "unknown").strip(),
        is_free=bool(data.get("is_free")),
        coming_soon=bool(release.get("coming_soon")),
        release_date=str(release.get("date") or "").strip(),
        price=parse_steam_price(data.get("price_overview")),
        developers=string_tuple(data.get("developers")),
        publishers=string_tuple(data.get("publishers")),
        platforms=tuple(name for name, enabled in platforms.items() if enabled),
        genres=description_tuple(data.get("genres")),
        categories=description_tuple(data.get("categories")),
        languages=parse_languages(data.get("supported_languages")),
        controller_support=str(data.get("controller_support") or "").strip(),
        achievement_count=int(achievements.get("total") or 0),
        dlc_count=len(data.get("dlc") or []),
        metacritic_score=optional_int(metacritic.get("score")),
        recommendation_count=optional_int(recommendations.get("total")),
        required_age=str(data.get("required_age") or "").strip(),
        content_notes=clean_html_text(descriptors.get("notes")),
        website=str(data.get("website") or "").strip(),
    )


def parse_steam_price(value: Any) -> SteamPrice | None:
    if not isinstance(value, dict):
        return None
    try:
        initial = Decimal(str(value["initial"])) / 100
        current = Decimal(str(value["final"])) / 100
    except (KeyError, InvalidOperation, TypeError):
        return None
    return SteamPrice(
        current=current,
        initial=initial,
        currency=str(value.get("currency") or "").upper(),
        discount=int(value.get("discount_percent") or 0),
    )


def parse_region_prices(value: Any) -> list[RegionPrice]:
    if not isinstance(value, list):
        return []
    regions = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        code = str(item.get("cc") or "").upper()
        current = optional_decimal(item.get("current"))
        if not code or current is None or code in seen:
            continue
        seen.add(code)
        regions.append(
            RegionPrice(
                code=code,
                name=str(item.get("name") or "").strip(),
                current_rmb=current,
                initial_rmb=optional_decimal(item.get("initial")),
                discount=int(item.get("discount") or 0),
            )
        )
    return regions


def optional_decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def description_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        str(item.get("description") or "").strip()
        for item in value
        if isinstance(item, dict) and str(item.get("description") or "").strip()
    )


def parse_languages(value: Any) -> tuple[str, ...]:
    raw_text = str(value or "").split("<br", 1)[0]
    text = clean_html_text(raw_text).replace("*", "")
    return tuple(part.strip() for part in text.split(",") if part.strip())


def clean_html_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"<br\s*/?>", ", ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip(" ,")
