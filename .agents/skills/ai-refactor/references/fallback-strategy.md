# Phase 1 · 过渡期 Fallback 策略

> 已确认：终态 **取消裸 8%**；过渡期可短暂保留 fallback。

---

## 1. 什么是 Fallback

当 **本应能被动插话的路径** 无法走分析员时，用极低概率随机决定是否调用专家——**仅临时**，不是第三套长期逻辑。

---

## 2. 触发条件（须同时满足）

1. `analyst.fallback_random_prob > 0`
2. 分析员路径不可用，满足其一：
   - `analyst.enabled = false`
   - 分析员调用超时 / 抛错 / 连续 N 次 `parse_error`
3. 状态为 `FAMILIAR`（**不要**在 `NOT_PRESENT` 对每条消息抽卡）  
   - 若连 FAMILIAR 检测都未上线：过渡期允许「热闹启发未就绪」时对普通消息用 **更低** 的 `fallback_legacy_prob`（建议 ≤0.03），并打 `WARN` 日志
4. 好感闸门通过
5. 群 CD 通过
6. 不在 `OBSERVATION`

`SUMMONED`：**禁止**用随机 fallback 代替回复；分析员挂了则：
- `force_reply_when_summoned=true` → 跳过分析员直接专家（策略用默认句「正常友好地回应用户」）
- 否则可简短本地回复「我愣了一下，再说一次？」——**实现阶段再定**，文档倾向 **直接专家**。

---

## 3. 建议配置时间线

| 阶段 | `fallback_random_prob` | 说明 |
|------|------------------------|------|
| 刚接上状态机、分析员未稳 | `0.03`～`0.05` | 仅 `FAMILIAR` |
| 分析员稳定 1～2 周 | `0.01` | 收紧 |
| 终态 | `0` | **关闭**；只靠状态机+分析员 |

旧常量 `PASSIVE_TRIGGER_BASE_PROB = 0.08`：**删除或恒等于 fallback 配置**，禁止两处各抽一次。

---

## 4. 日志与可观测

Fallback 触发必须打：

```text
[FakeAi] fallback used reason=analyst_timeout group=… prob=…
```

便于确认「过渡期」长度；若 fallback 占被动回复 >30%，优先修分析员而非加大概率。

---

## 5. 关停清单（终态）

- [ ] `fallback_random_prob = 0`
- [ ] 代码路径无 `random` 被动分支（或死代码删除）
- [ ] 文档与配置注释标明「已关」
