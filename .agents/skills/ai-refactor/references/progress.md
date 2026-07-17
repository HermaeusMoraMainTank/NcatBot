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
