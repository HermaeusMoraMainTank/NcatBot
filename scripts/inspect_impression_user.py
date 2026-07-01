"""Inspect FakeAi user_impression row for a given QQ id."""
import json
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "db" / "memory.db"
UID = int(sys.argv[1]) if len(sys.argv) > 1 else 541518108


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(user_impression)")
    cols = [r[1] for r in cur.fetchall()]
    print("columns:", cols)

    cur.execute(
        "SELECT * FROM user_impression WHERE CAST(user_id AS TEXT) = ? OR user_id = ?",
        (str(UID), UID),
    )
    row = cur.fetchone()
    if not row:
        print(f"NO ROW for user_id={UID}")
        conn.close()
        return

    d = dict(row)
    print(f"FOUND user_id={d['user_id']!r} (type {type(d['user_id']).__name__})")
    print("---")

    json_fields = ("events", "new_knowledge", "important_events")
    for k, v in d.items():
        if k in json_fields:
            raw = v or ""
            print(f"{k}: raw_len={len(raw)}")
            try:
                parsed = json.loads(raw) if raw else []
                print(f"  json OK, count={len(parsed)}")
                total = sum(len(str(x)) for x in parsed)
                print(f"  total_str_len={total}")
                for i, item in enumerate(parsed[:3]):
                    s = str(item)
                    print(f"  [{i}] len={len(s)} preview={s[:80]!r}")
            except json.JSONDecodeError as e:
                print(f"  JSON_DECODE_ERROR: {e}")
                print(f"  head={raw[:300]!r}")
        elif k == "impression":
            print(f"impression: len={len(v or '')}")
            print(f"  preview={(v or '')[:200]!r}")
        else:
            print(f"{k}: {v!r}")

    cur.execute(
        """
        SELECT user_id,
               length(events) + length(new_knowledge) + length(important_events) AS json_total,
               length(impression) AS imp_len
        FROM user_impression
        ORDER BY json_total DESC
        LIMIT 8
        """
    )
    print("\nTop rows by JSON field size:")
    for r in cur.fetchall():
        mark = " <-- TARGET" if int(r["user_id"]) == UID else ""
        print(f"  {r['user_id']}: json_total={r['json_total']}, imp_len={r['imp_len']}{mark}")

    conn.close()


if __name__ == "__main__":
    main()
