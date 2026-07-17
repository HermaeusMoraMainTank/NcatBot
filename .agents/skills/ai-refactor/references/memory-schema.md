# Phase 2 · 记忆 Schema 与迁移

> 目标：在现有 SQLite（`memory.db`）上增量引入「主动记忆」，**不砍**印象/好感/摘要/知识旧表。  
> 无 Soul；检索先 LIKE，可选后续 FTS5。

---

## 1. 现状表（保留）

| 表 | 职责 | 改造态度 |
|----|------|----------|
| `user_impression` | 印象、好感、events… | **只读扩展**；禁止破坏 `favorability_score` / `relation_tag` |
| `group_summary` | 群时段摘要 | **保留**；注入策略改为「按需摘要」而非每次硬塞超长 |
| `important_event` | 群重要事件 | **保留**；可逐步与 `memory_record` 双写后 deprecate（推迟） |
| `knowledge_base` | keyword + content | **保留**；写入规范改为短条目；检索弱化被动大段注入 |
| `app_meta` | 迁移标记 | **保留** |

---

## 2. 新增表（草案）

### 2.1 `memory_record` — 主动铭记

```sql
CREATE TABLE IF NOT EXISTS memory_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    judgment TEXT NOT NULL,          -- ≤120 字结论句
    reasoning TEXT DEFAULT '',       -- 可选依据，≤200
    tags TEXT NOT NULL DEFAULT '[]', -- JSON 字符串数组
    strength REAL NOT NULL DEFAULT 50,  -- 0~100
    memory_type TEXT NOT NULL DEFAULT 'knowledge',
        -- knowledge | preference | episode | relation
    scope TEXT NOT NULL DEFAULT 'public',
        -- public | user:<id> | group:<id>
    source_user_id INTEGER,
    created_at INTEGER NOT NULL,
    last_recalled_at INTEGER DEFAULT 0,
    recall_count INTEGER DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1   -- 0=睡眠 consolidation 软删除
);

CREATE INDEX IF NOT EXISTS idx_memory_scope_active
    ON memory_record(scope, active, strength DESC);
CREATE INDEX IF NOT EXISTS idx_memory_type
    ON memory_record(memory_type, active);
```

### 2.2 `memory_tag` — 可选（首期可省略）

首期 tags 仅存 JSON 数组即可。若标签查询变热，再拆关联表。

### 2.3 知识表轻迁移（无新表）

为 `knowledge_base` 增加列（幂等 migration）：

```sql
ALTER TABLE knowledge_base ADD COLUMN strength REAL DEFAULT 50;
ALTER TABLE knowledge_base ADD COLUMN active INTEGER DEFAULT 1;
ALTER TABLE knowledge_base ADD COLUMN content_len INTEGER DEFAULT 0;
-- content_len 写入时维护，便于巩固任务筛「过长条目」
```

---

## 3. Scope 约定

| scope | 含义 | 谁写入 |
|-------|------|--------|
| `public` | 全群共用常识/人设外知识 | remember 未指定 / 知识库 |
| `user:<qq>` | 对某人的事实偏好 | remember 明确对人 |
| `group:<gid>` | 仅该群梗/约定 | 群摘要巩固或 remember |

召回优先级（同 query）：`user` 命中 > `group` 命中 > `public`；再按 `strength`、`last_recalled` 衰减排序。

---

## 4. 迁移策略

### 4.1 原则

- **禁止**删除旧库文件；`FAKEAI_MEMORY_DB` 路径逻辑不变。
- 所有 DDL 走现有 `_run_migrations` 幂等路径。
- `app_meta` 记：`memory_schema_version = 1`。

### 4.2 双写（可选阶段）

| 阶段 | 行为 |
|------|------|
| M0 现状 | 仅旧表 |
| M1 文档确认后实现 | 建 `memory_record`；工具写入新表；旧印象更新逻辑不动 |
| M2 | 定时巩固：从高价值 `important_event` / 印象片段 **复制** 到 memory_record（不删源） |
| M3 | 专家 prompt 优先 `recall`；旧 `get_knowledge_text` 降为兜底 |

### 4.3 回滚

- 代码开关 `cognition.memory_record_enabled=false` → 读写回退旧路径。
- 表可留着不删；无需 drop。

---

## 5. 睡眠巩固（简化版职责）

周期（默认 3600s，可配）后台任务：

1. `strength < 15` 且 `recall_count=0` 且年龄 > 7 天 → `active=0`
2. 近重复：相同 `judgment` 规范化后相似度过高 → 保留 strength 高的，其它 `active=0`
3. `knowledge_base`：`content_len > 120` 打日志提醒（不自动删）；`hit_count=0` 且很旧可降 strength
4. **不做**向量重建、JSON Web 备份面板（推迟）；可选每天 copy 一份 `memory.db` 到 `data/db/backup/`

---

## 6. 与交互层边界

- 分析员 **不读** memory_record（控制短 prompt）。
- 专家调用前：`recall(topic 或 最近用户句, limit=3~5)` 注入占位符 `{related_memories}`。
- 被动大段 `{related_knowledge}`：限总长 ≤300 字，且单条 content ≤100 字；超长截断并记 warn。
