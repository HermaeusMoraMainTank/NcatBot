"""开箱专用的共享借贷账本。

两个插件共用同一个 SQLite（`data/case_loan/loan.db`），因为借来的钱
既能开 CS2 箱也能开 Dota 瓶。规则：

- 单次最多借 1,000,000 金币
- 借到的额度只能用于开箱/开瓶，不能提现
- 还款优先抵消"未花掉的额度"，再抵债务（避免出现"还完债还能白花"）
- 出售所得自动优先还款
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

MAX_LOAN = 1_000_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS loan (
    user_id TEXT PRIMARY KEY,
    debt INTEGER NOT NULL DEFAULT 0,
    credit INTEGER NOT NULL DEFAULT 0,
    last_borrow_date TEXT NOT NULL DEFAULT '',
    total_borrowed INTEGER NOT NULL DEFAULT 0,
    total_debt INTEGER NOT NULL DEFAULT 0,
    total_repaid INTEGER NOT NULL DEFAULT 0,
    peak_debt INTEGER NOT NULL DEFAULT 0,
    loan_count INTEGER NOT NULL DEFAULT 0,
    rebirths INTEGER NOT NULL DEFAULT 0,
    last_rebirth_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS loan_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    amount INTEGER NOT NULL DEFAULT 0,
    debt_after INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_loan_log_user ON loan_log(user_id, id DESC);
"""


class LoanStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(loan)")}
        for name, ddl in (
            ("total_debt", "INTEGER NOT NULL DEFAULT 0"),
            ("total_repaid", "INTEGER NOT NULL DEFAULT 0"),
            ("peak_debt", "INTEGER NOT NULL DEFAULT 0"),
            ("loan_count", "INTEGER NOT NULL DEFAULT 0"),
            ("rebirths", "INTEGER NOT NULL DEFAULT 0"),
            ("last_rebirth_at", "REAL NOT NULL DEFAULT 0"),
        ):
            if name not in columns:
                self._conn.execute(f"ALTER TABLE loan ADD COLUMN {name} {ddl}")
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------

    def _row(self, user_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM loan WHERE user_id = ?", (str(user_id),)).fetchone()
        return dict(row) if row else {"user_id": str(user_id), "debt": 0, "credit": 0, "last_borrow_date": ""}

    def status(self, user_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._row(user_id)
        return {
            "debt": int(row["debt"]),
            "credit": int(row["credit"]),
            "can_borrow": int(row["debt"]) <= 0,
            "total_borrowed": int(row.get("total_borrowed") or 0),
            "total_debt": int(row.get("total_debt") or 0),
            "total_repaid": int(row.get("total_repaid") or 0),
            "peak_debt": int(row.get("peak_debt") or 0),
            "loan_count": int(row.get("loan_count") or 0),
            "rebirths": int(row.get("rebirths") or 0),
            "last_rebirth_at": float(row.get("last_rebirth_at") or 0),
        }

    def borrow(self, user_id: str, amount: int, limit: int = MAX_LOAN) -> dict[str, Any]:
        amount = int(amount)
        if amount <= 0:
            return {"ok": False, "reason": "金额必须大于 0"}
        if amount > limit:
            return {"ok": False, "reason": f"单次最多借 {limit}"}
        with self._lock:
            row = self._row(user_id)
            if int(row["debt"]) > 0:
                return {"ok": False, "reason": "上一笔还没还清，先还款或重生"}
            peak = max(int(row.get("peak_debt") or 0), int(row["debt"]) + amount)
            self._conn.execute(
                """INSERT INTO loan
                   (user_id, debt, credit, last_borrow_date, total_borrowed, total_debt,
                    peak_debt, loan_count, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     debt = loan.debt + excluded.debt,
                     credit = loan.credit + excluded.credit,
                     last_borrow_date = '',
                     total_borrowed = loan.total_borrowed + excluded.total_borrowed,
                     total_debt = loan.total_debt + excluded.total_debt,
                     peak_debt = MAX(loan.peak_debt, excluded.peak_debt),
                     loan_count = loan.loan_count + 1,
                     updated_at = excluded.updated_at""",
                (str(user_id), amount, amount, "", amount, amount, peak, 1, time.time()),
            )
            self._conn.commit()
            row = self._row(user_id)
            self._log(user_id, "borrow", amount, int(row["debt"]))
        return {"ok": True, "debt": int(row["debt"]), "credit": int(row["credit"])}

    def spend_credit(self, user_id: str, amount: int) -> int:
        """从可用额度里扣，返回实际扣掉的额度。"""
        if amount <= 0:
            return 0
        with self._lock:
            row = self._row(user_id)
            used = min(int(row["credit"]), int(amount))
            if used:
                self._conn.execute(
                    "UPDATE loan SET credit = credit - ?, updated_at = ? WHERE user_id = ?",
                    (used, time.time(), str(user_id)),
                )
                self._conn.commit()
            return used

    def repay(self, user_id: str, amount: int) -> dict[str, Any]:
        """用钱包余额还款：先抵消未花额度，再抵债务。"""
        amount = int(amount)
        with self._lock:
            row = self._row(user_id)
            debt = int(row["debt"])
            credit = int(row["credit"])
            if debt <= 0:
                return {"ok": False, "reason": "没有待还的借款", "debt": 0, "credit": credit}
            pay = min(amount, debt)
            new_debt = debt - pay
            # 未花额度不能超过债务
            new_credit = min(credit, new_debt)
            self._conn.execute(
                "UPDATE loan SET debt = ?, credit = ?, total_repaid = total_repaid + ?, updated_at = ? "
                "WHERE user_id = ?",
                (new_debt, new_credit, pay, time.time(), str(user_id)),
            )
            self._conn.commit()
            self._log(user_id, "repay", pay, new_debt)
        return {"ok": True, "paid": pay, "debt": new_debt, "credit": new_credit}

    def _log(self, user_id: str, kind: str, amount: int, debt_after: int) -> None:
        self._conn.execute(
            "INSERT INTO loan_log (user_id, kind, amount, debt_after, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(user_id), kind, int(amount), int(debt_after), time.time()),
        )
        self._conn.commit()

    def clear(self, user_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE loan SET debt = 0, credit = 0, updated_at = ? WHERE user_id = ?",
                (time.time(), str(user_id)),
            )
            self._conn.commit()

    def record_rebirth(self, user_id: str) -> dict[str, Any]:
        """重生：清账、重生次数 +1、记录时间与历史欠款。"""
        with self._lock:
            row = self._row(user_id)
            debt_before = int(row["debt"])
            self._conn.execute(
                """INSERT INTO loan (user_id, debt, credit, rebirths, last_rebirth_at, updated_at)
                   VALUES (?, 0, 0, 1, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     debt = 0, credit = 0,
                     rebirths = loan.rebirths + 1,
                     last_rebirth_at = excluded.last_rebirth_at,
                     updated_at = excluded.updated_at""",
                (str(user_id), time.time(), time.time()),
            )
            self._conn.commit()
            self._log(user_id, "rebirth", debt_before, 0)
            row = self._row(user_id)
        return {
            "debt_before": debt_before,
            "rebirths": int(row.get("rebirths") or 0),
            "last_rebirth_at": float(row.get("last_rebirth_at") or 0),
        }

    def history(self, user_id: str, limit: int = 5) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT kind, amount, debt_after, created_at FROM loan_log "
                "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (str(user_id), int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]
