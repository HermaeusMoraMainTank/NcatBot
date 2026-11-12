"""开箱插件共享数据模型。

CS2 数据来自 ByMykel/CSGO-API（MIT），Dota 2 数据来自 Liquipedia（CC-BY-SA 3.0），
价格来自 Skinport 公共接口。字段保持与上游 JSON 同名，便于排查数据来源。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SkinItem:
    """一件 CS2 皮肤（按图案聚合，不含磨损后缀）。"""

    id: str
    name: str
    weapon: str
    category: str
    rarity_id: str
    rarity_name: str
    color: str
    min_float: float | None
    max_float: float | None
    stattrak: bool
    souvenir: bool
    paint_index: str | None
    image: str | None
    collection_id: str = ""
    collection_name: str = ""
    crate_ids: tuple[str, ...] = ()
    name_cn: str = ""
    collection_name_cn: str = ""

    @property
    def tradeable_rarity(self) -> str:
        return self.rarity_name


@dataclass(slots=True)
class CrateInfo:
    """一个 CS2 箱子（武器箱 / 手套箱）。"""

    id: str
    name: str
    type: str | None
    market_hash_name: str
    first_sale_date: str
    image: str | None
    key_name: str | None
    cn_name: str = ""
    tiers: dict[str, list[str]] = field(default_factory=dict)
    rare: list[str] = field(default_factory=list)
    tier_odds: dict[str, float] = field(default_factory=dict)

    def items_of(self, tier: str) -> list[str]:
        return list(self.tiers.get(tier) or [])


@dataclass(slots=True)
class PriceQuote:
    market_hash_name: str
    currency: str
    min_price: float | None
    median_price: float | None
    mean_price: float | None
    quantity: int
    updated_at: int

    def buy(self) -> float | None:
        return self.min_price or self.median_price

    def sell(self, fee: float) -> float | None:
        base = self.median_price or self.min_price
        return round(base * (1 - fee), 2) if base else None


@dataclass(slots=True)
class TreasureItem:
    name: str
    tier: str
    icon: str | None = None


@dataclass(slots=True)
class Treasure:
    """一个 Dota 2 宝瓶。"""

    name: str
    cn_name: str
    defindex: int
    releasedate: str
    availability: list[str]
    purchasable: bool
    marketable: bool
    tradeable: bool
    price_cny: float | None
    contents: dict[str, list[TreasureItem]] = field(default_factory=dict)
    odds: dict[str, list[float]] = field(default_factory=dict)
    bonus_rewards: list[dict[str, Any]] = field(default_factory=list)
    recycle_rule: dict[str, Any] | None = None
    icon: str | None = None
    source: str = ""
    fetched_at: str = ""

    @property
    def tiers(self) -> list[str]:
        order = ["Rare", "Very Rare", "Ultra Rare", "Cosmically Rare"]
        return [tier for tier in order if tier in self.contents]

    def regular_items(self) -> list[TreasureItem]:
        return list(self.contents.get("Regular") or [])

    def items_of(self, tier: str) -> list[TreasureItem]:
        return list(self.contents.get(tier) or [])
