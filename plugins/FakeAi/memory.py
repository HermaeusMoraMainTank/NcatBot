"""
FakeAi 长期记忆模块
使用 SQLite 存储群聊总结作为 AI 的长期记忆
"""

import json
import os
import time
import logging
import ast
import re
from pathlib import Path
from typing import List, Optional, Dict
from datetime import datetime
import aiosqlite

_log = logging.getLogger(__name__)

# 供 FakeAi 主模块在「仅传入 event」时取回插件实例（主模块重载后 _fake_ai_plugin_instance 会重置，memory 通常不会重载）
fake_ai_plugin_ref = None


def _extract_json_object_candidate(text: str, required_key: str) -> str | None:
    """从文本中提取包含指定 key 的第一个平衡 JSON 对象片段。"""
    if not text:
        return None
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    if f'"{required_key}"' in candidate:
                        return candidate
                    break
        start = text.find("{", start + 1)
    return None


def _parse_llm_json_object(text: str, required_key: str) -> dict | None:
    """
    解析 LLM 输出中的 JSON 对象。
    先做平衡括号提取，再做多轮容错（尾逗号、python-dict 风格）。
    """
    candidate = _extract_json_object_candidate(text, required_key)
    if not candidate:
        return None

    attempts = [candidate]
    attempts.append(candidate.strip().replace("```json", "").replace("```", "").strip())
    attempts.append(
        attempts[-1]
        .replace("，", ",")
        .replace("：", ":")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )
    attempts.append(re.sub(r",\s*([}\]])", r"\1", attempts[-1]))

    for attempt in attempts:
        try:
            parsed = json.loads(attempt)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue

    try:
        parsed = ast.literal_eval(candidate)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return None

def _pick_fakeai_database_path() -> Path:
    """选择数据库文件：环境变量 > 仅 memory.db > 默认 cwd/data/db/memory.db。"""
    env = os.environ.get("FAKEAI_MEMORY_DB", "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    cwd_db_dir = (Path.cwd() / "data" / "db").resolve()
    repo_db_dir = (
        Path(__file__).resolve().parent.parent.parent / "data" / "db"
    ).resolve()

    candidates = [cwd_db_dir / "memory.db", repo_db_dir / "memory.db"]
    existing = [p for p in candidates if p.is_file()]
    if existing:
        if len(existing) > 1:
            try:
                chosen = max(existing, key=lambda p: p.stat().st_size)
                _log.warning(
                    "[FakeAi Memory] 发现多份数据库，按文件大小选用：%s | all=%s",
                    chosen,
                    ", ".join(str(x) for x in existing),
                )
                return chosen
            except OSError:
                pass
        return existing[0]

    return cwd_db_dir / "memory.db"


DATABASE_PATH = _pick_fakeai_database_path()
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)


class MemoryManager:
    """长期记忆管理器"""

    _instance: Optional["MemoryManager"] = None
    _db: Optional[aiosqlite.Connection] = None
    _database_path: Optional[Path] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def database_path(self) -> Path:
        """当前（或即将）使用的 SQLite 文件绝对路径。"""
        if self._database_path is not None:
            return self._database_path
        return _pick_fakeai_database_path()

    async def _ensure_db(self):
        """确保数据库已初始化"""
        if self._db is None:
            await self.init_db()

    async def init_db(self):
        """初始化数据库（每次连接前重新解析路径，避免 cwd/环境变量与导入时不一致）。"""
        try:
            if self._db is None:
                path = _pick_fakeai_database_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                self._database_path = path
                self._db = await aiosqlite.connect(str(path))
                self._db.row_factory = aiosqlite.Row
                await self._create_tables()
                cur = await self._db.execute("SELECT COUNT(*) FROM user_impression")
                imp_n = (await cur.fetchone())[0]
                _log.info(
                    "[FakeAi Memory] 数据库已就绪: %s | user_impression 记录数: %s",
                    path,
                    imp_n,
                )
            # 热重载插件时连接可能已存在，迁移仍需执行
            await self._run_migrations()
        except Exception as e:
            _log.error(f"[FakeAi Memory] 数据库初始化失败: {e}")

    async def _run_migrations(self):
        """执行 schema 迁移（幂等，可重复调用）。"""
        if self._db is None:
            return
        await self._migrate_drop_nickname_column()
        await self._migrate_favorability_v2()
        await self._migrate_relation_tag()

    async def _migrate_relation_tag(self):
        """添加 relation_tag 列（自由文本：闺蜜/妻子/丈夫等）。"""
        from .favorability import DEFAULT_RELATION_TAGS

        cursor = await self._db.execute("PRAGMA table_info(user_impression)")
        columns = [row[1] for row in await cursor.fetchall()]
        if "relation_tag" not in columns:
            await self._db.execute(
                "ALTER TABLE user_impression ADD COLUMN relation_tag TEXT DEFAULT ''"
            )
            await self._db.commit()

        for uid, tag in DEFAULT_RELATION_TAGS.items():
            await self._db.execute(
                """UPDATE user_impression SET relation_tag = ?
                   WHERE user_id = ? AND (relation_tag IS NULL OR relation_tag = '')""",
                (tag, uid),
            )
        await self._db.commit()

    async def _create_tables(self):
        """创建数据表"""
        await self._db.executescript("""
            -- 群聊总结表（长期记忆）
            CREATE TABLE IF NOT EXISTS group_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                summary TEXT NOT NULL,
                key_topics TEXT NOT NULL,
                participant_ids TEXT NOT NULL,
                message_count INTEGER DEFAULT 0,
                start_time INTEGER NOT NULL,
                end_time INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            );
            
            CREATE INDEX IF NOT EXISTS idx_summary_group_time 
                ON group_summary(group_id, created_at DESC);
            
            -- 用户印象表（全局，按用户ID统一管理）
            CREATE TABLE IF NOT EXISTS user_impression (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                username TEXT DEFAULT '',
                gender TEXT DEFAULT '',
                impression TEXT NOT NULL,
                favorability INTEGER DEFAULT 0,
                pending_favorability INTEGER DEFAULT 0,
                events TEXT DEFAULT '[]',
                new_knowledge TEXT DEFAULT '[]',
                important_events TEXT DEFAULT '[]',
                last_updated INTEGER NOT NULL,
                interaction_count INTEGER DEFAULT 1
            );
            
            CREATE INDEX IF NOT EXISTS idx_impression_user 
                ON user_impression(user_id);
            
            -- 重要事件表（记住重要的事情）
            CREATE TABLE IF NOT EXISTS important_event (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                description TEXT NOT NULL,
                related_users TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                importance INTEGER DEFAULT 1
            );
            
            CREATE INDEX IF NOT EXISTS idx_event_group_time 
                ON important_event(group_id, created_at DESC);
            
            -- 知识库表（永久存储所有学到的知识）
            CREATE TABLE IF NOT EXISTS knowledge_base (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                content TEXT NOT NULL,
                source_user_id INTEGER,
                source_username TEXT DEFAULT '',
                created_at INTEGER NOT NULL,
                hit_count INTEGER DEFAULT 0,
                last_hit_at INTEGER DEFAULT 0
            );
            
            CREATE INDEX IF NOT EXISTS idx_knowledge_keyword 
                ON knowledge_base(keyword);
            CREATE INDEX IF NOT EXISTS idx_knowledge_content
                ON knowledge_base(content);
        """)
        await self._db.commit()

    async def _migrate_favorability_v2(self):
        """旧整数好感度迁移至 favorability_score（-50~100 有界分数）。"""
        from .favorability import finalize_migrated_score, migrate_old_score

        cursor = await self._db.execute("PRAGMA table_info(user_impression)")
        columns = [row[1] for row in await cursor.fetchall()]
        if "favorability_score" not in columns:
            await self._db.execute(
                "ALTER TABLE user_impression ADD COLUMN favorability_score REAL"
            )
            await self._db.commit()

        cursor = await self._db.execute(
            """SELECT user_id, favorability, pending_favorability, favorability_score
               FROM user_impression"""
        )
        rows = await cursor.fetchall()
        migrated = 0
        for row in rows:
            if row["favorability_score"] is not None:
                continue
            old = int(row["favorability"] or 0)
            pending = int(row["pending_favorability"] or 0)
            uid = int(row["user_id"])
            score = finalize_migrated_score(migrate_old_score(old + pending), uid)
            await self._db.execute(
                """UPDATE user_impression SET favorability_score = ?, pending_favorability = 0,
                   favorability = ?
                   WHERE user_id = ?""",
                (score, int(round(score)), row["user_id"]),
            )
            migrated += 1

        if migrated:
            await self._db.commit()
            _log.info(
                "[FakeAi Memory] 好感度 v2 迁移完成: %s 条记录 → favorability_score (-50~100)",
                migrated,
            )

        await self._rebalance_relation_scores()

    async def _rebalance_relation_scores(self):
        """纠偏：核心关系用户可达 100；修正旧 VIP 误顶满的其他用户。"""
        from .favorability import (
            MAX_SCORE_USERS,
            REBALANCE_SCORE_TARGETS,
            finalize_migrated_score,
        )

        await self._ensure_db()
        cursor = await self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='app_meta'"
        )
        if not await cursor.fetchone():
            await self._db.execute(
                "CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT)"
            )

        cursor = await self._db.execute(
            "SELECT value FROM app_meta WHERE key = 'favorability_v3_rebalanced'"
        )
        if await cursor.fetchone():
            return

        fixed = 0
        for uid, target in REBALANCE_SCORE_TARGETS.items():
            score = finalize_migrated_score(target, uid)
            cur = await self._db.execute(
                "SELECT favorability_score FROM user_impression WHERE user_id = ?",
                (uid,),
            )
            row = await cur.fetchone()
            if not row:
                continue
            if abs(float(row["favorability_score"] or 0) - score) > 0.05:
                await self._db.execute(
                    """UPDATE user_impression SET favorability_score = ?, favorability = ?
                       WHERE user_id = ?""",
                    (score, int(round(score)), uid),
                )
                fixed += 1
                _log.info("[FakeAi Memory] 关系纠偏 user_id=%s -> %.1f", uid, score)

        # 核心关系用户保留满分能力（物述有栖等）
        for uid in MAX_SCORE_USERS:
            await self._db.execute(
                """UPDATE user_impression SET favorability_score = MAX(favorability_score, 95.0)
                   WHERE user_id = ?""",
                (uid,),
            )

        # 非核心用户误迁满分的压到 99
        placeholders = ",".join("?" * len(MAX_SCORE_USERS))
        cur = await self._db.execute(
            f"""UPDATE user_impression SET favorability_score = 99.0, favorability = 99
               WHERE favorability_score >= 99.9 AND user_id NOT IN ({placeholders})""",
            tuple(MAX_SCORE_USERS),
        )
        if cur.rowcount:
            fixed += cur.rowcount

        await self._db.execute(
            "INSERT OR REPLACE INTO app_meta (key, value) VALUES ('favorability_v3_rebalanced', '1')"
        )
        await self._db.commit()
        if fixed:
            _log.info("[FakeAi Memory] 好感度关系纠偏完成: %s 条", fixed)

    def _read_favorability_score(self, row: aiosqlite.Row) -> float:
        """从行数据读取好感度分数（兼容未迁移库）。"""
        from .favorability import FAVOR_DEFAULT, migrate_old_score

        if "favorability_score" in row.keys() and row["favorability_score"] is not None:
            return float(row["favorability_score"])
        old = int(row["favorability"] if "favorability" in row.keys() else 0)
        pending = int(
            row["pending_favorability"] if "pending_favorability" in row.keys() else 0
        )
        if old == 0 and pending == 0:
            return FAVOR_DEFAULT
        return migrate_old_score(old + pending)

    def _read_relation_tag(self, row: aiosqlite.Row) -> str:
        if "relation_tag" not in row.keys() or not row["relation_tag"]:
            return ""
        return str(row["relation_tag"]).strip()

    async def _migrate_drop_nickname_column(self):
        """移除 user_impression.nickname 列（已弃用，易与群昵称混淆）。"""
        cursor = await self._db.execute("PRAGMA table_info(user_impression)")
        columns = [row[1] for row in await cursor.fetchall()]
        if "nickname" not in columns:
            return
        await self._db.execute("ALTER TABLE user_impression DROP COLUMN nickname")
        await self._db.commit()
        _log.info("[FakeAi Memory] 已删除 user_impression.nickname 列")

    async def close(self):
        """关闭数据库连接"""
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        return self._db

    # ========== 群聊总结相关 ==========

    async def save_summary(
        self,
        group_id: int,
        summary: str,
        key_topics: List[str],
        participant_ids: List[int],
        message_count: int,
        start_time: int,
        end_time: int,
    ) -> int:
        """保存群聊总结"""
        cursor = await self._db.execute(
            """INSERT INTO group_summary 
               (group_id, summary, key_topics, participant_ids, 
                message_count, start_time, end_time, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                group_id,
                summary,
                json.dumps(key_topics, ensure_ascii=False),
                json.dumps(participant_ids),
                message_count,
                start_time,
                end_time,
                int(time.time()),
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_recent_summaries(
        self,
        group_id: int,
        limit: int = 5,
        hours: int = 24,
    ) -> List[Dict]:
        """获取最近的群聊总结"""
        since_time = int(time.time()) - hours * 3600
        cursor = await self._db.execute(
            """SELECT * FROM group_summary 
               WHERE group_id = ? AND created_at >= ?
               ORDER BY created_at DESC LIMIT ?""",
            (group_id, since_time, limit),
        )
        rows = await cursor.fetchall()

        result = []
        for row in rows:
            result.append(
                {
                    "id": row["id"],
                    "summary": row["summary"],
                    "key_topics": json.loads(row["key_topics"]),
                    "participant_ids": json.loads(row["participant_ids"]),
                    "message_count": row["message_count"],
                    "start_time": row["start_time"],
                    "end_time": row["end_time"],
                    "created_at": row["created_at"],
                }
            )
        return result

    async def get_long_term_memory(self, group_id: int, max_length: int = 500) -> str:
        """获取格式化的长期记忆文本"""
        await self._ensure_db()
        summaries = await self.get_recent_summaries(group_id, limit=3, hours=48)

        if not summaries:
            return "（暂无长期记忆）"

        memory_parts = []
        for s in reversed(summaries):  # 从旧到新排列
            time_str = datetime.fromtimestamp(s["created_at"]).strftime("%m-%d %H:%M")
            topics = "、".join(s["key_topics"][:3]) if s["key_topics"] else "闲聊"
            memory_parts.append(f"[{time_str}] {topics}: {s['summary']}")

        memory_text = "\n".join(memory_parts)

        # 截断过长的记忆
        if len(memory_text) > max_length:
            memory_text = memory_text[:max_length] + "..."

        return memory_text

    # ========== 用户印象相关（全局管理，按用户ID） ==========

    # 冻结用户列表（好感度不更新）
    FROZEN_USERS = [794383252]

    async def update_user_impression(
        self,
        user_id: int,
        impression_data: Dict,
        username: str = "",
    ):
        """更新用户印象（全局版本，按用户ID统一管理）

        Args:
            user_id: 用户ID
            impression_data: 包含以下字段的字典
                - gender: 性别
                - impression: 印象关键词（如"音乐爱好者,热情支持,互动积极"）
                - favorability_change: 好感度变化值（正负整数）
                - events: 最近互动事件列表
                - new_knowledge: 新学到的知识列表
                - important_events: 重要事件列表
            username: 用户名/群昵称
        """
        from .favorability import (
            DEFAULT_RELATION_TAGS,
            FAVOR_DEFAULT,
            apply_favor_change,
            get_relation_floor,
            normalize_relation_tag,
        )

        # 冻结用户：跳过好感度更新
        if user_id in self.FROZEN_USERS:
            _log.info(f"[FakeAi] 冻结用户 {user_id}，跳过印象和好感度更新")
            return

        now = int(time.time())

        # 将新知识保存到知识库（永久存储）
        new_knowledge = impression_data.get("new_knowledge", [])
        if new_knowledge:
            saved_count = await self.save_knowledge_batch(
                new_knowledge, source_user_id=user_id, source_username=username
            )
            if saved_count > 0:
                _log.info(f"[FakeAi] 从用户 {user_id} 学到 {saved_count} 条新知识")

        # 获取现有数据
        existing = await self.get_user_impression_full(user_id)

        if existing:
            # 更新现有记录
            # 如果传入了新的用户名，则更新；否则保留原来的
            final_username = username if username else existing.get("username", "")
            gender = impression_data.get("gender") or existing.get("gender", "")
            impression = impression_data.get("impression") or existing.get(
                "impression", ""
            )
            current_score = existing.get("favorability_score")
            if current_score is None:
                current_score = existing.get("favorability", FAVOR_DEFAULT)
            favorability_change = impression_data.get("favorability_change", 0)
            new_favorability = apply_favor_change(
                float(current_score), favorability_change, user_id
            )
            new_pending = 0
            new_interaction_count = existing.get("interaction_count", 0) + 1
            _log.info(
                f"[FakeAi] 用户 {user_id} 好感度: {current_score} -> {new_favorability}"
            )

            # 合并事件列表（保留最近10条）
            old_events = existing.get("events", [])
            new_events = impression_data.get("events", [])
            merged_events = (old_events + new_events)[-10:]

            # 合并新知识（保留最近20条）
            old_knowledge = existing.get("new_knowledge", [])
            new_knowledge = impression_data.get("new_knowledge", [])
            merged_knowledge = (old_knowledge + new_knowledge)[-20:]

            # 合并重要事件（保留最近10条）
            old_important = existing.get("important_events", [])
            new_important = impression_data.get("important_events", [])
            merged_important = (old_important + new_important)[-10:]

            relation_tag = existing.get("relation_tag") or ""
            if "relation_tag" in impression_data:
                parsed = normalize_relation_tag(impression_data.get("relation_tag"))
                if parsed:
                    relation_tag = parsed

            await self._db.execute(
                """UPDATE user_impression SET
                   username = ?,
                   gender = ?,
                   impression = ?,
                   favorability = ?,
                   favorability_score = ?,
                   pending_favorability = ?,
                   relation_tag = ?,
                   events = ?,
                   new_knowledge = ?,
                   important_events = ?,
                   last_updated = ?,
                   interaction_count = ?
                   WHERE CAST(user_id AS TEXT) = ?""",
                (
                    final_username,
                    gender,
                    impression,
                    int(round(new_favorability)),
                    new_favorability,
                    new_pending,
                    relation_tag,
                    json.dumps(merged_events, ensure_ascii=False),
                    json.dumps(merged_knowledge, ensure_ascii=False),
                    json.dumps(merged_important, ensure_ascii=False),
                    now,
                    new_interaction_count,
                    str(int(user_id)),
                ),
            )
        else:
            # 插入新记录
            initial_change = impression_data.get("favorability_change", 0)
            base_score = get_relation_floor(user_id) or FAVOR_DEFAULT
            initial_favorability = apply_favor_change(
                base_score, initial_change, user_id
            )
            relation_tag = normalize_relation_tag(
                impression_data.get("relation_tag")
            ) or DEFAULT_RELATION_TAGS.get(user_id, "")

            await self._db.execute(
                """INSERT INTO user_impression 
                   (user_id, username, gender, impression, favorability, favorability_score,
                    pending_favorability, relation_tag, events, new_knowledge, important_events,
                    last_updated, interaction_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (
                    user_id,
                    username,
                    impression_data.get("gender", ""),
                    impression_data.get("impression", ""),
                    int(round(initial_favorability)),
                    initial_favorability,
                    0,
                    relation_tag or "",
                    json.dumps(impression_data.get("events", []), ensure_ascii=False),
                    json.dumps(
                        impression_data.get("new_knowledge", []), ensure_ascii=False
                    ),
                    json.dumps(
                        impression_data.get("important_events", []), ensure_ascii=False
                    ),
                    now,
                ),
            )

        await self._db.commit()

    async def get_user_impression_full(self, user_id: int) -> Optional[Dict]:
        """获取用户完整印象数据（全局）"""
        await self._ensure_db()
        if self._db is None:
            _log.error("[FakeAi Memory] get_user_impression_full: 数据库未连接")
            return None
        # CAST：兼容历史上 user_id 以 INTEGER / TEXT 等不同方式写入的行
        uid_key = str(int(user_id))
        cursor = await self._db.execute(
            """SELECT * FROM user_impression
               WHERE CAST(user_id AS TEXT) = ? OR user_id = ?""",
            (uid_key, int(user_id)),
        )
        row = await cursor.fetchone()
        if not row:
            return None

        uid = row["user_id"]

        def _parse_json_list(field: str) -> list:
            if field not in row.keys() or not row[field]:
                return []
            try:
                parsed = json.loads(row[field])
                return parsed if isinstance(parsed, list) else []
            except json.JSONDecodeError as e:
                _log.error(
                    "[FakeAi Memory] user_impression.%s JSON 损坏 user_id=%s: %s",
                    field,
                    uid,
                    e,
                )
                return []

        score = self._read_favorability_score(row)
        return {
            "user_id": uid,
            "username": row["username"] if "username" in row.keys() else "",
            "gender": row["gender"] if "gender" in row.keys() else "",
            "impression": row["impression"],
            "favorability_score": score,
            "favorability": score,
            "pending_favorability": 0,
            "relation_tag": row["relation_tag"]
            if "relation_tag" in row.keys() and row["relation_tag"]
            else "",
            "events": _parse_json_list("events"),
            "new_knowledge": _parse_json_list("new_knowledge"),
            "important_events": _parse_json_list("important_events"),
            "interaction_count": row["interaction_count"],
            "last_updated": row["last_updated"],
        }

    async def get_user_impression(self, user_id: int) -> Optional[str]:
        """获取用户印象（简单版本，兼容旧代码）"""
        data = await self.get_user_impression_full(user_id)
        return data.get("impression") if data else None

    async def get_all_user_impressions(self, limit: int = 20) -> List[Dict]:
        """获取所有活跃用户的完整印象（全局）"""
        cursor = await self._db.execute(
            """SELECT * FROM user_impression 
               ORDER BY interaction_count DESC, last_updated DESC
               LIMIT ?""",
            (limit,),
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            score = self._read_favorability_score(row)
            result.append(
                {
                    "user_id": row["user_id"],
                    "username": row["username"] if "username" in row.keys() else "",
                    "gender": row["gender"] if "gender" in row.keys() else "",
                    "impression": row["impression"],
                    "favorability_score": score,
                    "favorability": score,
                    "pending_favorability": 0,
                    "relation_tag": self._read_relation_tag(row),
                    "events": json.loads(row["events"])
                    if "events" in row.keys() and row["events"]
                    else [],
                    "new_knowledge": json.loads(row["new_knowledge"])
                    if "new_knowledge" in row.keys() and row["new_knowledge"]
                    else [],
                    "important_events": json.loads(row["important_events"])
                    if "important_events" in row.keys() and row["important_events"]
                    else [],
                    "interaction_count": row["interaction_count"],
                }
            )
        return result

    async def get_users_impressions_by_ids(
        self, user_ids: List[int], limit: int = 10
    ) -> List[Dict]:
        """根据用户ID列表获取印象（用于获取当前群聊中活跃用户的印象）"""
        if not user_ids:
            return []

        uid_keys = [str(int(u)) for u in user_ids]
        placeholders = ",".join("?" * len(uid_keys))
        cursor = await self._db.execute(
            f"""SELECT * FROM user_impression 
               WHERE CAST(user_id AS TEXT) IN ({placeholders})
               ORDER BY interaction_count DESC, last_updated DESC
               LIMIT ?""",
            (*uid_keys, limit),
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            score = self._read_favorability_score(row)
            result.append(
                {
                    "user_id": row["user_id"],
                    "username": row["username"] if "username" in row.keys() else "",
                    "gender": row["gender"] if "gender" in row.keys() else "",
                    "impression": row["impression"],
                    "favorability_score": score,
                    "favorability": score,
                    "pending_favorability": 0,
                    "relation_tag": self._read_relation_tag(row),
                    "events": json.loads(row["events"])
                    if "events" in row.keys() and row["events"]
                    else [],
                    "new_knowledge": json.loads(row["new_knowledge"])
                    if "new_knowledge" in row.keys() and row["new_knowledge"]
                    else [],
                    "important_events": json.loads(row["important_events"])
                    if "important_events" in row.keys() and row["important_events"]
                    else [],
                    "interaction_count": row["interaction_count"],
                }
            )
        return result

    async def get_user_impressions_text(
        self, group_id: int = None, user_ids: List[int] = None, max_length: int = 800
    ) -> str:
        """获取格式化的用户印象文本（详细版本）

        Args:
            group_id: 群ID（已弃用，保留参数兼容性）
            user_ids: 指定用户ID列表，如果提供则只获取这些用户的印象
            max_length: 最大文本长度
        """
        await self._ensure_db()
        if user_ids:
            impressions = await self.get_users_impressions_by_ids(user_ids, limit=8)
        else:
            impressions = await self.get_all_user_impressions(limit=8)

        if not impressions:
            return "（暂无用户印象）"

        from .favorability import format_favorability

        lines = []
        for imp in impressions:
            user_id = imp["user_id"]
            username = imp.get("username", "")
            gender = imp.get("gender", "")
            impression = imp.get("impression", "")
            favorability = float(imp.get("favorability", 0))
            relation_tag = (imp.get("relation_tag") or "").strip()
            events = imp.get("events", [])
            new_knowledge = imp.get("new_knowledge", [])
            important_events = imp.get("important_events", [])

            # 构建用户印象文本（显示用户名和ID）
            display_name = f"{username}({user_id})" if username else str(user_id)
            user_lines = [f"【{display_name}】"]
            if gender:
                user_lines.append(f"  性别: {gender}")
            if impression:
                user_lines.append(f"  印象: {impression}")
            user_lines.append(f"  好感: {format_favorability(favorability)}")
            if relation_tag:
                user_lines.append(f"  关系: {relation_tag}")
            if events:
                user_lines.append(f"  事件: {', '.join(events[-5:])}")
            if new_knowledge:
                user_lines.append(f"  新知识: {', '.join(new_knowledge[-5:])}")
            if important_events:
                user_lines.append(f"  重要事件: {', '.join(important_events[-3:])}")

            lines.append("\n".join(user_lines))

        text = "\n\n".join(lines)
        if len(text) > max_length:
            text = text[:max_length] + "..."

        return text if text else "（暂无用户印象）"

    async def update_user_favorability(self, user_id: int, change: int):
        """单独更新用户好感度（立即应用）"""
        from .favorability import apply_favor_change

        existing = await self.get_user_impression_full(user_id)
        if existing:
            current = float(existing.get("favorability_score", existing.get("favorability", 0)))
            new_favorability = apply_favor_change(current, change, user_id)
            await self._db.execute(
                """UPDATE user_impression SET favorability = ?, favorability_score = ?
                   WHERE CAST(user_id AS TEXT) = ?""",
                (int(round(new_favorability)), new_favorability, str(int(user_id))),
            )
            await self._db.commit()
            return new_favorability
        return None

    # ========== 重要事件相关 ==========

    async def save_important_event(
        self,
        group_id: int,
        event_type: str,
        description: str,
        related_users: List[int],
        importance: int = 1,
    ):
        """保存重要事件"""
        await self._db.execute(
            """INSERT INTO important_event 
               (group_id, event_type, description, related_users, created_at, importance)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                group_id,
                event_type,
                description,
                json.dumps(related_users),
                int(time.time()),
                importance,
            ),
        )
        await self._db.commit()

    async def get_recent_events(self, group_id: int, limit: int = 5) -> List[Dict]:
        """获取最近的重要事件"""
        cursor = await self._db.execute(
            """SELECT * FROM important_event 
               WHERE group_id = ?
               ORDER BY importance DESC, created_at DESC
               LIMIT ?""",
            (group_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "event_type": row["event_type"],
                "description": row["description"],
                "related_users": json.loads(row["related_users"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    # ========== 知识库相关 ==========

    async def save_knowledge(
        self,
        keyword: str,
        content: str,
        source_user_id: int = None,
        source_username: str = "",
    ) -> bool:
        """保存或更新知识到知识库（以 keyword 为唯一标识）

        如果 keyword 已存在，会融合新旧内容，使知识更完善
        如果 keyword 不存在，创建新记录

        Args:
            keyword: 知识关键词（唯一标识）
            content: 知识内容/说明
            source_user_id: 来源用户ID
            source_username: 来源用户名

        Returns:
            是否有变更（新增或更新）
        """
        # 检查是否已存在相同 keyword 的知识
        cursor = await self._db.execute(
            """SELECT id, content, source_username FROM knowledge_base 
               WHERE keyword = ?""",
            (keyword,),
        )
        existing = await cursor.fetchone()

        if existing:
            old_content = existing["content"] or ""
            new_content = content or ""

            # 如果新内容为空或与旧内容相同，不更新
            if not new_content or new_content == old_content:
                return False

            # 如果新内容已包含在旧内容中，不更新
            if new_content in old_content:
                return False

            # 融合内容：将新信息追加到旧内容
            # 用分号分隔不同来源的描述
            if old_content:
                merged_content = f"{old_content}；{new_content}"
            else:
                merged_content = new_content

            # 更新来源信息（追加新来源）
            old_sources = existing["source_username"] or ""
            if source_username and source_username not in old_sources:
                new_sources = (
                    f"{old_sources}, {source_username}"
                    if old_sources
                    else source_username
                )
            else:
                new_sources = old_sources

            await self._db.execute(
                """UPDATE knowledge_base 
                   SET content = ?, source_username = ?, source_user_id = ?
                   WHERE id = ?""",
                (merged_content, new_sources, source_user_id, existing["id"]),
            )
            await self._db.commit()
            _log.info(
                f"[FakeAi Knowledge] 更新知识: {keyword}: {old_content} → {merged_content}"
            )
            return True
        else:
            # 新增知识
            await self._db.execute(
                """INSERT INTO knowledge_base 
                   (keyword, content, source_user_id, source_username, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (keyword, content, source_user_id, source_username, int(time.time())),
            )
            await self._db.commit()
            _log.info(f"[FakeAi Knowledge] 新增知识: {keyword}: {content}")
            return True

    async def save_knowledge_batch(
        self,
        knowledge_list: List[str],
        source_user_id: int = None,
        source_username: str = "",
    ) -> int:
        """批量保存知识

        Args:
            knowledge_list: 知识列表，格式为 "关键词:说明" 或 "关键词"
            source_user_id: 来源用户ID
            source_username: 来源用户名

        Returns:
            成功保存的数量
        """
        saved_count = 0
        for item in knowledge_list:
            if not item or not isinstance(item, str):
                continue

            # 解析 "关键词:说明" 或 "关键词：说明" 格式
            if ":" in item:
                parts = item.split(":", 1)
                keyword = parts[0].strip()
                content = parts[1].strip() if len(parts) > 1 else ""
            elif "：" in item:
                parts = item.split("：", 1)
                keyword = parts[0].strip()
                content = parts[1].strip() if len(parts) > 1 else ""
            else:
                keyword = item.strip()
                content = ""

            if keyword:
                if await self.save_knowledge(
                    keyword, content, source_user_id, source_username
                ):
                    saved_count += 1

        return saved_count

    async def search_knowledge(
        self,
        query: str,
        limit: int = 5,
    ) -> List[Dict]:
        """根据查询文本搜索相关知识

        使用关键词匹配搜索知识库，返回相关的知识条目

        Args:
            query: 查询文本（会提取其中的关键词进行匹配）
            limit: 返回结果数量上限

        Returns:
            匹配的知识列表
        """
        await self._ensure_db()
        if not query:
            return []

        # 提取查询中可能的关键词（2字以上的词）
        # 简单分词：按标点和空格分割，过滤短词
        import re as regex

        words = regex.split(r"[,，.。!！?？\s:：;；、\n]+", query)
        keywords = [w.strip() for w in words if w.strip() and len(w.strip()) >= 2]

        if not keywords:
            return []

        results = []
        seen_ids = set()

        # 对每个关键词进行模糊匹配
        for kw in keywords:
            cursor = await self._db.execute(
                """SELECT * FROM knowledge_base 
                   WHERE keyword LIKE ? OR content LIKE ?
                   ORDER BY hit_count DESC, created_at DESC
                   LIMIT ?""",
                (f"%{kw}%", f"%{kw}%", limit),
            )
            rows = await cursor.fetchall()
            for row in rows:
                if row["id"] not in seen_ids:
                    seen_ids.add(row["id"])
                    results.append(
                        {
                            "id": row["id"],
                            "keyword": row["keyword"],
                            "content": row["content"],
                            "source_user_id": row["source_user_id"],
                            "source_username": row["source_username"],
                            "hit_count": row["hit_count"],
                        }
                    )

        # 按命中次数排序，取前 limit 条
        results.sort(key=lambda x: x["hit_count"], reverse=True)
        return results[:limit]

    async def update_knowledge_hit(self, knowledge_ids: List[int]):
        """更新知识条目的命中次数

        Args:
            knowledge_ids: 被命中的知识ID列表
        """
        await self._ensure_db()
        if not knowledge_ids:
            return

        now = int(time.time())
        for kid in knowledge_ids:
            await self._db.execute(
                """UPDATE knowledge_base 
                   SET hit_count = hit_count + 1, last_hit_at = ?
                   WHERE id = ?""",
                (now, kid),
            )
        await self._db.commit()

    async def get_knowledge_text(self, query: str, max_length: int = 300) -> str:
        """获取格式化的相关知识文本

        Args:
            query: 查询文本
            max_length: 最大文本长度

        Returns:
            格式化的知识文本
        """
        await self._ensure_db()
        knowledge_items = await self.search_knowledge(query, limit=5)

        if not knowledge_items:
            return ""

        # 更新命中次数
        await self.update_knowledge_hit([k["id"] for k in knowledge_items])

        lines = []
        for item in knowledge_items:
            keyword = item["keyword"]
            content = item["content"]
            if content:
                lines.append(f"- {keyword}: {content}")
            else:
                lines.append(f"- {keyword}")

        text = "\n".join(lines)
        if len(text) > max_length:
            text = text[:max_length] + "..."

        return text

    async def get_all_knowledge_count(self) -> int:
        """获取知识库中的知识总数"""
        cursor = await self._db.execute("SELECT COUNT(*) as count FROM knowledge_base")
        row = await cursor.fetchone()
        return row["count"] if row else 0

    async def get_favorability_ranking(self, top_n: int = 10) -> Dict[str, List[Dict]]:
        """获取好感度排行榜

        Args:
            top_n: 获取前N名和倒数N名

        Returns:
            {"top": [...], "bottom": [...]}
        """
        # 好感最高（按 v2 分数排序）
        cursor = await self._db.execute(
            """SELECT user_id, username, favorability, favorability_score
               FROM user_impression
               ORDER BY COALESCE(favorability_score, favorability) DESC
               LIMIT ?""",
            (top_n,),
        )
        top_rows = await cursor.fetchall()
        top_list = []
        for row in top_rows:
            score = self._read_favorability_score(row)
            top_list.append(
                {
                    "user_id": row["user_id"],
                    "username": row["username"] or "",
                    "favorability": score,
                }
            )

        # 负好感榜：仅 score < 0
        cursor = await self._db.execute(
            """SELECT user_id, username, favorability, favorability_score
               FROM user_impression
               WHERE COALESCE(favorability_score, favorability) < 0
               ORDER BY COALESCE(favorability_score, favorability) ASC
               LIMIT ?""",
            (top_n,),
        )
        bottom_rows = await cursor.fetchall()
        bottom_list = []
        for row in bottom_rows:
            score = self._read_favorability_score(row)
            bottom_list.append(
                {
                    "user_id": row["user_id"],
                    "username": row["username"] or "",
                    "favorability": score,
                }
            )

        return {"top": top_list, "bottom": bottom_list}

    async def cleanup_duplicate_knowledge(self) -> int:
        """清理重复的知识条目（合并相同 keyword 的知识）

        将相同 keyword 的多条记录合并为一条，内容用分号连接

        Returns:
            清理的重复记录数量
        """
        # 找出所有重复的 keyword
        cursor = await self._db.execute(
            """SELECT keyword, COUNT(*) as cnt FROM knowledge_base 
               GROUP BY keyword HAVING cnt > 1"""
        )
        duplicates = await cursor.fetchall()

        total_cleaned = 0

        for dup in duplicates:
            keyword = dup["keyword"]

            # 获取该 keyword 的所有记录
            cursor = await self._db.execute(
                """SELECT id, content, source_username, hit_count, created_at 
                   FROM knowledge_base WHERE keyword = ?
                   ORDER BY hit_count DESC, created_at ASC""",
                (keyword,),
            )
            records = await cursor.fetchall()

            if len(records) <= 1:
                continue

            # 合并所有内容（去重）
            contents = []
            sources = []
            total_hits = 0
            earliest_time = records[0]["created_at"]

            for r in records:
                content = r["content"] or ""
                if content and content not in contents:
                    # 检查是否已包含在其他内容中
                    is_subset = False
                    for existing in contents:
                        if content in existing:
                            is_subset = True
                            break
                    if not is_subset:
                        # 移除被新内容包含的旧内容
                        contents = [c for c in contents if c not in content]
                        contents.append(content)

                source = r["source_username"] or ""
                if source and source not in sources:
                    sources.append(source)

                total_hits += r["hit_count"] or 0
                if r["created_at"] < earliest_time:
                    earliest_time = r["created_at"]

            merged_content = "；".join(contents) if contents else ""
            merged_sources = ", ".join(sources) if sources else ""

            # 保留第一条记录，更新内容
            keep_id = records[0]["id"]
            await self._db.execute(
                """UPDATE knowledge_base 
                   SET content = ?, source_username = ?, hit_count = ?, created_at = ?
                   WHERE id = ?""",
                (merged_content, merged_sources, total_hits, earliest_time, keep_id),
            )

            # 删除其他重复记录
            delete_ids = [r["id"] for r in records[1:]]
            if delete_ids:
                placeholders = ",".join("?" * len(delete_ids))
                await self._db.execute(
                    f"DELETE FROM knowledge_base WHERE id IN ({placeholders})",
                    delete_ids,
                )
                total_cleaned += len(delete_ids)

        await self._db.commit()
        if total_cleaned > 0:
            _log.info(f"[FakeAi Knowledge] 清理了 {total_cleaned} 条重复知识")

        return total_cleaned


# 全局实例
memory_manager = MemoryManager()


# ========== 总结生成器 ==========

SUMMARY_PROMPT = """你是一个群聊记录分析助手。请分析以下群聊记录，生成一个简短的总结。

要求：
1. 总结要简短，不超过100字
2. 提取3-5个关键话题词
3. 注意记录有趣或重要的事件
4. 用第三人称描述

群聊记录：
{chat_history}

请用以下JSON格式回复：
{{"summary": "简短总结", "key_topics": ["话题1", "话题2"], "important_events": ["事件1"]}}
"""


async def generate_summary_from_messages(
    messages: List[Dict], group_id: int = None
) -> Optional[Dict]:
    """
    调用AI生成消息总结

    Args:
        messages: 消息列表，每条消息包含 name, user_id, content

    Returns:
        {summary, key_topics, important_events} 或 None
    """
    if not messages or len(messages) < 5:
        return None

    # 格式化消息
    chat_lines = []
    for msg in messages:
        name = msg.get("name", "未知")
        content = msg.get("content", "")
        if content:
            chat_lines.append(f"{name}: {content}")

    chat_history = "\n".join(chat_lines[-50:])  # 最多50条消息

    if len(chat_history) < 100:  # 内容太少不值得总结
        return None

    try:
        from common.utils.AiUtil import AiUtil

        prompt = SUMMARY_PROMPT.format(chat_history=chat_history)
        response = await AiUtil.search_deepseek("请分析并总结", prompt)

        if group_id is not None:
            try:
                from common.utils.AiStatsRecorder import SOURCE_SUMMARY, record_from_response

                record_from_response(str(group_id), None, SOURCE_SUMMARY, response)
            except Exception as rec_err:
                _log.debug(f"[FakeAi Memory] 总结统计记录失败: {rec_err}")

        if isinstance(response, dict):
            content = response.get("content", "")
        else:
            content = response or ""

        result = _parse_llm_json_object(content, "summary")
        if result:
            return {
                "summary": result.get("summary", ""),
                "key_topics": result.get("key_topics", []),
                "important_events": result.get("important_events", []),
            }
    except Exception as e:
        _log.error(f"[FakeAi Memory] 生成总结失败: {e}")

    return None


# ========== 用户印象生成器 ==========

IMPRESSION_PROMPT = """你是一个群聊分析助手。请根据以下用户在群聊中的发言，分析这个人的详细特点。

要求：
1. 分析用户的性别（如果能判断）
2. 用关键词描述印象特点（用逗号分隔，如：音乐爱好者,热情支持,互动积极）
3. 根据对话态度判断好感度变化（-3到+3的整数，正面互动为正，负面为负；普通互动为0或±1）
4. 总结最近的互动事件（简短描述）
5. 提取用户分享的新知识或信息
6. 标记重要事件（如果有）
7. relation_tag（可选）：若互动密切、关系明确，用2-4字描述，如闺蜜、妻子、丈夫、室友、同事、网友等；普通群友留空字符串

用户名：{user_name}
用户发言：
{user_messages}

请用以下JSON格式回复：
{{
    "gender": "男/女/未知",
    "impression": "关键词1,关键词2,关键词3",
    "favorability_change": 0,
    "relation_tag": "",
    "events": ["事件1", "事件2"],
    "new_knowledge": ["知识1:简短说明", "知识2:简短说明"],
    "important_events": ["重要事件描述"]
}}

注意：
- impression 用逗号分隔的关键词，不超过5个
- events 是最近的互动行为，不超过3个
- new_knowledge 是用户提到的你不知道的知识点，格式为"名词:简短说明"
- important_events 只记录真正重要的事件
- relation_tag 不要对每个人都填，只有关系确实特殊时才填
- 如果某项无法判断，可以留空字符串或空数组
"""


async def generate_user_impression(
    user_name: str,
    messages: List[str],
    user_id: int = None,
    group_id: int = None,
) -> Optional[Dict]:
    """
    调用AI生成用户详细印象

    Args:
        user_name: 用户昵称
        messages: 用户的消息列表
        user_id: 用户ID（用于识别VIP用户）

    Returns:
        包含详细印象信息的字典，或 None
    """
    if not messages or len(messages) < 3:
        return None

    # 取最近的消息
    recent_messages = messages[-30:]
    user_messages = "\n".join(f"- {msg}" for msg in recent_messages if msg)

    if len(user_messages) < 50:
        return None

    # 构建带有用户ID信息的用户名
    user_info = f"{user_name}(ID:{user_id})" if user_id else user_name

    try:
        from common.utils.AiUtil import AiUtil

        prompt = IMPRESSION_PROMPT.format(
            user_name=user_info, user_messages=user_messages
        )
        response = await AiUtil.search_deepseek("请分析用户特点", prompt)

        if group_id is not None:
            try:
                from common.utils.AiStatsRecorder import SOURCE_IMPRESSION, record_from_response

                record_from_response(
                    str(group_id),
                    str(user_id) if user_id else None,
                    SOURCE_IMPRESSION,
                    response,
                )
            except Exception as rec_err:
                _log.debug(f"[FakeAi Memory] 印象统计记录失败: {rec_err}")

        if isinstance(response, dict):
            content = response.get("content", "")
        else:
            content = response or ""

        result = _parse_llm_json_object(content, "gender")
        if result:
            return {
                "gender": result.get("gender", ""),
                "impression": result.get("impression", ""),
                "favorability_change": max(
                    -3, min(3, int(result.get("favorability_change", 0)))
                ),
                "relation_tag": result.get("relation_tag", ""),
                "events": result.get("events", []),
                "new_knowledge": result.get("new_knowledge", []),
                "important_events": result.get("important_events", []),
            }

    except Exception as e:
        _log.error(f"[FakeAi Memory] 生成用户印象失败: {e}")

    return None
