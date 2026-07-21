"""TodayWaifu SQLite 持久化。"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any


class WaifuStore:
    """线程安全的 TodayWaifu 数据存储。"""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            timeout=30,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS active_users (
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    last_active REAL NOT NULL,
                    PRIMARY KEY (group_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS wife_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    wife_id TEXT NOT NULL,
                    wife_name TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    forced INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_wife_records_date_group
                    ON wife_records (date, group_id);

                CREATE TABLE IF NOT EXISTS force_marry_cd (
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    last_time REAL NOT NULL,
                    PRIMARY KEY (group_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS propose_cd (
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    expire_at REAL NOT NULL,
                    related_user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    PRIMARY KEY (group_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS rbq_stats (
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    forced_at REAL NOT NULL
                );
                """
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── 活跃用户 ──────────────────────────────────────────────

    def touch_active(
        self,
        group_id: str,
        user_id: str,
        ts: float | None = None,
    ) -> None:
        """更新用户最近活跃时间。"""
        now = float(ts if ts is not None else time.time())
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO active_users (group_id, user_id, last_active)
                VALUES (?, ?, ?)
                ON CONFLICT(group_id, user_id) DO UPDATE SET
                    last_active = excluded.last_active
                """,
                (str(group_id), str(user_id), now),
            )
            self._conn.commit()

    def cleanup_active(self, days: int, max_records: int) -> int:
        """清理过期活跃记录；超限时按全局最旧裁剪。返回删除条数。"""
        cutoff = time.time() - max(0, int(days)) * 86400
        removed = 0
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM active_users WHERE last_active < ?",
                (cutoff,),
            )
            removed += cur.rowcount if cur.rowcount > 0 else 0

            if max_records > 0:
                total = self._conn.execute(
                    "SELECT COUNT(*) FROM active_users"
                ).fetchone()[0]
                overflow = int(total) - int(max_records)
                if overflow > 0:
                    cur = self._conn.execute(
                        """
                        DELETE FROM active_users WHERE rowid IN (
                            SELECT rowid FROM active_users
                            ORDER BY last_active ASC
                            LIMIT ?
                        )
                        """,
                        (overflow,),
                    )
                    removed += cur.rowcount if cur.rowcount > 0 else 0

            self._conn.commit()
        return removed

    def list_active(self, group_id: str, days: int) -> set[str]:
        """返回群内最近 days 天活跃过的 user_id 集合。"""
        cutoff = time.time() - max(0, int(days)) * 86400
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT user_id FROM active_users
                WHERE group_id = ? AND last_active >= ?
                """,
                (str(group_id), cutoff),
            ).fetchall()
        return {str(r["user_id"]) for r in rows}

    # ── 抽老婆记录 ────────────────────────────────────────────

    def get_today_records(self, group_id: str, date_str: str) -> list[dict[str, Any]]:
        """获取某群某日全部抽老婆记录。"""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, date, group_id, user_id, wife_id, wife_name,
                       timestamp, forced
                FROM wife_records
                WHERE group_id = ? AND date = ?
                ORDER BY id ASC
                """,
                (str(group_id), date_str),
            ).fetchall()
        return [self._row_to_wife_dict(r) for r in rows]

    def get_user_today_records(
        self,
        group_id: str,
        user_id: str,
        date_str: str,
    ) -> list[dict[str, Any]]:
        """获取某用户某日抽老婆记录。"""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, date, group_id, user_id, wife_id, wife_name,
                       timestamp, forced
                FROM wife_records
                WHERE group_id = ? AND user_id = ? AND date = ?
                ORDER BY id ASC
                """,
                (str(group_id), str(user_id), date_str),
            ).fetchall()
        return [self._row_to_wife_dict(r) for r in rows]

    def add_wife_record(
        self,
        date: str,
        group_id: str,
        user_id: str,
        wife_id: str,
        wife_name: str,
        timestamp: str,
        forced: bool = False,
        daily_limit: int = 1,
    ) -> None:
        """写入抽老婆记录；daily_limit<=1 时覆盖当日，否则追加至上限。"""
        gid, uid = str(group_id), str(user_id)
        forced_i = 1 if forced else 0
        with self._lock:
            if daily_limit <= 1:
                self._conn.execute(
                    """
                    DELETE FROM wife_records
                    WHERE group_id = ? AND user_id = ? AND date = ?
                    """,
                    (gid, uid, date),
                )
                self._conn.execute(
                    """
                    INSERT INTO wife_records
                        (date, group_id, user_id, wife_id, wife_name, timestamp, forced)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (date, gid, uid, str(wife_id), wife_name, timestamp, forced_i),
                )
            else:
                count = self._conn.execute(
                    """
                    SELECT COUNT(*) FROM wife_records
                    WHERE group_id = ? AND user_id = ? AND date = ?
                    """,
                    (gid, uid, date),
                ).fetchone()[0]
                if int(count) >= int(daily_limit):
                    # 已达上限：随机替换一条旧记录
                    self._conn.execute(
                        """
                        DELETE FROM wife_records WHERE id = (
                            SELECT id FROM wife_records
                            WHERE group_id = ? AND user_id = ? AND date = ?
                            ORDER BY RANDOM()
                            LIMIT 1
                        )
                        """,
                        (gid, uid, date),
                    )
                self._conn.execute(
                    """
                    INSERT INTO wife_records
                        (date, group_id, user_id, wife_id, wife_name, timestamp, forced)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (date, gid, uid, str(wife_id), wife_name, timestamp, forced_i),
                )
            self._conn.commit()

    def clear_today_records(
        self,
        group_id: str | None = None,
        date_str: str | None = None,
    ) -> None:
        """清除指定群/日期的记录；date_str 为空则用今天。"""
        d = date_str or date.today().isoformat()
        with self._lock:
            if group_id is None:
                self._conn.execute(
                    "DELETE FROM wife_records WHERE date = ?",
                    (d,),
                )
            else:
                self._conn.execute(
                    "DELETE FROM wife_records WHERE group_id = ? AND date = ?",
                    (str(group_id), d),
                )
            self._conn.commit()

    @staticmethod
    def _row_to_wife_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "date": row["date"],
            "group_id": row["group_id"],
            "user_id": row["user_id"],
            "wife_id": row["wife_id"],
            "wife_name": row["wife_name"],
            "timestamp": row["timestamp"],
            "forced": bool(row["forced"]),
        }

    # ── 强娶 CD ───────────────────────────────────────────────

    def get_force_cd(self, group_id: str, user_id: str) -> float | None:
        """返回强娶上次时间戳，无记录则 None。"""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT last_time FROM force_marry_cd
                WHERE group_id = ? AND user_id = ?
                """,
                (str(group_id), str(user_id)),
            ).fetchone()
        return float(row["last_time"]) if row else None

    def set_force_cd(self, group_id: str, user_id: str, ts: float) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO force_marry_cd (group_id, user_id, last_time)
                VALUES (?, ?, ?)
                ON CONFLICT(group_id, user_id) DO UPDATE SET
                    last_time = excluded.last_time
                """,
                (str(group_id), str(user_id), float(ts)),
            )
            self._conn.commit()

    def clear_force_cd(self, group_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM force_marry_cd WHERE group_id = ?",
                (str(group_id),),
            )
            self._conn.commit()

    # ── 求婚 CD ───────────────────────────────────────────────

    def get_propose_cd(self, group_id: str, user_id: str) -> dict[str, Any] | None:
        """返回求婚 CD 记录，无则 None。"""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT group_id, user_id, expire_at, related_user_id, role
                FROM propose_cd
                WHERE group_id = ? AND user_id = ?
                """,
                (str(group_id), str(user_id)),
            ).fetchone()
        if not row:
            return None
        return {
            "group_id": row["group_id"],
            "user_id": row["user_id"],
            "expire_at": float(row["expire_at"]),
            "related_user_id": row["related_user_id"],
            "role": row["role"],
        }

    def set_propose_cd(
        self,
        group_id: str,
        user_id: str,
        expire_at: float,
        related_user_id: str,
        role: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO propose_cd
                    (group_id, user_id, expire_at, related_user_id, role)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(group_id, user_id) DO UPDATE SET
                    expire_at = excluded.expire_at,
                    related_user_id = excluded.related_user_id,
                    role = excluded.role
                """,
                (
                    str(group_id),
                    str(user_id),
                    float(expire_at),
                    str(related_user_id),
                    str(role),
                ),
            )
            self._conn.commit()

    def clear_propose_cd(self, group_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM propose_cd WHERE group_id = ?",
                (str(group_id),),
            )
            self._conn.commit()

    # ── RBQ 统计 ──────────────────────────────────────────────

    def add_rbq(self, group_id: str, user_id: str, ts: float) -> None:
        """追加一次被强娶记录。"""
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO rbq_stats (group_id, user_id, forced_at)
                VALUES (?, ?, ?)
                """,
                (str(group_id), str(user_id), float(ts)),
            )
            self._conn.commit()

    def rbq_ranking(
        self,
        group_id: str,
        days: int = 30,
        limit: int = 10,
    ) -> list[tuple[str, int]]:
        """返回群内 RBQ 排行 (user_id, count)。"""
        cutoff = time.time() - max(0, int(days)) * 86400
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT user_id, COUNT(*) AS cnt
                FROM rbq_stats
                WHERE group_id = ? AND forced_at >= ?
                GROUP BY user_id
                ORDER BY cnt DESC, user_id ASC
                LIMIT ?
                """,
                (str(group_id), cutoff, max(1, int(limit))),
            ).fetchall()
        return [(str(r["user_id"]), int(r["cnt"])) for r in rows]

    def cleanup_rbq(self, days: int = 30) -> None:
        """删除超过 days 天的 RBQ 记录。"""
        cutoff = time.time() - max(0, int(days)) * 86400
        with self._lock:
            self._conn.execute(
                "DELETE FROM rbq_stats WHERE forced_at < ?",
                (cutoff,),
            )
            self._conn.commit()
