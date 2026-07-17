# 目标架构草案（非实现）

> Phase 0 交付物。描述 **想长成什么样**，不规定马上写哪些文件。  
> 实现阶段再拆具体类名与迁移步骤。

---

## 1. 模块边界（逻辑分包）

```text
FakeAi（或将来同名生态）
├── interaction/     ← Heart 对标
│   ├── state_machine   # NOT_PRESENT / SUMMONED / FAMILIAR / OBSERVATION
│   ├── analyst         # 同模型短 prompt → Decision
│   ├── triggers        # @、别名、复读、密集
│   └── context_builder # ReplyCache → 干净多发言人文本
├── cognition/       ← Memory 对标（无 Soul）
│   ├── impressions     # 现有印象+好感（保留）
│   ├── memories        # 带 strength/tags 的主动记忆
│   ├── knowledge       # 短条目知识
│   └── tools           # remember / recall（分期）
├── expression/      ← Smile 对标
│   ├── sticker_catalog # 人工目录
│   ├── sticker_replace # :名: → Image 段
│   └── sticker_ingest  # 扩展点：LLM 入库（默认禁用、先不实现）
└── persona/           ← 自研保留
    ├── yaml_persona
    └── favorability    # 暖度闸门
```

**已确认形态**：物理上保持 **单个 FakeAi 插件** + 上述逻辑子模块，不拆三插件。

---

## 2. 单条群消息数据流（目标）

```text
GroupMessage
    │
    ▼
白名单 / 排除插件命令 / 冻结用户
    │
    ▼
入账 context_builder（所有人消息都进缓存）
    │
    ▼
state_machine.determine()
    │
    ├─ NOT_PRESENT 且非触发 ──► 结束（仍记缓存）
    ├─ OBSERVATION（未结束）──► 仅紧急 SUMMONED 可打断
    ├─ SUMMONED / FAMILIAR ──► analyst.decide()
    │                              │
    │                              ├─ should_reply=false ──► 结束
    │                              └─ true + 过好感闸门
    │                                        │
    │                                        ▼
    │                              拉取印象 + recall 相关记忆/知识
    │                              组装 persona YAML + strategy
    │                                        │
    │                                        ▼
    │                              专家模型生成 JSON/文本
    │                                        │
    │                                        ▼
    │                              sticker_replace → 发送
    │                                        │
    │                                        ▼
    │                              → OBSERVATION；异步巩固
    └─ …
```

---

## 3. 分析员 Decision

定稿见 [analyst-decision.md](analyst-decision.md)。状态机见 [interaction-state-machine.md](interaction-state-machine.md)。

---

## 4. 记忆模型

定稿见：

- [memory-schema.md](memory-schema.md) — 表结构与迁移  
- [knowledge-short-entry.md](knowledge-short-entry.md) — 短条目  
- [memory-tools.md](memory-tools.md) — remember / recall  

工具（P2）：`remember` / `recall`；笔记读写推迟。

---

## 5. 表情

定稿见：

- [expression-catalog.md](expression-catalog.md)  
- [expression-replace.md](expression-replace.md)  
- [expression-ingest.md](expression-ingest.md)  

P3：人工目录 + `:名:` 替换；入库默认关。

---

## 6. 配置面（概念项，非文件格式）

```text
groups.whitelist
persona.aliases[]          # 「蓝晴」等
interaction.observation_sec
interaction.familiar_cooldown_sec
interaction.echo_threshold / window
interaction.dense_threshold / window / min_participants
analyst.enabled
analyst.use_short_prompt     # 同模型短调用（已确认）
analyst.fallback_random_prob # 过渡期可 >0；终态 0
favorability.*               # 沿用
memory.sleep_interval
memory.min_message_length
expression.enabled
expression.max_per_message
expression.catalog_path
expression.ingest_enabled    # 默认 false；预留 LLM 入库
```

硬编码 QQ / 群号最终应外置；改造中逐步迁移，避免一次大爆改。

---

## 7. 非目标（明确不做）

- 复刻 AstrBot Plugin Pages / 钩子体系  
- 引入 Tantivy/重排栈作为 Phase 1~2 依赖  
- Soul 四维能量模拟  
- 用 Memory 替换好感度  
- 把 `plugins/Meme` 吃掉  
- 在 Phase 0 写任何业务代码  

---

## 8. 迁移直觉（进入实现后）

1. 先抽 `state_machine` + `analyst`，被动触发改走状态机（人设与记忆暂不动）。  
2. 再规范 `context_builder` 文本格式。  
3. 再升级 `memory` schema（可并存旧表，双写/迁移）。  
4. 最后加 sticker 后处理。  

每步保留管理命令可读旧数据；**禁止**无备份砍库。
