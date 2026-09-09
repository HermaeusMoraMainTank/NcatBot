from __future__ import annotations

import sys
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins"))

from GroupEconomy.config import DEFAULT_CONFIG, XP_PER_CHECKIN, level_for_xp, xp_for_level  # noqa: E402
from GroupEconomy.store import EconomyStore  # noqa: E402
from TodayWaifu.core import propose_auto_accept_probability  # noqa: E402
from ncatbot.types import At, PlainText  # noqa: E402
from GroupEconomy.GroupEconomy import GroupEconomy  # noqa: E402


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
    assert store.account("100")["xp"] == 2 * XP_PER_CHECKIN
    assert store.global_rank("100") == 1
    store.close()


def test_checkin_default_reward_can_fund_mcfish_starter_purchase():
    assert DEFAULT_CONFIG["checkin_min_reward"] == 30
    assert DEFAULT_CONFIG["checkin_max_reward"] == 60
    assert DEFAULT_CONFIG["gift_min_amount"] == 30
    assert DEFAULT_CONFIG["gift_max_amount"] == 60


def test_existing_accounts_are_migrated_to_xp(tmp_path: Path):
    database = tmp_path / "economy.db"
    connection = sqlite3.connect(database)
    connection.execute(
        """CREATE TABLE accounts (
           user_id TEXT PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0,
           score INTEGER NOT NULL DEFAULT 0, total_earned INTEGER NOT NULL DEFAULT 0,
           total_spent INTEGER NOT NULL DEFAULT 0, nickname TEXT NOT NULL DEFAULT '',
           updated_at REAL NOT NULL DEFAULT 0)"""
    )
    connection.execute("INSERT INTO accounts(user_id, score) VALUES ('100', 2)")
    connection.commit()
    connection.close()

    store = EconomyStore(database)
    assert store.account("100")["xp"] == 2 * XP_PER_CHECKIN
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


def test_grant_adds_balance_and_records_ledger(tmp_path: Path):
    store = EconomyStore(tmp_path / "economy.db")

    result = store.grant("200", 100)

    assert result == {"ok": True, "amount": 100, "balance": 100}
    account = store.account("200")
    assert account["balance"] == 100
    assert account["total_earned"] == 100
    ledger = store._conn.execute(
        "SELECT delta, reason, related_user_id FROM ledger WHERE user_id = ?",
        ("200",),
    ).fetchone()
    assert tuple(ledger) == (100, "admin_grant", "")
    store.close()


def test_admin_deduct_reduces_balance_and_rejects_overdraft(tmp_path: Path):
    """GE-01: 管理员负数充钱命令通过原子扣款降低余额且禁止负余额。"""
    store = EconomyStore(tmp_path / "economy.db")
    store.grant("200", 100)

    deducted = store.change_balance("200", -40, reason="admin_deduct")
    overdraft = store.change_balance("200", -61, reason="admin_deduct")

    assert deducted == {"ok": True, "delta": -40, "balance": 60}
    assert overdraft == {"ok": False, "balance": 60, "reason": "insufficient"}
    assert store.account("200")["balance"] == 60
    entries = store._conn.execute(
        "SELECT delta, reason FROM ledger WHERE user_id = ? ORDER BY id", ("200",)
    ).fetchall()
    assert [tuple(row) for row in entries] == [(100, "admin_grant"), (-40, "admin_deduct")]
    store.close()


def _grant_event(user_id: str, target_id: str, amount: str):
    event = SimpleNamespace(
        sender=SimpleNamespace(user_id=user_id),
        message=[PlainText(text="充钱 "), At(user_id=target_id), PlainText(text=f" {amount}")],
        reply=AsyncMock(),
    )
    return event


@pytest.mark.asyncio
async def test_grant_command_is_restricted_to_authorized_user():
    plugin = GroupEconomy.__new__(GroupEconomy)
    plugin.store = MagicMock()
    plugin._target_in_group = AsyncMock(return_value=True)

    await plugin._grant(_grant_event("123", "200", "100"))

    plugin.store.grant.assert_not_called()
    plugin._target_in_group.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorized_grant_command_updates_target_wallet():
    plugin = GroupEconomy.__new__(GroupEconomy)
    plugin.store = MagicMock()
    plugin.store.grant.return_value = {"balance": 100, "amount": 100, "ok": True}
    plugin._target_in_group = AsyncMock(return_value=True)
    event = _grant_event("273421673", "200", "100")

    await plugin._grant(event)

    plugin.store.grant.assert_called_once_with("200", 100, reason="admin_grant")
    event.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_authorized_negative_grant_command_deducts_target_wallet():
    """GE-02: 管理员输入负数时扣减目标钱包。"""
    plugin = GroupEconomy.__new__(GroupEconomy)
    plugin.store = MagicMock()
    plugin.store.change_balance.return_value = {"ok": True, "balance": 60}
    plugin._target_in_group = AsyncMock(return_value=True)
    event = _grant_event("273421673", "200", "-40")

    await plugin._grant(event)

    plugin.store.grant.assert_not_called()
    plugin.store.change_balance.assert_called_once_with("200", -40, reason="admin_deduct")
    assert "扣减 40" in event.reply.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_authorized_negative_grant_command_reports_insufficient_balance():
    """GE-03: 负数扣减超过余额时保持失败并提示当前余额。"""
    plugin = GroupEconomy.__new__(GroupEconomy)
    plugin.store = MagicMock()
    plugin.store.change_balance.return_value = {
        "ok": False,
        "balance": 20,
        "reason": "insufficient",
    }
    plugin._target_in_group = AsyncMock(return_value=True)
    event = _grant_event("273421673", "200", "-40")

    await plugin._grant(event)

    assert "扣减失败" in event.reply.await_args.kwargs["text"]
    assert "20" in event.reply.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_ranking_commands_accept_rank_aliases():
    plugin = GroupEconomy.__new__(GroupEconomy)
    plugin._ranking = AsyncMock()
    for text in ("查看等级排名", "查看等级排行", "查看钱包排名", "查看钱包排行"):
        event = SimpleNamespace(raw_message=text, reply=AsyncMock())
        await plugin.handle_message(event)

    assert plugin._ranking.await_count == 4
    assert plugin._ranking.await_args_list[0].kwargs == {}
    assert plugin._ranking.await_args_list[1].kwargs == {}
    assert plugin._ranking.await_args_list[2].kwargs == {"wallet": True}
    assert plugin._ranking.await_args_list[3].kwargs == {"wallet": True}


def test_level_curve_is_easy_early_and_harder_later():
    assert level_for_xp(0) == 0
    assert level_for_xp(xp_for_level(1)) == 1
    assert level_for_xp(xp_for_level(30)) == 30
    assert xp_for_level(10) - xp_for_level(9) < xp_for_level(30) - xp_for_level(29)
    assert level_for_xp(xp_for_level(30) + 999999) == 30


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
