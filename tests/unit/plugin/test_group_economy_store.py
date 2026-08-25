from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins"))

from GroupEconomy.config import level_for_score  # noqa: E402
from GroupEconomy.store import EconomyStore  # noqa: E402
from TodayWaifu.core import propose_auto_accept_probability  # noqa: E402


def test_checkin_is_idempotent_and_builds_global_score(tmp_path: Path):
    store = EconomyStore(tmp_path / "economy.db")
    first = store.checkin("100", "甲", 20, "2026-01-01")
    duplicate = store.checkin("100", "甲", 20, "2026-01-01")
    second = store.checkin("100", "甲", 30, "2026-01-02")

    assert first["ok"] is True
    assert duplicate["ok"] is False
    assert second["ok"] is True
    assert store.account("100")["balance"] == 50
    assert store.account("100")["score"] == 2
    assert store.global_rank("100") == 1
    store.close()


def test_gift_respects_daily_limit_and_wallet_reserve(tmp_path: Path):
    store = EconomyStore(tmp_path / "economy.db")
    store.checkin("100", "甲", 50, "2026-01-01")

    sent = store.gift("group", "100", "200", 30, 10, 4, "喜欢", "2026-01-01")
    duplicate = store.gift("group", "100", "201", 1, 10, 1, "喜欢", "2026-01-01")
    too_much = store.gift("group", "100", "202", 11, 10, 1, "喜欢", "2026-01-02")

    assert sent["ok"] is True
    assert store.gift_status("group", "100", "2026-01-01")["receiver_id"] == "200"
    assert duplicate["ok"] is False and duplicate["reason"] == "daily"
    assert too_much["ok"] is False and too_much["reason"] == "reserve"
    assert store.account("100")["balance"] == 20
    store.close()


def test_level_thresholds_match_score_plugin_progression():
    assert level_for_score(0) == 0
    assert level_for_score(10) == 1
    assert level_for_score(1200) == 10
    assert level_for_score(9999) == 10


def test_wallet_ranking_uses_balance_and_exposes_total_earned(tmp_path: Path):
    store = EconomyStore(tmp_path / "economy.db")
    store.checkin("100", "余额高", 30, "2026-01-01")
    store.checkin("200", "经验高", 50, "2026-01-01")
    store.checkin("200", "经验高", 50, "2026-01-02")
    store.gift("group", "200", "300", 80, 0, 1, "喜欢", "2026-01-02")

    level_rows = store.global_ranking(10)
    wallet_rows = store.wallet_ranking(10)

    assert [row["user_id"] for row in level_rows[:2]] == ["200", "100"]
    assert [row["user_id"] for row in wallet_rows[:2]] == ["100", "200"]
    assert wallet_rows[0]["balance"] == 30
    assert wallet_rows[0]["total_earned"] == 30
    store.close()


def test_proposal_probability_has_no_favor_gate():
    low = propose_auto_accept_probability({}, -20)
    high = propose_auto_accept_probability({}, 100)

    assert 0 < low < high <= 0.90
