# Phase 3 · 贴纸目录与 Meme 边界

> FakeAi `expression/` 对标 Angel Smile；与 `plugins/Meme`（meme-generator 制图服务）**分流**。

---

## 1. 职责对照

| | FakeAi 贴纸（Smile） | `plugins/Meme` |
|--|---------------------|----------------|
| 触发 | AI 回复正文里的 `:贴纸名:` | 用户命令词（来自 `memeKeys.json`） |
| 素材 | 本地静态图库存 | 远端 `127.0.0.1:2233` 按模板渲染 |
| 谁选图 | 模型按情绪点名 | 用户打关键词 + 可选 @/图 |
| FakeAi 侧 | 发送前替换 | **继续排除**：命中 meme 关键词不进 AI 闲聊 |

禁止：把制图服务结果自动灌进贴纸库；禁止用贴纸目录取代 meme 命令。

---

## 2. 目录约定

根路径（可配 `expression.catalog_path`）：

```text
data/fakeai/stickers/
├── 坏笑.webp
├── 无语/
│   ├── 无语.webp
│   ├── 无语(2).webp
│   └── 无语(3).webp
└── .ingest_hash_index.json   # 仅入库扩展用；不进可用列表
```

### 扫描规则（对齐 Smile，略放宽格式）

| 规则 | 说明 |
|------|------|
| 根目录文件 | 文件名（无扩展名）= 贴纸名 |
| 一级子目录 | 目录名 = 贴纸名；内为变体，发送时 **随机抽一张** |
| 同名冲突 | **文件夹优先**于根目录同名文件 |
| 允许扩展名 | `.webp` `.png` `.jpg` `.jpeg` `.gif`（不强制转 WebP） |
| 忽略 | 点开头名、非图片、二级以上深目录、`.ingest_hash_index.json` |

启动/热重载时扫描生成 `catalog: dict[str, list[Path]]`。

---

## 3. 注入专家 prompt

在人设后追加短块（列表过长则截断）：

```text
【可用贴纸】需要表情时在正文插入 :贴纸名: （系统会另发一条图片，不要指望和文字同一条）。
每轮最多 1 个。可用：坏笑、无语、鼓掌、…
没有合适的就不要强行加。
```

- 名称按字典序；总长建议 ≤500 字，超出则只列高频/手工 `priority` 列表（配置可选）。
- 分析员短 prompt：**不**注入贴纸列表。

---

## 4. 人工收拢流程（已确认；无自动入库时）

在未开启 LLM/命令自动入库前，采用 **批量代整理 + 人工二校**：

```text
1. 用户提供一批图片（对话附件 / 指定文件夹）
2. Agent 扫图、起贴纸名、写入 data/fakeai/stickers/（单图或变体文件夹）
3. 生成「名 ↔ 文件」清单给用户
4. 用户人工二次修改：改名、删废图、合并变体、补漏
5. 重载/再扫描 catalog 后生效
```

约定：

- Agent 起名优先短中文情绪词（坏笑、无语、鼓掌…），避免哈希文件名。
- 明显重复图可先归同一文件夹变体，或标在清单里请用户裁夺。
- **不**替代用户最终审美；Agent 只做初收拢。
- 此流程写进 Phase 3/4 实现备忘；与 `ingest_enabled` 无关。

---

## 5. 配置项（概念）

```text
expression.enabled = true
expression.catalog_path = data/fakeai/stickers
expression.max_per_message = 1    # 每轮回复最多贴纸张数（每张单独一条消息）
expression.ingest_enabled = false
```
