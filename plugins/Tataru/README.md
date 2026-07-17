# Tataru（塔塔露）

NcatBot 统一 FF14 查询插件，合并原 `Universalis` / `FF14House` / `FF14LogsInfo` / `FF14RisingStoneInfo` 能力。

## 上游来源（更新时对照）

| 项 | 内容 |
|----|------|
| 原仓库 | https://github.com/jawwe/astrbot_plugin_tataru |
| 本地对照副本 | `plugins/Tataru/legacy/tataru_main.py`（浅克隆时同步过） |
| 许可证 | MIT（以原仓库 LICENSE 为准） |

同步上游时优先 diff 对方 `main.py` 与本插件 `engine.py` / `service.py`。

## 指令

发送 `帮帮忙` 查看完整列表。主要指令：

| 指令 | 说明 |
|------|------|
| 暖暖 / 选门 / 仙人彩 / 抽卡 | 本地或轻量查询 |
| 日历 / 攻略 / 招募 / 看看微博 | 活动、攻略、招募板、微博 |
| 物品 / 价格 | 物品与市场物价 |
| 房子 / 房屋 | 空房查询 |
| 输出 / logs | FFLogs 分位与角色战绩 |
| 石之家 … | 帖子/招募/绑定/签到/幻化/部队等 |
| 石之家 玩家 角色名 [服务器] | 原 RisingStone 玩家信息卡 |

## 配置

见同目录 `config.yaml`，或全局 `plugin.plugin_configs.Tataru`。

常用项：`weibo_cookie`、`fflogs_client_id` / `fflogs_client_secret`、`font_path`、`risingstones_owner_curl`。

石之家玩家卡仍读取 `data/txt/cookie.txt`（与旧版一致）。

## 旧插件

以下插件的 `manifest.toml` 已重命名为 `manifest.toml.disabled`，避免命令冲突：

- Universalis
- FF14House
- FF14LogsInfo
- FF14RisingStoneInfo

需要恢复时把文件名改回即可。
