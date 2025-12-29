"""
LearningChat 数据模型模块
使用 aiosqlite 进行异步数据库操作
"""

import os
import json
import logging
from pathlib import Path
from functools import cached_property
from typing import List, Optional, Tuple
import aiosqlite

try:
    import jieba_fast as jieba
    import jieba_fast.analyse as jieba_analyse
except ImportError:
    import jieba
    import jieba.analyse as jieba_analyse

from .config import config_manager

log = logging.getLogger(__name__)

# 数据库路径
DATABASE_PATH = Path("data") / "LearningChat" / "learning_chat.db"
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

# jieba 配置
jieba.setLogLevel(jieba.logging.INFO)
config = config_manager.config
if config.dictionary:
    for word in config.dictionary:
        jieba.add_word(word)


class DatabaseManager:
    """数据库管理器"""
    
    _instance: Optional["DatabaseManager"] = None
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
    
    async def _create_tables(self):
        """创建数据表"""
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS message (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                raw_message TEXT NOT NULL,
                plain_text TEXT NOT NULL,
                time INTEGER NOT NULL
            );
            
            CREATE INDEX IF NOT EXISTS idx_message_group_time 
                ON message(group_id, time DESC);
            CREATE INDEX IF NOT EXISTS idx_message_id 
                ON message(message_id);
            
            CREATE TABLE IF NOT EXISTS context (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keywords TEXT NOT NULL,
                time INTEGER NOT NULL,
                count INTEGER DEFAULT 1
            );
            
            CREATE INDEX IF NOT EXISTS idx_context_keywords 
                ON context(keywords);
            CREATE INDEX IF NOT EXISTS idx_context_time 
                ON context(time DESC);
            
            CREATE TABLE IF NOT EXISTS answer (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keywords TEXT NOT NULL,
                group_id INTEGER NOT NULL,
                count INTEGER DEFAULT 1,
                time INTEGER NOT NULL,
                messages TEXT NOT NULL,
                context_id INTEGER,
                FOREIGN KEY (context_id) REFERENCES context(id)
            );
            
            CREATE INDEX IF NOT EXISTS idx_answer_keywords 
                ON answer(keywords);
            CREATE INDEX IF NOT EXISTS idx_answer_context 
                ON answer(context_id);
            CREATE INDEX IF NOT EXISTS idx_answer_group 
                ON answer(group_id);
            
            CREATE TABLE IF NOT EXISTS blacklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keywords TEXT NOT NULL,
                global_ban INTEGER DEFAULT 0,
                ban_group_id TEXT NOT NULL DEFAULT '[]'
            );
            
            CREATE INDEX IF NOT EXISTS idx_blacklist_keywords 
                ON blacklist(keywords);
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


# 全局数据库管理器
db_manager = DatabaseManager()


class ChatMessage:
    """聊天消息模型"""
    
    def __init__(
        self,
        group_id: int,
        user_id: int,
        message_id: int,
        message: str,
        raw_message: str,
        plain_text: str,
        time: int,
        id: int = None,
    ):
        self.id = id
        self.group_id = group_id
        self.user_id = user_id
        self.message_id = message_id
        self.message = message
        self.raw_message = raw_message
        self.plain_text = plain_text
        self.time = time
    
    @cached_property
    def is_plain_text(self) -> bool:
        """是否纯文本"""
        return "[CQ:" not in self.message
    
    @cached_property
    def keyword_list(self) -> List[str]:
        """获取纯文本部分的关键词列表"""
        if not self.is_plain_text and not len(self.plain_text):
            return []
        return jieba_analyse.extract_tags(
            self.plain_text, 
            topK=config_manager.config.keywords_size
        )
    
    @cached_property
    def keywords(self) -> str:
        """获取纯文本部分的关键词结果"""
        if not self.is_plain_text and not len(self.plain_text):
            return self.message
        return (
            self.message if len(self.keyword_list) < 2 
            else " ".join(self.keyword_list)
        )
    
    async def save(self):
        """保存消息到数据库"""
        db = db_manager.db
        if self.id is None:
            cursor = await db.execute(
                """INSERT INTO message 
                   (group_id, user_id, message_id, message, raw_message, plain_text, time)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (self.group_id, self.user_id, self.message_id, 
                 self.message, self.raw_message, self.plain_text, self.time)
            )
            self.id = cursor.lastrowid
        else:
            await db.execute(
                """UPDATE message SET 
                   group_id=?, user_id=?, message_id=?, message=?, 
                   raw_message=?, plain_text=?, time=?
                   WHERE id=?""",
                (self.group_id, self.user_id, self.message_id,
                 self.message, self.raw_message, self.plain_text, 
                 self.time, self.id)
            )
        await db.commit()
    
    @classmethod
    async def filter(
        cls,
        group_id: int = None,
        user_id: int = None,
        message_id: int = None,
        time__gte: int = None,
    ) -> List["ChatMessage"]:
        """查询消息"""
        db = db_manager.db
        conditions = []
        params = []
        
        if group_id is not None:
            conditions.append("group_id = ?")
            params.append(group_id)
        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)
        if message_id is not None:
            conditions.append("message_id = ?")
            params.append(message_id)
        if time__gte is not None:
            conditions.append("time >= ?")
            params.append(time__gte)
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = f"SELECT * FROM message WHERE {where_clause} ORDER BY time DESC"
        
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        
        return [cls._from_row(row) for row in rows]
    
    @classmethod
    def _from_row(cls, row) -> "ChatMessage":
        """从数据库行创建对象"""
        return cls(
            id=row["id"],
            group_id=row["group_id"],
            user_id=row["user_id"],
            message_id=row["message_id"],
            message=row["message"],
            raw_message=row["raw_message"],
            plain_text=row["plain_text"],
            time=row["time"],
        )


class ChatContext:
    """聊天上下文模型"""
    
    def __init__(
        self,
        keywords: str,
        time: int,
        count: int = 1,
        id: int = None,
    ):
        self.id = id
        self.keywords = keywords
        self.time = time
        self.count = count
    
    async def save(self):
        """保存到数据库"""
        db = db_manager.db
        if self.id is None:
            cursor = await db.execute(
                "INSERT INTO context (keywords, time, count) VALUES (?, ?, ?)",
                (self.keywords, self.time, self.count)
            )
            self.id = cursor.lastrowid
        else:
            await db.execute(
                "UPDATE context SET keywords=?, time=?, count=? WHERE id=?",
                (self.keywords, self.time, self.count, self.id)
            )
        await db.commit()
    
    @classmethod
    async def create(cls, keywords: str, time: int) -> "ChatContext":
        """创建新上下文"""
        context = cls(keywords=keywords, time=time)
        await context.save()
        return context
    
    @classmethod
    async def filter(
        cls,
        keywords: str = None,
        count__gte: int = None,
    ) -> List["ChatContext"]:
        """查询上下文"""
        db = db_manager.db
        conditions = []
        params = []
        
        if keywords is not None:
            conditions.append("keywords = ?")
            params.append(keywords)
        if count__gte is not None:
            conditions.append("count >= ?")
            params.append(count__gte)
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = f"SELECT * FROM context WHERE {where_clause} ORDER BY time DESC"
        
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        
        return [cls._from_row(row) for row in rows]
    
    @classmethod
    def _from_row(cls, row) -> "ChatContext":
        """从数据库行创建对象"""
        return cls(
            id=row["id"],
            keywords=row["keywords"],
            time=row["time"],
            count=row["count"],
        )


class ChatAnswer:
    """聊天回答模型"""
    
    def __init__(
        self,
        keywords: str,
        group_id: int,
        time: int,
        messages: List[str] = None,
        count: int = 1,
        context_id: int = None,
        id: int = None,
    ):
        self.id = id
        self.keywords = keywords
        self.group_id = group_id
        self.time = time
        self.messages = messages or []
        self.count = count
        self.context_id = context_id
    
    async def save(self):
        """保存到数据库"""
        db = db_manager.db
        messages_json = json.dumps(self.messages, ensure_ascii=False)
        
        if self.id is None:
            cursor = await db.execute(
                """INSERT INTO answer 
                   (keywords, group_id, count, time, messages, context_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (self.keywords, self.group_id, self.count, 
                 self.time, messages_json, self.context_id)
            )
            self.id = cursor.lastrowid
        else:
            await db.execute(
                """UPDATE answer SET 
                   keywords=?, group_id=?, count=?, time=?, messages=?, context_id=?
                   WHERE id=?""",
                (self.keywords, self.group_id, self.count,
                 self.time, messages_json, self.context_id, self.id)
            )
        await db.commit()
    
    @classmethod
    async def create(
        cls, 
        keywords: str, 
        group_id: int, 
        time: int, 
        context: ChatContext,
        messages: List[str] = None
    ) -> "ChatAnswer":
        """创建新回答"""
        answer = cls(
            keywords=keywords,
            group_id=group_id,
            time=time,
            messages=messages or [],
            context_id=context.id,
        )
        await answer.save()
        return answer
    
    @classmethod
    async def filter(
        cls,
        keywords: str = None,
        group_id: int = None,
        context: ChatContext = None,
        count__gte: int = None,
        keywords__in: List[str] = None,
    ) -> List["ChatAnswer"]:
        """查询回答"""
        db = db_manager.db
        conditions = []
        params = []
        
        if keywords is not None:
            conditions.append("keywords = ?")
            params.append(keywords)
        if group_id is not None:
            conditions.append("group_id = ?")
            params.append(group_id)
        if context is not None:
            conditions.append("context_id = ?")
            params.append(context.id)
        if count__gte is not None:
            conditions.append("count >= ?")
            params.append(count__gte)
        if keywords__in is not None and keywords__in:
            placeholders = ",".join(["?" for _ in keywords__in])
            conditions.append(f"keywords IN ({placeholders})")
            params.extend(keywords__in)
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = f"SELECT * FROM answer WHERE {where_clause} ORDER BY time DESC"
        
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        
        return [cls._from_row(row) for row in rows]
    
    @classmethod
    async def delete_by_keywords(cls, keywords: str, group_id: int = None):
        """删除指定关键词的回答"""
        db = db_manager.db
        if group_id is not None:
            await db.execute(
                "DELETE FROM answer WHERE keywords = ? AND group_id = ?",
                (keywords, group_id)
            )
        else:
            await db.execute(
                "DELETE FROM answer WHERE keywords = ?",
                (keywords,)
            )
        await db.commit()
    
    @classmethod
    async def get_cross_group_keywords(cls, threshold: int) -> List[str]:
        """获取跨群回复的关键词列表"""
        db = db_manager.db
        cursor = await db.execute(
            """SELECT keywords FROM answer 
               GROUP BY keywords 
               HAVING COUNT(DISTINCT group_id) >= ?""",
            (threshold,)
        )
        rows = await cursor.fetchall()
        return [row["keywords"] for row in rows]
    
    @classmethod
    def _from_row(cls, row) -> "ChatAnswer":
        """从数据库行创建对象"""
        messages = json.loads(row["messages"]) if row["messages"] else []
        return cls(
            id=row["id"],
            keywords=row["keywords"],
            group_id=row["group_id"],
            time=row["time"],
            messages=messages,
            count=row["count"],
            context_id=row["context_id"],
        )


class ChatBlackList:
    """聊天黑名单模型"""
    
    def __init__(
        self,
        keywords: str,
        global_ban: bool = False,
        ban_group_id: List[int] = None,
        id: int = None,
    ):
        self.id = id
        self.keywords = keywords
        self.global_ban = global_ban
        self.ban_group_id = ban_group_id or []
    
    async def save(self):
        """保存到数据库"""
        db = db_manager.db
        ban_group_json = json.dumps(self.ban_group_id)
        
        if self.id is None:
            cursor = await db.execute(
                """INSERT INTO blacklist 
                   (keywords, global_ban, ban_group_id)
                   VALUES (?, ?, ?)""",
                (self.keywords, int(self.global_ban), ban_group_json)
            )
            self.id = cursor.lastrowid
        else:
            await db.execute(
                """UPDATE blacklist SET 
                   keywords=?, global_ban=?, ban_group_id=?
                   WHERE id=?""",
                (self.keywords, int(self.global_ban), 
                 ban_group_json, self.id)
            )
        await db.commit()
    
    @classmethod
    async def filter(cls, keywords: str = None) -> List["ChatBlackList"]:
        """查询黑名单"""
        db = db_manager.db
        if keywords is not None:
            cursor = await db.execute(
                "SELECT * FROM blacklist WHERE keywords = ?",
                (keywords,)
            )
        else:
            cursor = await db.execute("SELECT * FROM blacklist")
        
        rows = await cursor.fetchall()
        return [cls._from_row(row) for row in rows]
    
    @classmethod
    def _from_row(cls, row) -> "ChatBlackList":
        """从数据库行创建对象"""
        ban_group_id = json.loads(row["ban_group_id"]) if row["ban_group_id"] else []
        return cls(
            id=row["id"],
            keywords=row["keywords"],
            global_ban=bool(row["global_ban"]),
            ban_group_id=ban_group_id,
        )

