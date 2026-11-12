"""Dota 2 宝瓶规则与存储（DU 系列）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins"))

from CaseOpening.store_dota import TreasureStore
from common.case_data import SNAPSHOT_DIR, load_dota_snapshot
from common.case_data.rules import (
    advance_counters,
    guaranteed_row,
    odds_for_open,
    pity_row,
    regular_pick,
    roll_dota_bonus,
)


# DU-01 递增表读取：第 1 行、越界取末行
def test_odds_lookup():
    table = [0.0005, 0.0017, 0.0053]
    assert odds_for_open(table, 1) == pytest.approx(0.0005)
    assert odds_for_open(table, 3) == pytest.approx(0.0053)
    assert odds_for_open(table, 99) == pytest.approx(0.0053)
    assert pity_row(table, 0) == 1
    assert pity_row(table, 99) == 3


# DU-02 保底行：表内第一个 100%
def test_guaranteed_row():
    assert guaranteed_row([0.5, 0.9, 1.0]) == 3
    assert guaranteed_row([0.5, 0.9]) is None


# DU-03 未命中时各档 +1，命中档清零，且不越过表尾
def test_advance_counters():
    odds = {"Rare": [0.1] * 3, "Very Rare": [0.01] * 3}
    advanced = advance_counters({"Rare": 1, "Very Rare": 2}, odds, None)
    assert advanced == {"Rare": 2, "Very Rare": 3}
    reset = advance_counters({"Rare": 3, "Very Rare": 3}, odds, "Rare")
    assert reset == {"Rare": 1, "Very Rare": 3}
    capped = advance_counters({"Rare": 3, "Very Rare": 3}, odds, None)
    assert capped == {"Rare": 3, "Very Rare": 3}
    # 一次开瓶命中多档时，多档一起清零
    multi = advance_counters({"Rare": 3, "Very Rare": 3}, odds, ["Rare", "Very Rare"])
    assert multi == {"Rare": 1, "Very Rare": 1}


# DU-04 保底命中：末行 100% 必定出货；多档同时 100% 会给多件
def test_roll_hits_guaranteed_row():
    odds = {"Ultra Rare": [0.0001, 1.0]}
    assert roll_dota_bonus(odds, {"Ultra Rare": 2}) == ["Ultra Rare"]

    multi = {"Rare": [0.0005, 1.0], "Ultra Rare": [0.0001, 1.0]}
    assert roll_dota_bonus(multi, {"Rare": 2, "Ultra Rare": 2}) == ["Rare", "Ultra Rare"]


# DU-10 开瓶保底：额外档独立判定，可同时给多件（第 50 瓶珍稀 + 珍稀超凡）
def test_trove_50th_gives_multiple_bonus():
    treasures, _ = load_dota_snapshot(SNAPSHOT_DIR / "dota.json.gz")
    trove = treasures["Trove of Terror"]
    counters = {"Rare": 50, "Very Rare": 50, "Ultra Rare": 50}
    hits = roll_dota_bonus(trove.odds, counters)
    assert "Rare" in hits and "Ultra Rare" in hits


# DU-05 普通档重复保护：集齐前不重复，集齐后回到全池
def test_regular_pick_avoids_duplicates():
    pool = ["a", "b", "c"]
    picked = {regular_pick(pool, []) for _ in range(80)}
    assert picked == {"a", "b", "c"}
    assert regular_pick(pool, ["a", "b"]) == "c"
    assert regular_pick(pool, pool) in pool


# DU-06 快照：13 个瓶、全部可购买、概率表 50 行、累计奖励解析正确
def test_snapshot_treasures():
    treasures, _ = load_dota_snapshot(SNAPSHOT_DIR / "dota.json.gz")
    assert len(treasures) == 13
    assert all(t.purchasable for t in treasures.values())
    assert {t.price_cny for t in treasures.values()} == {17.0, 21.0, 35.0}
    for treasure in treasures.values():
        assert treasure.cn_name
        assert treasure.contents
        for tier, table in treasure.odds.items():
            assert table, tier
        for tier in treasure.contents:
            if tier == "Regular":
                continue
            assert tier in treasure.odds

    snake = treasures["The Charms of the Snake"]
    assert snake.cn_name == "蛇年珍藏"
    assert snake.price_cny == 17.0
    assert len(snake.contents["Regular"]) == 7
    assert guaranteed_row(snake.odds["Rare"]) == 40

    trove = treasures["Trove of Terror"]
    assert trove.price_cny == 35.0
    assert trove.bonus_rewards == [
        {"opens": 12, "items": ["Obscene Eye Ball"]},
        {"opens": 40, "items": ["R'shan the Eternal", "Baby R'shan the Eternal"]},
    ]
    assert guaranteed_row(trove.odds["Ultra Rare"]) == 50
    assert trove.recycle_rule == {"inputs": 6, "output": 1}
    assert trove.icon and trove.icon.startswith("https://liquipedia.net/commons/images/")
    assert all(t.icon for t in treasures.values())


# DU-07 快照：不可交易的瓶内容必须标记为不可市场
def test_snapshot_market_flags():
    treasures, _ = load_dota_snapshot(SNAPSHOT_DIR / "dota.json.gz")
    assert treasures["Dragon's Hoard"].marketable is True
    assert treasures["Trove of Terror"].marketable is False


# DU-08 存储：计数 / 收集 / 累计奖励只发一次
def test_store_pity_collected_bonus(tmp_path: Path):
    store = TreasureStore(tmp_path / "dota.db")
    assert store.pity("100", "Trove of Terror") == {}
    store.set_pity("100", "Trove of Terror", "Rare", 4)
    assert store.pity("100", "Trove of Terror") == {"Rare": 4}

    assert store.collected("100", "Trove of Terror") == set()
    store.mark_collected("100", "Trove of Terror", "Stoneback Sovereign")
    assert store.collected("100", "Trove of Terror") == {"Stoneback Sovereign"}

    assert store.claim_bonus("100", "Trove of Terror", 12) is True
    assert store.claim_bonus("100", "Trove of Terror", 12) is False
    assert store.claimed_bonus("100", "Trove of Terror") == {12}

    store.log_open("100", "Trove of Terror", 350, "Rare", "Curse of the Wyrmbender")
    stats = store.stats("100")
    assert stats["opens"] == 1 and stats["spent"] == 350 and stats["rare"] == 1
    store.close()


# DU-09 存储：饰品入库不带交易锁
def test_store_no_trade_lock(tmp_path: Path):
    store = TreasureStore(tmp_path / "dota.db")
    item_id = store.add_item(
        "100",
        "9",
        treasure="蛇年珍藏",
        item_name="Cobra-Ka",
        tier="Rare",
        icon=None,
        marketable=False,
        price_cny=None,
    )
    row = store.item("100", item_id)
    assert row["marketable"] == 0
    assert row["tradeable_at"] == pytest.approx(row["obtained_at"], abs=1)
    store.close()
