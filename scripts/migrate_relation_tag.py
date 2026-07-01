import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "db" / "memory.db"
SEEDS = {635773721: "闺蜜"}


def main() -> None:
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cols = [r[1] for r in cur.execute("PRAGMA table_info(user_impression)")]
    if "relation_tag" not in cols:
        cur.execute(
            "ALTER TABLE user_impression ADD COLUMN relation_tag TEXT DEFAULT ''"
        )
        print("added relation_tag column")
    for uid, tag in SEEDS.items():
        cur.execute(
            """UPDATE user_impression SET relation_tag = ?
               WHERE user_id = ? AND (relation_tag IS NULL OR relation_tag = '')""",
            (tag, uid),
        )
    conn.commit()
    cur.execute(
        "SELECT user_id, relation_tag FROM user_impression WHERE user_id IN (273421673,635773721,541518108)"
    )
    print("sample:", cur.fetchall())
    conn.close()


if __name__ == "__main__":
    main()
