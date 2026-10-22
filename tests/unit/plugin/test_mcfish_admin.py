"""Tests for the authenticated McFish management surface."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import aiohttp
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins"))

from McFish.admin_server import DEFAULT_ADMIN_TOKEN, McFishAdminServer
from McFish.admin_service import McFishAdminService


class FakeEconomyStore:
    def __init__(self):
        self.balances: dict[str, int] = {}

    def account(self, user_id: str) -> dict:
        return {"balance": self.balances.setdefault(str(user_id), 0)}

    def change_balance(self, user_id: str, delta: int, *, reason: str) -> dict:
        user_id = str(user_id)
        balance = self.balances.setdefault(user_id, 0)
        if balance + delta < 0:
            return {"ok": False, "balance": balance, "reason": "insufficient"}
        self.balances[user_id] = balance + delta
        return {"ok": True, "delta": delta, "balance": balance + delta}


class FakeEconomy:
    def __init__(self):
        self.store = FakeEconomyStore()


class FakeMcFish:
    def __init__(self, db_path: Path):
        self._db = sqlite3.connect(db_path)
        self._locks: dict[str, asyncio.Lock] = {}
        self.states: dict[str, dict] = {}
        self._economy = FakeEconomy()
        self.rod_names = {"木竿", "铁竿"}
        self.names = {"鳕鱼", "铁竿", "钓鱼碎片"}
        self.discounts = {"鳕鱼": 100, "铁竿": 100}
        self.inventory = {"鳕鱼": 10, "铁竿": 1}
        self._shop_custom_prices: dict[str, int] = {}
        self._db.executescript(
            """
            CREATE TABLE players(user_id TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at REAL NOT NULL);
            CREATE TABLE fishing_groups(group_id TEXT PRIMARY KEY, group_name TEXT NOT NULL, updated_at REAL NOT NULL);
            CREATE TABLE fishing_group_members(group_id TEXT NOT NULL, user_id TEXT NOT NULL, display_name TEXT NOT NULL, last_seen_at REAL NOT NULL, PRIMARY KEY(group_id, user_id));
            CREATE TABLE admin_adjustments(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, reason TEXT NOT NULL, old_state TEXT NOT NULL, new_state TEXT NOT NULL, created_at REAL NOT NULL, operator_id TEXT NOT NULL DEFAULT '', action TEXT NOT NULL DEFAULT 'legacy');
            CREATE TABLE shop(day TEXT PRIMARY KEY, discounts TEXT NOT NULL, inventory TEXT NOT NULL, custom_prices TEXT NOT NULL DEFAULT '{}');
            """
        )
        self._db.execute(
            "INSERT INTO fishing_groups VALUES ('100', '测试群', 10)"
        )
        state = {"pack": {"鳕鱼": 2}, "fish_level": 3, "fish_xp": 22, "equip": None}
        self._db.execute(
            "INSERT INTO players VALUES ('200', ?, 10)",
            (json.dumps(state, ensure_ascii=False),),
        )
        self._db.execute(
            "INSERT INTO fishing_group_members VALUES ('100', '200', '小鱼', 10)"
        )
        self._db.commit()

    def _state(self, user_id: str) -> dict:
        row = self._db.execute(
            "SELECT state FROM players WHERE user_id = ?", (str(user_id),)
        ).fetchone()
        state = json.loads(row[0]) if row else {"pack": {}}
        state.setdefault("pack", {})
        return state

    def _save(self, user_id: str, state: dict) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO players VALUES (?, ?, 20)",
            (str(user_id), json.dumps(state, ensure_ascii=False)),
        )
        self._db.commit()

    def _add(
        self,
        state: dict,
        name: str,
        number: int = 1,
        *,
        pole: bool = False,
        pole_data: dict | None = None,
    ) -> None:
        if pole:
            state["pack"].setdefault(name, [])
            state["pack"][name].extend(
                dict(pole_data or {"durable": 50, "maintenance": 0, "induce": 0, "favor": 0})
                for _ in range(number)
            )
        else:
            state["pack"][name] = int(state["pack"].get(name, 0)) + number

    def _grantable_item_names(self):
        return self.names

    def _user_lock(self, user_id: str) -> asyncio.Lock:
        return self._locks.setdefault(str(user_id), asyncio.Lock())

    def _shop_data(self):
        return self.discounts, self.inventory

    def _shop_price(self, name, discounts):
        if name in self._shop_custom_prices:
            return self._shop_custom_prices[name]
        return (5000 if name == "铁竿" else 10) * discounts.get(name, 100) // 100

    def set_config(self, _key, _value):
        return None

    def close(self):
        self._db.close()


def _service(plugin: FakeMcFish) -> McFishAdminService:
    return McFishAdminService(
        plugin,
        rod_names=plugin.rod_names,
        fixed_price_names={"铁竿"},
    )


def test_group_players_and_player_detail_are_grouped_without_copying_inventory(tmp_path: Path):
    plugin = FakeMcFish(tmp_path / "mcfish.db")
    service = _service(plugin)

    assert service.groups()[0]["group_id"] == "100"
    assert service.groups()[0]["avatar_url"] == "/mcfish-avatar/group/100"
    players = service.group_players("100")
    assert players[0]["user_id"] == "200"
    assert players[0]["display_name"] == "小鱼"
    assert players[0]["avatar_url"] == "/mcfish-avatar/user/200"
    detail = service.player("200")
    assert detail["avatar_url"] == "/mcfish-avatar/user/200"
    assert detail["items"] == [{"name": "鳕鱼", "kind": "item", "count": 2}]
    assert detail["groups"][0]["group_name"] == "测试群"
    plugin.close()


@pytest.mark.asyncio
async def test_grant_item_supports_stackable_items_and_rods_and_audits(tmp_path: Path):
    plugin = FakeMcFish(tmp_path / "mcfish.db")
    service = _service(plugin)

    detail = await service.grant_item("200", "鳕鱼", 3, "webui@test")
    assert next(item for item in detail["items"] if item["name"] == "鳕鱼")["count"] == 5
    detail = await service.grant_item("200", "铁竿", 2, "webui@test")
    rod = next(item for item in detail["items"] if item["name"] == "铁竿")
    assert rod["count"] == 2
    audit = service.audit()
    assert len(audit) == 2
    assert audit[0]["operator_id"] == "webui@test"
    assert audit[0]["action"] == "item_grant"
    plugin.close()


@pytest.mark.asyncio
async def test_grant_rod_attributes_and_adjusts_balance_with_audit(tmp_path: Path):
    plugin = FakeMcFish(tmp_path / "mcfish.db")
    service = _service(plugin)

    detail = await service.grant_item(
        "200",
        "铁竿",
        2,
        "webui@test",
        rod_attributes={"durable": 17, "maintenance": 4, "induce": 2, "favor": 3},
    )
    rods = next(item for item in detail["items"] if item["name"] == "铁竿")
    assert rods["details"] == [
        {"durable": 17, "maintenance": 4, "induce": 2, "favor": 3, "craftable": True},
        {"durable": 17, "maintenance": 4, "induce": 2, "favor": 3, "craftable": True},
    ]

    detail = await service.adjust_balance("200", "grant", 1000, "webui@test", "活动补偿")
    assert detail["gold"] == 1000
    detail = await service.adjust_balance("200", "deduct", 250, "webui@test", "违规扣除")
    assert detail["gold"] == 750
    assert service.audit()[0]["action"] == "gold_deduct"
    assert "违规扣除" in service.audit()[0]["reason"]
    plugin.close()


def test_shop_listing_sets_custom_price_and_inventory_with_audit(tmp_path: Path):
    plugin = FakeMcFish(tmp_path / "mcfish.db")
    service = _service(plugin)

    result = service.set_listing("鳕鱼", 77, 12, "webui@test")
    cod = next(item for item in result["items"] if item["name"] == "鳕鱼")
    assert cod["price"] == 77
    assert cod["stock"] == 12
    assert cod["custom_price"] == 77
    assert service.audit()[0]["action"] == "shop_listing_update"
    plugin.close()


def test_shop_listing_stores_wood_rods_in_the_existing_special_stock(tmp_path: Path):
    plugin = FakeMcFish(tmp_path / "mcfish.db")
    plugin.names.add("木竿")
    service = _service(plugin)

    result = service.set_listing(
        "木竿",
        25,
        2,
        "webui@test",
        rod_attributes={"durable": 24, "maintenance": 3, "induce": 1, "favor": 2},
    )

    wood = next(item for item in result["items"] if item["name"] == "木竿")
    assert wood["stock"] == 2
    assert plugin.inventory["初始木竿"] == [
        {"durable": 24, "maintenance": 3, "induce": 1, "favor": 2},
        {"durable": 24, "maintenance": 3, "induce": 1, "favor": 2},
    ]
    assert "木竿" not in plugin.inventory
    plugin.close()


def test_discount_validation_keeps_fixed_rods_and_rejects_out_of_range(tmp_path: Path):
    plugin = FakeMcFish(tmp_path / "mcfish.db")
    service = _service(plugin)
    with pytest.raises(ValueError, match="固定价格"):
        service.set_discounts({"铁竿": 120}, "webui@test")
    with pytest.raises(ValueError, match="1% 到 300%"):
        service.set_discounts({"鳕鱼": 301}, "webui@test")
    result = service.set_discounts({"鳕鱼": 80}, "webui@test")
    cod = next(item for item in result["items"] if item["name"] == "鳕鱼")
    assert cod["multiplier"] == 80
    assert cod["price"] == 8
    assert service.audit()[0]["action"] == "shop_discount_update"
    plugin.close()


@pytest.mark.asyncio
async def test_admin_http_requires_token_and_exposes_group_api(tmp_path: Path):
    plugin = FakeMcFish(tmp_path / "mcfish.db")
    server = McFishAdminServer(plugin, host="127.0.0.1", port=0, token="secret")
    await server.start()
    try:
        async with aiohttp.ClientSession() as client:
            response = await client.get(server.url)
            assert response.status == 200
            assert "McFish 管理台" in await response.text()
            response = await client.get(f"{server.url}/api/mcfish/groups")
            assert response.status == 401
            response = await client.get(
                f"{server.url}/api/mcfish/groups",
                headers={"Authorization": "Bearer secret"},
            )
            assert response.status == 200
            payload = await response.json()
            assert payload["data"][0]["group_id"] == "100"
            response = await client.post(
                f"{server.url}/api/mcfish/players/200/balance",
                headers={"Authorization": "Bearer secret"},
                json={"mode": "grant", "amount": 123, "note": "测试"},
            )
            assert response.status == 200
            assert (await response.json())["data"]["gold"] == 123
            response = await client.put(
                f"{server.url}/api/mcfish/shop/today/listings",
                headers={"Authorization": "Bearer secret"},
                json={"item": "鳕鱼", "price": 77, "stock": 8},
            )
            assert response.status == 200
            listed = next(
                item for item in (await response.json())["data"]["items"] if item["name"] == "鳕鱼"
            )
            assert listed["price"] == 77
            assert listed["stock"] == 8
    finally:
        await server.stop()
        plugin.close()


@pytest.mark.asyncio
async def test_admin_http_uses_default_token_when_config_is_empty(tmp_path: Path):
    plugin = FakeMcFish(tmp_path / "mcfish.db")
    server = McFishAdminServer(plugin, host="127.0.0.1", port=0, token="")
    await server.start()
    try:
        assert server.token == DEFAULT_ADMIN_TOKEN
        async with aiohttp.ClientSession() as client:
            response = await client.get(
                f"{server.url}/api/mcfish/session",
                headers={"Authorization": f"Bearer {DEFAULT_ADMIN_TOKEN}"},
            )
            assert response.status == 200
    finally:
        await server.stop()
        plugin.close()


@pytest.mark.asyncio
async def test_admin_avatar_routes_serve_user_and_group_images(tmp_path: Path, monkeypatch):
    from McFish import admin_server

    plugin = FakeMcFish(tmp_path / "mcfish.db")
    avatar = tmp_path / "avatar.jpg"
    avatar.write_bytes(b"avatar")
    monkeypatch.setattr(admin_server.CommonUtil, "get_avatar", lambda _user_id: avatar)
    monkeypatch.setattr(admin_server.CommonUtil, "get_group_avatar", lambda _group_id: avatar)
    server = McFishAdminServer(plugin, host="127.0.0.1", port=0, token="secret")
    await server.start()
    try:
        async with aiohttp.ClientSession() as client:
            user = await client.get(f"{server.url}/mcfish-avatar/user/200")
            group = await client.get(f"{server.url}/mcfish-avatar/group/100")
            invalid = await client.get(f"{server.url}/mcfish-avatar/user/not-a-user")
            assert user.status == 200
            assert group.status == 200
            assert invalid.status == 404
    finally:
        await server.stop()
        plugin.close()
