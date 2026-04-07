# 工作记录模板

每次框架变更完成后，在 `memory/` 目录创建工作记录文件。

**文件命名**：`memory/YYYY-MM-DD_简要描述.md`

**模板**：

~~~markdown
# [日期] 简要标题

## 变更类型
fix / feat / refactor / docs

## 内容
- 问题/需求描述
- 改动的文件和原因

## 四位一体
- Code: path/to/file.py
- Test: tests/xxx/test_yyy.py
- Docs: docs/docs/notes/guide/xxx, docs/docs/notes/reference/yyy
- Skill: .agents/skills/xxx（如适用）

## 备注
~~~
