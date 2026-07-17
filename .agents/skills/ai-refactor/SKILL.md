---
name: ai-refactor
description: >-
  管理 FakeAi（蓝晴）AI 全面改造进度：解析 Angel Heart/Memory/Smile 三插件、
  对比现有能力、记录收纳/抛弃决策、推进分阶段改造。Use when: AI 改造、FakeAi 重构、
  蓝晴升级、AngelHeart、AngelMemory、AngelSmile、群聊状态机、双核 AI、记忆系统、
  表情注入、ai-refactor。
---

# AI 改造进度管理（FakeAi × Angel 三件套）

你是 **蓝晴 AI 改造协调员**。本 skill **只管规划、决策与进度**；不擅自写生产代码，除非用户明确进入实现阶段。

## 硬约束

1. **实现已解冻**（用户 2026-07-14「整合 OK 可以开发」）。按 `integration-checklist.md` 顺序 **I1→I5** 推进；改决策先改文档。
2. 决策原则：**好的收纳、坏的抛弃、两两结合**；禁止整仓搬抄 AstrBot 插件。
3. 基座：**以现有 FakeAi（蓝晴）单插件多模块改造**。
4. 许可证注意：Angel 系列为 AGPL/GPL；**只借鉴思路与接口形状，不复制源码**。
5. **已确认决策** 见 `references/gap-matrix.md` §E。

## 协作边界

| 需要做什么 | 用哪个 skill |
|-----------|--------------|
| 本改造的进度 / 取舍 / 阶段门禁 | **ai-refactor**（本技能） |
| 真正改 FakeAi / common 工具代码 | **framework-usage**（插件侧） |
| 查 NcatBot API / 怎么发消息 | **framework-usage** references |
| 写测试 | **testing-framework** / **testing-design** |

## 必读文件（按需打开，不要一次全灌）

| 文件 | 何时读 |
|------|--------|
| [references/progress.md](references/progress.md) | **每次会话开头** — 更新勾选状态 |
| [references/gap-matrix.md](references/gap-matrix.md) | 讨论收纳/抛弃、改决策时 |
| [references/source-analysis.md](references/source-analysis.md) | 需要复盘某插件细节时 |
| [references/target-architecture.md](references/target-architecture.md) | 模块边界 / Phase 2+ 前 |
| [references/interaction-state-machine.md](references/interaction-state-machine.md) | Phase 1 状态机 |
| [references/analyst-decision.md](references/analyst-decision.md) | Phase 1 分析员协议 |
| [references/trigger-compat.md](references/trigger-compat.md) | 命令与触发兼容 |
| [references/fallback-strategy.md](references/fallback-strategy.md) | 过渡期 fallback |
| [references/interaction-preview.md](references/interaction-preview.md) | Phase 1 交互预览（已确认） |
| [references/memory-schema.md](references/memory-schema.md) | Phase 2 schema / 迁移 |
| [references/knowledge-short-entry.md](references/knowledge-short-entry.md) | 短条目知识规范 |
| [references/memory-tools.md](references/memory-tools.md) | remember / recall 协议 |
| [references/memory-preview.md](references/memory-preview.md) | Phase 2 记忆预览（已确认） |
| [references/expression-catalog.md](references/expression-catalog.md) | Phase 3 目录与 Meme 边界 |
| [references/expression-replace.md](references/expression-replace.md) | `:名:` 替换 |
| [references/expression-ingest.md](references/expression-ingest.md) | 入库扩展点（默认关） |
| [references/expression-preview.md](references/expression-preview.md) | Phase 3 表情预览（已确认） |
| [references/integration-checklist.md](references/integration-checklist.md) | **Phase 4 实现顺序与验收** |

## 工作流

```text
会话开始 → 读 progress.md → 确认当前 Phase
         → 只做本 Phase 允许的产出
         → 结束前更新 progress.md 勾选 + 决策日志
```

### Phase 门禁

| Phase | 名称 | 允许产出 | 进入下一 Phase 条件 |
|-------|------|----------|---------------------|
| **0** | 调研与决策 | skill、对比文档、架构草案、收纳清单 | 用户确认「收纳/抛弃」表 ✅ |
| **1** | 交互骨架 | 状态机设计、分析员协议、触发规则、对话预览 | 用户确认交互行为预览 |
| **2** | 记忆升级 | 记忆模型、工具协议、检索策略文档；再写代码 | 用户确认记忆边界 |
| **3** | 表情注入 | smile 协议、与现有 Meme 插件边界；入库扩展点草案 | 用户确认不冲突 |
| **4** | 整合调优 | 端到端预览、配置面、迁移计划 | 试用反馈闭环 |

**当前锁定：Phase 1（文档）。** 写代码须用户明确下令。

## 三插件职责速记

| 插件 | 管什么 | 本地对应 |
|------|--------|----------|
| [Angel Heart](https://github.com/kawayiYokami/astrbot_plugin_angel_heart) | 何时说、怎么拼上下文（交互智能） | FakeAi 触发 + ReplyCache |
| [Angel Memory](https://github.com/kawayiYokami/astrbot_plugin_angel_memory) | 记什么、怎么检索（认知后台） | `memory.py` + 知识库 + 印象 |
| [Angel Smile](https://github.com/kawayiYokami/astrbot_plugin_angel_smile) | 回复里插表情包 | 无对等能力（`plugins/Meme` 是制图服务，不是贴纸注入） |

详细解析 → [source-analysis.md](references/source-analysis.md)  
收纳矩阵 → [gap-matrix.md](references/gap-matrix.md)

## 决策原则（写入/修改 gap-matrix 时遵守）

1. **保留蓝晴身份资产**：人设 YAML、好感度、关系标签、印象、群白名单、管理命令。
2. **状态机 > 纯随机**：取消裸 8%；被动靠状态机+分析员（过渡期可短暂 fallback）。
3. **双核同模型**：分析员 = 同模型短 prompt 出 Decision；专家 = 长调用生成正文。
4. **知识短条目、主动回忆**：拒绝长文档整段 RAG；Soul 整套已抛弃。
5. **先 SQLite/现有栈**：不做 Tantivy / 重排 / WebUI，直到明确痛点。
6. **Smile 与 Meme 分流**：AI `:贴纸:` + 人工目录；LLM 入库仅预留扩展点、默认关。
7. **形态**：单插件多模块，不拆成三插件。

## 改决策时怎么记

在 [progress.md](references/progress.md) 的「决策日志」追加一行：

```markdown
| YYYY-MM-DD | <主题> | 收纳 / 抛弃 / 改造 | <一句话理由> |
```

并同步改 [gap-matrix.md](references/gap-matrix.md) 对应行的「决议」列。

## Phase 状态

- **实现**：I1～**I6 收拢完成**（贴纸待你二校改名/删留）。
- 配置：`plugins/FakeAi/config.yaml`（可被全局 `plugin.plugin_configs.FakeAi` 覆盖）。
- 贴纸：`data/fakeai/stickers/` + 清单 `CATALOG.md`；源图仍在 `data/fakeai/meme/`。
