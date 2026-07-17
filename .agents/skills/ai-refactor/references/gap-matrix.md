# 现有 FakeAi vs Angel 三件套 — 对比与收纳决议

> 现有基座：`plugins/FakeAi/`（蓝晴）+ `plugins/common/utils/AiUtil.py`  
> 决议列取值：`收纳` | `改造收纳` | `保留自研` | `抛弃` | `推迟`  
> **状态：已于 2026-07-14 由用户确认（全听默认建议）。** 改决议时同步改 `progress.md` 决策日志。

---

## A. 能力总表

| 能力域 | FakeAi 现状 | Angel 来源 | 决议 | 结合方式 |
|--------|-------------|------------|------|----------|
| 主动触发（@ / 名字） | `@` 或消息含「蓝晴」 | Heart `SUMMONED` | **改造收纳** | 状态机事件驱动；保留「蓝晴」别名可配置 |
| 被动插话 | 固定 ~8% 随机 + 好感过滤 | Heart 混脸熟/分析员 | **改造收纳** | **取消裸 8%**；主路径=状态机+分析员；过渡期可短暂保留 fallback |
| 回复后安静 | 仅群 CD 10s | Heart `OBSERVATION` | **收纳** | 观测期（可配）> 硬 CD；CD 可保留作下限 |
| 复读/热闹触发 | 无 | Heart echo/dense | **收纳** | 阈值进配置；混脸熟冷却防刷屏 |
| 双核 AI | 单模型 `AiUtil` 直出 | Heart Analyzer+Expert | **收纳** | **同模型短 prompt** 做分析员 Decision；另一次长调用生成正文 |
| 上下文带发言人 | `ReplyCache` JSON（name/id/content） | Heart Ledger 重写 | **改造收纳** | 保留缓存；规范成「时间+群名片+内容」文本；插件代发 `source` 继续保留 |
| 私聊链路 | 基本无 | Heart 私聊入账 | **推迟** | 当前以群聊为主 |
| 门锁/事件扣押 | 群 CD 防并发 | Heart door lock | **推迟** | Phase 1 用现有 CD；并发痛了再上 |
| 好感度等级 | 完善（-50~100、档位、回复概率） | 无对等 | **保留自研** | 作「暖度/是否理人」主信号；**不**被 Soul 替换 |
| 用户印象 | LLM 定期画像 + 查询命令 | Memory 画像/记忆 | **改造收纳** | 印象继续；逐渐迁到主动 remember |
| 群聊摘要记忆 | 定时 summary → SQLite | Memory 巩固 | **改造收纳** | 摘要保留；加睡眠式清理/合并 |
| 知识库 | keyword LIKE + 被动注入 | Memory 短条目 + 工具 | **改造收纳** | 采纳短条目哲学；弱化被动灌入；后续加 recall 工具 |
| 主动记忆工具 | 无 | Memory 4 tools | **收纳（分期）** | P2：`remember`/`recall`；笔记工具可后置 |
| BM25/向量/重排 | LIKE 搜索 | Tantivy 栈 | **推迟** | 先用 SQLite FTS5；向量/重排有明确痛点再说 |
| Soul 四维能量 | 无 | Memory Soul | **抛弃（整套）** | **已确认**；与好感度重叠；温度/长短用配置或策略字符串 |
| Memory WebUI | 命令查询 | Plugin Pages | **推迟** | 先命令 + DB；管理面板非刚需 |
| Scope 分桶 | 群维度 summary + 全局印象/知识 | scope_map | **改造收纳** | 群记忆 / 用户记忆 / 公共知识 三桶即可 |
| 表情注入 | 无（回复纯文本/JSON） | Smile `:名:` | **收纳** | AI 回复后处理；独立贴纸库存目录 |
| 贴纸 LLM 入库 | 无 | Smile `meme` 工具 | **推迟（接口预留）** | P3 先人工目录；设计上保留可接 LLM 入库，默认关闭 |
| 用户命令制图 | `plugins/Meme` 另一套 | 无 | **保留自研** | 与 Smile **分流**，禁止合并成一个插件 |
| 人设 YAML | 有占位符注入 | Heart prompt modules | **保留自研 + 改造** | YAML 继续做人设；策略注入参考 Heart 模块化 |
| 视觉描述图 | 有 | Heart 图片转述 | **保留自研** | 可吸收 dHash 缓存思路（推迟） |
| 白名单群 | `FAKEAI_ALLOWED_GROUPS` | Heart 白名单 | **保留自研** | 迁到配置文件，去掉硬编码最终态 |
| 管理命令 | 印象/知识库/排行/余额/说话 | Memory 面板部分 | **保留自研** | 改造后命令对齐新数据模型 |
| 插件代发入上下文 | `add_plugin_sent_reply` | 无直接对等 | **保留自研** | 高价值，必须保留 |
| AstrBot 钩子/Plugin Pages | N/A | 平台绑定 | **抛弃** | 用 NcatBot 插件 API 重写等价物 |
| 插件形态 | 单文件偏巨石 | Heart/Memory/Smile 三件套 | **单插件多模块** | FakeAi 内 `interaction/` `cognition/` `expression/` `persona/` |

---

## B. 分插件决议摘要

### Heart → FakeAi 交互层

| 收纳 | 抛弃 / 推迟 |
|------|-------------|
| 4 状态机（可裁剪实现） | AstrBot oneshot hook 原样 |
| 分析员 Decision JSON（同模型短 prompt） | FrontDesk/Secretary 角色名仪式感（可内化为模块） |
| 观测期 / 混脸熟冷却 | 完整门锁+扣押（推迟） |
| 复读 / 密集检测 | 私聊全面接管（推迟） |
| 决策策略注入正文 prompt | AGPL 源码 |
| 取消裸 8%（过渡期可留 fallback） | — |

### Memory → FakeAi 记忆层

| 收纳 | 抛弃 / 推迟 |
|------|-------------|
| 主动工具记/忆 | Soul 四维整套（**已确认抛弃**） |
| 短条目知识哲学 | Tantivy / 嵌入 / rerank（推迟） |
| 睡眠巩固（简化：清理弱知识、合并重复） | 完整 WebUI |
| 记忆 strength / tags 概念 | 长文档被动 RAG |
| 与 Heart「后置增强」的职责拆分思想 | GPL 源码 |

### Smile → FakeAi 发送层

| 收纳 | 抛弃 / 推迟 |
|------|-------------|
| `:贴纸名:` 语法 + 发送前替换 | 与 `plugins/Meme` 合并 |
| 贴纸列表注入 system/user 提示 | 强制 WebP-only（可跟现有格式） |
| 每条消息贴纸上限 | LLM 自动入库：**P3 不做，接口预留** |
| 独立 `data/.../stickers/` 人工目录 | — |

---

## C. 现有 FakeAi「必须留下」的资产清单

实现改造时 **禁止无故删掉**：

1. 蓝晴人设与 YAML 占位符链路（`load_yaml_data` / `answer_ai`）
2. `favorability.py` 分数/档位/回复概率/关系地板
3. `user_impression` / 查印象 / 好感排行
4. `add_plugin_sent_reply`（其他插件上下文可见）
5. 群白名单与管理旁路用户
6. 图片描述 / 视觉测试链路
7. AiStats / Token 用量记录（若已接）
8. 「蓝晴知识库」等用户已习惯的命令名（可扩展别名，勿直接消灭）

---

## D. 确认后的组合公式

```text
何时说 = Heart 状态机
       ∩ 好感度闸门（FakeAi）
       ∩ 分析员 should_reply（同模型短 prompt）
       （过渡期：分析员不可用时可选极低概率 fallback；终态取消裸 8%）

怎么说 = 蓝晴人设 YAML
       + 分析员 reply_strategy 注入
       + 相关印象/回忆（Memory 改造）
       + 可选 :贴纸:（Smile，人工目录）

说完后 = 进入 OBSERVATION
       + 异步更新印象/记忆巩固

形态   = 单插件 FakeAi + 逻辑多模块
```

---

## E. 分歧点 — 已确认（2026-07-14）

| # | 议题 | 决议 |
|---|------|------|
| 1 | 被动插话 | **取消裸 8%**；主路径=状态机+分析员；过渡期可短暂留 fallback |
| 2 | 分析员模型 | **同模型短 prompt**（与正文同一模型族/配置，调用更短） |
| 3 | Soul | **整套抛弃** |
| 4 | Smile 入库 | **P3 人工目录**；设计保留 LLM meme 入库扩展点，默认关闭 |
| 5 | 插件形态 | **FakeAi 单插件多模块**（不拆三插件） |
