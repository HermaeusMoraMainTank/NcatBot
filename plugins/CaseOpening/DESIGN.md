# CS2 开箱 / Dota 2 宝瓶 双插件设计文档

> 状态：**已实现**（M1–M7 完成，19 个单元测试通过）
> 版本：v28（2026-11-11：重生与借贷卡片补人民币）
> 目标机器人：NcatBot 5.x（合并插件 `plugins/CaseOpening/`：`cs2.py` + `dota.py` + 共享借贷）
> 数据原则：**全部来自可验证的真实数据源**；拿不到的部分明确标注，绝不编造
> 调研日期：2026-11-05 ~ 11-06（所有 HTTP 状态码为本机实测）

---

## 0. TL;DR

| 问题 | 结论 |
|------|------|
| CS2 物品/箱子/钥匙/概率有免费 API 吗 | **有**。`ByMykel/CSGO-API` 提供完整箱子、皮肤、收藏品、钥匙 JSON，含每个箱子的具体内容与稀有度分层 |
| CS2 实时价格有免费源吗 | **有**。`api.skinport.com` 提供 730/570 双端 **CNY** 批量报价（37,228 / 27,635 条），免 key |
| Dota 2 宝瓶内容与概率有 API 吗 | **没有直接 API**，但 **Liquipedia MediaWiki API 有逐瓶完整数据**：内容分层 + 逐次开启的递增概率表（1~50 次）+ 是否可交易 |
| Dota 2 宝瓶物品价格 | `skinport 570` 有 CNY 报价（普通套装 ¥0.2 级），但 **2024–2026 可购买的宝瓶全部不在任何公开市场有价**（连 Steam 市场都没有），只能取国服售价 |
| Dota 2 宝瓶售价 | 已人工核对 13 个瓶：**¥17（金龙/蛇年/灵幻奇观）、¥21（其余 9 个）、¥35（惊世奇珍）** → 170 / 210 / 350 金币 |
| Steam 社区市场 API 能直连吗 | **本机不能**（`steamcommunity.com` / `store.steampowered.com` 连接超时）。但 `api.steampowered.com` **可以**（免 key 可调 `IStoreBrowseService`），可惜它不含游戏内饰品价 |
| 真实 CS2 开箱回本率 | 实测（Skinport 中位价 ×0.85 + 钥匙 ¥17.93）：热门箱 **32%–61%**，Spectrum 2 因市场报价异常达 105% → 由护栏自动下架 |

已定参数：**1 元 = 10 金币**；开一个 Kilowatt 箱 **191 金币**，期望回收 **62 金币**，单次净消耗 **129 金币**。全服 2,029 万余额理论可吸收 ≈ 17.6 万次开箱。

---

## 1. 现状：金币确实过剩

`data/GroupEconomy/data/economy.db` 实测（2026-11-05）：

| 指标 | 数值 |
|------|------|
| 账户数 | 355 |
| 总余额 | 20,292,577 |
| 平均余额 | 57,162 |
| 最高余额 | 12,397,900 |
| 累计收入 Top 来源 | `mcfish_sale` 663 笔 / +23,560,528 |
| 签到收入 | 1172 笔 / 仅 +49,631 |

结论：金币超发几乎全部来自 McFish 卖鱼（一次性大额流入），签到（30~60/天）已几乎不构成供给。**需要一个可重复、有正反馈（能开出稀有物）、单次金额足够大**的消耗器——开箱正合适。

设计约束：

1. 消耗必须**可重复**且**单次金额大**（否则对 1,200 万余额的玩家无感）。
2. 必须有**真实价值锚点**，否则玩家会发现"假箱子"，消耗也就没有说服力。
3. 必须有**少量正向反馈**（开出刀/手套），否则玩家会直接不玩。
4. 不能在数据上说谎：价格、概率标"来源"和"更新时间"。

---

## 2. 数据源调研（全部实测）

### 2.1 CS2：ByMykel/CSGO-API（主数据源）

`https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/en/`

| 文件 | 大小 | 内容 | 实测 |
|------|------|------|------|
| `crates.json` | 7.23 MB | 481 条（箱子/胶囊/纪念包）：`id/name/type/first_sale_date/market_hash_name/contains/contains_rare/image` | 200 ✅ |
| `skins.json` | 4.75 MB | 2,126 条皮肤（按图案聚合）：`weapon/category/pattern/min_float/max_float/rarity/stattrak/souvenir/collections/crates/image` | 200 ✅ |
| `collections.json` | 1.21 MB | 收藏品 → 皮肤映射（汰换合同必需） | 200 ✅ |
| `keys.json` | 33 KB | 39 把钥匙：`name/description/crates[]` | 200 ✅ |

`crates.json` 共 481 条（箱子 + 胶囊 + 包），类型分布（实测）：`Case 42`、`Souvenir 150`、`Autograph Capsule 139`、`Sticker Capsule 100`、`Souvenir Highlight 14`、`Music Kit Box 13`、`type=null 9`、`Patch Capsule 7`、`Pins 4`、`Graffiti 3`。

单箱结构实测（Kilowatt Case）：

```text
contains      17 条 = Mil-Spec 7 / Restricted 5 / Classified 3 / Covert 2
contains_rare 13 条 = 该箱专属稀有特殊池（Kukri Knife 系列）
first_sale_date 2024-01-16
```

关键点：**稀有特殊池是逐箱不同的**（Fracture Case 52 把、Kilowatt 13 把），必须按箱子存，不能全局共用。

`skins.json` 稀有度分布（实测，与 CS2 六档完全一致）：

| rarity.id | 名称 | 数量 |
|---|---|---|
| `rarity_common_weapon` | Consumer Grade | 200 |
| `rarity_uncommon_weapon` | Industrial Grade | 204 |
| `rarity_rare_weapon` | Mil-Spec Grade | 446 |
| `rarity_mythical_weapon` | Restricted | 311 |
| `rarity_legendary_weapon` | Classified | 183 |
| `rarity_ancient_weapon` | Covert | 687 |
| `rarity_ancient` | Extraordinary（刀/手套） | 94 |
| `rarity_contraband_weapon` | Contraband | 1 |

`keys.json` 实测要点：
- `CS:GO Case Key` 覆盖 4 个老箱（"This key will open any Valve Series Weapon Case"）。
- 其余 38 把是**逐箱专用**：`Fracture Case Key`、`Kilowatt Case Key`、`Gallery Case Key`、`Fever Case Key` … 全部 `crates` 只有 1 个。
- 因此"买钥匙"必须做成"箱子 → 钥匙"查表，不能一把通用钥匙开所有箱。

### 2.2 Dota 2：Liquipedia MediaWiki API（主数据源）

`https://liquipedia.net/dota2/api.php`（**必须** `--compressed`，必须自定义 UA；否则 406）

单瓶页面同时提供四类数据（实测 `Immortal Treasure I 2022`）：

1. **元信息**：`defindex / name / prefab=Treasure / rarity / releasedate / availability / purchasable / tradeable / marketable`
2. **内容分层**：`Regular` / `Rare` / `Very Rare` / `Ultra Rare` / `Cosmically Rare`，每条是 `{{Cosmetic|物品名}}`，渲染后 HTML 里有 `alt=物品名` + `img src=/commons/images/...`
3. **递增概率全表**（官方披露，1~50 次逐行给概率）
4. **回收规则**：如"任意 4 件可回收再得 1 瓶 + 1 个糖果袋"

实测数据节选（Immortal Treasure I 2022 的官方披露表）：

| 第 N 瓶 | Rare | Very Rare | Ultra Rare |
|---|---|---|---|
| 1st | 1:2000（0.05%） | 1:20000（0.005%） | 1:100000（0.001%） |
| 5th | 1:51（1.96%） | 1:276（0.36%） | 1:2303（0.043%） |
| 10th | 1:10.4（9.61%） | 1:56（1.78%） | 1:464（0.17%） |
| 20th | 1:2.6（38.46%） | 1:12.7（7.87%） | 1:105（0.95%） |
| 40th | 1:1.0（**100%**） | 1:3.3（30.30%） | 1:26（3.84%） |
| 50th | 1:1.0（100%） | 1:2.2（45.45%） | 1:17（5.88%） |

Cosmically Rare 不递增，固定 **1/250,000（0.0004%）**。

即"宝瓶保底"= **每个稀有度档位独立计数，命中即清零；Rare 档在第 40 瓶为 100% 保底**。表格逐瓶不同，必须按瓶存表，不能用一个公式凑。

其他实测规则（Liquipedia `Treasure` 页）：
- 内容按稀有度分层，"物品自身稀有度不影响掉落概率"。
- **重复保护**：同一宝瓶连续开启必出不同物品；未收集齐前不会重复；**Rare / Very Rare / Extremely Rare 不受此保护**。
- 开出的物品 **7 天内不可交易/上架**，但可赠送一次；Extremely Rare 不受限。
- Dota Plus 可把宝瓶奖励回收为**碎片**，同一宝瓶 **6 碎片 → 1 瓶 + 2,000 碎片币**。
- 新式宝瓶（如 `Spring Treasure 2025`）：`availability = Dota Plus, Shard Shop`、`marketable = no`、只有 Regular 档、无递增档位。

Liquipedia 现有 **191 个宝瓶条目**（含加载界面宝箱），可直接批量抓。

### 2.3 价格：Skinport（主）/ ByMykel price tracker（备）

`https://api.skinport.com/v1/items?app_id=730|570&currency=CNY`

| 项 | 实测 |
|---|---|
| 认证 | 免 key，但**必须** `Accept-Encoding: br, gzip`（只发 gzip 会 406："Client must support Brotli compression"） |
| CS2 (730) | 200，2.37 MB 压缩，**37,228** 条 |
| Dota 2 (570) | 200，1.69 MB 压缩，**27,635** 条 |
| 字段 | `market_hash_name / currency / suggested_price / min_price / max_price / mean_price / median_price / quantity / item_page / market_page / updated_at` |
| 刷新 | `updated_at` = 请求时刻（逐分钟级） |

实测价格（CNY，`min_price`，2026-11-05）：

| 物品 | 价格 | 在售量 |
|---|---|---|
| Kilowatt Case | ¥1.16 | 11,671 |
| Fracture Case | ¥2.31 | 30,628 |
| Gallery Case | ¥3.93 | 21,618 |
| Fever Case | ¥3.47 | 37,547 |
| CS:GO Case Key（旧版可交易） | ¥43.68 | 119 |
| AK-47 \| Redline (Field-Tested) | ¥167.16 | 559 |
| StatTrak™ AK-47 \| Redline (FT) | ¥384.31 | 101 |
| ★ Karambit \| Fade (Factory New) | ¥10,784.49 | 20 |
| ★ Sport Gloves \| Pandora's Box (FT) | ¥19,673.99 | 15 |

Dota 2 侧实测（CNY，`min_price`）：

| 物品 | 价格 | 在售量 |
|---|---|---|
| Immortal Treasure I 2022（宝瓶本体） | ¥6.55 | 14 |
| Immortal Treasure II 2022 | ¥21.57 | 16 |
| Crownfall Treasure I | ¥8.17 | 1 |
| Infernal Cavalcade（瓶内普通件） | ¥0.23 | — |
| Blastmitt Berserker Bundle（Rare 档） | ¥3.54 | — |
| Golden Infernal Cavalcade（Very Rare 档） | ¥0.85 | 51 |
| Spring Treasure 2025（Dota Plus 瓶） | **无价** | 0 |

> 上面 Dota 条目是历史瓶，仅用于证明"老饰品有真实市场价"；本次上架范围是 2024–2026 的 13 个可购买瓶，它们**没有市场价**（见 §6.0.1）。

**注意**：`Fracture Case Key` 在 Skinport **查不到**——新版钥匙游戏内购买后不可交易，市场无货。这符合真实规则，钥匙价必须走 Valve 定价而非市场价。

备用源 `ByMykel/counter-strike-price-tracker/static/latest.json`：200，USD 分单位，34,500 条，但 `metadata.updated_at = 2026-08-08`（**已停更 3 个月**），只能当离线兜底，不能当主源。

### 2.4 图片（全部实测 200）

| 用途 | URL 模式 | 实测 |
|---|---|---|
| CS2 皮肤图 | `https://community.akamai.steamstatic.com/economy/image/<hash>`（来自 `skins[].image`） | 200 `image/png` 189,930 B |
| CS2 收藏品图标 | `https://cdn.steamstatic.com/apps/730/icons/econ/set_icons/...`（来自 `collections[].image`） | 200 |
| Dota 游戏内图标 | `https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/items/<name>.png` | 200 `image/png` 12,674 B |
| Dota 饰品/宝瓶图标 | `https://liquipedia.net/commons/images/<a>/<bb>/Cosmetic_icon_<Name>.png` | 200 61,537 B（需 UA） |

Dota 图标获取方式（已实测）：抓宝瓶页面渲染 HTML → 正则取 `alt=物品名` + `src=/commons/images/...`，补全域名即可下载。**不要**用 `Special:FilePath`（403）。

### 2.5 不可用来源（已排除）

| 来源 | 状态 | 影响 |
|---|---|---|
| `steamcommunity.com/market/priceoverview` | 连接超时（本机网络） | 不能用 Steam 市场价，改 Skinport |
| `store.steampowered.com` | 连接超时 | Valve 概率公告页无法程序化抓取（数值仍引用该公告） |
| `dota2.fandom.com` | 连接超时 | 用 Liquipedia 替代 |
| `liquipedia.net/api-terms-of-use` | 403（需浏览器） | 限速/署名条款按社区惯例执行，实施时人工确认 |
| `Steam WebAPI IEconItems_570/GetSchemaURL` | 404（该接口不存在） | 不能拿 Dota 官方 schema，改用 Liquipedia |
| `IEconItems_570/GetStoreMetaData`（Dota 商店+价格） | **403 Access denied** | 这是唯一含 Dota 游戏内售价的接口，拿不到 |
| `buff.163.com/api/market/goods`（国内最大 CS2 饰品市场） | 200 但返回 `Login Required` | 需登录 cookie，未采用 |
| `api.youpin898.com` / `api.c5game.com` / `api.csfloat.com` / `api.pricempire.com` | 空响应 / 403 / 域名不存在 / 403 | 全部不可用 |
| `steamdb.info` / `dota2.fandom.com` / `csgotrader.app` | 403 / 超时 / 已停 | 弃用 |

### 2.6 Steam 不可达时的替代方案（实测结论）

| 需求 | 有没有替代 | 具体做法 |
|---|---|---|
| CS2 饰品/箱子价 | ✅ 有，且更好 | Skinport CNY 整表（37,228 条），与本模型的金币口径一致 |
| CS2 钥匙价 | ✅ 固定值 | 游戏内 $2.49 是常量，无需抓取；旧版可交易钥匙用 Skinport（`CS:GO Case Key` ¥43.68） |
| Dota 饰品价 | ⚠️ 部分 | 可交易的老饰品走 Skinport 570（27,635 条）；2024–2026 新宝瓶内容多为**不可交易**，本来就没有市场价 |
| Dota 宝瓶游戏内售价 | ❌ 无 | `GetStoreMetaData` 403、Steam 市场无该商品、Skinport 无该商品、Liquipedia 不记价 → **只能人工核对一次** |
| 概率/公告原文 | ✅ 有 | 概率走 Liquipedia（含官方披露表）；Valve 公告仅作引用 |

补充实测：`api.steampowered.com` **本身可达**（`IStoreBrowseService/GetItems` 免 key 返回 200），被墙的只是 `store` / `community` 两个域名。所以"有没有 Steam key"不是问题所在——问题是 Dota 游戏内饰品价格只存在于 `IEconItems_570/GetStoreMetaData`，而它对未授权调用返回 403。

---

## 3. 保真度矩阵（哪些 1:1，哪些有差异）

### CS2

| 机制 | 真实规则 | 本插件 | 保真度 |
|---|---|---|---|
| 五档概率 | 79.92 / 15.98 / 3.20 / 0.64 / 0.26 % | 完全一致，逐箱读 `contains` 分档 | ✅ 1:1 |
| 档内单件概率 | 该档概率 ÷ 该档物品数（均匀） | 完全一致 | ✅ |
| 稀有特殊池 | 逐箱不同（13~65 把） | 读 `contains_rare` | ✅ |
| 箱子价格 | 市场浮动 | Skinport `min_price` → 金币 | ✅ 真实报价 |
| 钥匙 | 游戏内 $2.49，绑定不可交易 | 固定价 `2.49 USD × 汇率`（配置） | ✅ 规则一致 |
| StatTrak | 社区实测约 10%，Valve 未公开 | 10%，卡片标注"社区实测" | ⚠️ 标注 |
| 磨损 float | 开箱在 `[min_float, max_float]` 均匀 | 完全一致 | ✅ |
| 磨损分档 | FN 0–0.07 / MW 0.07–0.15 / FT 0.15–0.38 / WW 0.38–0.45 / BS 0.45–1.00 | 完全一致 | ✅ |
| 汰换合同 | 10 件同稀有度同 StatTrak；按收藏品件数加权选收藏品；`out = avg × (max−min) + min`；刀/手套不可参与；纪念品不可参与 | 完全一致（规则依据 Counter-Strike Wiki，Valve 未公开技术规范） | ✅ |
| 检视图案（Doppler 相位 / Case Hardened 蓝宝） | 影响真实价格，可达数倍 | **不建模**，按基础名定价，卡片显示 `paint_index` 供参考 | ❌ 明确说明 |
| 印花/挂件/命名 | 有 | 不做 | ❌ 明确说明 |
| 租借（`rental`）箱子 | 有 | 不做 | ❌ |

### Dota 2

| 机制 | 真实规则 | 本插件 | 保真度 |
|---|---|---|---|
| 内容分层 | Regular / Rare / Very Rare / Ultra Rare / Cosmically Rare | 完全一致 | ✅ |
| 普通档概率 | 均匀分配剩余概率 | 完全一致 | ✅ |
| 递增概率 | 官方逐瓶表，命中清零 | 按瓶存 1~50 行表，逐用户计数 | ✅ 1:1 |
| 保底 | 表格给出的保底行（如某瓶第 40 瓶 Rare=100%） | 完全一致，逐瓶读表，不写死次数 | ✅ |
| Cosmically Rare | 固定 1/250,000 | 完全一致 | ✅ |
| 重复保护 | 集齐前不重复；Rare+ 不保护 | 完全一致（按用户记录已得物品） | ✅ |
| 交易锁 | 7 天不可交易，可赠送一次 | `tradeable_at = +7d`，赠送命令可选 | ✅ |
| 回收碎片 | Dota Plus：6 碎片 → 1 瓶 + 2000 shards | 6 碎片 → 1 瓶 + 2000 金币（shards 无对应物，映射为金币，配置项） | ⚠️ 改编 |
| 瓶级回收 | 逐瓶不同（如某瓶 4 件换 1 瓶 + 糖果袋） | 快照存 `recycle_rule`，无规则则不支持 | ⚠️ 按瓶适配 |
| 宝瓶获取 | 战令/游戏内商店/Dota Plus 碎片/掉落 | 只上 `purchasable=yes` 的瓶，价格取游戏内售价 | ⚠️ 需人工核价 |
| 宝瓶本体价格 | 多数不可交易，无任何公开报价 | 人工核对表（10 条），标"游戏内售价"与核对日期 | ⚠️ 标注 |
| 累计奖励 | 如 `Trove of Terror` 开 12 瓶送 1 件、40 瓶送 2 件 | 完全一致，按瓶存 `bonus_rewards` | ✅ 1:1 |

---

## 4. 共享数据层：`plugins/common/case_data/`

两个插件共用一次数据抓取与价格缓存，避免双份 7MB JSON。

### 4.1 文件布局（已实现）

```text
plugins/common/case_data/
├── __init__.py
├── models.py          # dataclass：CrateInfo / SkinItem / PriceQuote / Treasure / TreasureItem
├── rules.py           # CS2 概率表、磨损档、汰换公式；Dota 递增表、保底、重复保护
├── prices.py          # Skinport 批量价（br/gzip）+ 磁盘缓存 + TTL
├── images.py          # 图片下载/缓存/清理
├── render.py          # 两个插件共用的卡片渲染
├── snapshot.py        # 快照构建与加载（含 Liquipedia 解析）
└── data/              # 随仓库分发的快照（约 340 KB）
    ├── cs2.json.gz            # 10 个箱子 + 2126 条皮肤
    ├── dota.json.gz           # 13 个宝瓶（内容 + 概率表 + 累计奖励）
    ├── prices_730.json.gz     # CS2 价格兜底（4923 条）
    └── prices_570.json.gz     # Dota 价格兜底（60 条）

plugins/CaseOpening/
├── manifest.toml  CaseOpening.py      # 生命周期 / 经济 / 借贷 / 统一帮助 / 分发
├── cs2.py  store_cs2.py               # CS2 模块
├── dota.py  store_dota.py             # Dota 模块
└── config.py                          # 合并配置（cs2_* / dota_* 前缀）

scripts/build_case_data.py    # 重新生成快照（--refresh / --skip-prices）
tests/unit/plugin/test_csgo_case.py      # CU-01 ~ CU-10
tests/unit/plugin/test_dota_treasure.py  # DU-01 ~ DU-09
```

运行时缓存（gitignore）：`data/CsgoCase/data/{csgo_case.db, images/}`、`data/DotaTreasure/data/{dota_treasure.db, images/}`。

### 4.2 快照字段（归一化后）

```jsonc
// cs_crates.json 单条
{
  "id": "crate-4613", "name": "Kilowatt Case", "type": "Case",
  "def_index": "4613", "first_sale_date": "2024-01-16",
  "market_hash_name": "Kilowatt Case", "image": "https://...",
  "key": "Kilowatt Case Key",
  "tiers": {"Mil-Spec Grade": ["skin-..."], "Restricted": [...],
            "Classified": [...], "Covert": [...]},
  "rare": ["skin-...", "..."]
}

// dota_treasures.json 单条
{
  "name": "Trove of Terror", "defindex": 34454,
  "releasedate": "2026-10-06", "availability": ["In-game Store Purchase"],
  "purchasable": true, "marketable": false, "tradeable": false,
  "contents": {"Regular": ["Stoneback Sovereign", "..."],
               "Rare": ["Curse of the Wyrmbender", "Mesmer-Eye"],
               "Very Rare": ["Hell's Legion"],
               "Ultra Rare": ["Sepulchral Stalker", "Maw of the Megalodon"]},
  "icons": {"Curse of the Wyrmbender": "https://liquipedia.net/commons/images/.../Cosmetic_icon_Curse_of_the_Wyrmbender.png"},
  "odds": {"Rare": [0.0005, 0.0017, 0.0053, "..."],   // 1~50 行，按表头映射，列数可变
           "Very Rare": [0.00005, "..."],
           "Ultra Rare": [0.00001, "...", 1.0]},
  "bonus_rewards": [{"opens": 12, "items": ["Obscene Eye Ball"]},
                    {"opens": 40, "items": ["R'shan the Eternal", "Baby R'shan the Eternal"]}],
  "recycle_rule": {"inputs": 6, "output": 1},
  "unlock_marketable_at": null,
  "source": "https://liquipedia.net/dota2/Trove_of_Terror",
  "fetched_at": "2026-11-05T10:00:00+08:00"
}
```

### 4.3 价格策略

| 项 | 规则 |
|---|---|
| 主源 | Skinport `app_id=730/570`，`currency=CNY`，每 6 小时刷新（带 `If-None-Match`） |
| 取值 | 买价用 `min_price`；回收价用 `median_price × (1 − 手续费)`（`median` 缺失回退 `min`）；`quantity=0` 视为无市价 |
| 缺价兜底 | ① 同图案其他磨损中位数 × 1.15；② ByMykel USD 快照 × 汇率；③ 该物品禁止上架，仅可收藏 |
| 无价物品 | Dota 不可交易套装、Doppler 相位 → 明确显示"无市价"，只能保留/回收 |
| 限流 | Skinport ≤ 1 次/10 分钟（整表拉取）；图片 ≤ 5 并发，单域名 ≤ 5 req/s |
| 版权 | 图片只做本地缓存用于渲染，不对外分发 |

### 4.4 数据库（每插件独立 SQLite，WAL）

```sql
-- 共用语义
inventory(id, user_id, group_id, app_id, item_key, display_name,
          rarity, stattrak, wear, float_value, paint_index,
          acquired_at, price_cny_at_acquire, tradeable_at, marketable, source_tx)

case_tx(id, user_id, kind, crate_key, cost, result_item_id, created_at)   -- 开箱流水（无库存表）
market_tx(id, user_id, item_id, side, price, fee, created_at)             -- 买卖流水
dota_pity(user_id, treasure_key, tier, counter, updated_at)               -- 递增计数
dota_collected(user_id, treasure_key, item_name, first_at)                -- 重复保护
dota_bonus(user_id, treasure_key, opens, claimed_at)                      -- 累计奖励领取
dota_recycle(user_id, treasure_key, sets, last_at)                        -- 6 件换 1 瓶
```

### 4.5 与经济系统对接

依赖 `GroupEconomy`（与 `McFish`/`Robbery` 同款做法：`get_plugin("GroupEconomy").store.change_balance`）：

| 操作 | reason | 说明 |
|---|---|---|
| 买箱/买钥匙/买宝瓶 | `cs_buy_case` / `cs_buy_key` / `dota_buy_treasure` | 负数，余额不足即失败 |
| 卖物品 | `cs_sell` / `dota_sell` | 正数，扣手续费 |
| 汰换 | `cs_tradeup` | 只消耗物品，不动余额 |
| 回收 | `dota_recycle` | 6 件同瓶物品换 1 瓶，不动余额 |

所有扣费走 `change_balance(...)` 单事务，失败回滚，避免"扣了钱没出物品"。

---

## 5. 插件 A：`CsgoCase`

### 5.0 上架的 20 个箱子（10 个热门 + 10 个高价值）

按 Skinport 在售量（流动性）+ 近两年新箱挑选，数据为 2026-11-05 实测：

| 箱子 | 上线日期 | 箱价 | 成本（+钥匙） | 期望回收 | 回本率 | 单次净消耗 | 在售量 | 稀有池 |
|---|---|---|---|---|---|---|---|---|
| Kilowatt Case | 2024-01-16 | ¥1.16 | ¥19.09 | ¥6.18 | 32.3% | 129 币 | 11,671 | 13 |
| Fever Case | 2025-02-04 | ¥3.47 | ¥21.40 | ¥11.20 | 52.4% | 102 币 | 37,547 | 52 |
| Gallery Case | 2024-07-01 | ¥3.93 | ¥21.86 | ¥12.67 | 58.0% | 92 币 | 21,618 | 13 |
| Fracture Case | 2020-07-13 | ¥2.31 | ¥20.24 | ¥6.84 | 33.8% | 134 币 | 30,628 | 52 |
| Dreams & Nightmares | 2021-11-17 | ¥5.93 | ¥23.86 | ¥11.45 | 48.0% | 124 币 | 58,179 | 50 |
| Recoil Case | 2022-05-30 | ¥1.62 | ¥19.55 | ¥7.26 | 37.1% | 123 币 | 47,894 | 24 |
| Prisma 2 Case | 2020-03-24 | ¥5.93 | ¥23.86 | ¥14.55 | 61.0% | 93 币 | 50,362 | 52 |
| Danger Zone Case | 2018-11-29 | ¥5.78 | ¥23.71 | ¥11.10 | 46.8% | 126 币 | 85,193 | 52 |
| Snakebite Case | 2021-03-26 | ¥2.62 | ¥20.55 | ¥9.95 | 48.4% | 106 币 | 25,756 | 24 |
| Revolution Case | 2023-01-31 | ¥1.46 | ¥19.39 | ¥6.89 | 35.5% | 125 币 | 18,828 | 24 |

回本率算法：档位概率 × 档内均价（按 `float` 落在各磨损区间的概率加权，再把 StatTrak 按 90/10 混合）× 档数，稀有池按 0.26%；回收价用 Skinport **median_price × 0.85**，成本用 **min_price + 钥匙 ¥17.93**。

### 5.0.1 高价值老箱（2026-11-06 实测）

| 编号 | 箱子 | 箱价 | 成本 | 回本率 | 单次净消耗 | 镇箱之物（久经沙场价） |
|---|---|---|---|---|---|---|
| 11 | CS:GO Weapon Case | ¥650.07 | ¥668.00 | 41.3% | 3,819 币 | AK-47 \| Case Hardened ¥1,571 |
| 12 | Operation Bravo Case | ¥365.13 | ¥383.06 | 40.3% | 2,183 币 | AK-47 \| Fire Serpent ¥5,059 |
| 13 | eSports 2013 Case | ¥258.06 | ¥275.99 | 53.6% | 1,283 币 | AWP \| BOOM ¥564 |
| 14 | Operation Hydra Case | ¥161.77 | ¥179.70 | 69.7% | 591 币 | AWP \| Oni Taiji ¥1,609 |
| 15 | Operation Breakout Weapon Case | ¥45.22 | ¥63.15 | 49.8% | 314 币 | — |
| 16 | Huntsman Weapon Case | ¥43.75 | ¥61.68 | 66.9% | 204 币 | AK-47 \| Vulcan ¥1,415 |
| 17 | Operation Riptide Case | ¥38.52 | ¥56.45 | 68.8% | 207 币 | — |
| 18 | Operation Broken Fang Case | ¥31.58 | ¥49.51 | 60.2% | 205 币 | — |
| 19 | Chroma Case | ¥26.58 | ¥44.51 | 59.8% | 175 币 | — |
| 20 | Operation Phoenix Weapon Case | ¥20.26 | ¥38.19 | 60.8% | 149 币 | — |

> **AWP | 龙狙（Dragon Lore）不在任何武器箱里**。它只出现在古堡激战纪念包（Cobblestone Souvenir Package，如 ESL One Cologne 2014/2015、MLG Columbus 2016 等）中，纪念包不需要钥匙、属于另一套容器类型。
> 我们查了 Liquipedia CS 的纪念包页、各站概率文章，**没有任何可核对的纪念包概率表**（Valve 的官方赔率页在本机不可达），所以没有把纪念包塞进来——宁可不做，也不编一档假概率。如果要上，需要你在游戏内打开某个纪念包的"查看概率"抄一次数字，我按同样方式接。

**护栏**：`Spectrum 2 Case` 实测回本率 **104.9%**（市场报价异常，稀有池 60 件均价过高），会被 `max_return = 0.70` 自动下架并记录原因——否则玩家可以无限套利。同理 `CS:GO Weapon Case 2/3`、`eSports 2013 Winter`、`Glove Case`、`Spectrum`、`Gamma` 等 12 个回本率 > 70% 的箱子本次没有上架。

### 5.1 开箱流程

```text
1. 校验：有箱 + 有对应钥匙（或先扣钥匙钱）
2. 稀有度：按 79.92/15.98/3.20/0.64/0.26 抽档
3. 档内：在该箱该档的 n 件中等概率取 1（rare 池同理）
4. StatTrak：可 StatTrak 的物品按 10% 判定（刀可、手套不可）
5. 磨损：float = uniform(min_float, max_float)
6. 磨损名：按 §3 的区间映射
7. 定价：market_hash_name = [StatTrak™ | ★ ]前缀 + 名称 + (磨损)
   查 prices_730，无价则标记"暂无市价"
8. 入库：inventory 写一条，case_tx 写一条，扣费事务已提交
```

`market_hash_name` 拼装已验证的三种形态：

```text
AK-47 | Redline (Field-Tested)
StatTrak™ AK-47 | Redline (Field-Tested)
★ Karambit | Fade (Factory New)
★ Sport Gloves | Pandora's Box (Field-Tested)
★ StatTrak™ Karambit | Doppler (Factory New)
```

### 5.2 汰换合同（价值闭环关键）

```text
输入校验：10 件、同 rarity、同 stattrak、无 souvenir、非刀/手套
收藏品权重：weight[c] = count(inputs from c)
可选收藏品：仅保留"存在下一档物品"的收藏品
选中收藏品后：在其次档物品中等概率取 1
输出 float：avg = Σf/10;  out = avg × (tgt_max − tgt_min) + tgt_min
输出 StatTrak = 输入 StatTrak
```

示例（用真实数据）：10 件 `Mil-Spec`，其中 7 件来自 `The Spy Tech Collection`、3 件来自 `The Arabesque Collection` → 输出 70% 出自前者 `Restricted` 档、30% 出自后者。

### 5.3 指令与对话流

**帮助**

```text
用户: cs帮助
Bot:
【CS2 开箱】
cs箱子 [页码]                 箱子列表（图片 + 中文名 + 编号）
cs详情 <箱子|编号>            内容 / 概率 / 钥匙 / 价格
cs价值表 <箱子|编号> [页]     这个箱子每一件的当前价值
cs开箱 <箱子|编号> [1|10]     买入并立刻开箱，返回产物
cs背包 [页码]                 我的库存
cs物品 <编号>                 磨损 / float / 现价
cs出售 <编号|all>             按市价 ×0.85 回收
cs汰换 <编号×10>              汰换合同
cs汰换预览 <编号×10>          配方与出货概率，不执行
cs查价 <关键词>               市场查价（模糊匹配）
cs战绩                       开箱数 / 最贵掉落
cs榜                          开箱次数排行
```

**开箱**（箱子可用序号，例如 `cs开箱 8 10`）

```text
用户: cs开箱 8 10
Bot:  （卡片图）
      ┌ 千瓦武器箱 ×10 ─────────────────────────┐
      │ ★ 隐秘  AK-47 | Inheritance (久经磨损)   │
      │   磨损 0.2134 · ¥167 → 1,670 币          │
      │ 保密    M4A1-S | Black Lotus (略有磨损)  │
      └──────────────────────────────────────────┘
      千瓦武器箱 × 10：花费 1,920 币
```

**价值表**（每个箱子单独一张，可翻页）

```text
用户: cs价值表 8
Bot:  （卡片图：箱图 + 每件物品图 + 回收价区间）
      8. 千瓦武器箱 · 价值表 · 第 1/2 页 · 共 17 件
```

**汰换**

```text
用户: cs汰换 102 103 104 ... (10个编号)
Bot:  汰换完成：10 × 军规级 → 受限
      选中收藏品：The Spy Tech Collection (7/10 = 70%)
      出货：MP9 | 绯红新星 (略有磨损) 0.1042
      现价 ¥4.99 → 50 币
```

### 5.4 卡片渲染

- 单开：大图卡（物品图 512px、稀有度色条、名称、磨损+float、现价、本期开箱数）
- 十连：3×4 网格，底部汇总"成本 / 市值 / 净变化"
- 没图或下载失败：稀有度色块 + 武器名文字（不阻塞）
- 字体沿用 `GroupEconomy/render.py` 的 `_font()` 候选链，避免跨平台乱码

### 5.5 首版范围外（明确不做，避免"看起来像但不对"）

| 项 | 原因 |
|---|---|
| 纪念包（Souvenir 150 个） | 概率结构与普通箱不同，另有赛事/签名逻辑 |
| 印花胶囊 / 签名胶囊 / 音乐盒 / 涂鸦 | 不属于"开箱"核心，且无磨损机制 |
| 图案相位（Doppler Ruby/Sapphire、Case Hardened 蓝宝） | 价格可差数十倍，但无免 key 的真实分相位报价 |
| 印花贴附、命名牌、挂件 | 不参与本插件的价值循环 |
| Steam 交易冷却 | 本插件是站内市场，不复制 Steam 的 7 天冷却 |

---

## 6. 插件 B：`DotaTreasure`

### 6.0 首批上架的宝瓶（2024–2026 + 仅"可游戏内购买"，价格已核对）

筛选规则：**只上 `purchasable = yes` 的瓶**。`purchasable = no`（TI 宝瓶、现场掉落、加载界面箱、Dota Plus 碎片瓶）一律不上。13 个瓶的国服售价已由你在游戏内核对：

| 宝瓶（英文） | 国服中文名 | 上线 | 获取方式 | 国服售价 | 金币价 | 可交易 |
|---|---|---|---|---|---|---|
| Dragon's Hoard | 金龙宝藏 | 2024-02-08 | 游戏内商店 | ¥17 | 170 | ✅ |
| Crownfall Treasure I | 倾天之战珍藏 | 2024-04-18 | 倾天之战 | ¥21 | 210 | ❌ |
| Crownfall Treasure II | 倾天之战珍藏（第二套） | 2024-04-18 | 倾天之战 | ¥21 | 210 | ❌ |
| Crownfall Treasure III | 倾天之战珍藏（第二幕） | 2024-05-22 | 倾天之战 | ¥21 | 210 | ❌ |
| Crownfall 2024 Collector's Cache | 倾天之战典藏宝瓶 | 2024-07-09 | 倾天之战 | ¥21 | 210 | ❌ |
| Crownfall 2024 Collector's Cache II | 倾天之战典藏宝瓶 II | 2024-10-01 | 倾天之战 | ¥21 | 210 | ❌ |
| Winter 2024 Heroes' Hoard | 2024年隆冬英雄宝藏 | 2024-12-19 | 内购 | ¥21 | 210 | ❌ |
| The Charms of the Snake | 蛇年珍藏 | 2025-01-29 | 内购 | ¥17 | 170 | ❌ |
| Spring 2025 Heroes' Hoard | 2025年新春英雄宝藏 | 2025-05-22 | 内购 | ¥21 | 210 | ❌ |
| Cosmic 2025 Heroes' Hoard | 2025年寰宇英雄宝藏 | 2025-09-16 | 内购 | ¥21 | 210 | ❌ |
| Winter 2025 Collector's Cache | 2025年冬季典藏宝瓶 | 2025-12-16 | 内购 | ¥21 | 210 | ❌ |
| Treasure of Wonders | 灵幻奇观的珍藏 | 2026-06-25 | 暗黑狂欢 | ¥17 | 170 | ❌ |
| Trove of Terror | 惊世奇珍 | 2026-10-06 | 内购 | ¥35 | 350 | ❌ |

均价 ¥21.2 → **212 金币/瓶**。只有 `金龙宝藏` 标记为可交易，但 Skinport/Steam 均无在售记录，实际仍按内购价处理。

> 2026-11-06 已确认：以上 13 个瓶在国服商城**全部可直接购买**，包括倾天之战系列的 5 个（活动结束后仍在售），因此不需要按"已下架"剔除任何一项。

**明确不上（`purchasable = no`）**：`Spring Treasure 2024`、`Spring Treasure 2025`（Dota Plus 碎片，不是现金购买）、`Treasure of the Crimson Witness 2024/2025/2026`（TI 现场掉落）、各 `Loading Screen Treasure`、`Frostivus 2024 Loading Screen Treasure`。

### 6.0.1 价格表落地（已填好，写进 `data/DotaPrices.json`）

这 13 个瓶在任何公开渠道都没有价格（Skinport 570 全部 `MISSING`、Steam 市场无此商品、`IEconItems_570/GetStoreMetaData` 403、Liquipedia 不记价），所以用一次性人工核价，插件只读不猜：

```jsonc
{
  "Dragon's Hoard":                     {"price_cny": 17, "checked_at": "2026-11-06", "note": "金龙宝藏 · 游戏内商店"},
  "Crownfall Treasure I":               {"price_cny": 21, "checked_at": "2026-11-06", "note": "倾天之战珍藏"},
  "Crownfall Treasure II":              {"price_cny": 21, "checked_at": "2026-11-06", "note": "倾天之战珍藏 第二套"},
  "Crownfall Treasure III":             {"price_cny": 21, "checked_at": "2026-11-06", "note": "倾天之战珍藏 第二幕"},
  "Crownfall 2024 Collector's Cache":   {"price_cny": 21, "checked_at": "2026-11-06", "note": "倾天之战典藏宝瓶"},
  "Crownfall 2024 Collector's Cache II":{"price_cny": 21, "checked_at": "2026-11-06", "note": "倾天之战典藏宝瓶 II"},
  "Winter 2024 Heroes' Hoard":          {"price_cny": 21, "checked_at": "2026-11-06", "note": "2024年隆冬英雄宝藏"},
  "The Charms of the Snake":            {"price_cny": 17, "checked_at": "2026-11-06", "note": "蛇年珍藏"},
  "Spring 2025 Heroes' Hoard":          {"price_cny": 21, "checked_at": "2026-11-06", "note": "2025年新春英雄宝藏"},
  "Cosmic 2025 Heroes' Hoard":          {"price_cny": 21, "checked_at": "2026-11-06", "note": "2025年寰宇英雄宝藏"},
  "Winter 2025 Collector's Cache":      {"price_cny": 21, "checked_at": "2026-11-06", "note": "2025年冬季典藏宝瓶"},
  "Treasure of Wonders":                {"price_cny": 17, "checked_at": "2026-11-06", "note": "灵幻奇观的珍藏"},
  "Trove of Terror":                    {"price_cny": 35, "checked_at": "2026-11-06", "note": "惊世奇珍"}
}
```

- `price_cny` 为空 → 该瓶**不上架**，列表显示"待核价"（目前 13 项已填满）。
- 金币价 = `price_cny × 10`，卡片显示"国服售价 ¥X · 核对于 2026-11-06"。
- 内容物 `marketable=false`（除金龙宝藏外全部）**不能卖回金币**，只能收藏/赠送/回收——与真实游戏一致，这些瓶是**纯消耗、无返币**。
- 若后续国服调价，只需改这张表，不用动代码。

### 6.1 与真实一致的开箱流程

```text
1. 取该瓶 odds 表，读用户 (treasure, tier) 计数器 → 行号 n（1 起）
2. 逐档判定：Rare / Very Rare / Ultra Rare 用第 n 行概率
3. Cosmically Rare 独立 1/250,000
4. 命中某档 → 该档物品中取一件（受重复保护约束）→ 该档计数清零
5. 未命中 → 三档计数各 +1（封顶最后一行）
6. 普通档：剩余概率均匀分配给 Regular 池中未获得的物品
7. 若 Regular 全收集 → 回退为全池随取
```

### 6.2 保底与重复保护（真实规则）

- **保底行由该瓶的官方表格给出**：`The Charms of the Snake` 只有 2 列（Rare/Very Rare），`Trove of Terror` 有 3 列且**第 50 瓶 Ultra Rare 也是 100%**。换瓶必须读各自表，不能写死"第 40 次"。
- 每档独立计数，命中该档才清零——这是官方规则（"reset after you receive that item"）。
- 重复保护只作用于 Regular；Rare+ 允许重复（官方明示）。

### 6.3 累计奖励与交易锁

- **瓶级回收数据仍保留在快照里**（`recycle_rule: {"inputs": 6, "output": 1}`），但按运营决定**不开放指令**：聊天里只保留开瓶、收藏、出售三条路径。
- **累计奖励**：`Trove of Terror` 有"开 12 瓶送 1 件（Obscene Eye Ball）、开 40 瓶送 2 件（R'shan / Baby R'shan）"；按瓶存 `bonus_rewards: [{opens: 12, items: [...]}, ...]`，达阈值自动发放（不计入稀有度判定）。
- **Ultra Rare 解锁日**：`Spring 2025 Heroes' Hoard` 的 Ultra Rare 2025-11-21 后可交易、`Cosmic 2025 Heroes' Hoard` 为 2026-03-15、`Winter 2025 Collector's Cache` 为 2026-06-01——快照存 `unlock_marketable_at`，到期后才有卖价。
- 开出的物品 `tradeable_at = now + 7d`；到期前只能"赠送一次"或拆解，不能卖。
- `marketable=false` 的内容一律不可卖，只可收藏/赠送/拆解。

### 6.4 指令与对话流

```text
用户: dota宝瓶
Bot:  （卡片：宝瓶图标 + 中文名 + 国服售价 + 金币价）
      1. 金龙宝藏        ¥17   170 币
      2. 倾天之战珍藏    ¥21   210 币
      ...

用户: dota一轮 蛇年珍藏
Bot:  （文字）
      蛇年珍藏 · 本轮进度
      珍稀：本轮第 4 次 → 1.13%（第 40 次必出）
      非常珍稀：本轮第 4 次 → 0.20%
      已收集普通：3/7
      累计开启：3 瓶

用户: dota开瓶 蛇年珍藏 5
Bot:  第 1 瓶：Q'uq'umatz Charmer        （常规 1/7）
      第 2 瓶：Call of the Coral Cultist （常规 2/7）
      第 3 瓶：Madam Mamba               （常规 3/7）
      第 4 瓶：Obsidian Ophis            （常规 4/7）
      第 5 瓶：Psionic Constrictor       （常规 5/7）
      5 瓶均未命中稀有档：Rare 7 → 12，Very Rare 3 → 8
      「蛇年珍藏」× 5：成本 850 币
```

> 表格列数不固定：`The Charms of the Snake` 只有 2 列（Rare / Very Rare），`Trove of Terror` 有 3 列且第 50 瓶 Ultra Rare 也是 100%。解析器必须按表头映射，不能按列位硬编码。

### 6.5 定价规则（已定）

- 只上 `purchasable = yes` 的瓶，价格取 `DotaPrices.json` 的国服售价（13 项已填：¥17 / ¥21 / ¥35 三档），卡片标明核对日期。
- `price_cny` 为空 → 不上架、不可购买，列表显示"待核价"。
- 内容 `marketable = false`（除金龙宝藏外全部）→ 不可卖金币，只能收藏/赠送/回收。
- `Dragon's Hoard`（金龙宝藏）标 `marketable = yes`，但 Skinport/Steam 均无在售记录，仍按 ¥17 内购价处理。
- 宝瓶内容物的市场价（Skinport 570）只用于"参考展示"，不参与回收定价。
- 全套统一价差别很大（¥17 / ¥21 / ¥35），所以列表页按"每瓶金币价"排序，玩家一眼能看出贵在哪。

### 6.6 首版范围外

| 项 | 原因 |
|---|---|
| 加载界面宝箱 / 队伍宝箱 | 无饰品价值，只换加载图 |
| TI 现场宝瓶（Crimson Witness 2024/25/26） | `purchasable = no`，按你的规则不上 |
| Dota Plus 碎片瓶（Spring Treasure 2024/2025） | 碎片不是现金购买，`purchasable = no` |
| 战令等级、糖果袋、签到卡等掉落物 | 非饰品，不影响金币循环 |
| 玩家间饰品交易 | v1 只与商店/回收系统交互，防对刷 |

---

## 7. 经济模型

### 7.1 汇率与费用（1 元 = 200 金币）

| 配置 | 默认 | 说明 |
|---|---|---|
| `coin_per_cny` | **200** | 开一个热门箱 ≈ 3,800~4,800 币；改这一个数即可整体缩放 |
| `sell_basis` | **median** | 回收价取 Skinport `median_price`（`min_price` 太薄、`mean_price` 太乐观） |
| `sell_fee` | 0.15 | 上架手续费（Steam 15% / Skinport 12% 取高者） |
| `key_price_usd` / `usd_cny` | 2.49 / 7.2 | 钥匙折算 ¥17.93 |
| `max_return` | **0.70** | 护栏：回本率高于 70% 的箱子自动下架（防套利） |
| `daily_open_limit` | 50 | 防上头 |
| `min_balance_keep` | 0 | 可留底 |

### 7.2 实测回本率（10 个上线箱，真实报价）

| 箱子 | 成本 | 期望回收 | 回本率 | 单次净消耗 |
|---|---|---|---|---|
| Kilowatt | ¥19.09 | ¥6.18 | 32.3% | 129 币 |
| Fever | ¥21.40 | ¥11.20 | 52.4% | 102 币 |
| Gallery | ¥21.86 | ¥12.67 | 58.0% | 92 币 |
| Fracture | ¥20.24 | ¥6.84 | 33.8% | 134 币 |
| Dreams & Nightmares | ¥23.86 | ¥11.45 | 48.0% | 124 币 |
| Recoil | ¥19.55 | ¥7.26 | 37.1% | 123 币 |
| Prisma 2 | ¥23.86 | ¥14.55 | 61.0% | 93 币 |
| Danger Zone | ¥23.71 | ¥11.10 | 46.8% | 126 币 |
| Snakebite | ¥20.55 | ¥9.95 | 48.4% | 106 币 |
| Revolution | ¥19.39 | ¥6.89 | 35.5% | 125 币 |

均值：回本 **45%**，按 1:200 折算单次净消耗约 **2,300 币**（原 1:10 口径为 115 币）。回本率区间 32%–61%，与真实 CS2 体感（社区统计约 60%~70%，但那是拿 Steam 挂单价算的）同一量级，且都用同一套"C2C 挂单价 → 0.85 回收"口径，玩家无法套利。

### 7.3 通胀吸收测算

CS2 侧：

| 场景 | 数值 |
|---|---|
| 全服余额 | 20,292,577 |
| 单次净消耗（均值 2,300 币） | 2,300 |
| 理论全吸收所需开箱次数 | ≈ 8,700 次 |
| 头部玩家余额 12,105,479 | ≈ 5,300 次 |
| 高价箱单次净消耗 | CS:GO 武器箱 81,498 / 英勇大行动 48,850 / 猎杀者 4,078 |
| 若全服每天 500 次普通 + 20 次高价 | 约 230 万币/天，接近近期日均流入 |

Dota 侧（按已核价：¥17 / ¥21 / ¥35 → 3,400 / 4,200 / 7,000 币，均价 **4,240 币/瓶**）：

| 场景 | 数值 |
|---|---|
| 单瓶价 | 3,400 / 4,200 / 7,000 币 |
| 返币 | **0**（内容不可交易，只能收藏/赠送/回收） |
| 全服余额可吸收瓶数 | ≈ 4,700 瓶 |
| 头部玩家 12,105,479 | ≈ 2,850 瓶 |
| 若每天开 50 瓶 | 每天净吸收 21 万币 |

**必须说清的现实**：近 7 天流水显示金币仍在以约 **283 万/天** 的净流入增长（主要来自 McFish 卖鱼）。按 1:200，CS2 每天 500 次普通箱 + 20 次高价箱约吸收 230 万/天，Dota 再加 20 万，基本能与流入打平、并缓慢消耗存量（2,012 万）。

如果目标是**压降余额**而不是打平，只调汇率不够——要么继续提高 `coin_per_cny`（如 500，但会把轻度玩家挡在门外），要么同时收紧 McFish 的卖鱼收益。这是两个独立开关。

### 7.4 反套利

- 只允许"商店买入 → 开箱/卖回商店"，不开放玩家间交易（v1），杜绝对刷。
- 回收价 = `median × 0.85`，且 **不高于买价的 0.85**（`min(median*0.85, buy*0.85)`），确保任何"买了立刻卖"都是亏的。
- **回本率护栏**：任意箱子 `EV / cost > 0.70` 时自动下架并记录，防止市场报价异常造成套利（实测已抓到 `Spectrum 2` 105%）。
- 同一物品在途时间戳 + 每日额度，防止脚本刷。

---

## 8. 风险与合规

| 风险 | 处理 |
|---|---|
| 数据版权 | `ByMykel/CSGO-API` 为 **MIT** 许可（GitHub 实测，823 ★，当日仍在更新）；Liquipedia 数据 **CC-BY-SA 3.0**，卡片/文档需署名"数据来源 Liquipedia"；图片仅本地渲染缓存 |
| 第三方站 ToS | Skinport 公共 API 有速率限制，仅整表定时拉取，不做高频请求；关闭 UA 伪装 |
| Steam 市场不可达 | 已用 Skinport 替代；若部署环境能访问 Steam，可加 `price_source=steam` 开关 |
| 赌博观感 | 开箱结果卡片固定附"概率公示 + 数据来源 + 更新时间"；`daily_open_limit`；不设充值入口 |
| 概率造假质疑 | 每次开箱写 `case_tx` 流水（含种子与时间），提供 `cs统计` 自证；卡片显示挂载的概率表版本号 |
| 未成年人/沉迷 | 不做抽奖式付费墙；不加倍投机制；不加 "保底进度条"诱导 UI（Dota 保底是真实规则，必须显示但中性表述） |

---

## 9. 里程碑

| 阶段 | 内容 | 状态 |
|---|---|---|
| M1 | `case_data` 快照抓取 + 归一化 + Liquipedia 解析器 | ✅ 完成（快照 340 KB） |
| M2 | `CsgoCase`：列表 / 详情 / 买 / 开 / 背包 / 卖 + 卡片 | ✅ 完成 |
| M3 | 汰换合同 + `cs汰换预览`（按输入 float 逐件推算） | ✅ 完成 |
| M4 | `DotaTreasure`：列表 / 详情 / 买 / 开 + 递增计数 + 重复保护 + 累计奖励 | ✅ 完成 |
| M5 | 回收 6→1 / 交易锁（赠送未做，见 §11 偏差） | ✅ 完成 |
| M6 | 核价表 / 价格 6h 刷新 / 图片缓存 / 统计 + 榜单 | ✅ 完成 |
| M7 | 单元测试 + 冒烟端到端 + 文档 | ✅ 19 个测试通过 |

---

## 10. 方案已定 + 剩余待办

已按你的决定锁定：

| 项 | 决定 |
|---|---|
| 汇率 | `coin_per_cny = 10`（1 元 = 10 币） |
| CS2 范围 | 20 个箱子（10 热门 + 10 高价值，见 §5.0），其余 22 个留待二期 |
| Dota 范围 | 2024–2026 且 `purchasable = yes` 的 13 个瓶（见 §6.0） |
| Dota 排除 | TI 宝瓶、Dota Plus 碎片瓶、加载界面箱 —— 不可游戏内直接购买，一律不上 |
| Dota 定价 | 已核价且已确认全部可购买：金龙宝藏 ¥17、蛇年珍藏 ¥17、灵幻奇观的珍藏 ¥17、惊世奇珍 ¥35、其余 9 个 ¥21（`DotaPrices.json`，核对日期 2026-11-06） |
| Steam key | 不配。已实测 `api.steampowered.com` 可达但拿不到 Dota 游戏内饰品价，Skinport 已覆盖 CS2 全量 |

**无阻塞项**，已按 §9 全部实现（见 §11）。二期可选：

1. 把 CS2 剩下的 32 个 Case 放进来（数据现成，`crates.json` 里全都有，只是需要再过一遍回本率护栏）。
2. Dota 侧如果要覆盖 2026 年之后新出的瓶，只需重跑一次快照脚本 + 补一行 `DotaPrices.json`。

---

## 11. 实施记录

### 11.1 交付物

| 模块 | 文件 | 说明 |
|---|---|---|
| 共享数据层 | `plugins/common/case_data/` | models / rules / prices / images / render / snapshot |
| 快照 | `plugins/common/case_data/data/*.json.gz` | 10 箱 + 2126 皮肤 + 13 瓶 + 价格兜底，共 340 KB |
| 构建脚本 | `scripts/build_case_data.py` | `--refresh` 重抓、`--skip-prices` 只重建物品 |
| 合并插件 | `plugins/CaseOpening/` | `cs2.py`（开箱/背包/出售/汰换/价值表/战绩）+ `dota.py`（开瓶/一轮/饰品/出售/战绩）+ 共享借贷与统一帮助 |
| 测试 | `tests/unit/plugin/test_csgo_case.py`、`test_dota_treasure.py` | 19 个用例（CU-01~10、DU-01~09） |

### 11.2 与原设计的偏差（都已落到代码）

1. **卡片渲染上移到共享层**（`common/case_data/render.py`），两个插件共用，避免重复两百行 Pillow 代码。
2. **赠送命令未实现**：设计里标为"可选"。当前只支持收藏/回收/出售，交易锁照常记录，后续加 `dota赠` 只需读 `tradeable_at`。
3. **Doppler / 蓝宝相位不建模**：仍按基础图案名取价，与 §3 保真度矩阵一致。
4. **Dota Ultra Rare 解锁日未建模**：这批瓶的内容基本不可交易，`tradeable_at` 统一按 7 天写；只有 `金龙宝藏` 标了 `marketable`，未来接分档解锁只需在快照加字段。
5. **新增 `cs榜` / `dota榜`**（设计 M6 提过"统计与排行榜"，实现为按次数 Top5）。
6. **箱子中文别名表**（`CASE_ALIASES`）：`千瓦/画廊/裂空/棱彩2/蛇噬/革命/危险地带/梦境与噩梦/反冲/狂热`，只用于搜索匹配，不改英文数据。

### 11.3 验证记录

| 项 | 结果 |
|---|---|
| `pytest tests/unit/plugin/test_csgo_case.py test_dota_treasure.py` | **19 passed** |
| 开箱概率抽样 20000 次 | 五档偏差均 < 2%，罕见档 < 0.2% |
| 汰换合同 | 10 × 千瓦军规 → 1 件受限，float 按 `avg×(max−min)+min` 落档 |
| Dota 保底 | `蛇年珍藏` 珍稀第 40 瓶 100%；`惊世奇珍` 珍稀超凡第 50 瓶 100% |
| Dota 累计奖励 | 开满 12 瓶自动发 `Obscene Eye Ball`，只发一次 |
| Dota 重复保护 | 普通档 12/12 收齐后才开始重复 |
| 交易锁 / 不可交易 | 非 marketable 物品拒绝出售；交易锁提示解锁日期 |
| 经济联动 | 扣费/回收走 `GroupEconomy.change_balance`，reason 分列 `cs_*` / `dota_*` |
| 卡片渲染 | 980 宽列表卡，稀有度色条 + 缩略图 + 价格，图片失败退化色块 |

已知非本次引入的失败用例（与本改动无关，涉及 `PluginLoader` 与 QQ 发送重试）：`test_ld07_reload_after_failed_load_retries`、`test_qq_send_util.py` 两个超时用例。

---

## 附：数据源清单

```text
CS2 物品/箱子/钥匙/收藏品  https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/en/{crates,skins,collections,keys}.json
CS2/Dota 实时价格(CNY)     https://api.skinport.com/v1/items?app_id={730,570}&currency=CNY   （需 br/gzip）
CS2 价格离线兜底           https://raw.githubusercontent.com/ByMykel/counter-strike-price-tracker/main/static/latest.json
Dota 宝瓶内容/概率/规则    https://liquipedia.net/dota2/api.php?action=parse&page=<Treasure>&prop=wikitext|text   （需 gzip + UA）
Dota 宝瓶规则总览          https://liquipedia.net/dota2/Treasure
Dota 饰品图标              https://liquipedia.net/commons/images/<a>/<bb>/Cosmetic_icon_<Name>.png
Valve 概率公告             https://store.steampowered.com/news/app/730/view/3644028848627416261   （本机网络不可达，仅作引用）
```

### 引用说明

- CS2 五档概率 79.92 / 15.98 / 3.20 / 0.64 / 0.26 来自 Valve 公示（上表 URL）；本机无法直连 `store.steampowered.com`，数值与 `ByMykel` 数据结构、社区资料三方一致。
- 汰换合同细则（10 件、收藏品加权、float 公式、刀/手套不可参与）来自 Counter-Strike Wiki；Valve 未发布技术规范，标注为"社区确认的游戏机制"。
- StatTrak ≈ 10% 为社区统计值，Valve 未公示，卡片上会明确标注。
- Dota 宝瓶递增概率为**官方在游戏内 treasury 面板披露、Liquipedia 转录**的数据，逐瓶不同。
- Liquipedia 内容为 CC-BY-SA 3.0，插件卡片与帮助文本需附"数据来源 Liquipedia"。


### 11.4 v5 修订（按运营反馈）

| 反馈 | 处理 |
|---|---|
| 箱子没有图片 | 箱子列表/详情/开箱卡/价值表都带图；Dota 宝瓶列表与开箱卡带宝瓶图标；饰品背包带物品图标（修掉了 Liquipedia 缩略图 URL 被截断导致 404 的问题） |
| 不要体现回报率 | 列表与详情删除"回本率/期望回收"；开箱卡删除"市值/净"；战绩删除"累计回收/净收支"；护栏仍然生效但只提示"报价异常，已暂停" |
| 需要每个箱子单独的价值表 | 新增 `cs价值表 <箱子|编号> [页]`：按稀有度分组、每页 12 件、带物品图与回收价区间 |
| 指令名不好听 | `cs开`→`cs开箱`、`cs买`→`cs买箱`、`cs卖`→`cs出售`、`cs统计`→`cs战绩`；Dota `dota开`→`dota开瓶`、`dota买`→`dota买瓶`、`dota背包`→`dota饰品`、`dota统计`→`dota战绩`（`cs汰换` 保留，见 §11.5） |
| Dota 不叫保底，叫一轮 | `dota保底` → `dota一轮`，文案改为"本轮第 N 次 / 第 X 次必出" |
| 不存在"换"的指令 | 删除 `dota换` / `dota拆`，帮助与背包页脚同步移除；快照里的 recycle 数据保留但不暴露 |
| 菜单字体重叠 | 重写渲染器：标题/副标题行距加大（16/62），行高 92，右侧价格与标题留 230px 安全距离；新增分组标题行与头部大图 |
| 箱子英文名 | 快照新增 `cn_name`：千瓦武器箱 / 狂热武器箱 / 画廊武器箱 / 裂空武器箱 / 梦境与噩梦武器箱 / 反冲武器箱 / 棱彩 2 号武器箱 / 危险地带武器箱 / 蛇噬武器箱 / 革命武器箱；列表显示"序号. 中文名"，英文名放副行 |
| 支持序号开箱 | `cs开箱 8 10` 可用；序号与列表展示编号一致，也支持中文名、中文别名、英文名 |


### 11.5 v6 修订（术语对齐官方）

- `汰换` 是国服官方术语（"汰换合同"/Trade Up Contract，社区也称"炼金"），上一版误改成"合成"，**已改回**：`cs汰换`、`cs汰换预览`，帮助与卡片文案统一为"汰换合同 / 配方 / 出货概率"。
- 外观与稀有度改用国服叫法：崭新出厂 / 略有磨损 / 久经沙场 / 破损不堪 / 战痕累累；消费级 / 工业级 / 军规级 / 受限 / 保密 / 隐秘 / 非凡。
- 卡片副行从 `Factory New · float 0.0410` 改为 `崭新出厂 · 磨损 0.0410`；概率脚注同步为"军规级 / 受限 / 保密 / 隐秘 / 非凡（刀/手套）"。
- 数据库里仍存英文外观名（用于拼 Steam/Skinport 市场名），中文只在展示层映射。


### 11.6 v7 修订（图片帮助 + 高价值箱）

- `cs帮助` / `dota帮助` 改成图片卡片：分组标题（箱子/开箱/库存/汰换/其他）+ 指令与说明，Dota 帮助带宝瓶图标。
- 箱子从 10 个扩到 **20 个**：新增 CS:GO 武器箱（¥650）、英勇大行动（¥365）、电竞 2013（¥258）、九头蛇（¥162）、突围（¥45）、猎杀者（¥44）、激流（¥39）、狂牙（¥32）、幻彩（¥27）、凤凰（¥20）。
- 选箱规则：只上回本率 ≤ 70% 的箱子，12 个回本率异常偏高的（Weapon Case 2/3、eSports 2013 Winter、Glove、Spectrum、Spectrum 2、Gamma 2、Gamma、Wildfire、Vanguard、eSports 2014 Summer、Winter Offensive）没有上。
- **龙狙**：确认不在任何武器箱，只在古堡纪念包；纪念包概率无公开可核对来源，本次不做，需要游戏内"查看概率"的数字才能接。


### 11.7 v8 修订（汇率 1:200）

- 全服 355 个账户余额中位数 0、p90 427、p95 2,936、p99 494,646、最高 1,210 万；而近 7 天新增流入约 283 万/天（McFish 卖鱼为主）。1:10 的汇率下，热门箱 191 币、最高价箱 6,680 币，对头部玩家等于免费。
- 调成 **1 元 = 200 金币**：热门箱 3,818 币、猎杀者 12,336 币、英勇大行动 81,762 币、CS:GO 武器箱 138,750 币；Dota 瓶 3,400 / 4,200 / 7,000 币。
- 单次净消耗从 115 币升到约 2,300 币；头部玩家 1,210 万可开约 5,300 次热门箱，或 87 个 CS:GO 武器箱（每个净消耗 8.1 万）。
- 只改 `config.py` 的 `coin_per_cny` 一个数即可整体缩放；历史数据（物品价格快照存的是人民币）不需要迁移。


### 11.8 v9 修订（去掉"先买后开"）

- 删除 `cs买箱` / `cs买钥匙` / `dota买瓶` 三个指令与 `stock` 表；`cs开箱`、`dota开瓶` 现在**一次扣费、立刻开、立刻返回产物**。
- 扣费口径不变：CS2 = 箱价 + 钥匙价，Dota = 国服售价 × 瓶数，仍走 `GroupEconomy.change_balance`。
- 帮助卡、README、指令表同步删除购买步骤；开箱卡不再出现"消耗库存"提示。


### 11.9 v10 修订（全中文显示）

- CS2 的箱子名与皮肤名改从 **ByMykel CSGO-API 的 `zh-CN` 数据集**取（`public/api/zh-CN/{crates,skins}.json`），与游戏内一致：如 `AWP | 镀铬大炮`、`AK-47 | 传承`、`反恐精英武器箱`、`反恐精英武器箱`、"头号特训"武器箱。
- 快照新增 `name_cn`（皮肤）与 `cn_name`（箱子，优先用 zh-CN，缺失回退手工别名表）；订单/价格仍用英文 `name` 拼 `market_hash_name`，不影响查价。
- 背包新增 `name_cn` 列并自动迁移旧库；开箱卡、价值表、物品详情、汰换产出全部显示中文。
- 去掉卡片里多余的英文副标题（箱子列表、详情、开箱、价值表、宝瓶列表/开箱）。
- Dota 套装名暂无中文数据源（Liquipedia 只有英文、官方接口 403、Steam 市场不可达），保留英文；新增可选覆盖表 `data/DotaTreasure/names_cn.json`（`{"英文名": "中文名"}`），填了即显示中文。


### 11.10 v11 修订（汰换输入 + 背包密度）

- 汰换支持三种输入：空格/逗号编号、区间 `1-10`、稀有度关键词 `军规`/`受限`/`保密`（自动挑 10 件：优先非 StatTrak、按回收价从低到高）。
- 编号错误时直接列出缺失的编号，不再笼统提示"有编号不存在"。
- 背包/饰品列表改成**卡片网格**：3 列 × 5 行 = 每页 15 件，带大图、编号角标、两行名称、稀有度与磨损。
- 收藏品名、出货提示、汰换结果也改成中文（收藏品名取自 zh-CN 数据，如"千瓦收藏品"）；汰换预览不再显示"期望回收"，只给概率、件数与出货均价。


### 11.11 v12 修订（背包卡片网格）

- 新增共享渲染器 `render_grid`：3 列卡片，单卡 268px 高，顶部稀有度色条，左上 `#编号`，中间物品图，下方两行名称（英文按词折行、中文按字折行）+ 稀有度/外观 + 价格。
- 背包与 Dota 饰品页每页 15 件（3×5），比列表版一屏多 25%。
- Dota 饰品卡片上的宝瓶名改用中文（金龙宝藏 / 惊世奇珍…）。
- 图片下载加 4 并发信号量 + 一次重试，避免 Liquipedia 连发 429 导致整页空白。


### 11.12 v13 修订（序号化 + 开箱贷 + 重生）

- 背包/饰品按**当前回收价从高到低**排序；卡片角标改为**每用户连续序号**（跨页连续），`cs物品 / cs出售 / cs汰换 / dota卖` 全部只认这个序号，不再暴露数据库自增 id。
- 新增共享开箱贷 `data/case_loan/loan.db`（`plugins/common/case_data/loan.py`）：
  - 单次 ≤ **100,000** 金币、每天 1 次；额度只能用于开箱/开瓶；
  - 扣费顺序：先用额度，再扣钱包；
  - 出售所得**优先自动还贷**，多余才进钱包；
  - 还款时未花额度跟随债务一起收缩（`credit ≤ debt` 不变式），不会出现"还完债还能白花"。
- 新增 `借贷 / 借钱 / 还款 / 重生` 指令（`cs` 与 `dota` 前缀各一套，共享同一账本）。
- **重生**：60 秒内二次确认后，钱包清零、CS2 与 Dota 背包全部清空、欠款清零，不可撤销。
- 新增测试 LN-01~05（借贷上限/每日一次/额度抵扣/清账/背包排序）。


### 11.13 v14 修订（借贷留痕）

- `loan` 表新增：`total_repaid`、`peak_debt`（历史最高欠款）、`loan_count`（借款次数）、`rebirths`、`last_rebirth_at`；旧库自动 ALTER 补齐。
- 新增 `loan_log` 事件表：`borrow / repay / rebirth` 每次记录金额与事后欠款，便于审计与后续做"欠款曲线"。
- `cs借贷` / `dota借贷` 展示历史留痕；`cs战绩` / `dota战绩` 增加"欠款 · 重生次数"一行。
- 重生改用 `record_rebirth()`：清账同时累计次数、记录时间，日志里保留"重生前欠款"。


### 11.14 v15 修订（合并插件 + 统一帮助）

- 两个插件合并为 **`CaseOpening`**：`CaseOpening.py` 负责生命周期、经济、借贷、统一帮助与分发；`cs2.py` / `dota.py` 是两个玩法模块，不继承插件基类，通过 owner 借用能力。以后加抽卡只需再挂一个模块。
- `开箱帮助` / `开瓶帮助` / `cs帮助` / `dota帮助` 返回**同一张说明图**，内含 CS2、Dota、借贷三组指令；指令前缀仍区分 `cs…` / `dota…`。
- 借贷命令两个前缀都认（`cs借贷` / `dota借贷` 等价）。
- 数据目录改为 `data/CaseOpening/data/{cs2,dota}`；首次加载会自动把旧的 `data/CsgoCase`、`data/DotaTreasure` 迁移过来（不覆盖已有文件）。
- 测试同步迁移：CU/DU 仍测各模块，CM-02 改为整体 `CaseOpening.on_load`。


### 11.15 v16 修订（累计欠款 + 重生变卖）

- 借贷新增 `total_debt`（累计欠款）：每次借款累加，重生与还款都不会清零；借贷页展示"累计欠款 / 已还 / 最高欠款 / 借款次数"。
- 重生改为**先变卖再清空**：`cs2.liquidate()` / `dota.liquidate()` 按当前回收价折算金币，Dota 只卖可上架且已过 7 天锁的饰品，其余照样清空；变卖所得先抵扣欠款，随后余额与剩余欠款清零。
- 两个 `liquidate` 只清本插件两个背包，**不触碰 McFish 等其它插件的数据**；提示文案已写明。
- 新增测试 LN-07（变卖清空）并扩展 LN-06 覆盖累计欠款。


### 11.16 v17 修订（取消 Dota 交易锁）

- 按运营要求**不模拟 7 天交易锁**：开出的饰品 `tradeable_at = obtained_at`，可立即 `dota卖`。
- 配置删除 `dota_trade_lock_days`；饰品卡片不再显示"锁 MM-DD"；`liquidate` 也不再判断锁。
- 仍保留"不可上架"（`marketable=false`）限制：这类内容只能收藏，不能出售。


### 11.17 v18 修订（借款规则调整）

- 单笔上限 100,000 → **1,000,000**。
- 取消"每天一次"，改成**同时只允许一笔欠款**：`debt > 0` 时再借会被拒绝（提示"上一笔还没还清，先还款或重生"）；还清或重生后立即可再借。
- `status` 暴露 `can_borrow`（= 无欠款），借贷页状态改为"可借 / 有欠款未还清"。
- 测试 LN-01 相应重写：上限、未清不可再借、还清可再借、重生可再借。


### 11.18 v19 修订（借贷命令去掉前缀）

- 借贷相关命令改为裸命令：`借贷` / `借钱 <金额>` / `还款 <金额>` / `重生 [确认]`。
- 旧的 `cs借贷`、`dota借钱` 等仍作为兼容别名处理（不写入帮助）。
- 帮助卡"借贷"分组同步改为无前缀写法。


### 11.19 v20 修订（借贷也走图片）

- `借贷` / `借钱` / `还款` / `重生` / `重生 确认` 全部改为卡片图，与开箱卡同一套渲染风格（分组标题、左侧色条、右侧金额）。
- 借贷卡：当前（可用额度/待还欠款/钱包余额/借款状态）+ 历史（累计欠款/累计已还）+ 重生（次数/上次时间）。
- 借钱、还款、重生确认、重生完成各有独立卡片；失败提示（额度超限、欠款未清、余额不足）仍用文字，保持即时。
- 卡片文件名改用纳秒时间戳，避免同一秒内多次操作互相覆盖。


### 11.20 v21 修订（彻底移除借款每日限制）

- 删除 `last_borrow_date` 的读写（字段保留仅为兼容旧库，不再参与判断），借贷逻辑只剩"无欠款即可借"。
- 文案同步：借钱卡片脚注改为"还清即可再借，没有每日次数限制"；帮助与 README 去掉"每天/每日"。
- 开箱/开瓶的每日上限（50 / 40）保持不变，那是开箱限制不是借款限制。


### 11.21 v22 修订（借还同义词）

- `借钱` / `借款` 都能借款；`还款` / `还钱` 都能还款（含 `cs`/`dota` 前缀兼容写法）。
- 帮助卡与 README 展示为「借钱 / 借款 <金额>」「还款 / 还钱 <金额>」。
- 参数写错时提示也同时给出两种写法。


### 11.22 v23 修订（取消开箱/开瓶每日上限）

- 删除 `cs2_daily_open_limit`（50）与 `dota_daily_open_limit`（40）两个配置，开箱/开瓶不再按天计数。
- 移除对应的拦截逻辑与两个 store 的 `opens_today()`。仍保留单次上限 `cs2_max_batch` / `dota_max_batch`（各 10）。
- 帮助脚注改为"无每日次数限制"。


### 11.23 v24 修订（帮助冲突 + 多档同时掉落）

- **`开箱帮助` 被钓鱼插件抢答**：McFish 的钓鱼开箱指令以「开箱」开头（`on_message(priority=29)`）。本插件 priority=32 先执行，处理帮助后主动设置 `event._data._propagation_stopped = True` 截断传播，钓鱼插件不会再回一句。同时新增 `箱子帮助` / `宝瓶帮助` 作为替代入口。
- **Dota 一次开瓶可出多件**：官方表格里 Rare / Very Rare / Ultra Rare 是**独立判定**，例如 `Trove of Terror` 第 50 瓶珍稀与珍稀超凡同时 100%。此前实现命中一个档位就返回，导致只出 1 件额外奖励；现在改为：
  - 普通档**必定**给 1 件（重复保护）；
  - 三个额外档独立判定，全部命中都发（各自清零计数），再叠加固定概率的宇宙珍稀；
  - 第 50 瓶实测产出 3 件：珍稀超凡 + 珍稀 + 普通。


### 11.24 v25 修订（修复大量箱子被拦 + 全量上架）

- **"获取价格异常"的根因**：护栏阈值 0.70 太严。近期行情下大量箱子回本率 70%–98%，被误判为异常；另有新增箱子没进价格快照导致"缺少报价"。
- 护栏改成 `max_return = 1.0`：只在 **EV ≥ 开箱成本（真套利）** 时下架。当前 42 个箱子里只有 `电竞 2013 冬季武器箱`（130.7%）被暂停。
- 上架范围扩到 **全部 42 个 Case**（原来 20 个），并重建价格快照覆盖所有箱内物品。
- 龙狙问题：`items_game.txt` 已拉取核对，CS2 已不再在客户端里保存箱子/纪念包的战利品权重表，纪念包概率无法从文件推导；官方"查看概率"页本机不可达。因此维持"不编概率"，待提供游戏内数字后接入。


### 11.25 v26 修订（接入古堡纪念包 / 龙狙）

- 运营提供了 Valve 纪念包公示概率：**消费级 79.87% / 工业级 15.97% / 军规级 3.33% / 受限 0.67% / 保密 0.13% / 隐秘 0.03%**，六档合计 100%。
- 快照新增 10 个 `Cobblestone Souvenir Package`（2014–2018），`key_name = null`、`tier_odds` 存六档概率；价格走 Skinport（科隆 2015 约 ¥4,033）。
- 开箱逻辑支持纪念包：按 `tier_odds` 抽档（档内均分）、不出 StatTrak、市场名加 `Souvenir ` 前缀、无需钥匙。
- EV/护栏按六档计算；科隆 2015 实测回本率约 **4%**（单开 806,640 币），属于真实彩票。
- 详情页展示六档概率与"无需钥匙"；价值表用纪念品挂牌价。
- 风险提示：纪念品龙狙（崭新）Skinport 报价 ¥117,799，售出约 2,000 万币；虽然期望仍是大额亏损，但命中一次会显著抬高货币存量。


### 11.26 v27 修订（去掉回本率护栏 + 金额双币种播报）

- 完全移除 `max_return` 护栏：不再因回本率低或高下架，仅当某箱所有物品都没有市场报价时才提示"暂不可开"。
- 开箱/开瓶在扣费前先播报明细，统一格式 `X 币（¥Y）`：
  - CS2：`箱 122 币（¥0.61） + 钥匙 3,586 币（¥17.93） = 合计 7,416 币（¥37.08）`
  - Dota：`2 × 3,400 币（¥17.00） = 合计 6,800 币（¥34.00）`
  - 结果卡副标题与结算文本同样带人民币。
- 重生确认卡新增"当前欠款"（金币 + 人民币）；重生结果卡新增"本次欠款 / 累计欠款"两行，均附人民币。
- 人民币换算 = 金币 ÷ `coin_per_cny`（默认 200）。


### 11.27 v28 修订（重生/借贷卡片补人民币）

- 重生结果卡的所有金额行都加人民币副行：本次欠款、累计欠款、变卖 CS2、变卖 Dota、变卖抵扣、钱包清零。
- 借贷状态卡的可用额度 / 待还欠款 / 钱包余额同样补 `¥` 副行。
- 统一换算：`¥ = 金币 ÷ coin_per_cny`，千分位显示。
