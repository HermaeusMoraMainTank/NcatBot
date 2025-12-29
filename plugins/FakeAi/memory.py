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
            
            -- 用户印象表（记住每个用户的特点）
            CREATE TABLE IF NOT EXISTS user_impression (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                impression TEXT NOT NULL,
                last_updated INTEGER NOT NULL,
                interaction_count INTEGER DEFAULT 1,
                UNIQUE(group_id, user_id)
            );
            
            CREATE INDEX IF NOT EXISTS idx_impression_group_user 
                ON user_impression(group_id, user_id);
            
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

    # ========== 用户印象相关 ==========
    
    async def update_user_impression(
        self,
        group_id: int,
        user_id: int,
        impression: str,
    ):
        """更新用户印象"""
        now = int(time.time())
        await self._db.execute(
            """INSERT INTO user_impression (group_id, user_id, impression, last_updated, interaction_count)
               VALUES (?, ?, ?, ?, 1)
               ON CONFLICT(group_id, user_id) DO UPDATE SET
               impression = ?,
               last_updated = ?,
               interaction_count = interaction_count + 1""",
            (group_id, user_id, impression, now, impression, now)
        )
        await self._db.commit()
    
    async def get_user_impression(self, group_id: int, user_id: int) -> Optional[str]:
        """获取用户印象"""
        cursor = await self._db.execute(
            "SELECT impression FROM user_impression WHERE group_id = ? AND user_id = ?",
            (group_id, user_id)
        )
        row = await cursor.fetchone()
        return row["impression"] if row else None
    
    async def get_group_user_impressions(self, group_id: int, limit: int = 10) -> List[Dict]:
        """获取群内活跃用户的印象"""
        cursor = await self._db.execute(
            """SELECT user_id, impression, interaction_count 
               FROM user_impression 
               WHERE group_id = ?
               ORDER BY interaction_count DESC, last_updated DESC
               LIMIT ?""",
            (group_id, limit)
        )
        rows = await cursor.fetchall()
        return [
            {
                "user_id": row["user_id"],
                "impression": row["impression"],
                "interaction_count": row["interaction_count"],
            }
            for row in rows
        ]
    
    async def get_user_impressions_text(self, group_id: int, max_length: int = 400) -> str:
        """获取格式化的用户印象文本"""
        impressions = await self.get_group_user_impressions(group_id, limit=8)
        
        if not impressions:
            return "（暂无用户印象）"
        
        lines = []
        for imp in impressions:
            user_id = imp["user_id"]
            impression = imp["impression"]
            if impression:
                lines.append(f"- {user_id}: {impression}")
        
        text = "\n".join(lines)
        if len(text) > max_length:
            text = text[:max_length] + "..."
        
        return text if text else "（暂无用户印象）"

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

IMPRESSION_PROMPT = """你是一个群聊分析助手。请根据以下某个用户在群聊中的发言，用一句话总结这个人的特点或性格。

要求：
1. 简短，不超过30字
2. 突出这个人的说话风格、兴趣爱好或性格特点
3. 如果消息太少或无法判断，返回空字符串
4. 不要使用"该用户"这类称呼，直接描述特点

用户名：{user_name}
用户发言：
{user_messages}

请直接回复一句话描述（不需要JSON格式），例如：
- 话多，喜欢玩游戏，经常熬夜
- 说话简短，偶尔吐槽
- 二次元爱好者，喜欢聊动漫
"""


async def generate_user_impression(user_name: str, messages: List[str]) -> Optional[str]:
    """
    调用AI生成用户印象
    
    Args:
        user_name: 用户昵称
        messages: 用户的消息列表
        
    Returns:
        一句话印象描述，或 None
    """
    if not messages or len(messages) < 3:
        return None
    
    # 取最近的消息
    recent_messages = messages[-30:]
    user_messages = "\n".join(f"- {msg}" for msg in recent_messages if msg)
    
    if len(user_messages) < 50:
        return None
    
    try:
        from common.utils.AiUtil import AiUtil
        
        prompt = IMPRESSION_PROMPT.format(
            user_name=user_name,
            user_messages=user_messages
        )
        response = await AiUtil.search_deepseek("请分析用户特点", prompt)
        
        if isinstance(response, dict):
            content = response.get("content", "")
        else:
            content = response or ""
        
        # 清理响应
        content = content.strip().strip('"').strip("'")
        
        # 过滤太长或无效的响应
        if content and len(content) <= 50 and not content.startswith("{"):
            return content
            
    except Exception as e:
        log.error(f"[FakeAi Memory] 生成用户印象失败: {e}")
    
    return None

