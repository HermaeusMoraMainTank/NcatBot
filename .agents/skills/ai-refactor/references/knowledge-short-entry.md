# Phase 2 · 短条目知识规范

> 采纳 Angel Memory 哲学：**知识库不是长文档 RAG**。  
> 服务对象：`knowledge_base` 写入、人工整理、`蓝晴知识库` 展示文案。

---

## 1. 什么算一条「好知识」

| 规则 | 要求 |
|------|------|
| 长度 | **正文 ≤100 字**（硬建议；写入时可 warn，巩固任务统计超标） |
| 结构 | 一句结论或「标题级事实 + 半句补充」 |
| 关键词 | `keyword` 3～16 字，便于 LIKE；避免整句当 keyword |
| 来源 | 记录 `source_user_id` / username（已有字段） |
| 禁止 | 整页攻略、长日志、多主题混杂 |

**好例子**

```text
keyword: 宫保鸡丁
content: 鸡丁腌制→花生炸香→鸡丁变色→干辣椒花椒→宫保汁收汁→花生。汁=酱油2+醋1+糖1+淀粉水
```

**坏例子**

```text
keyword: 川菜
content: （整章川菜大全 2000 字）
```

---

## 2. 写入来源与规则

| 来源 | 规则 |
|------|------|
| 现有印象流程产出的 `new_knowledge` | 入库前截断至 100 字；一条一条存 |
| 未来 `remember` 工具 | `memory_type=knowledge` 可同时（或仅）写入 memory_record；是否双写 knowledge_base 由实现开关控制，默认 **只写 memory_record** |
| 人工 / 管理命令（若有） | 同上长度 |

---

## 3. 检索与注入（专家侧）

1. 用用户本轮文本 / 分析员 `topic` 做 keyword/content LIKE。  
2. 最多取 **3** 条，总注入 ≤300 字。  
3. **不要**把「知识库全量」或「最近 5 条完整行」无筛选塞进 prompt。  
4. `蓝晴知识库` 命令：仍可展示统计与样本，但展示时截断 content，避免刷屏。

---

## 4. 文件夹/人工作业（可选，远期）

若以后支持从 `data/fakeai/knowledge/*.md` 导入：

- 用 `## 小标题` 切条  
- 每节 ≤100 字  
- 导入进 `knowledge_base` 或 `memory_record`  

**Phase 2 不实现文件监控**；本节约定避免以后走弯路。

---

## 5. 与记忆桶分工

| 类型 | 放哪 |
|------|------|
| 「用户 A 不吃香菜」 | `memory_record` scope=`user:A`，type=`preference` |
| 「本群周五开黑」 | `memory_record` scope=`group:G`，type=`episode` |
| 「宫保鸡丁做法卡片」 | `knowledge_base` 或 memory `public`+`knowledge` |
| 「对用户的整体脾气印象」 | 继续 `user_impression.impression`（不要拆碎进知识库） |
