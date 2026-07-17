# Phase 1 · 分析员 Decision Schema

> 同模型短 prompt（已确认）。分析员 **只决策、不扮演蓝晴长文**。

---

## 1. 输出 JSON Schema（定稿草案）

```json
{
  "should_reply": true,
  "urgency": "normal",
  "reply_strategy": "接一句梗就行，别展开",
  "topic": "群友复读「确实」",
  "silence_reason": null
}
```

| 字段 | 类型 | 必填 | 约束 |
|------|------|------|------|
| `should_reply` | bool | 是 | 是否建议调用专家生成回复 |
| `urgency` | string | 是 | 枚举：`low` \| `normal` \| `high` |
| `reply_strategy` | string | 是当 should_reply=true | ≤80 字；注入专家 prompt |
| `topic` | string | 是当 should_reply=true | ≤40 字；当前话题标签 |
| `silence_reason` | string\|null | 是当 should_reply=false | 简短原因，便于打日志 |

解析失败（非 JSON / 缺字段）：视为 `should_reply=false`，`silence_reason="parse_error"`（安全默认不回）。

---

## 2. 短 prompt 结构（概念）

**System（短）：**

```text
你是群聊秘书，只判断蓝晴要不要开口。
只输出一个 JSON 对象，不要 markdown，不要解释。
字段：should_reply, urgency, reply_strategy, topic, silence_reason。
召唤态：除非无意义（纯表情/只@无字），倾向 should_reply=true。
混脸熟：除非明显好玩或能接梗，倾向 should_reply=false。
不要扮演蓝晴说话；reply_strategy 是给正文模型的策略，不是对用户的回复。
```

**User（短）：**

```text
状态: SUMMONED|FAMILIAR
最近对话（新在下，已标发言人）:
[12:01] 小明: …
[12:01] 小红: …
[12:02] 小明: 蓝晴 …
```

上下文条数建议：最近 **8～12** 条纯文本摘要；图片用已有「[图片:简述]」占位。不注入完整人设 YAML、不注入知识库长文。

---

## 3. 状态对决策的偏置（代码侧，不单靠 prompt）

| 进入状态 | 代码偏置 | 说明 |
|----------|----------|------|
| `SUMMONED` | 若分析员 `should_reply=false` 且 `urgency!=high` 的否决理由仅为「可回可不回」类 — **仍可强制 true**（可选开关 `force_reply_when_summoned`，默认 **true**） | 被叫到必须有交代 |
| `SUMMONED` | 最新消息去 CQ 后长度为 0 或仅表情 — **强制 false** | 避免空喊 |
| `FAMILIAR` | 不强制；尊重分析员；否决率预期更高 | 热闹不等于要插话 |
| 分析员超时/异常 | 见 `fallback-strategy.md` | — |

---

## 4. 与专家调用的衔接

分析员返回 `should_reply=true` 且过好感闸门、群 CD 后：

1. 组装现有 YAML 人设 + `{user_impressions}` 等占位（暂保持现状）
2. **追加一块决策注入**（仿 Heart oneshot）：

```text
【本轮秘书决策】
话题：{topic}
策略：{reply_strategy}
请按策略回复，不要复述本段。
```

3. 专家用 **同模型完整调用** 生成现有 JSON/`content` 格式回复。

同一条消息路径上最多：**1 次短分析 + 1 次长生成**（召唤强制否决空消息时可为 0 次 LLM）。

---

## 5. Token / 成本预期

| 调用 | 大致规模 |
|------|----------|
| 分析员 | system ~200 token + 近期对话；输出 ~50 token |
| 专家 | 与现 `answer_ai` 同级 |

被动场景：大量消息只入账 → 仅 FAMILIAR/SUMMONED 才分析 → 显著少于「每条 8% 抽中就生成」。

---

## 6. 日志字段（实现时）

```text
group_id, state, should_reply, urgency, topic, silence_reason,
analyst_ms, favor_pass, cd_pass, replied
```
