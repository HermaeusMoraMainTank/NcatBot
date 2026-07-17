# Phase 3 · 贴纸入库扩展点（默认关闭）

> 已确认：P3 **只做人工目录**；此处仅定接口，便于日后接 LLM / 管理命令，避免改发送链。

---

## 1. 开关

```text
expression.ingest_enabled = false   # 默认
```

为 false 时：不注册工具、不暴露管理入库命令（若有也直接拒绝）。

---

## 2. 接口形状（未实现）

```text
collect(emotion: str, source: str) -> IngestResult

IngestResult:
  status: ok | dedup | reject | disabled
  path?: str
  reason?: str
```

| 参数 | 含义 |
|------|------|
| `emotion` | 贴纸名（将作为 `:emotion:`） |
| `source` | 本地路径 / `file://` / `http(s)` URL |

### 行为草案（启用后）

1. 下载或读取图片 → 校验类型与大小上限（如 2MB）。  
2. dHash（或 perceptual hash）查 `.ingest_hash_index.json`；命中 → `dedup`。  
3. 写入 `catalog_path`：首次文件 `名.ext`；同名再入则升为文件夹并编号 `(2)`…（对齐 Smile）。  
4. 刷新内存 catalog。  
5. **不**自动改扩展名为 WebP（保持原格式或可配）。

### 调用方（远期）

- LLM tool `sticker_collect`（需另开权限与提示词约束）  
- 管理员命令：`蓝晴收表情 坏笑` + 引用图片  

---

## 3. 当前收拢方式（非本接口）

日常补库存走 **人工批量**：用户交图 → Agent 写入 catalog 目录并起名 → 用户二校。  
见 `expression-catalog.md` §4。与本 `collect()` 扩展点独立。

## 4. 明确不做（当前）

- 模型自动看到图就入库  
- 从 `plugins/Meme` 渲染结果入库  
- WebUI 管理面板  

---

## 4. 与发送链解耦

`sticker_replace` **只读 catalog**；`sticker_ingest` 只写磁盘 + 刷新 catalog。二者不循环依赖。
