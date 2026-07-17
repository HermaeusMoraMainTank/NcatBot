from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class GameIdentity:
    appid: int
    label: str


@dataclass(frozen=True)
class SteamPrice:
    current: Decimal
    initial: Decimal
    currency: str
    discount: int


@dataclass(frozen=True)
class SteamGameDetails:
    appid: int
    name: str
    game_type: str
    is_free: bool
    coming_soon: bool
    release_date: str
    price: SteamPrice | None
    developers: tuple[str, ...]
    publishers: tuple[str, ...]
    platforms: tuple[str, ...]
    genres: tuple[str, ...]
    categories: tuple[str, ...]
    languages: tuple[str, ...]
    controller_support: str
    achievement_count: int
    dlc_count: int
    metacritic_score: int | None
    recommendation_count: int | None
    required_age: str
    content_notes: str
    website: str

    @property
    def store_url(self) -> str:
        return f"https://store.steampowered.com/app/{self.appid}/"


@dataclass(frozen=True)
class PricePoint:
    recorded_on: date
    price: Decimal
    currency: str
    rmb_price: Decimal | None
    discount: int


@dataclass(frozen=True)
class SaleEvent:
    started_on: date
    ended_on: date | None
    lowest_price: Decimal
    lowest_rmb_price: Decimal | None
    currency: str
    maximum_discount: int

    def duration_days(self, today: date) -> int:
        end = self.ended_on or today
        return max((end - self.started_on).days, 0)


@dataclass(frozen=True)
class PriceHistory:
    points: tuple[PricePoint, ...]
    events: tuple[SaleEvent, ...]
    lowest_price: Decimal | None
    lowest_currency: str
    lowest_date: date | None
    lowest_discount: int
    lowest_occurrences: int
    maximum_discount: int

    @property
    def current(self) -> PricePoint | None:
        return self.points[-1] if self.points else None

    @property
    def active_sale(self) -> SaleEvent | None:
        if self.events and self.events[-1].ended_on is None:
            return self.events[-1]
        return None

    @property
    def last_completed_sale(self) -> SaleEvent | None:
        for event in reversed(self.events):
            if event.ended_on is not None:
                return event
        return None


@dataclass(frozen=True)
class RegionPrice:
    code: str
    name: str
    current_rmb: Decimal
    initial_rmb: Decimal | None
    discount: int

    @property
    def label(self) -> str:
        return f"{self.name} / {self.code}" if self.name else self.code
