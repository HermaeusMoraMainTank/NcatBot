"""开箱规则实现：CS2 概率/磨损/汰换，Dota 2 递增概率。

数值来源（详见 plugins/CaseOpening/DESIGN.md）：
- CS2 五档概率 79.92 / 15.98 / 3.20 / 0.64 / 0.26（Valve 公示）
- 磨损区间、汰换公式、StatTrak 约 10%（Counter-Strike Wiki，Valve 未公开技术规范）
- Dota 递增概率逐瓶存表（Liquipedia 转录的游戏内公示）
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

# --------------------------------------------------------------------------- CS2

TIER_ORDER: tuple[str, ...] = (
    "Mil-Spec Grade",
    "Restricted",
    "Classified",
    "Covert",
)

TIER_ODDS: dict[str, float] = {
    "Mil-Spec Grade": 0.7992,
    "Restricted": 0.1598,
    "Classified": 0.0320,
    "Covert": 0.0064,
}

RARE_TIER_NAME = "Extraordinary"
RARE_ODDS = 0.0026
STATTRAK_CHANCE = 0.10

WEAR_BANDS: tuple[tuple[str, float, float], ...] = (
    ("Factory New", 0.00, 0.07),
    ("Minimal Wear", 0.07, 0.15),
    ("Field-Tested", 0.15, 0.38),
    ("Well-Worn", 0.38, 0.45),
    ("Battle-Scarred", 0.45, 1.00),
)

WEAR_NAMES = tuple(name for name, _, _ in WEAR_BANDS)

#: 国服客户端外观名（CS2 中文）
WEAR_CN: dict[str, str] = {
    "Factory New": "崭新出厂",
    "Minimal Wear": "略有磨损",
    "Field-Tested": "久经沙场",
    "Well-Worn": "破损不堪",
    "Battle-Scarred": "战痕累累",
}

#: 国服客户端稀有度名（CS2 中文）
RARITY_CN: dict[str, str] = {
    "Consumer Grade": "消费级",
    "Industrial Grade": "工业级",
    "Mil-Spec Grade": "军规级",
    "Restricted": "受限",
    "Classified": "保密",
    "Covert": "隐秘",
    "Extraordinary": "非凡",
    "Contraband": "违禁",
}


def wear_label(wear: str) -> str:
    """外观英文名 → 国服中文；未知值原样返回。"""
    return WEAR_CN.get(wear, wear) if wear else "无磨损"


def rarity_label(rarity: str) -> str:
    return RARITY_CN.get(rarity, rarity)

# 汰换合同不可参与的类别（刀/手套）
_NO_TRADEUP_CATEGORIES = ("Knives", "Gloves")


def wear_for_float(value: float) -> str:
    """把 0~1 的磨损值映射为外观名（区间左闭右开）。"""
    v = min(max(float(value), 0.0), 1.0)
    for name, low, high in WEAR_BANDS:
        if low <= v < high:
            return name
    return WEAR_NAMES[-1]


def wear_probabilities(min_float: float, max_float: float) -> dict[str, float]:
    """在 [min_float, max_float] 上均匀取磨损时，各外观的出现概率。"""
    span = float(max_float) - float(min_float)
    if span <= 0:
        return {}
    result: dict[str, float] = {}
    for name, low, high in WEAR_BANDS:
        overlap = min(float(max_float), high) - max(float(min_float), low)
        if overlap > 0:
            result[name] = overlap / span
    return result


def roll_float(min_float: float, max_float: float, rng: random.Random | None = None) -> float:
    rng = rng or random
    return round(rng.uniform(float(min_float), float(max_float)), 8)


def market_hash_name(
    name: str,
    wear: str | None = None,
    *,
    stattrak: bool = False,
    souvenir: bool = False,
) -> str:
    """拼出 Steam/Skinport 使用的市场名。

    ``★ Karambit | Fade (Factory New)`` / ``StatTrak™ AK-47 | Redline (Field-Tested)``
    / ``Souvenir AWP | Dragon Lore (Factory New)``；无磨损的（原版刀）只保留 ``★`` 前缀。
    """
    base = name
    if souvenir and not base.startswith("Souvenir "):
        base = "Souvenir " + base
    if stattrak and not base.startswith("StatTrak™"):
        if base.startswith("★ "):
            base = "★ StatTrak™ " + base[2:]
        else:
            base = "StatTrak™ " + base
    if wear:
        return f"{base} ({wear})"
    return base


def is_tradeup_compatible(category: str) -> bool:
    """刀/手套不可参与汰换；Souvenir 变体标记不影响普通皮肤参与。"""
    return category not in _NO_TRADEUP_CATEGORIES


def next_tier(tier: str) -> str | None:
    try:
        index = TIER_ORDER.index(tier)
    except ValueError:
        return None
    return TIER_ORDER[index + 1] if index + 1 < len(TIER_ORDER) else None


@dataclass(slots=True)
class TradeupInput:
    float_value: float
    tier: str
    stattrak: bool
    collection_id: str


def collection_weights(inputs: Sequence[TradeupInput], eligible: Mapping[str, int]) -> dict[str, int]:
    """按输入件数给收藏品加权，``eligible`` 为"存在下一档物品"的收藏品件数表。"""
    weights: dict[str, int] = {}
    for item in inputs:
        if item.collection_id in eligible and eligible[item.collection_id] > 0:
            weights[item.collection_id] = weights.get(item.collection_id, 0) + 1
    return weights


def tradeup_float(inputs: Sequence[TradeupInput], target_min: float, target_max: float) -> float:
    """真实公式：先取输入磨损均值，再按目标皮肤的 float 区间线性映射。"""
    if not inputs:
        raise ValueError("trade-up requires inputs")
    average = sum(float(item.float_value) for item in inputs) / len(inputs)
    value = average * (float(target_max) - float(target_min)) + float(target_min)
    return round(min(max(value, float(target_min)), float(target_max)), 8)


# --------------------------------------------------------------------------- Dota 2

DOTA_TIER_ORDER: tuple[str, ...] = ("Rare", "Very Rare", "Ultra Rare", "Cosmically Rare")


def odds_for_open(odds_table: Sequence[float], counter: int) -> float:
    """按"第几次开瓶"取概率；表尾即保底，越界按最后一行处理。"""
    if not odds_table:
        return 0.0
    index = max(1, int(counter)) - 1
    index = min(index, len(odds_table) - 1)
    return float(odds_table[index])


def pity_row(odds_table: Sequence[float], counter: int) -> int:
    """返回当前使用的行号（1 起），用于展示"第 N 次"。"""
    if not odds_table:
        return 1
    return min(max(1, int(counter)), len(odds_table))


def guaranteed_row(odds_table: Sequence[float]) -> int | None:
    """表里第一个 100% 的行号，没有则 None。"""
    for index, value in enumerate(odds_table, start=1):
        if float(value) >= 0.999:
            return index
    return None


def roll_dota_bonus(
    odds: Mapping[str, Sequence[float]],
    counters: Mapping[str, int],
    cosmic_odds: float = 0.0,
    rng: random.Random | None = None,
) -> list[str]:
    """一次开瓶可能命中多个额外档位。

    官方表格里各档是**独立判定**的：例如某瓶第 50 次珍稀与珍稀超凡都是 100%，
    一次开瓶就会同时给两件额外奖励。这里按独立判定返回命中的全部档位，
    再叠加固定的宇宙珍稀。
    """
    rng = rng or random
    hits: list[str] = []
    for tier in ("Rare", "Very Rare", "Ultra Rare"):
        table = odds.get(tier)
        if not table:
            continue
        if rng.random() < odds_for_open(table, counters.get(tier, 1)):
            hits.append(tier)
    if cosmic_odds and rng.random() < cosmic_odds:
        hits.append("Cosmically Rare")
    return hits


def advance_counters(
    counters: Mapping[str, int],
    odds: Mapping[str, Sequence[float]],
    hit_tiers: Iterable[str] | str | None,
) -> dict[str, int]:
    """命中档清零回 1，其余档 +1（封顶末行）。"""
    if isinstance(hit_tiers, str) or hit_tiers is None:
        hit_set = {hit_tiers} if hit_tiers else set()
    else:
        hit_set = set(hit_tiers)
    result = dict(counters)
    for tier, table in odds.items():
        if not table:
            continue
        result[tier] = 1 if tier in hit_set else min(result.get(tier, 1) + 1, len(table))
    if "Cosmically Rare" in hit_set:
        result["Cosmically Rare"] = 1
    return result


def regular_pick(
    items: Iterable[str],
    collected: Iterable[str],
    rng: random.Random | None = None,
) -> str:
    """普通档掉落：优先未收集的；全收集后回到全池随机（官方重复保护规则）。"""
    rng = rng or random
    pool = list(items)
    if not pool:
        raise ValueError("empty treasure pool")
    seen = set(collected)
    fresh = [name for name in pool if name not in seen]
    return rng.choice(fresh or pool)
