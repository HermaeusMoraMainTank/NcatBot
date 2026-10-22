"""Management operations for the McFish admin panel.

The service deliberately exposes a small, typed surface instead of accepting
arbitrary SQL or raw player JSON from the browser.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from .mcfish_rules import FISH_KINGS, HIDDEN_FISH

DEFAULT_ROD_DURABILITIES = {
    "木竿": 30,
    "铁竿": 50,
    "金竿": 70,
    "钻石竿": 100,
    "下界合金竿": 150,
    "三叉戟": 301,
}


class McFishAdminService:
    """Read and write the parts of McFish that are safe to manage remotely."""

    def __init__(
        self,
        plugin,
        *,
        rod_names: set[str],
        fixed_price_names: set[str],
        rod_durabilities: dict[str, int] | None = None,
    ):
        self.plugin = plugin
        self.rod_names = set(rod_names)
        self.fixed_price_names = set(fixed_price_names)
        self.rod_durabilities = dict(rod_durabilities or DEFAULT_ROD_DURABILITIES)

    @property
    def db(self):
        return self.plugin._db

    def _account(self, user_id: str) -> dict[str, Any]:
        economy = getattr(self.plugin, "_economy", None)
        store = getattr(economy, "store", None)
        if store is None:
            return {"balance": 0}
        try:
            account = store.account(str(user_id))
        except (AttributeError, KeyError, TypeError, ValueError, sqlite3.Error):
            return {"balance": 0}
        return {"balance": int(account.get("balance", 0))}

    def _member_name(self, user_id: str) -> str:
        row = self.db.execute(
            "SELECT display_name FROM fishing_group_members WHERE user_id = ? "
            "ORDER BY last_seen_at DESC LIMIT 1",
            (str(user_id),),
        ).fetchone()
        return str(row[0]) if row and row[0] else str(user_id)

    @staticmethod
    def _avatar_url(kind: str, identity: str) -> str:
        return f"/mcfish-avatar/{kind}/{quote(str(identity), safe='')}"

    @staticmethod
    def _item_count(value: Any) -> int:
        if isinstance(value, list):
            return len(value)
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def _player_summary(
        self,
        user_id: str,
        display_name: str = "",
        updated_at: float | None = None,
        raw_state: str | None = None,
    ) -> dict[str, Any]:
        if raw_state:
            try:
                state = json.loads(raw_state)
            except (TypeError, ValueError):
                state = {}
        else:
            state = self.plugin._state(str(user_id))
        pack = state.get("pack") if isinstance(state, dict) else {}
        pack = pack if isinstance(pack, dict) else {}
        return {
            "user_id": str(user_id),
            "display_name": display_name or self._member_name(str(user_id)),
            "avatar_url": self._avatar_url("user", user_id),
            "fish_level": int(state.get("fish_level", 1) or 1),
            "fish_xp": int(state.get("fish_xp", 0) or 0),
            "gold": self._account(str(user_id))["balance"],
            "equipped_rod": (state.get("equip") or {}).get("name"),
            "inventory_total": sum(self._item_count(value) for value in pack.values()),
            "updated_at": updated_at,
        }

    def groups(self, search: str = "") -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT g.group_id, g.group_name, g.updated_at, COUNT(m.user_id) "
            "FROM fishing_groups g LEFT JOIN fishing_group_members m ON m.group_id = g.group_id "
            "GROUP BY g.group_id, g.group_name, g.updated_at ORDER BY g.updated_at DESC"
        ).fetchall()
        result = [
            {
                "group_id": str(group_id),
                "group_name": str(group_name or group_id),
                "avatar_url": self._avatar_url("group", group_id),
                "player_count": int(player_count),
                "updated_at": float(updated_at),
            }
            for group_id, group_name, updated_at, player_count in rows
        ]
        ungrouped = self.db.execute(
            "SELECT COUNT(*) FROM players p WHERE NOT EXISTS "
            "(SELECT 1 FROM fishing_group_members m WHERE m.user_id = p.user_id)"
        ).fetchone()
        if ungrouped and int(ungrouped[0]) > 0:
            result.append(
                {
                    "group_id": "__ungrouped__",
                    "group_name": "未分组玩家",
                    "avatar_url": None,
                    "player_count": int(ungrouped[0]),
                    "updated_at": None,
                }
            )
        search = str(search or "").strip().lower()
        if not search:
            return result
        return [
            row
            for row in result
            if search in row["group_id"].lower() or search in row["group_name"].lower()
        ]

    def group_players(self, group_id: str, search: str = "") -> list[dict[str, Any]]:
        group_id = str(group_id)
        if group_id == "__ungrouped__":
            rows = self.db.execute(
                "SELECT p.user_id, '', p.updated_at, p.state FROM players p "
                "WHERE NOT EXISTS (SELECT 1 FROM fishing_group_members m WHERE m.user_id = p.user_id) "
                "ORDER BY p.updated_at DESC"
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT m.user_id, m.display_name, p.updated_at, p.state "
                "FROM fishing_group_members m LEFT JOIN players p ON p.user_id = m.user_id "
                "WHERE m.group_id = ? ORDER BY m.last_seen_at DESC",
                (group_id,),
            ).fetchall()
        result = [
            self._player_summary(user_id, display_name, updated_at, raw_state)
            for user_id, display_name, updated_at, raw_state in rows
        ]
        search = str(search or "").strip().lower()
        if not search:
            return result
        return [
            row
            for row in result
            if search in row["user_id"].lower() or search in row["display_name"].lower()
        ]

    def player(self, user_id: str) -> dict[str, Any]:
        user_id = str(user_id)
        row = self.db.execute(
            "SELECT state, updated_at FROM players WHERE user_id = ?", (user_id,)
        ).fetchone()
        state = self.plugin._state(user_id)
        pack = state.get("pack", {})
        items = []
        for name, value in sorted(pack.items()):
            count = self._item_count(value)
            if count <= 0:
                continue
            entry: dict[str, Any] = {
                "name": str(name),
                "kind": "rod" if name in self.rod_names else "item",
                "count": count,
            }
            if isinstance(value, list):
                entry["details"] = [
                    {
                        "durable": int(item.get("durable", 0)),
                        "maintenance": int(item.get("maintenance", 0)),
                        "induce": int(item.get("induce", 0)),
                        "favor": int(item.get("favor", 0)),
                        "craftable": item.get("craftable", True) is not False,
                    }
                    for item in value
                    if isinstance(item, dict)
                ]
            items.append(entry)
        groups = [
            {
                "group_id": group_id,
                "group_name": group_name or group_id,
                "avatar_url": self._avatar_url("group", group_id),
            }
            for group_id, group_name in self.db.execute(
                "SELECT group_id, group_name FROM fishing_groups g "
                "JOIN fishing_group_members m USING (group_id) WHERE m.user_id = ? "
                "ORDER BY g.group_id",
                (user_id,),
            ).fetchall()
        ]
        summary = self._player_summary(
            user_id,
            updated_at=float(row[1]) if row else None,
            raw_state=json.dumps(state, ensure_ascii=False),
        )
        summary.update(
            {
                "items": items,
                "equipped": state.get("equip"),
                "accessories": list(state.get("accessories", [])),
                "active_consumables": dict(state.get("active_consumables", {})),
                "groups": groups,
            }
        )
        return summary

    def grantable_items(self) -> list[dict[str, Any]]:
        names = sorted(self.plugin._grantable_item_names())
        return [
            {
                "name": name,
                "kind": "rod" if name in self.rod_names else "item",
                **(
                    {"max_durability": self.rod_durabilities[name]}
                    if name in self.rod_names and name in self.rod_durabilities
                    else {}
                ),
                "base_price": self._base_price(name),
                "fixed_price": name in self.fixed_price_names,
            }
            for name in names
        ]

    def _base_price(self, name: str) -> int:
        if name in FISH_KINGS:
            return int(FISH_KINGS[name].get("base_price", 0))
        if name in HIDDEN_FISH:
            return int(HIDDEN_FISH[name].get("price", 0))
        try:
            return max(0, int(self.plugin._shop_price(name, {name: 100})))
        except (AttributeError, KeyError, TypeError, ValueError):
            return max(0, int(getattr(self.plugin, "_prices", {}).get(name, 0)))

    def _rod_attributes(
        self, item_name: str, attributes: dict[str, Any] | None
    ) -> dict[str, int] | None:
        if attributes is None:
            return None
        if not isinstance(attributes, dict):
            raise TypeError("鱼竿属性必须是 JSON 对象")
        maximum = self.rod_durabilities.get(item_name)
        if maximum is None:
            raise ValueError("该物品不是可配置属性的鱼竿")
        try:
            normalized = {
                "durable": int(attributes.get("durable", maximum)),
                "maintenance": int(attributes.get("maintenance", 0)),
                "induce": int(attributes.get("induce", 0)),
                "favor": int(attributes.get("favor", 0)),
            }
        except (TypeError, ValueError) as exc:
            raise ValueError("鱼竿属性必须是整数") from exc
        ranges = {
            "durable": (1, maximum, f"耐久必须在 1 到 {maximum} 之间"),
            "maintenance": (0, 9, "维修次数必须在 0 到 9 之间"),
            "induce": (0, 3, "诱钓等级必须在 0 到 3 之间"),
            "favor": (0, 3, "海之眷顾等级必须在 0 到 3 之间"),
        }
        for key, (minimum, maximum_value, message) in ranges.items():
            if not minimum <= normalized[key] <= maximum_value:
                raise ValueError(message)
        return normalized

    async def grant_item(
        self,
        user_id: str,
        item_name: str,
        quantity: int,
        operator_id: str,
        *,
        rod_attributes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        user_id = str(user_id)
        item_name = str(item_name).strip()
        quantity = int(quantity)
        if quantity <= 0 or quantity > 100000:
            raise ValueError("发放数量必须在 1 到 100000 之间")
        if item_name not in self.plugin._grantable_item_names():
            raise ValueError("该物品不在钓鱼背包物品清单中")
        normalized_rod_attributes = self._rod_attributes(item_name, rod_attributes)
        async with self.plugin._user_lock(user_id):
            state = self.plugin._state(user_id)
            old_state = json.dumps(state, ensure_ascii=False, sort_keys=True)
            if item_name in self.rod_names:
                for _ in range(quantity):
                    self.plugin._add(
                        state,
                        item_name,
                        pole=True,
                        pole_data=normalized_rod_attributes,
                    )
            else:
                self.plugin._add(state, item_name, quantity)
            self.plugin._save(user_id, state)
            new_state = json.dumps(state, ensure_ascii=False, sort_keys=True)
            self._audit(
                user_id=user_id,
                operator_id=operator_id,
                action="item_grant",
                reason=(
                    f"后台发放 {item_name} ×{quantity}"
                    if normalized_rod_attributes is None
                    else (
                        f"后台发放 {item_name} ×{quantity}"
                        f"（耐久{normalized_rod_attributes['durable']}，"
                        f"维修{normalized_rod_attributes['maintenance']}，"
                        f"诱钓{normalized_rod_attributes['induce']}，"
                        f"眷顾{normalized_rod_attributes['favor']}）"
                    )
                ),
                old_state=old_state,
                new_state=new_state,
            )
            return self.player(user_id)

    async def adjust_balance(
        self,
        user_id: str,
        mode: str,
        amount: int,
        operator_id: str,
        note: str = "",
    ) -> dict[str, Any]:
        user_id = str(user_id)
        mode = str(mode).strip().lower()
        amount = int(amount)
        if mode not in {"grant", "deduct"}:
            raise ValueError("金币操作必须是充值或扣减")
        if amount <= 0 or amount > 1_000_000_000:
            raise ValueError("金币数量必须在 1 到 1000000000 之间")
        economy = getattr(self.plugin, "_economy", None)
        store = getattr(economy, "store", None)
        if store is None or not hasattr(store, "change_balance"):
            raise ValueError("金币系统不可用")
        note = str(note or "").strip()
        if len(note) > 120:
            raise ValueError("操作备注不能超过 120 个字符")
        delta = amount if mode == "grant" else -amount
        label = "充值" if mode == "grant" else "扣减"
        async with self.plugin._user_lock(user_id):
            before = self._account(user_id)["balance"]
            result = store.change_balance(
                user_id,
                delta,
                reason=f"mcfish_admin_{mode}",
            )
            if not result.get("ok"):
                raise ValueError(f"扣减失败，当前余额仅有 {before} 金币")
            after = int(result["balance"])
            self._audit(
                user_id=user_id,
                operator_id=operator_id,
                action=f"gold_{mode}",
                reason=f"后台{label} {amount} 金币" + (f"：{note}" if note else ""),
                old_state=json.dumps({"balance": before}, ensure_ascii=False),
                new_state=json.dumps({"balance": after}, ensure_ascii=False),
            )
            return self.player(user_id)

    def shop_today(self) -> dict[str, Any]:
        discounts, inventory = self.plugin._shop_data()
        custom_prices = getattr(self.plugin, "_shop_custom_prices", {})
        rows = []
        for name, multiplier in sorted(discounts.items()):
            if name in ("垃圾", "宝藏诅咒"):
                continue
            storage_name = "初始木竿" if name == "木竿" else name
            stock = inventory.get(storage_name, 0)
            rows.append(
                {
                    "name": name,
                    "multiplier": int(multiplier),
                    "base_price": int(self.plugin._shop_price(name, {name: 100})),
                    "price": int(self.plugin._shop_price(name, discounts)),
                    "stock": len(stock) if isinstance(stock, list) else int(stock or 0),
                    "adjustable": name not in self.fixed_price_names,
                    "custom_price": int(custom_prices.get(name, 0) or 0),
                }
            )
        today = datetime.now(UTC).astimezone().date().isoformat()
        return {"day": today, "items": rows}

    def set_listing(
        self,
        item_name: str,
        price: int,
        stock: int,
        operator_id: str,
        *,
        rod_attributes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        item_name = str(item_name).strip()
        price = int(price)
        stock = int(stock)
        if item_name not in self.plugin._grantable_item_names():
            raise ValueError("该物品不在钓鱼物品清单中")
        if item_name in {"垃圾", "宝藏诅咒"}:
            raise ValueError("该物品不能上架商店")
        if price <= 0 or price > 1_000_000_000:
            raise ValueError("售价必须在 1 到 1000000000 之间")
        if stock < 0 or stock > 100000:
            raise ValueError("库存必须在 0 到 100000 之间")
        if item_name in self.fixed_price_names:
            fixed_price = self._base_price(item_name)
            if fixed_price and price != fixed_price:
                raise ValueError(f"{item_name} 为固定价格商品，售价必须是 {fixed_price} 金币")
        normalized_rod_attributes = self._rod_attributes(item_name, rod_attributes)
        discounts, inventory = self.plugin._shop_data()
        custom_prices = dict(getattr(self.plugin, "_shop_custom_prices", {}))
        storage_name = "初始木竿" if item_name == "木竿" else item_name
        old_listing = {
            "price": int(custom_prices.get(item_name, self.plugin._shop_price(item_name, discounts))),
            "stock": self._item_count(inventory.get(storage_name, 0)),
        }
        if stock == 0:
            inventory.pop(storage_name, None)
            inventory.pop(item_name, None)
            custom_prices.pop(item_name, None)
            discounts.pop(item_name, None)
        elif item_name in self.rod_names:
            attributes = normalized_rod_attributes or {
                "durable": self.rod_durabilities[item_name],
                "maintenance": 0,
                "induce": 0,
                "favor": 0,
            }
            inventory[storage_name] = [dict(attributes) for _ in range(stock)]
            if storage_name != item_name:
                inventory.pop(item_name, None)
            discounts[item_name] = 100
            if item_name not in self.fixed_price_names:
                custom_prices[item_name] = price
        else:
            inventory[item_name] = stock
            discounts[item_name] = 100
            if item_name not in self.fixed_price_names:
                custom_prices[item_name] = price
        day = datetime.now(UTC).astimezone().date().isoformat()
        row = self.db.execute("SELECT 1 FROM shop WHERE day = ?", (day,)).fetchone()
        if row is None:
            self.plugin._shop_data()
        try:
            self.db.execute(
                "UPDATE shop SET discounts = ?, inventory = ?, custom_prices = ? WHERE day = ?",
                (
                    json.dumps(discounts, ensure_ascii=False),
                    json.dumps(inventory, ensure_ascii=False),
                    json.dumps(custom_prices, ensure_ascii=False),
                    day,
                ),
            )
        except sqlite3.OperationalError as exc:
            raise ValueError("商店数据表缺少 custom_prices 字段，请重启 McFish 完成迁移") from exc
        self.db.commit()
        self.plugin._shop_custom_prices = custom_prices
        new_listing = {
            "price": int(custom_prices.get(item_name, price)),
            "stock": self._item_count(inventory.get(storage_name, 0)),
        }
        self._audit(
            user_id=f"shop:{day}",
            operator_id=operator_id,
            action="shop_listing_update",
            reason=f"后台上架 {item_name}，售价 {price}，库存 {stock}",
            old_state=json.dumps(old_listing, ensure_ascii=False, sort_keys=True),
            new_state=json.dumps(new_listing, ensure_ascii=False, sort_keys=True),
        )
        return self.shop_today()

    def set_discounts(
        self, updates: dict[str, int], operator_id: str
    ) -> dict[str, Any]:
        if not isinstance(updates, dict) or not updates:
            raise ValueError("至少需要提交一个价格倍率")
        discounts, _ = self.plugin._shop_data()
        normalized: dict[str, int] = {}
        for name, value in updates.items():
            name = str(name)
            if name not in discounts:
                raise ValueError(f"商品不存在或今天未进入商店：{name}")
            if name in self.fixed_price_names:
                if int(value) != 100:
                    raise ValueError(f"{name} 为固定价格商品，倍率必须保持 100%")
                continue
            try:
                value = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} 的倍率必须是整数") from exc
            if not 1 <= value <= 300:
                raise ValueError(f"{name} 的倍率必须在 1% 到 300% 之间")
            normalized[name] = value
        if not normalized:
            return self.shop_today()
        old = {name: int(discounts.get(name, 100)) for name in normalized}
        discounts.update(normalized)
        day = datetime.now(UTC).astimezone().date().isoformat()
        row = self.db.execute("SELECT discounts FROM shop WHERE day = ?", (day,)).fetchone()
        if row is None:
            self.plugin._shop_data()
        self.db.execute(
            "UPDATE shop SET discounts = ? WHERE day = ?",
            (json.dumps(discounts, ensure_ascii=False), day),
        )
        self.db.commit()
        self._audit(
            user_id=f"shop:{day}",
            operator_id=operator_id,
            action="shop_discount_update",
            reason="后台调整当日商品价格倍率",
            old_state=json.dumps({"day": day, "discounts": old}, ensure_ascii=False, sort_keys=True),
            new_state=json.dumps({"day": day, "discounts": normalized}, ensure_ascii=False, sort_keys=True),
        )
        return self.shop_today()

    def audit(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        rows = self.db.execute(
            "SELECT id, user_id, operator_id, action, reason, created_at "
            "FROM admin_adjustments ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id": int(row[0]),
                "user_id": str(row[1]),
                "operator_id": str(row[2] or "legacy"),
                "action": str(row[3] or "legacy"),
                "reason": str(row[4]),
                "created_at": float(row[5]),
            }
            for row in rows
        ]

    def _audit(
        self,
        *,
        user_id: str,
        operator_id: str,
        action: str,
        reason: str,
        old_state: str,
        new_state: str,
    ) -> None:
        self.db.execute(
            "INSERT INTO admin_adjustments "
            "(user_id, operator_id, action, reason, old_state, new_state, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, operator_id, action, reason, old_state, new_state, time.time()),
        )
        self.db.commit()
