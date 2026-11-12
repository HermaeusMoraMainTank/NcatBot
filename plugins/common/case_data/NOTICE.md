# 数据来源与许可

本目录（`plugins/common/case_data/`）以及 `CaseOpening` 插件（CS2 与 Dota 两个子模块）使用的数据：

| 数据 | 来源 | 许可 |
|---|---|---|
| CS2 箱子 / 皮肤 / 钥匙 / 收藏品 / 图片 | [ByMykel/CSGO-API](https://github.com/ByMykel/CSGO-API) | MIT |
| Dota 2 宝瓶内容 / 递增概率 / 图标 | [Liquipedia Dota 2](https://liquipedia.net/dota2/Treasure)（游戏内公示表转录） | CC-BY-SA 3.0 |
| CS2 / Dota 2 实时价格 | [Skinport](https://skinport.com) 公共接口（CNY） | 仅作行情参考，本地缓存用于渲染 |
| Dota 2 宝瓶国服售价 | 游戏内商城人工核对（2026-11-06） | — |
| CS2 五档概率 | Valve 公示 79.92 / 15.98 / 3.20 / 0.64 / 0.26 | — |

- Liquipedia 内容以 **CC-BY-SA 3.0** 提供，机器人与帮助文本需标注"数据来源 Liquipedia"。
- 图片仅缓存到本地用于卡片渲染，不对外再分发。
- StatTrak ≈ 10% 为社区统计值，Valve 未公开；卡片与帮助中已标注。
- 汰换合同细则（10 件、收藏品加权、float 公式）来自 Counter-Strike Wiki，属社区确认的游戏机制。

快照重建：`python scripts/build_case_data.py`（`--refresh` 忽略缓存）。
