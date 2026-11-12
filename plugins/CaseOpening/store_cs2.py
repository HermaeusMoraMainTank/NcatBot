"""CS2 开箱的 SQLite 存储（CaseOpening/cs2 使用）。

单库单文件，WAL 模式；所有写操作在进程内锁下完成，
扣费与入库由插件层用"先写事务再改余额"的顺序保证一致。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

MAX_INVENTORY = 200

SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    group_id TEXT NOT NULL DEFAULT '',
    skin_id TEXT NOT NULL,
    name TEXT NOT NULL,
    name_cn TEXT NOT NULL DEFAULT '',
    wear TEXT NOT NULL DEFAULT '',
    float_value REAL NOT NULL DEFAULT 0,
    stattrak INTEGER NOT NULL DEFAULT 0,
    rarity TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '#b0c3d9',
    market_hash_name TEXT NOT NULL DEFAULT '',
    price_cny REAL,
    acquired_at REAL NOT NULL,
    source TEXT NOT NULL DEFAULT 'open'
);
CREATE INDEX IF NOT EXISTS idx_inventory_user ON inventory(user_id, id DESC);

CREATE TABLE IF NOT EXISTS open_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    crate TEXT NOT NULL,
    cost INTEGER NOT NULL DEFAULT 0,
    item_name TEXT NOT NULL DEFAULT '',
    rarity TEXT NOT NULL DEFAULT '',
    price_cny REAL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_open_user ON open_log(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS market_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    item_name TEXT NOT NULL DEFAULT '',
    side TEXT NOT NULL DEFAULT 'sell',
    amount INTEGER NOT NULL DEFAULT 0,
    fee INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
"""


class CaseStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(inventory)")}
        if "name_cn" not in columns:
            self._conn.execute("ALTER TABLE inventory ADD COLUMN name_cn TEXT NOT NULL DEFAULT ''")
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ 库存

    def add_item(
        self,
        user_id: str,
        group_id: str,
        *,
        skin_id: str,
        name: str,
        name_cn: str = "",
        wear: str,
        float_value: float,
        stattrak: bool,
        rarity: str,
        color: str,
        market_hash_name: str,
        price_cny: float | None,
        source: str = "open",
    ) -> int:
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO inventory
                   (user_id, group_id, skin_id, name, name_cn, wear, float_value, stattrak,
                    rarity, color, market_hash_name, price_cny, acquired_at, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(user_id),
                    str(group_id),
                    skin_id,
                    name,
                    name_cn,
                    wear,
                    float(float_value),
                    1 if stattrak else 0,
                    rarity,
                    color,
                    market_hash_name,
                    price_cny,
                    now,
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

    # ------------------------------------------------------------------ 日志

    def log_open(
        self,
        user_id: str,
        crate: str,
        cost: int,
        item_name: str,
        rarity: str,
        price_cny: float | None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO open_log (user_id, crate, cost, item_name, rarity, price_cny, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (str(user_id), crate, int(cost), item_name, rarity, price_cny, time.time()),
            )
            self._conn.commit()

    def log_market(self, user_id: str, item_name: str, side: str, amount: int, fee: int = 0) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO market_log (user_id, item_name, side, amount, fee, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (str(user_id), item_name, side, int(amount), int(fee), time.time()),
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

    def best_drop(self, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT item_name, rarity, price_cny FROM open_log "
                "WHERE user_id = ? AND price_cny IS NOT NULL ORDER BY price_cny DESC LIMIT 1",
                (str(user_id),),
            ).fetchone()
        return dict(row) if row else None

    def stats(self, user_id: str) -> dict[str, Any]:
        with self._lock:
            opens = self._conn.execute(
                "SELECT COUNT(*) AS c, COALESCE(SUM(cost),0) AS cost FROM open_log WHERE user_id = ?",
                (str(user_id),),
            ).fetchone()
            earned = self._conn.execute(
                "SELECT COALESCE(SUM(amount),0) AS total FROM market_log WHERE user_id = ?",
                (str(user_id),),
            ).fetchone()
            best = self._conn.execute(
                "SELECT item_name, rarity, price_cny FROM open_log "
                "WHERE user_id = ? AND price_cny IS NOT NULL ORDER BY price_cny DESC LIMIT 1",
                (str(user_id),),
            ).fetchone()
        return {
            "opens": int(opens["c"]),
            "spent": int(opens["cost"]),
            "earned": int(earned["total"]),
            "best": dict(best) if best else None,
        }
