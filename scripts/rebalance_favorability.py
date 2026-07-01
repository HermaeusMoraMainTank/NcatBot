"""一次性纠偏好感度：仅 273421673 可为 100。"""

import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "db" / "memory.db"

FIXES = {
    273421673: 100.0,
    541518108: 88.5,
}
MAX_SCORE_USERS = (273421673, 635773721)


def main() -> None:
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT)"
    )
    for uid, score in FIXES.items():
        cur.execute(
            "UPDATE user_impression SET favorability_score = ?, favorability = ? WHERE user_id = ?",
            (score, int(round(score)), uid),
        )
    placeholders = ",".join("?" * len(MAX_SCORE_USERS))
    cur.execute(
        f"""UPDATE user_impression SET favorability_score = 99.0, favorability = 99
           WHERE favorability_score >= 99.9 AND user_id NOT IN ({placeholders})""",
        MAX_SCORE_USERS,
    )
    cur.execute(
        "INSERT OR REPLACE INTO app_meta (key, value) VALUES ('favorability_v3_rebalanced', '1')"
    )
    conn.commit()
    cur.execute(
        "SELECT user_id, favorability_score FROM user_impression WHERE user_id IN (273421673,635773721,541518108) ORDER BY favorability_score DESC"
    )
    print("rebalanced:", cur.fetchall())
    cur.execute("SELECT COUNT(*) FROM user_impression WHERE favorability_score >= 99.9")
    print("users_at_100:", cur.fetchone()[0])
    conn.close()


if __name__ == "__main__":
    main()
