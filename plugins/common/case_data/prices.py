"""价格层：Skinport 公共接口 + 磁盘缓存。

Skinport 要求 ``Accept-Encoding: br, gzip``（只发 gzip 会 406），货币用 CNY。
Steam 社区市场在部署环境不可达，所以这里只保留 Skinport 一个实时源。
"""

from __future__ import annotations

import gzip
import json
import time
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx

from .models import PriceQuote

SKINPORT_URL = "https://api.skinport.com/v1/items"
DEFAULT_TTL = 6 * 3600


class PriceSourceError(RuntimeError):
    pass


def parse_skinport(payload: Iterable[dict[str, Any]], currency: str = "CNY") -> dict[str, PriceQuote]:
    quotes: dict[str, PriceQuote] = {}
    for row in payload:
        name = str(row.get("market_hash_name") or "").strip()
        if not name:
            continue
        quotes[name] = PriceQuote(
            market_hash_name=name,
            currency=str(row.get("currency") or currency),
            min_price=_float(row.get("min_price")),
            median_price=_float(row.get("median_price")),
            mean_price=_float(row.get("mean_price")),
            quantity=int(row.get("quantity") or 0),
            updated_at=int(row.get("updated_at") or 0),
        )
    return quotes


def _float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


async def fetch_skinport(
    app_id: int,
    *,
    currency: str = "CNY",
    timeout: float = 90.0,
) -> dict[str, PriceQuote]:
    headers = {
        "Accept-Encoding": "br, gzip",
        "User-Agent": "NcatBot-CaseData/1.0",
    }
    params = {"app_id": str(app_id), "currency": currency}
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(SKINPORT_URL, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        raise PriceSourceError(f"Skinport 请求失败: {exc}") from exc
    if not isinstance(payload, list):
        raise PriceSourceError("Skinport 返回格式异常")
    return parse_skinport(payload, currency)


def save_prices(path: Path, quotes: dict[str, PriceQuote], *, fetched_at: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": float(fetched_at or time.time()),
        "quotes": {name: asdict(quote) for name, quote in quotes.items()},
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.open(path, "wb") as handle:
        handle.write(raw)


def load_prices(path: Path) -> tuple[dict[str, PriceQuote], float]:
    if not path.exists():
        return {}, 0.0
    with gzip.open(path, "rb") as handle:
        payload = json.loads(handle.read().decode("utf-8"))
    quotes = {
        name: PriceQuote(**row) for name, row in (payload.get("quotes") or {}).items()
    }
    return quotes, float(payload.get("fetched_at") or 0.0)


def is_fresh(path: Path, ttl: int = DEFAULT_TTL) -> bool:
    if not path.exists():
        return False
    try:
        with gzip.open(path, "rb") as handle:
            payload = json.loads(handle.read().decode("utf-8"))
        return time.time() - float(payload.get("fetched_at") or 0) < ttl
    except Exception:  # noqa: BLE001 - 损坏缓存当作过期
        return False


class PriceBook:
    """内存价格表 + 买价/回收价换算。"""

    def __init__(self, quotes: dict[str, PriceQuote] | None = None, fetched_at: float = 0.0) -> None:
        self.quotes: dict[str, PriceQuote] = quotes or {}
        self.fetched_at = fetched_at

    @classmethod
    def from_file(cls, path: Path) -> PriceBook:
        quotes, fetched_at = load_prices(path)
        return cls(quotes, fetched_at)

    def __len__(self) -> int:
        return len(self.quotes)

    def quote(self, market_hash_name: str) -> PriceQuote | None:
        return self.quotes.get(market_hash_name)

    def buy_price(self, market_hash_name: str) -> float | None:
        quote = self.quote(market_hash_name)
        return quote.buy() if quote else None

    def sell_price(self, market_hash_name: str, fee: float) -> float | None:
        quote = self.quote(market_hash_name)
        return quote.sell(fee) if quote else None

    def has(self, market_hash_name: str) -> bool:
        quote = self.quote(market_hash_name)
        return bool(quote and (quote.min_price or quote.median_price))

    def age_hours(self) -> float:
        return (time.time() - self.fetched_at) / 3600 if self.fetched_at else float("inf")
