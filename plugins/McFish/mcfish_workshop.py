"""Workshop recipes for McFish (P6).

Two delivery-style recipes: the trident (kings + sand) and the oracle deck
(all nine cards).  Delivery state lives on the player under ``workshop``.
"""

from __future__ import annotations

from .mcfish_content import CARD_NAMES
from .mcfish_rules import FISH_KINGS

TRIDENT = {
    "id": "trident",
    "name": "三叉戟",
    "gold": 70000,
    "items": {"鱼王鳞片": 8, "时砂": 5},
}

ORACLE = {
    "id": "oracle",
    "name": "神谕卡组",
    "gold": 0,
    "items": {},
    "cards": True,
}

RECIPES = {recipe["id"]: recipe for recipe in (TRIDENT, ORACLE)}


def recipe_of(name: str) -> dict | None:
    return next((recipe for recipe in RECIPES.values() if recipe["name"] == name), None)


def trident_unlocked(state: dict) -> bool:
    if int(state.get("fish_level", 1)) < 60:
        return False
    stats = state.get("king_stats") or {}
    return all(int(stats.get(name, {}).get("caught", 0)) > 0 for name in FISH_KINGS)


def oracle_unlocked(state: dict) -> bool:
    return bool(state.get("cards"))


def recipe_unlocked(recipe: dict, state: dict) -> bool:
    if recipe["id"] == "trident":
        return trident_unlocked(state)
    if recipe["id"] == "oracle":
        return oracle_unlocked(state)
    return False


def progress(state: dict, recipe: dict) -> dict:
    bucket = (state.get("workshop") or {}).get(recipe["id"]) or {}
    return {
        "delivered": dict(bucket.get("delivered") or {}),
        "claimed": bool(bucket.get("claimed")),
    }


def delivered_count(state: dict, recipe: dict, item: str) -> int:
    return int(progress(state, recipe)["delivered"].get(item, 0))


def can_claim(state: dict, recipe: dict) -> bool:
    if progress(state, recipe)["claimed"]:
        return False
    if recipe.get("cards"):
        return all(name in (state.get("cards") or {}) for name in CARD_NAMES)
    delivered = progress(state, recipe)["delivered"]
    return all(int(delivered.get(item, 0)) >= need for item, need in recipe["items"].items())


def mark_delivered(state: dict, recipe: dict, item: str, count: int) -> None:
    bucket = state.setdefault("workshop", {}).setdefault(recipe["id"], {"delivered": {}, "claimed": False})
    delivered = bucket.setdefault("delivered", {})
    delivered[item] = int(delivered.get(item, 0)) + int(count)


def mark_claimed(state: dict, recipe: dict) -> None:
    bucket = state.setdefault("workshop", {}).setdefault(recipe["id"], {"delivered": {}, "claimed": False})
    bucket["claimed"] = True
    bucket["delivered"] = {}


def status_text(state: dict, recipe: dict) -> str:
    info = progress(state, recipe)
    if info["claimed"]:
        return "已领取"
    if not recipe_unlocked(recipe, state):
        if recipe["id"] == "trident":
            return "未解锁（等级 60 + 8 条限时鱼王）"
        return "未解锁（至少钓到 1 张卡牌）"
    if recipe.get("cards"):
        owned = sum(1 for name in CARD_NAMES if name in (state.get("cards") or {}))
        return f"卡牌 {owned}/9"
    parts = [
        f"{item} {int(info['delivered'].get(item, 0))}/{need}"
        for item, need in recipe["items"].items()
    ]
    return "、".join(parts)
