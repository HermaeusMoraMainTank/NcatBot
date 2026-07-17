from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .models import PriceHistory, PricePoint, SaleEvent


def parse_price_history(value: dict[str, Any]) -> PriceHistory:
    raw_points = value.get("prices") if isinstance(value.get("prices"), list) else []
    points = sorted(
        (point for item in raw_points if (point := parse_price_point(item)) is not None),
        key=lambda point: point.recorded_on,
    )
    points = deduplicate_points(points)
    events = build_sale_events(points)

    lowest_info = value.get("lowest_info") if isinstance(value.get("lowest_info"), dict) else {}
    lowest_v2 = value.get("lowest_info_v2") if isinstance(value.get("lowest_info_v2"), dict) else {}
    lowest_price = to_decimal(lowest_info.get("price"))
    lowest_date = parse_history_date(lowest_info.get("date"))
    lowest_discount = int(lowest_info.get("discount") or 0)
    lowest_currency = str(lowest_v2.get("currency") or "").strip()

    if lowest_price is None and points:
        lowest_point = min(points, key=lambda point: point.price)
        lowest_price = lowest_point.price
        lowest_date = lowest_point.recorded_on
        lowest_discount = lowest_point.discount
        lowest_currency = lowest_point.currency
    elif not lowest_currency and points:
        lowest_currency = points[0].currency

    return PriceHistory(
        points=tuple(points),
        events=tuple(events),
        lowest_price=lowest_price,
        lowest_currency=lowest_currency,
        lowest_date=lowest_date,
        lowest_discount=lowest_discount,
        lowest_occurrences=count_lowest_occurrences(points, lowest_price),
        maximum_discount=max((point.discount for point in points), default=0),
    )


def parse_price_point(value: Any) -> PricePoint | None:
    if not isinstance(value, dict):
        return None
    recorded_on = parse_history_date(value.get("date"))
    price = to_decimal(value.get("price"))
    if recorded_on is None or price is None:
        return None
    return PricePoint(
        recorded_on=recorded_on,
        price=price,
        currency=str(value.get("currency") or "").strip(),
        rmb_price=to_decimal(value.get("rmb_price")),
        discount=int(value.get("discount") or 0),
    )


def build_sale_events(points: list[PricePoint]) -> list[SaleEvent]:
    events = []
    active: SaleEvent | None = None
    for point in points:
        if point.discount > 0:
            if active is None:
                active = SaleEvent(
                    started_on=point.recorded_on,
                    ended_on=None,
                    lowest_price=point.price,
                    lowest_rmb_price=point.rmb_price,
                    currency=point.currency,
                    maximum_discount=point.discount,
                )
                continue
            active = SaleEvent(
                started_on=active.started_on,
                ended_on=None,
                lowest_price=min(active.lowest_price, point.price),
                lowest_rmb_price=min_optional(active.lowest_rmb_price, point.rmb_price),
                currency=point.currency or active.currency,
                maximum_discount=max(active.maximum_discount, point.discount),
            )
        elif active is not None:
            events.append(
                SaleEvent(
                    started_on=active.started_on,
                    ended_on=point.recorded_on,
                    lowest_price=active.lowest_price,
                    lowest_rmb_price=active.lowest_rmb_price,
                    currency=active.currency,
                    maximum_discount=active.maximum_discount,
                )
            )
            active = None

    if active is not None:
        events.append(active)
    return events


def count_lowest_occurrences(points: list[PricePoint], lowest_price: Decimal | None) -> int:
    if lowest_price is None:
        return 0
    occurrences = 0
    previously_lowest = False
    for point in points:
        is_lowest = point.price == lowest_price
        if is_lowest and not previously_lowest:
            occurrences += 1
        previously_lowest = is_lowest
    return occurrences


def deduplicate_points(points: list[PricePoint]) -> list[PricePoint]:
    by_date = {point.recorded_on: point for point in points}
    return [by_date[recorded_on] for recorded_on in sorted(by_date)]


def parse_history_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromtimestamp(float(text), timezone.utc).date()
    except (TypeError, ValueError, OSError):
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None


def to_decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def min_optional(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)
