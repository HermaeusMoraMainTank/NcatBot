# Docs/Code 一致性审计报告

> 生成：`check_doc_consistency.py --json`
> 修复日期：2026-03-24

## 修复前后对比

| 指标 | 修复前 | 修复后 | 降幅 |
|------|--------|--------|------|
| broken_link | 522（实际 123） | **2**（均为 `.agents/` 内部文件） | 98% |
| missing_readme | 7 | **0** | 100% |
| missing_from_index | 18 | **1**（新建 `misc/README.md`） | 94% |
| unlabeled_code_block | 26 | **0** | 100% |
| loc_exceeded | 11 | 11（仅报告，非错误） | — |

## 已执行修复

### 1. 脚本修正（`check_doc_consistency.py`）

- 修正 `_normalize_link_target`：先剥离 `<>`，再去 `#anchor`，并对 `<file.md>#anchor` 格式做二次剥离
- 新增 `_iter_link_matches`：跳过 Markdown 代码块中的链接匹配
- 扩展 `TEMPLATE_LINK_PATTERNS`：过滤 `<file.md>`、`"path"` 等占位示例

### 2. P0 断链修复

| 修复类型 | 涉及文件 | 修复方式 |
|----------|---------|---------|
| 绝对路径 → 相对路径 | `guide/11. 架构与概念/2. 核心概念.md`（16 处） | `reference/2. 适配器/` → `../../reference/7. 适配器/` 等 |
| 旧英文文件名 → 中文 | `guide/3. 插件开发/README.md`、`guide/9. 测试指南/README.md` | `1.quick-start.md` → `<1. 快速开始.md>` 等 |
| %20 编码 → VuePress `<>` | `reference/9. 测试框架/`、`guide/9. 测试指南/`、`guide/2. 适配器/` | `2.%20测试工具.md` → `<2. 测试工具.md>` |
| 错误目录前缀 | `reference/7. 适配器/README.md` | `guide/adapter/` → `<../../guide/2. 适配器/>` |
| 多余 `reference/` 前缀 | `reference/1. Bot API/6. Misc/1. API.md` | `../../reference/8. 工具模块/` → `../../8. 工具模块/` |
| 错误章节编号 | `reference/README.md` | `9. 测试指南/` → `9. 测试框架/` |
| Skill 内部相对路径 | `.agents/skills/docs-sync/references/checks.md` | `./../` → `../../` |

### 3. 补齐缺失 README（7 个目录）

- `docs/docs/notes/misc/`
- `docs/docs/notes/reference/1. Bot API/1. 通用/`
- `docs/docs/notes/reference/1. Bot API/2. QQ/`
- `docs/docs/notes/reference/1. Bot API/3. Bilibili/`
- `docs/docs/notes/reference/1. Bot API/5. AI/`
- `docs/docs/notes/reference/1. Bot API/6. Misc/`
- `docs/docs/notes/reference/10. CLI/`

### 4. Code↔Docs 对齐

- `ncatbot/utils/__init__.py`：将 `AdapterEventError` 加入 `__all__` 并显式 import
- `reference/8. 工具模块/README.md`：
  - Quick Reference 补充多行 import 示例
  - 新增「其他导出」表格：`ConfigValueError`、`Status/status`、`async_*` 网络函数、`is_interactive/set_interactive` 等 12 个符号

### 5. 代码块语言标注

使用 `fix_code_blocks.py --apply` 自动标注 26 个未标注代码块：
- text: 17、python: 5、markdown: 2、bash: 2

## 剩余事项

### 仍存在的 2 条断链（`.agents/` 内部，P2）

| 文件 | 说明 |
|------|------|
| `.agents/skills/docs-sync/reports/2026-03-19_full-audit.md` | 旧报告中的历史引用 |
| `.agents/skills/docs-sync/SKILL.md` | YAML frontmatter 描述文本中的示例路径 |

### loc_exceeded（仅报告，11 个文件）

超过 400 行的文件均为参考文档或架构总览，内容本身完整，无需强制拆分。

### Phase B — guide ↔ reference 对齐

未做自动化逐段 diff。建议后续按主题域（插件 / 消息 / Bot API / 配置 / RBAC / CLI / 测试 / 多平台）做人工抽样对照。
