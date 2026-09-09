from __future__ import annotations

import sqlite3
import threading
import time
from datetime import date, timedelta
from pathlib import Path

from .config import XP_PER_CHECKIN


class EconomyStore:
    """账户、签到和礼物的单库事务存储。"""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                user_id TEXT PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 0,
                score INTEGER NOT NULL DEFAULT 0,
                xp INTEGER NOT NULL DEFAULT 0,
                total_earned INTEGER NOT NULL DEFAULT 0,
                total_spent INTEGER NOT NULL DEFAULT 0,
                nickname TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS checkins (
                user_id TEXT NOT NULL,
                day TEXT NOT NULL,
                reward INTEGER NOT NULL,
                score_delta INTEGER NOT NULL,
                streak INTEGER NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (user_id, day)
            );
            CREATE TABLE IF NOT EXISTS ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                delta INTEGER NOT NULL,
                reason TEXT NOT NULL,
                related_user_id TEXT NOT NULL DEFAULT '',
                request_id TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS gifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                receiver_id TEXT NOT NULL,
                day TEXT NOT NULL,
                amount INTEGER NOT NULL,
                favor_delta INTEGER NOT NULL,
                mood TEXT NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(group_id, sender_id, day)
            );
            """
        )
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(accounts)")}
        if "xp" not in columns:
            self._conn.execute("ALTER TABLE accounts ADD COLUMN xp INTEGER NOT NULL DEFAULT 0")
        self._conn.execute(
            "UPDATE accounts SET xp = score * ? WHERE xp = 0 AND score > 0",
            (XP_PER_CHECKIN,),
        )
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _ensure_account(self, user_id: str, nickname: str = "") -> None:
        self._conn.execute(
            """INSERT INTO accounts(user_id, nickname, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
               nickname = CASE WHEN excluded.nickname <> '' THEN excluded.nickname ELSE accounts.nickname END,
               updated_at = excluded.updated_at""",
            (str(user_id), nickname, time.time()),
        )

    def checkin(self, user_id: str, nickname: str, reward: int, day: str | None = None):
        day = day or date.today().isoformat()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_account(user_id, nickname)
                old = self._conn.execute(
                    "SELECT balance, score, xp FROM accounts WHERE user_id = ?",
                    (str(user_id),),
                ).fetchone()
                previous = self._conn.execute(
                    "SELECT day, streak FROM checkins WHERE user_id = ? ORDER BY day DESC LIMIT 1",
                    (str(user_id),),
                ).fetchone()
                streak = 1
                if previous:
                    try:
                        consecutive = date.fromisoformat(day) - date.fromisoformat(previous["day"])
                        if consecutive == timedelta(days=1):
                            streak = int(previous["streak"]) + 1
                    except ValueError:
                        streak = 1
                cur = self._conn.execute(
                    """INSERT OR IGNORE INTO checkins
                       (user_id, day, reward, score_delta, streak, created_at)
                       VALUES (?, ?, ?, 1, ?, ?)""",
                    (str(user_id), day, int(reward), streak, time.time()),
                )
                if cur.rowcount == 0:
                    row = self._conn.execute(
                        "SELECT * FROM accounts WHERE user_id = ?", (str(user_id),)
                    ).fetchone()
                    self._conn.commit()
                    return {"ok": False, "balance": int(row["balance"]), "score": int(row["score"]), "streak": streak}
                self._conn.execute(
                    """UPDATE accounts SET balance = balance + ?, score = score + 1, xp = xp + ?,
                       total_earned = total_earned + ?, updated_at = ? WHERE user_id = ?""",
                    (int(reward), XP_PER_CHECKIN, int(reward), time.time(), str(user_id)),
                )
                self._conn.execute(
                    """INSERT INTO ledger(user_id, delta, reason, request_id, created_at)
                       VALUES (?, ?, 'checkin', ?, ?)""",
                    (str(user_id), int(reward), f"checkin:{user_id}:{day}", time.time()),
                )
                self._conn.commit()
                return {"ok": True, "balance": int(old["balance"]) + int(reward), "score": int(old["score"]) + 1, "xp": int(old["xp"]) + XP_PER_CHECKIN, "streak": streak, "reward": int(reward)}
            except Exception:
                self._conn.rollback()
                raise

    def account(self, user_id: str) -> dict:
        with self._lock:
            self._ensure_account(user_id)
            row = self._conn.execute("SELECT * FROM accounts WHERE user_id = ?", (str(user_id),)).fetchone()
            self._conn.commit()
        return dict(row)

    def grant(self, user_id: str, amount: int, reason: str = "admin_grant") -> dict:
        """Add wallet balance through the ledger for an explicit administrative grant."""
        amount = int(amount)
        if amount <= 0:
            raise ValueError("grant amount must be positive")
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_account(str(user_id))
                row = self._conn.execute(
                    "SELECT balance FROM accounts WHERE user_id = ?", (str(user_id),)
                ).fetchone()
                request_id = f"grant:{user_id}:{time.time_ns()}"
                self._conn.execute(
                    """UPDATE accounts SET balance = balance + ?,
                       total_earned = total_earned + ?, updated_at = ?
                       WHERE user_id = ?""",
                    (amount, amount, time.time(), str(user_id)),
                )
                self._conn.execute(
                    """INSERT INTO ledger
                       (user_id, delta, reason, request_id, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (str(user_id), amount, str(reason), request_id, time.time()),
                )
                balance = int(row["balance"]) + amount
                self._conn.commit()
                return {"ok": True, "amount": amount, "balance": balance}
            except Exception:
                self._conn.rollback()
                raise

    def change_balance(
        self,
        user_id: str,
        delta: int,
        *,
        reason: str,
        related_user_id: str = "",
    ) -> dict:
        """Atomically change a wallet balance and append a ledger entry."""
        delta = int(delta)
        if delta == 0:
            raise ValueError("balance delta must not be zero")
        user_id = str(user_id)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_account(user_id)
                row = self._conn.execute(
                    "SELECT balance FROM accounts WHERE user_id = ?", (user_id,)
                ).fetchone()
                old_balance = int(row["balance"])
                new_balance = old_balance + delta
                if new_balance < 0:
                    self._conn.rollback()
                    return {"ok": False, "balance": old_balance, "reason": "insufficient"}
                request_id = f"balance:{reason}:{user_id}:{time.time_ns()}"
                self._conn.execute(
                    """UPDATE accounts SET balance = ?, total_earned = total_earned + ?,
                       total_spent = total_spent + ?, updated_at = ? WHERE user_id = ?""",
                    (new_balance, max(delta, 0), max(-delta, 0), time.time(), user_id),
                )
                self._conn.execute(
                    """INSERT INTO ledger
                       (user_id, delta, reason, related_user_id, request_id, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (user_id, delta, str(reason), str(related_user_id), request_id, time.time()),
                )
                self._conn.commit()
                return {"ok": True, "delta": delta, "balance": new_balance}
            except Exception:
                self._conn.rollback()
                raise

    def transfer_balance(
        self,
        sender_id: str,
        receiver_id: str,
        amount: int,
        *,
        reason: str,
    ) -> dict:
        """Atomically transfer wallet balance and append both ledger entries."""
        amount = int(amount)
        sender_id = str(sender_id)
        receiver_id = str(receiver_id)
        if amount <= 0 or sender_id == receiver_id:
            return {"ok": False, "reason": "invalid"}
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_account(sender_id)
                self._ensure_account(receiver_id)
                rows = self._conn.execute(
                    "SELECT user_id, balance FROM accounts WHERE user_id IN (?, ?)",
                    (sender_id, receiver_id),
                ).fetchall()
                balances = {str(row["user_id"]): int(row["balance"]) for row in rows}
                sender_balance = balances[sender_id]
                receiver_balance = balances[receiver_id]
                if sender_balance < amount:
                    self._conn.rollback()
                    return {"ok": False, "balance": sender_balance, "reason": "insufficient"}

                now = time.time()
                self._conn.execute(
                    """UPDATE accounts SET balance = balance - ?, total_spent = total_spent + ?, updated_at = ?
                       WHERE user_id = ?""",
                    (amount, amount, now, sender_id),
                )
                self._conn.execute(
                    """UPDATE accounts SET balance = balance + ?, total_earned = total_earned + ?, updated_at = ?
                       WHERE user_id = ?""",
                    (amount, amount, now, receiver_id),
                )
                request_id = f"transfer:{reason}:{sender_id}:{receiver_id}:{time.time_ns()}"
                self._conn.execute(
                    """INSERT INTO ledger(user_id, delta, reason, related_user_id, request_id, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (sender_id, -amount, reason, receiver_id, f"{request_id}:sender", now),
                )
                self._conn.execute(
                    """INSERT INTO ledger(user_id, delta, reason, related_user_id, request_id, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (receiver_id, amount, reason, sender_id, f"{request_id}:receiver", now),
                )
                self._conn.commit()
                return {
                    "ok": True,
                    "amount": amount,
                    "sender_balance": sender_balance - amount,
                    "receiver_balance": receiver_balance + amount,
                }
            except Exception:
                self._conn.rollback()
                raise

    def global_ranking(self, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT user_id, nickname, score, xp, balance, total_earned, total_spent
                   FROM accounts ORDER BY xp DESC, user_id ASC LIMIT ?""",
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def global_rank(self, user_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                """SELECT COUNT(*) + 1 AS rank FROM accounts
                   WHERE xp > (SELECT xp FROM accounts WHERE user_id = ?)""",
                (str(user_id),),
            ).fetchone()
        return int(row["rank"]) if row else 1

    def wallet_ranking(self, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT user_id, nickname, score, xp, balance, total_earned, total_spent FROM accounts
                   ORDER BY balance DESC, total_earned DESC, user_id ASC LIMIT ?""",
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def last_checkin(self, user_id: str, day: str | None = None) -> dict | None:
        day = day or date.today().isoformat()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM checkins WHERE user_id = ? AND day = ?",
                (str(user_id), day),
            ).fetchone()
        return dict(row) if row else None

    def gift_status(
        self, group_id: str, sender_id: str, day: str | None = None
    ) -> dict | None:
        day = day or date.today().isoformat()
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM gifts
                   WHERE group_id = ? AND sender_id = ?
                     AND (day = ? OR day LIKE ?)
                   ORDER BY id DESC LIMIT 1""",
                (str(group_id), str(sender_id), day, f"{day}:%"),
            ).fetchone()
        return dict(row) if row else None

    def gift(
        self, group_id: str, sender_id: str, receiver_id: str, amount: int,
        reserve: int, favor_delta: int, mood: str, day: str | None = None,
        enforce_daily_limit: bool = True,
    ) -> dict:
        if int(amount) <= 0 or int(reserve) < 0:
            return {"ok": False, "reason": "invalid"}
        day = day or date.today().isoformat()
        gift_day = day if enforce_daily_limit else f"{day}:{time.time_ns()}"
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_account(sender_id)
                row = self._conn.execute("SELECT balance FROM accounts WHERE user_id = ?", (str(sender_id),)).fetchone()
                if int(row["balance"]) - int(amount) < int(reserve):
                    self._conn.rollback()
                    return {"ok": False, "reason": "reserve", "balance": int(row["balance"])}
                try:
                    self._conn.execute(
                        """INSERT INTO gifts(group_id, sender_id, receiver_id, day, amount, favor_delta, mood, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (str(group_id), str(sender_id), str(receiver_id), gift_day, int(amount), int(favor_delta), mood, time.time()),
                    )
                except sqlite3.IntegrityError:
                    self._conn.rollback()
                    return {"ok": False, "reason": "daily", "balance": int(row["balance"])}
                request_id = f"gift:{group_id}:{sender_id}:{gift_day}"
                self._conn.execute(
                    "UPDATE accounts SET balance = balance - ?, total_spent = total_spent + ?, updated_at = ? WHERE user_id = ?",
                    (int(amount), int(amount), time.time(), str(sender_id)),
                )
                self._conn.execute(
                    "INSERT INTO ledger(user_id, delta, reason, related_user_id, request_id, created_at) VALUES (?, ?, 'gift', ?, ?, ?)",
                    (str(sender_id), -int(amount), str(receiver_id), request_id, time.time()),
                )
                balance = int(row["balance"]) - int(amount)
                self._conn.commit()
                return {"ok": True, "amount": int(amount), "balance": balance, "favor_delta": int(favor_delta), "mood": mood}
            except Exception:
                self._conn.rollback()
                raise
