"""Dota 2 宝瓶的 SQLite 存储（CaseOpening/dota 使用）。"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    group_id TEXT NOT NULL DEFAULT '',
    treasure TEXT NOT NULL,
    item_name TEXT NOT NULL,
    tier TEXT NOT NULL DEFAULT 'Regular',
    icon TEXT,
    marketable INTEGER NOT NULL DEFAULT 0,
    price_cny REAL,
    obtained_at REAL NOT NULL,
    tradeable_at REAL NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'open'
);
CREATE INDEX IF NOT EXISTS idx_dota_inv_user ON inventory(user_id, id DESC);

CREATE TABLE IF NOT EXISTS pity (
    user_id TEXT NOT NULL,
    treasure TEXT NOT NULL,
    tier TEXT NOT NULL,
    counter INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL,
    PRIMARY KEY (user_id, treasure, tier)
);

CREATE TABLE IF NOT EXISTS collected (
    user_id TEXT NOT NULL,
    treasure TEXT NOT NULL,
    item_name TEXT NOT NULL,
    first_at REAL NOT NULL,
    PRIMARY KEY (user_id, treasure, item_name)
);

CREATE TABLE IF NOT EXISTS bonus (
    user_id TEXT NOT NULL,
    treasure TEXT NOT NULL,
    threshold INTEGER NOT NULL,
    claimed_at REAL NOT NULL,
    PRIMARY KEY (user_id, treasure, threshold)
);

CREATE TABLE IF NOT EXISTS open_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    treasure TEXT NOT NULL,
    cost INTEGER NOT NULL DEFAULT 0,
    tier TEXT NOT NULL DEFAULT 'Regular',
    item_name TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dota_open_user ON open_log(user_id, created_at DESC);
"""


class TreasureStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ 背包

    def add_item(
        self,
        user_id: str,
        group_id: str,
        *,
        treasure: str,
        item_name: str,
        tier: str,
        icon: str | None,
        marketable: bool,
        price_cny: float | None,
        trade_lock_days: int = 0,
        source: str = "open",
    ) -> int:
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO inventory
                   (user_id, group_id, treasure, item_name, tier, icon, marketable,
                    price_cny, obtained_at, tradeable_at, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(user_id),
                    str(group_id),
                    treasure,
                    item_name,
                    tier,
                    icon,
                    1 if marketable else 0,
                    price_cny,
                    now,
                    now + trade_lock_days * 86400,
                    source,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def item(self, user_id: str, item_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM inventory WHERE id = ? AND user_id = ?",
                (int(item_id), str(user_id)),
            ).fetchone()
        return dict(row) if row else None

    def items(self, user_id: str, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM inventory WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                (str(user_id), int(limit), int(offset)),
            ).fetchall()
        return [dict(row) for row in rows]

    def items_by_ids(self, user_id: str, ids: list[int]) -> list[dict[str, Any]]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM inventory WHERE user_id = ? AND id IN ({placeholders})",
                (str(user_id), *[int(i) for i in ids]),
            ).fetchall()
        return [dict(row) for row in rows]

    def items_sorted(self, user_id: str, limit: int = 200) -> list[dict[str, Any]]:
        """按价格从高到低（无价排最后），同价按编号倒序。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM inventory WHERE user_id = ? "
                "ORDER BY COALESCE(price_cny, 0) DESC, id DESC LIMIT ?",
                (str(user_id), int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def clear_user(self, user_id: str) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM inventory WHERE user_id = ?", (str(user_id),))
            self._conn.commit()
            return int(cur.rowcount)

    def count(self, user_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM inventory WHERE user_id = ?", (str(user_id),)
            ).fetchone()
        return int(row["c"])

    def remove_items(self, user_id: str, ids: list[int]) -> int:
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            cur = self._conn.execute(
                f"DELETE FROM inventory WHERE user_id = ? AND id IN ({placeholders})",
                (str(user_id), *[int(i) for i in ids]),
            )
            self._conn.commit()
            return int(cur.rowcount)

    # ------------------------------------------------------------------ 计数

    def pity(self, user_id: str, treasure: str) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT tier, counter FROM pity WHERE user_id = ? AND treasure = ?",
                (str(user_id), treasure),
            ).fetchall()
        return {str(row["tier"]): int(row["counter"]) for row in rows}

    def set_pity(self, user_id: str, treasure: str, tier: str, counter: int) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO pity (user_id, treasure, tier, counter, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, treasure, tier) DO UPDATE SET
                     counter = excluded.counter, updated_at = excluded.updated_at""",
                (str(user_id), treasure, tier, int(counter), time.time()),
            )
            self._conn.commit()

    def collected(self, user_id: str, treasure: str) -> set[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT item_name FROM collected WHERE user_id = ? AND treasure = ?",
                (str(user_id), treasure),
            ).fetchall()
        return {str(row["item_name"]) for row in rows}

    def mark_collected(self, user_id: str, treasure: str, item_name: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO collected (user_id, treasure, item_name, first_at) VALUES (?, ?, ?, ?)",
                (str(user_id), treasure, item_name, time.time()),
            )
            self._conn.commit()

    def opens(self, user_id: str, treasure: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM open_log WHERE user_id = ? AND treasure = ?",
                (str(user_id), treasure),
            ).fetchone()
        return int(row["c"])

    def claimed_bonus(self, user_id: str, treasure: str) -> set[int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT threshold FROM bonus WHERE user_id = ? AND treasure = ?",
                (str(user_id), treasure),
            ).fetchall()
        return {int(row["threshold"]) for row in rows}

    def claim_bonus(self, user_id: str, treasure: str, threshold: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO bonus (user_id, treasure, threshold, claimed_at) VALUES (?, ?, ?, ?)",
                (str(user_id), treasure, int(threshold), time.time()),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def log_open(self, user_id: str, treasure: str, cost: int, tier: str, item_name: str) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO open_log (user_id, treasure, cost, tier, item_name, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (str(user_id), treasure, int(cost), tier, item_name, time.time()),
            )
            self._conn.commit()

    def leaderboard(self, limit: int = 5) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT user_id, COUNT(*) AS opens, COALESCE(SUM(cost),0) AS spent
                   FROM open_log GROUP BY user_id ORDER BY opens DESC LIMIT ?""",
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def stats(self, user_id: str) -> dict[str, Any]:
        with self._lock:
            opens = self._conn.execute(
                "SELECT COUNT(*) AS c, COALESCE(SUM(cost),0) AS cost FROM open_log WHERE user_id = ?",
                (str(user_id),),
            ).fetchone()
            rare = self._conn.execute(
                "SELECT COUNT(*) AS c FROM open_log WHERE user_id = ? AND tier <> 'Regular'",
                (str(user_id),),
            ).fetchone()
            best = self._conn.execute(
                """SELECT treasure, item_name, tier FROM open_log
                   WHERE user_id = ? AND tier <> 'Regular'
                   ORDER BY CASE tier WHEN 'Cosmically Rare' THEN 4 WHEN 'Ultra Rare' THEN 3
                                      WHEN 'Very Rare' THEN 2 ELSE 1 END DESC LIMIT 1""",
                (str(user_id),),
            ).fetchone()
        return {
            "opens": int(opens["c"]),
            "spent": int(opens["cost"]),
            "rare": int(rare["c"]),
            "best": dict(best) if best else None,
        }
