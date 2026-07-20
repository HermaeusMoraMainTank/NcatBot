"""谁艾特我 — SQLite 持久化。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import List, Optional

from .models import (
    PendingAt,
    StoredMessage,
    context_from_jsonable,
    context_to_jsonable,
)


class AtStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS pending_ats (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        group_id INTEGER NOT NULL,
                        target_user_id TEXT NOT NULL,
                        message_id TEXT NOT NULL,
                        atter_user_id TEXT NOT NULL,
                        atter_nickname TEXT NOT NULL,
                        timestamp REAL NOT NULL,
                        created_at REAL NOT NULL,
                        context_json TEXT NOT NULL DEFAULT '[]'
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_pending_target
                    ON pending_ats (group_id, target_user_id, created_at)
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_pending_message
                    ON pending_ats (group_id, message_id)
                    """
                )
                conn.commit()

    def insert(self, pending: PendingAt) -> PendingAt:
        payload = json.dumps(context_to_jsonable(pending.context), ensure_ascii=False)
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO pending_ats (
                        group_id, target_user_id, message_id,
                        atter_user_id, atter_nickname, timestamp, created_at, context_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(pending.group_id),
                        str(pending.target_user_id),
                        str(pending.message_id),
                        str(pending.atter_user_id),
                        str(pending.atter_nickname),
                        float(pending.timestamp),
                        float(pending.created_at),
                        payload,
                    ),
                )
                conn.commit()
                pending.id = int(cur.lastrowid)
        return pending

    def update_context(self, pending_id: int, context: List[StoredMessage]) -> None:
        payload = json.dumps(context_to_jsonable(context), ensure_ascii=False)
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE pending_ats SET context_json = ? WHERE id = ?",
                    (payload, int(pending_id)),
                )
                conn.commit()

    def list_for_target(
        self, group_id: int, target_user_id: str, *, expire_seconds: int
    ) -> List[PendingAt]:
        self.cleanup_expired(expire_seconds)
        cutoff = time.time() - max(1, int(expire_seconds))
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM pending_ats
                    WHERE group_id = ? AND target_user_id = ? AND created_at >= ?
                    ORDER BY timestamp ASC, id ASC
                    """,
                    (int(group_id), str(target_user_id), cutoff),
                ).fetchall()
        return [self._row_to_pending(r) for r in rows]

    def delete_ids(self, ids: List[int]) -> None:
        if not ids:
            return
        with self._lock:
            with self._connect() as conn:
                conn.executemany(
                    "DELETE FROM pending_ats WHERE id = ?",
                    [(int(i),) for i in ids],
                )
                conn.commit()

    def delete_for_target(self, group_id: int, target_user_id: str) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM pending_ats WHERE group_id = ? AND target_user_id = ?",
                    (int(group_id), str(target_user_id)),
                )
                conn.commit()

    def handle_recall(self, group_id: int, message_id: str) -> int:
        """撤回：删除以该消息为主的 pending，并从其它 pending 上下文中剔除。返回影响行数。"""
        mid = str(message_id)
        gid = int(group_id)
        affected = 0
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    "DELETE FROM pending_ats WHERE group_id = ? AND message_id = ?",
                    (gid, mid),
                )
                affected += cur.rowcount
                rows = conn.execute(
                    "SELECT id, context_json FROM pending_ats WHERE group_id = ?",
                    (gid,),
                ).fetchall()
                for row in rows:
                    try:
                        ctx = json.loads(row["context_json"] or "[]")
                    except Exception:
                        continue
                    if not isinstance(ctx, list):
                        continue
                    new_ctx = [
                        m
                        for m in ctx
                        if not (isinstance(m, dict) and str(m.get("message_id")) == mid)
                    ]
                    if len(new_ctx) == len(ctx):
                        continue
                    conn.execute(
                        "UPDATE pending_ats SET context_json = ? WHERE id = ?",
                        (json.dumps(new_ctx, ensure_ascii=False), int(row["id"])),
                    )
                    affected += 1
                conn.commit()
        return affected

    def cleanup_expired(self, expire_seconds: int) -> int:
        cutoff = time.time() - max(1, int(expire_seconds))
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    "DELETE FROM pending_ats WHERE created_at < ?",
                    (cutoff,),
                )
                conn.commit()
                return int(cur.rowcount)

    def load_all_alive(self, expire_seconds: int) -> List[PendingAt]:
        self.cleanup_expired(expire_seconds)
        cutoff = time.time() - max(1, int(expire_seconds))
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM pending_ats
                    WHERE created_at >= ?
                    ORDER BY id ASC
                    """,
                    (cutoff,),
                ).fetchall()
        return [self._row_to_pending(r) for r in rows]

    @staticmethod
    def _row_to_pending(row: sqlite3.Row) -> PendingAt:
        try:
            raw = json.loads(row["context_json"] or "[]")
        except Exception:
            raw = []
        return PendingAt(
            id=int(row["id"]),
            group_id=int(row["group_id"]),
            target_user_id=str(row["target_user_id"]),
            message_id=str(row["message_id"]),
            atter_user_id=str(row["atter_user_id"]),
            atter_nickname=str(row["atter_nickname"]),
            timestamp=float(row["timestamp"]),
            created_at=float(row["created_at"]),
            context=context_from_jsonable(raw),
        )
