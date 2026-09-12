# SteamPrice（Steam 价格 / 小黑盒）

无需 API Key，用 Steam 商店 + 小黑盒公开数据查当前价、史低、促销、区价和资料。

## 上游来源（更新时对照）

| 项 | 内容 |
|----|------|
| 原仓库 | https://github.com/penguin-madagascar/astrbot_plugin_steam_price_heybox |
| 本地入口 | `plugins/SteamPrice/SteamPrice.py`、`steam_price.py` 等 |
| 许可证 | MIT（logo 见 NOTICE.md） |

同步上游时优先对照对方 `steam_price.py` / `api_clients.py` / `main.py`。

## 指令

入口：`steam价格`（别名：`小黑盒查价`、`steam查价`）

| 指令 | 说明 |
|------|------|
| `steam价格` | 帮助 |
| `steam价格 [-地区] [--] <目标>` | 当前价 / 史低 / 促销摘要 |
| `steam价格 历史 [-地区] [--] <目标>` | 促销历史 |
| `steam价格 区价 [--] <目标>` | 全球区价 |
| `steam价格 资料 [--] <目标>` | 基础资料 |
| `steam价格 详细资料 [--] <目标>` | 详细资料 |

目标可为游戏名、appid 或 Steam 商店链接。

## 配置

见 `config.yaml`。

名称校正：已接入 `common.utils.AiUtil.search_deepseek`（DeepSeek），在 Steam 首次搜名前校正俗称/拼写/未知中文地区；失败时仍走内置别名与原始名称。
