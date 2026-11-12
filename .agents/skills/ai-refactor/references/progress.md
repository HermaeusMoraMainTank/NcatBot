# AI 改造进度 Checklist

**当前锁定：I1～I6 收拢完成；贴纸待你二校**

## 实现序

- [x] I1 状态机 + 分析员
- [x] I2 可读历史
- [x] I3 记忆
- [x] I4 贴纸
- [x] **I5 配置外置** → `plugins/FakeAi/config.yaml` + `settings.py`（ConfigMixin）
- [x] **I6 供图收拢** → `data/fakeai/stickers/` + `CATALOG.md`（待你二校改名/删留）

## I5 怎么改配置

1. 优先改全局 `config.yaml` 里 `plugin.plugin_configs.FakeAi`
2. 或改 `plugins/FakeAi/config.yaml` 默认值
3. 重启 / 热重载 FakeAi

常用键：`allowed_groups`（空=不限）、`aliases`、`interaction.*`、`cognition.*`、`expression.*`、`admin_ids`

## I6 贴纸

- 源图：`data/fakeai/meme/`
- 已命名副本：`data/fakeai/stickers/`（共 54）
- 清单与场景说明：`data/fakeai/stickers/CATALOG.md`
- 重命名脚本（可再跑）：`data/fakeai/_organize_stickers.py`

## I8 人设重做（2026-11-08）

完整链路梳理见 [fakeai-pipeline.md](fakeai-pipeline.md)。

草稿法人设（lanqing_lab）已删除。诊断与参考项目见
[humanlike-research.md](humanlike-research.md)。

已实现：

- 人设减重：`lanqing_agent.yml` system 约 1.1k 字（原 ~4k），无「不许」清单，也不再保留「最近的生活」流水账。
- 语感样本：`style_corpus.py` + `scripts/build_fakeai_style_corpus.py`，当前 500 条，每轮注入 4 条。
- 回复形态：`_shape_reply`（连续「？」换说法、长句 30% 拆两条）；不做重复末字。
- 闲聊直出（参考 MaiBot Replyer）：普通聊天不经过工具循环，直接生成 1~2 条，并预取 3 条相关记忆；需要插件/识图时仍走 agent。配置 `direct_chat_enabled`。
- 表情补充：自动补图先按回复情绪桶挑，再退回随机。
- 回复策划：直出前一次短调用定情绪/旧事/梗/表情桶，注入 Replyer；`planner_enabled` 可关。
- 记忆具体性：只用真实聊天记录（`_recent_anchor` 上次对话 + 印象/记忆）；不注入虚构的「此刻状态」。
- 节奏：`_reading_delay` + 随机触发退避（15s→300s）。
- 评估：`scripts/eval_fakeai_persona.py`，基线 `data/FakeAi/eval/baseline_20261108.json`。

## 决策日志

| 日期 | 主题 | 结论 | 理由 |
|------|------|------|------|
| 2026-11-07 | 冷场起话 | 移除 | 用户要求删掉主动起话功能；蓝晴只做被动响应，不再由定时任务开口 |
