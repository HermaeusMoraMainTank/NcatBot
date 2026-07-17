# Phase 4 · 整合清单与实现优先级

> Phase 0～3 文档已确认。本文件是 **开写代码前的总验收与顺序**；仍不自动实现。

---

## 1. 实现顺序（强制）

| 序 | 范围 | 文档锚点 | 完成定义（验收） |
|----|------|----------|------------------|
| **I1** | 状态机 + 分析员 + 取消裸 8% + 观测期 | `interaction-*` / `analyst-*` / `fallback-*` | @/别名→SUMMONED；复读/密集→FAMILIAR；回后 OBSERVATION；命令仍优先；fallback 可配且默认过渡值或 0 |
| **I2** | context 文本规范化（发言人格式） | `target-architecture` / I1 内 | 专家上下文可读「时间+名片+内容」；`add_plugin_sent_reply` 仍在 |
| **I3** | `memory_record` + remember/recall + 短知识注入上限 | `memory-*` / `knowledge-*` | 表迁移幂等；工具可选开；印象/好感命令回归通过 |
| **I4** | 贴纸 catalog + 分开发图 | `expression-*` | `:名:` 剥离后先字后图；与 Meme 排除仍在 |
| **I5** | 配置外置 + 硬编码收敛 | 本文件 §3 | 群白名单/别名/观测秒数等可配 |
| **I6** | （按需）你供图表情 → Agent 收拢 → 你二校 | `expression-catalog` §4 | 清单交付；非运行时 ingest |

禁止：先做贴纸/记忆、后做状态机（被动触发会继续烂）。

---

## 2. 全链路体验验收（人工点检）

```text
[ ] @蓝晴 有字 → 会回
[ ] 空 @ → 不回
[ ] 蓝晴印象 / 排行 / 知识库 / 余额 → 命令正常，不走闲聊错乱
[ ] 普通「我下班了」→ 不回（无裸 8%）
[ ] 复读达标 → 可能回也可能否（分析员）；回完有观测期
[ ] 观测期内路人续话不插；再叫蓝晴会回
[ ] 低好感用户叫名 → 仍可被闸门挡
[ ] 「记住我…」→ 可 remember（工具开启时）
[ ] 临时「下楼买奶茶」→ 不进长期记忆
[ ] 回复含 :坏笑: → 先文字消息，再单独图片消息
[ ] 用户打 meme 关键词 → 仍走 Meme 插件，FakeAi 不抢
[ ] 插件代发消息仍在上下文中可被提及
```

---

## 3. 配置外置（目标键，格式实现时定）

至少迁出代码常量：

- `groups.whitelist`
- `persona.aliases` / bot qq
- `interaction.observation_sec` / `group_cd_sec` / familiar&echo&dense 参数
- `analyst.enabled` / `fallback_random_prob` / `force_reply_when_summoned`
- `cognition.tools_enabled` / 注入上限
- `expression.*`

管理员 QQ、关系地板等敏感名单：可留代码或独立本地 secrets，**不要**进公开文档示例真值扩散；迁移时保持行为兼容。

---

## 4. 回归资产（禁止破坏）

见 `gap-matrix.md` §C：人设 YAML、好感、印象、排行、`add_plugin_sent_reply`、视觉链路、知识库命令名等。

---

## 5. 文档侧收口条件

- [x] Phase 1～3 预览用户确认  
- [x] 本整合清单用户确认（整合 OK）  
- [x] 用户下达「可以开发」→ 从 **I1** 开工（已完成 I1）
