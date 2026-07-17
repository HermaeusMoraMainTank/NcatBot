# Phase 2 · remember / recall 工具协议

> 对标 Angel Memory 的 `angel_remember` / `angel_recall`，收窄为 2 个工具。  
> `note_read` / `note_create`：**本阶段不做**（记入推迟）。

---

## 1. 何时挂载工具

| 场景 | 是否暴露工具 |
|------|----------------|
| 分析员短调用 | **否** |
| 专家正文生成 | **是**（`cognition.tools_enabled=true` 时） |
| 命令查询（印象/排行） | 否 |

工具调用失败不得阻断回复：忽略工具结果，只发文本。

---

## 2. `remember`

### 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `judgment` | string | 是 | ≤120 字铭记句 |
| `reasoning` | string | 否 | ≤200 |
| `tags` | string[] | 否 | 每项 ≤16 字，最多 8 个 |
| `strength` | number | 否 | 默认 60；钳制 1～100 |
| `memory_type` | string | 否 | `knowledge`\|`preference`\|`episode`\|`relation` |
| `scope` | string | 否 | `public`\|`user:<id>`\|`group:<id>`；默认按对话推断 |

### 行为

1. 规范化 `judgment`（去首尾空白、合并空白）。  
2. 若存在 active 且规范化文本相同 → **加强** strength（+10，封顶 100），不插新行。  
3. 否则 INSERT `memory_record`。  
4. 返回：`{ "ok": true, "id": N, "action": "create"|"reinforce" }`

### 专家侧提示（节选）

```text
仅在用户明确说过的长期事实/偏好时调用 remember。
不要记闲聊、一次性行程（除非用户强调「记住」）。
不要把好感度数值写进记忆；好感由系统维护。
```

---

## 3. `recall`

### 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | 否 | 检索词；空则按 scope 取 strength 最高 |
| `limit` | number | 否 | 默认 5，最大 8 |
| `scope_hint` | string | 否 | 优先桶 |

### 行为

1. 候选：`active=1`，scope ∈ { user 当前, group 当前, public }。  
2. 若有 query：judgment/tags/reasoning LIKE；否则按 strength DESC。  
3. 更新 `last_recalled_at`、`recall_count`。  
4. 返回精简列表：

```json
{
  "items": [
    {
      "id": 1,
      "judgment": "…",
      "tags": ["…"],
      "strength": 72,
      "scope": "user:123",
      "memory_type": "preference"
    }
  ]
}
```

框架也可在专家调用 **前** 自动 `recall(topic)` 一次注入，减少模型漏调；工具仍保留给「追问式」回忆。

---

## 4. 自动注入 vs 工具

| 机制 | Phase 2 建议 |
|------|----------------|
| 专家前自动 recall(topic) | **开**；limit=3 |
| 专家可再 recall | **开** |
| 每轮强制 remember | **关**；由模型决定 |
| 后台从对话抽取记忆（小模型） | **推迟**；先靠工具 + 现有印象任务 |

---

## 5. 安全与配额

- 单轮最多 `remember` **2** 次、`recall` **2** 次。  
- `judgment` 含明显隐私（身份证号等）→ 拒绝并返回 ok=false（简单正则即可）。  
- 管理员命令可列/删 memory（实现阶段再定命令名，避免与「蓝晴知识库」混淆）。

---

## 6. 与好感度边界（重申）

- remember **不**改 `favorability_score`。  
- 关系标签 `relation_tag` 仍由印象流程/专用逻辑维护，不经 remember 乱写（除非日后单独 `memory_type=relation` + 白名单校验）。
