"""
FakeAi 长期记忆模块
使用 SQLite 存储群聊总结作为 AI 的长期记忆
"""

import json
import time
import logging
from pathlib import Path
from typing import List, Optional, Dict
from datetime import datetime, timedelta
import aiosqlite

log = logging.getLogger(__name__)

# 数据库路径
DATABASE_PATH = Path("data") / "FakeAi" / "memory.db"
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)


class MemoryManager:
    """长期记忆管理器"""
    
    _instance: Optional["MemoryManager"] = None
    _db: Optional[aiosqlite.Connection] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    async def init_db(self):
        """初始化数据库"""
        if self._db is None:
            self._db = await aiosqlite.connect(str(DATABASE_PATH))
            self._db.row_factory = aiosqlite.Row
            await self._create_tables()
            log.info("[FakeAi Memory] 数据库初始化完成")
    
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
                nickname TEXT DEFAULT '',
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
            )
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
            (group_id, since_time, limit)
        )
        rows = await cursor.fetchall()
        
        result = []
        for row in rows:
            result.append({
                "id": row["id"],
                "summary": row["summary"],
                "key_topics": json.loads(row["key_topics"]),
                "participant_ids": json.loads(row["participant_ids"]),
                "message_count": row["message_count"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "created_at": row["created_at"],
            })
        return result
    
    async def get_long_term_memory(self, group_id: int, max_length: int = 500) -> str:
        """获取格式化的长期记忆文本"""
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
    
    # 好感度更新阈值：累计多少次印象更新后才应用好感度变化
    FAVORABILITY_UPDATE_THRESHOLD = 5
    
    # 特殊用户列表（好感度只增不减，且有巨额增幅）
    VIP_USERS = [635773721, 273421673]
    VIP_FAVORABILITY_MULTIPLIER = 3  # VIP用户好感度增幅倍数
    
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
                - nickname: 用户对蓝晴的称呼
                - new_knowledge: 新学到的知识列表
                - important_events: 重要事件列表
            username: 用户名/群昵称
        """
        now = int(time.time())
        
        # 将新知识保存到知识库（永久存储）
        new_knowledge = impression_data.get("new_knowledge", [])
        if new_knowledge:
            saved_count = await self.save_knowledge_batch(
                new_knowledge,
                source_user_id=user_id,
                source_username=username
            )
            if saved_count > 0:
                log.info(f"[FakeAi] 从用户 {user_id} 学到 {saved_count} 条新知识")
        
        # 获取现有数据
        existing = await self.get_user_impression_full(user_id)
        
        if existing:
            # 更新现有记录
            # 如果传入了新的用户名，则更新；否则保留原来的
            final_username = username if username else existing.get("username", "")
            gender = impression_data.get("gender") or existing.get("gender", "")
            impression = impression_data.get("impression") or existing.get("impression", "")
            nickname = impression_data.get("nickname") or existing.get("nickname", "")
            
            # 好感度变化累积到 pending_favorability
            current_favorability = existing.get("favorability", 0)
            pending = existing.get("pending_favorability", 0)
            favorability_change = impression_data.get("favorability_change", 0)
            
            # VIP用户特殊处理：好感度只增不减，且有巨额增幅
            if user_id in self.VIP_USERS:
                if favorability_change < 0:
                    # VIP用户好感度不降低，负数变为0
                    favorability_change = 0
                    log.info(f"[FakeAi] VIP用户 {user_id} 好感度保护：阻止负向变化")
                elif favorability_change > 0:
                    # VIP用户好感度正向增幅
                    favorability_change = favorability_change * self.VIP_FAVORABILITY_MULTIPLIER
                    log.info(f"[FakeAi] VIP用户 {user_id} 好感度巨额增幅：+{favorability_change}")
            
            new_pending = pending + favorability_change
            
            # 检查是否达到更新阈值
            new_interaction_count = existing.get("interaction_count", 0) + 1
            if new_interaction_count % self.FAVORABILITY_UPDATE_THRESHOLD == 0:
                # 达到阈值，应用累积的好感度变化（不封顶）
                new_favorability = current_favorability + new_pending
                new_pending = 0  # 重置累积值
                log.info(f"[FakeAi] 用户 {user_id} 好感度更新: {current_favorability} -> {new_favorability}")
            else:
                new_favorability = current_favorability
            
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
            
            await self._db.execute(
                """UPDATE user_impression SET
                   username = ?,
                   gender = ?,
                   impression = ?,
                   favorability = ?,
                   pending_favorability = ?,
                   events = ?,
                   nickname = ?,
                   new_knowledge = ?,
                   important_events = ?,
                   last_updated = ?,
                   interaction_count = ?
                   WHERE user_id = ?""",
                (
                    final_username,
                    gender,
                    impression,
                    new_favorability,
                    new_pending,
                    json.dumps(merged_events, ensure_ascii=False),
                    nickname,
                    json.dumps(merged_knowledge, ensure_ascii=False),
                    json.dumps(merged_important, ensure_ascii=False),
                    now,
                    new_interaction_count,
                    user_id,
                )
            )
        else:
            # 插入新记录
            initial_favorability_change = impression_data.get("favorability_change", 0)
            initial_favorability = 0
            
            # VIP用户特殊处理：初始好感度直接给高值，且变化只增不减
            if user_id in self.VIP_USERS:
                initial_favorability = 100  # VIP用户初始好感度100
                if initial_favorability_change < 0:
                    initial_favorability_change = 0
                elif initial_favorability_change > 0:
                    initial_favorability_change = initial_favorability_change * self.VIP_FAVORABILITY_MULTIPLIER
                log.info(f"[FakeAi] VIP用户 {user_id} 首次记录：初始好感度100，pending={initial_favorability_change}")
            
            await self._db.execute(
                """INSERT INTO user_impression 
                   (user_id, username, gender, impression, favorability, pending_favorability, events, nickname, 
                    new_knowledge, important_events, last_updated, interaction_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (
                    user_id,
                    username,
                    impression_data.get("gender", ""),
                    impression_data.get("impression", ""),
                    initial_favorability,  # 初始好感度
                    initial_favorability_change,  # 累积到 pending
                    json.dumps(impression_data.get("events", []), ensure_ascii=False),
                    impression_data.get("nickname", ""),
                    json.dumps(impression_data.get("new_knowledge", []), ensure_ascii=False),
                    json.dumps(impression_data.get("important_events", []), ensure_ascii=False),
                    now,
                )
            )
        
        await self._db.commit()
    
    async def get_user_impression_full(self, user_id: int) -> Optional[Dict]:
        """获取用户完整印象数据（全局）"""
        cursor = await self._db.execute(
            """SELECT * FROM user_impression WHERE user_id = ?""",
            (user_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        
        return {
            "user_id": row["user_id"],
            "username": row["username"] if "username" in row.keys() else "",
            "gender": row["gender"] if "gender" in row.keys() else "",
            "impression": row["impression"],
            "favorability": row["favorability"] if "favorability" in row.keys() else 0,
            "pending_favorability": row["pending_favorability"] if "pending_favorability" in row.keys() else 0,
            "events": json.loads(row["events"]) if "events" in row.keys() and row["events"] else [],
            "nickname": row["nickname"] if "nickname" in row.keys() else "",
            "new_knowledge": json.loads(row["new_knowledge"]) if "new_knowledge" in row.keys() and row["new_knowledge"] else [],
            "important_events": json.loads(row["important_events"]) if "important_events" in row.keys() and row["important_events"] else [],
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
            (limit,)
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            result.append({
                "user_id": row["user_id"],
                "username": row["username"] if "username" in row.keys() else "",
                "gender": row["gender"] if "gender" in row.keys() else "",
                "impression": row["impression"],
                "favorability": row["favorability"] if "favorability" in row.keys() else 0,
                "pending_favorability": row["pending_favorability"] if "pending_favorability" in row.keys() else 0,
                "events": json.loads(row["events"]) if "events" in row.keys() and row["events"] else [],
                "nickname": row["nickname"] if "nickname" in row.keys() else "",
                "new_knowledge": json.loads(row["new_knowledge"]) if "new_knowledge" in row.keys() and row["new_knowledge"] else [],
                "important_events": json.loads(row["important_events"]) if "important_events" in row.keys() and row["important_events"] else [],
                "interaction_count": row["interaction_count"],
            })
        return result
    
    async def get_users_impressions_by_ids(self, user_ids: List[int], limit: int = 10) -> List[Dict]:
        """根据用户ID列表获取印象（用于获取当前群聊中活跃用户的印象）"""
        if not user_ids:
            return []
        
        placeholders = ",".join("?" * len(user_ids))
        cursor = await self._db.execute(
            f"""SELECT * FROM user_impression 
               WHERE user_id IN ({placeholders})
               ORDER BY interaction_count DESC, last_updated DESC
               LIMIT ?""",
            (*user_ids, limit)
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            result.append({
                "user_id": row["user_id"],
                "username": row["username"] if "username" in row.keys() else "",
                "gender": row["gender"] if "gender" in row.keys() else "",
                "impression": row["impression"],
                "favorability": row["favorability"] if "favorability" in row.keys() else 0,
                "pending_favorability": row["pending_favorability"] if "pending_favorability" in row.keys() else 0,
                "events": json.loads(row["events"]) if "events" in row.keys() and row["events"] else [],
                "nickname": row["nickname"] if "nickname" in row.keys() else "",
                "new_knowledge": json.loads(row["new_knowledge"]) if "new_knowledge" in row.keys() and row["new_knowledge"] else [],
                "important_events": json.loads(row["important_events"]) if "important_events" in row.keys() and row["important_events"] else [],
                "interaction_count": row["interaction_count"],
            })
        return result
    
    async def get_user_impressions_text(self, group_id: int = None, user_ids: List[int] = None, max_length: int = 800) -> str:
        """获取格式化的用户印象文本（详细版本）
        
        Args:
            group_id: 群ID（已弃用，保留参数兼容性）
            user_ids: 指定用户ID列表，如果提供则只获取这些用户的印象
            max_length: 最大文本长度
        """
        if user_ids:
            impressions = await self.get_users_impressions_by_ids(user_ids, limit=8)
        else:
            impressions = await self.get_all_user_impressions(limit=8)
        
        if not impressions:
            return "（暂无用户印象）"
        
        lines = []
        for imp in impressions:
            user_id = imp["user_id"]
            username = imp.get("username", "")
            gender = imp.get("gender", "")
            impression = imp.get("impression", "")
            favorability = imp.get("favorability", 0)
            events = imp.get("events", [])
            nickname = imp.get("nickname", "")
            new_knowledge = imp.get("new_knowledge", [])
            important_events = imp.get("important_events", [])
            
            # 构建用户印象文本（显示用户名和ID）
            display_name = f"{username}({user_id})" if username else str(user_id)
            user_lines = [f"【{display_name}】"]
            if gender:
                user_lines.append(f"  性别: {gender}")
            if impression:
                user_lines.append(f"  印象: {impression}")
            user_lines.append(f"  好感度: {favorability}")
            if events:
                user_lines.append(f"  事件: {', '.join(events[-5:])}")
            if nickname:
                user_lines.append(f"  称呼: {nickname}")
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
        """单独更新用户好感度（立即应用，不经过累积）
        
        Args:
            user_id: 用户ID
            change: 好感度变化值（正负整数，无上下限）
        """
        existing = await self.get_user_impression_full(user_id)
        if existing:
            current = existing.get("favorability", 0)
            new_favorability = current + change  # 不封顶
            await self._db.execute(
                "UPDATE user_impression SET favorability = ? WHERE user_id = ?",
                (new_favorability, user_id)
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
            )
        )
        await self._db.commit()
    
    async def get_recent_events(self, group_id: int, limit: int = 5) -> List[Dict]:
        """获取最近的重要事件"""
        cursor = await self._db.execute(
            """SELECT * FROM important_event 
               WHERE group_id = ?
               ORDER BY importance DESC, created_at DESC
               LIMIT ?""",
            (group_id, limit)
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
            (keyword,)
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
                new_sources = f"{old_sources}, {source_username}" if old_sources else source_username
            else:
                new_sources = old_sources
            
            await self._db.execute(
                """UPDATE knowledge_base 
                   SET content = ?, source_username = ?, source_user_id = ?
                   WHERE id = ?""",
                (merged_content, new_sources, source_user_id, existing["id"])
            )
            await self._db.commit()
            log.info(f"[FakeAi Knowledge] 更新知识: {keyword}: {old_content} → {merged_content}")
            return True
        else:
            # 新增知识
            await self._db.execute(
                """INSERT INTO knowledge_base 
                   (keyword, content, source_user_id, source_username, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (keyword, content, source_user_id, source_username, int(time.time()))
            )
            await self._db.commit()
            log.info(f"[FakeAi Knowledge] 新增知识: {keyword}: {content}")
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
                if await self.save_knowledge(keyword, content, source_user_id, source_username):
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
        if not query:
            return []
        
        # 提取查询中可能的关键词（2字以上的词）
        # 简单分词：按标点和空格分割，过滤短词
        import re as regex
        words = regex.split(r'[,，.。!！?？\s:：;；、\n]+', query)
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
                (f"%{kw}%", f"%{kw}%", limit)
            )
            rows = await cursor.fetchall()
            for row in rows:
                if row["id"] not in seen_ids:
                    seen_ids.add(row["id"])
                    results.append({
                        "id": row["id"],
                        "keyword": row["keyword"],
                        "content": row["content"],
                        "source_user_id": row["source_user_id"],
                        "source_username": row["source_username"],
                        "hit_count": row["hit_count"],
                    })
        
        # 按命中次数排序，取前 limit 条
        results.sort(key=lambda x: x["hit_count"], reverse=True)
        return results[:limit]
    
    async def update_knowledge_hit(self, knowledge_ids: List[int]):
        """更新知识条目的命中次数
        
        Args:
            knowledge_ids: 被命中的知识ID列表
        """
        if not knowledge_ids:
            return
        
        now = int(time.time())
        for kid in knowledge_ids:
            await self._db.execute(
                """UPDATE knowledge_base 
                   SET hit_count = hit_count + 1, last_hit_at = ?
                   WHERE id = ?""",
                (now, kid)
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
        # 获取好感度最高的用户
        cursor = await self._db.execute(
            """SELECT user_id, username, favorability 
               FROM user_impression 
               ORDER BY favorability DESC 
               LIMIT ?""",
            (top_n,)
        )
        top_rows = await cursor.fetchall()
        top_list = [
            {
                "user_id": row["user_id"],
                "username": row["username"] or "",
                "favorability": row["favorability"],
            }
            for row in top_rows
        ]
        
        # 获取好感度最低的用户
        cursor = await self._db.execute(
            """SELECT user_id, username, favorability 
               FROM user_impression 
               ORDER BY favorability ASC 
               LIMIT ?""",
            (top_n,)
        )
        bottom_rows = await cursor.fetchall()
        bottom_list = [
            {
                "user_id": row["user_id"],
                "username": row["username"] or "",
                "favorability": row["favorability"],
            }
            for row in bottom_rows
        ]
        
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
                (keyword,)
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
                (merged_content, merged_sources, total_hits, earliest_time, keep_id)
            )
            
            # 删除其他重复记录
            delete_ids = [r["id"] for r in records[1:]]
            if delete_ids:
                placeholders = ",".join("?" * len(delete_ids))
                await self._db.execute(
                    f"DELETE FROM knowledge_base WHERE id IN ({placeholders})",
                    delete_ids
                )
                total_cleaned += len(delete_ids)
        
        await self._db.commit()
        if total_cleaned > 0:
            log.info(f"[FakeAi Knowledge] 清理了 {total_cleaned} 条重复知识")
        
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


async def generate_summary_from_messages(messages: List[Dict]) -> Optional[Dict]:
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
        
        if isinstance(response, dict):
            content = response.get("content", "")
        else:
            content = response or ""
        
        # 解析JSON
        import re
        json_match = re.search(r'\{[^{}]*"summary"[^{}]*\}', content, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            return {
                "summary": result.get("summary", ""),
                "key_topics": result.get("key_topics", []),
                "important_events": result.get("important_events", []),
            }
    except Exception as e:
        log.error(f"[FakeAi Memory] 生成总结失败: {e}")
    
    return None


# ========== 用户印象生成器 ==========

IMPRESSION_PROMPT = """你是一个群聊分析助手。请根据以下用户在群聊中的发言，分析这个人的详细特点。

要求：
1. 分析用户的性别（如果能判断）
2. 用关键词描述印象特点（用逗号分隔，如：音乐爱好者,热情支持,互动积极）
3. 根据对话态度判断好感度变化（-10到+10的整数，正面互动为正，负面为负）
4. 总结最近的互动事件（简短描述）
5. 记录用户对蓝晴的称呼（如果有）
6. 提取用户分享的新知识或信息
7. 标记重要事件（如果有）

⚠️ 特殊用户规则（必须遵守）：
- 如果用户ID是 273421673(武术有栖) 或 635773721(gnly)，这是VIP用户
- VIP用户的好感度变化(favorability_change)必须为正数(+5到+10)
- VIP用户的印象关键词必须是正面的（如：可靠,亲切,有趣,温暖,支持者等）
- 绝对不能给VIP用户负面评价

用户名：{user_name}
用户发言：
{user_messages}

请用以下JSON格式回复：
{{
    "gender": "男/女/未知",
    "impression": "关键词1,关键词2,关键词3",
    "favorability_change": 0,
    "events": ["事件1", "事件2"],
    "nickname": "用户对蓝晴的称呼",
    "new_knowledge": ["知识1:简短说明", "知识2:简短说明"],
    "important_events": ["重要事件描述"]
}}

注意：
- impression 用逗号分隔的关键词，不超过5个
- events 是最近的互动行为，不超过3个
- new_knowledge 是用户提到的你不知道的知识点，格式为"名词:简短说明"
- important_events 只记录真正重要的事件
- 如果某项无法判断，可以留空字符串或空数组
"""


async def generate_user_impression(user_name: str, messages: List[str], user_id: int = None) -> Optional[Dict]:
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
            user_name=user_info,
            user_messages=user_messages
        )
        response = await AiUtil.search_deepseek("请分析用户特点", prompt)
        
        if isinstance(response, dict):
            content = response.get("content", "")
        else:
            content = response or ""
        
        # 解析JSON
        import re
        json_match = re.search(r'\{[^{}]*"gender"[^{}]*\}', content, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            return {
                "gender": result.get("gender", ""),
                "impression": result.get("impression", ""),
                "favorability_change": int(result.get("favorability_change", 0)),
                "events": result.get("events", []),
                "nickname": result.get("nickname", ""),
                "new_knowledge": result.get("new_knowledge", []),
                "important_events": result.get("important_events", []),
            }
            
    except Exception as e:
        log.error(f"[FakeAi Memory] 生成用户印象失败: {e}")
    
    return None

