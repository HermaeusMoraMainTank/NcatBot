from __future__ import annotations

import asyncio
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "plugins"))

from McFish import mcfish_market as market
from McFish.McFish import McFish


def _plugin(tmp_path: Path) -> McFish:
    plugin = McFish.__new__(McFish)
    plugin._db = sqlite3.connect(str(tmp_path / "market.db"))
    plugin._db.execute(
        "CREATE TABLE IF NOT EXISTS shop (day TEXT PRIMARY KEY, discounts TEXT NOT NULL, inventory TEXT NOT NULL, custom_prices TEXT NOT NULL DEFAULT '{}')"
    )
    plugin._db.commit()
    plugin._prices = {"吸血鳗": 100}
    plugin._types = {}
    return plugin


def test_market_factor_falls_with_oversupply():
    assert market.factor(0, "fish") == pytest.approx(1.0)
    assert market.factor(market.depth("fish"), "fish") == pytest.approx(0.5)
    assert market.factor(10**6, "fish") == market.MIN_FACTOR
    assert market.MIN_FACTOR < 0.45


def test_rarer_fish_react_more_strongly_to_the_same_volume():
    assert market.factor(100, "fish", 50) > market.factor(100, "fish", 1200)


def test_each_sale_moves_only_its_own_item(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._prices.update({"橙色洞穴孔雀鱼": 180, "追踪鱼": 300})
    other_before = plugin._market_sale_price("追踪鱼")
    own_before = plugin._market_sale_price("橙色洞穴孔雀鱼")

    plugin._market_apply("橙色洞穴孔雀鱼", 50)

    assert plugin._market_sale_price("追踪鱼") == other_before
    assert plugin._market_sale_price("橙色洞穴孔雀鱼") < own_before


def test_market_converges_back_to_the_baseline_after_sales_stop():
    pressure = 80.0
    rest = market.rest_pressure("fish")
    assert rest < 0
    one_day = market.converge(pressure, "fish", 1)
    assert one_day == pytest.approx(rest + (pressure - rest) * market.DECAY_PER_DAY)
    assert market.factor(one_day, "fish") > market.factor(pressure, "fish")
    month = market.converge(pressure, "fish", 60)
    assert market.factor(month, "fish") == pytest.approx(market.factor(rest, "fish"), rel=1e-3)


def test_market_batch_price_matches_selling_one_by_one():
    start = market.rest_pressure("fish")
    batch = market.average_factor(start, "fish", 200) * 200
    sequential = 0.0
    pressure = start
    for _ in range(200):
        sequential += market.factor(pressure, "fish")
        pressure += 1
    assert batch == pytest.approx(sequential, rel=0.02)
    assert market.average_factor(start, "fish", 200) < market.factor(start, "fish")


def test_market_pressure_is_persisted_and_decays_over_days(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._market_apply("吸血鳗", 200)
    stored = plugin._db.execute(
        "SELECT pressure, updated FROM market WHERE item = ?", ("吸血鳗",)
    ).fetchone()
    assert stored[0] > 150

    plugin._db.execute(
        "UPDATE market SET updated = ? WHERE item = ?",
        (time.time() - 3 * 86400, "吸血鳗"),
    )
    plugin._db.commit()
    decayed = plugin._market_pressure("吸血鳗")
    assert decayed < stored[0]
    assert decayed > market.rest_pressure("fish")


def test_market_sale_price_drops_after_dumping_and_recovers_later(tmp_path):
    plugin = _plugin(tmp_path)
    before = plugin._market_sale_price("吸血鳗")
    plugin._market_apply("吸血鳗", 200)
    dumped = plugin._market_sale_price("吸血鳗")
    assert dumped < before * 0.7

    plugin._db.execute(
        "UPDATE market SET updated = ? WHERE item = ?",
        (time.time() - 60 * 86400, "吸血鳗"),
    )
    plugin._db.commit()
    assert plugin._market_sale_price("吸血鳗") == pytest.approx(before, rel=0.02)


def test_market_skips_fixed_price_items_and_hidden_fish(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._prices.update({"木竿": 180, "灿阳竿": 36000, "宝藏诅咒": 0})
    plugin._types = {"木竿": "pole"}
    for name in ("木竿", "灿阳竿", "宝藏诅咒"):
        assert plugin._market_enabled(name) is False
    assert plugin._market_enabled("吸血鳗") is True


def test_market_discounts_reports_current_factor(tmp_path):
    plugin = _plugin(tmp_path)
    assert plugin._market_discounts({"吸血鳗": 100})["吸血鳗"] == 102
    plugin._market_apply("吸血鳗", 180)
    assert plugin._market_discounts({"吸血鳗": 100})["吸血鳗"] == 53


def test_selling_records_supply_pressure(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._economy_change = lambda *_args: True
    plugin._save = lambda *_args: None
    plugin._choose = AsyncMock(return_value=SimpleNamespace(key="confirm"))
    plugin.get_config = lambda key, default=None: 0.8 if key == "sell_tax" else default
    plugin._types = {"吸血鳗": "fish"}
    plugin._shop_data = lambda: ({}, {})
    plugin._sale_tax_factor = lambda _name, _state: 0.8
    event = SimpleNamespace(sender=SimpleNamespace(user_id="100"), reply=AsyncMock())
    state = {"pack": {"吸血鳗": 10}}

    asyncio.run(plugin._sell(event, state, "吸血鳗 10"))

    assert state["pack"]["吸血鳗"] == 0
    assert plugin._market_pressure("吸血鳗") > 0
