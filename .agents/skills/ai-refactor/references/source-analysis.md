# Angel 三插件功能解析

> 来源仓库（只借鉴思路，不复制代码）：
> - Heart: https://github.com/kawayiYokami/astrbot_plugin_angel_heart
> - Memory: https://github.com/kawayiYokami/astrbot_plugin_angel_memory
> - Smile: https://github.com/kawayiYokami/astrbot_plugin_angel_smile

---

## 1. Angel Heart — 交互智能

**一句话**：教 AI「什么时候开口、开口该用什么策略」，并重写群聊上下文，避免多人发言人混淆。

### 1.1 4 状态机

| 状态 | 含义 | 典型进入条件 |
|------|------|--------------|
| `NOT_PRESENT` 不在场 | 静默观察 | 默认 / 闭嘴 / 冷却 |
| `SUMMONED` 被呼唤 | 准备响应 | `@` 自己、唤醒词/昵称 |
| `GETTING_FAMILIAR` 混脸熟 | 主动融入 | 复读达标、密集讨论达标（且不在冷却） |
| `OBSERVATION` 观测中 | 回复后安静 | 刚回过话；超时降级 |

优先级（Heart）：闭嘴 > 被呼唤 > 观测中 > 混脸熟 > 不在场。

附带机制：混脸熟冷却、观测期超时降级、会话门锁/事件扣押（防并发抢答）、耐心计时器。

### 1.2 两级 AI（双核）

| 角色 | 职责 | 成本 |
|------|------|------|
| 轻量「分析员」`LLMAnalyzer` | 看上下文 → `should_reply` + `reply_strategy` + `topic` | 低 |
| 重量「专家」 | AstrBot 主链路真正生成回复 | 高，按需 |

分析员决策会 **oneshot 注入** 到专家请求提示词末尾。

### 1.3 上下文管理

- `ConversationLedger`：全量入账（群/私聊）
- 重写为带 **发言人 + 时间戳** 的 prompt 字符串
- 系统人设与动态上下文隔离
- 图片转述 + dHash 缓存

### 1.4 角色分层

- **FrontDesk（前台）**：收消息、缓存、状态检查、改写 prompt
- **Secretary（秘书）**：按状态决定是否调分析员、是否放行回复

### 1.5 配置面（概念）

白名单、分析模型、唤醒别名、复读/密集阈值、观测时长、混脸熟冷却、最大上下文 token、策略提示词模块（`prompts/modules/`）。

### 1.6 与 Memory 关系

Heart 负责聊天链路；Memory **依赖 Heart 的增强上下文** 才能工作得好。组合语义：Heart = 嘴与眼色，Memory = 脑子。

---

## 2. Angel Memory — 认知后台

**一句话**：主动记忆 + 检索 + 巩固，而不是把长文档砸进每次 prompt。

### 2.1 三层认知

| 层 | 职责 |
|----|------|
| Soul 灵魂 | 4 维能量槽：回忆深度 / 印象深度 / 表达欲 / 创造力 → 映射到检索量、温度等 |
| DeepMind 潜意识 | 后台：预处理、检索、强化、合并、睡眠巩固 |
| LLM 主意识 | 通过工具主动记/忆/写笔记 |

### 2.2 四个 LLM 工具

| 工具 | 作用 |
|------|------|
| `angel_remember` | 永久铭记（judgment / tags / strength / type） |
| `angel_recall` | 回忆核心记忆 + 笔记摘要 |
| `angel_note_read` | 按短 ID 分页读笔记正文 |
| `angel_note_create` | 整理归档 Markdown 笔记 |

### 2.3 检索栈

- 基线：BM25（Tantivy + CJK 1~2-gram）
- 可选：嵌入向量 + RRF 融合
- 可选：rerank（官方认为比向量更值）
- 睡眠巩固：弱记忆清理、索引维护、JSON 备份

### 2.4 知识库哲学（强观点）

- **不是长文档 RAG**；推荐 ≤100 字短条目
- 被动灌长文 = 上下文污染
- 正确姿势：短卡片 + AI **主动** note 工具整理

### 2.5 运营面

Plugin Pages WebUI：总览、记忆浏览、用户画像、Tags、向量检索、笔记、导入导出、维护状态。

### 2.6 Scope

`conversation_scope_map`：按人格名 / 会话 ID 分桶记忆（恋爱 / 家人 / public…）。

---

## 3. Angel Smile — 表情注入

**一句话**：让 LLM 在回复正文里用 `:贴纸名:`，发送前替换成图；并可工具入库。

### 3.1 流程

1. 扫描 `memes/` 生成可用列表 → 注入提示词  
2. 模型输出含 `:meme名:`  
3. 发送前替换为图片  
4. 可选：`meme` 工具把聊天图收进库存（WebP、多变体文件夹、dHash 去重）

### 3.2 目录规则

- 根目录单图：文件名 = 贴纸名  
- 一级文件夹 = 贴纸名，内为变体 `(2)` `(3)`…  
- 配置：`max_stickers_per_message`（默认 1）

### 3.3 与 Heart/Memory

独立能力，不依赖 Memory；通常挂在 LLM 回复链路上即可。

---

## 4. 三件套如何咬合（参考架构）

```text
消息入账 ──► Heart 状态机
              │
              ├─ 不回 → 结束
              └─ 回 → 分析员决策注入
                        │
                        ▼
              Memory 注入相关记忆/笔记 + Soul 参数
                        │
                        ▼
              专家 LLM 生成正文（可含 :贴纸:）
                        │
                        ▼
              Smile 替换贴纸 → 发出
                        │
                        ▼
              Memory 后台强化 / 巩固
```

对我们改造的启示：**按「交互 / 记忆 / 表情」三模块拆**，不要做成一个巨石脚本。
