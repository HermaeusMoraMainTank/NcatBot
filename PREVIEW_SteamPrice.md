# Steam 价格（小黑盒）功能预览

> 上游：https://github.com/penguin-madagascar/astrbot_plugin_steam_price_heybox  
> 插件名：`SteamPrice`（`plugins/SteamPrice/`）  
> LLM：`AiUtil.search_deepseek` 校正游戏名 / 未知中文地区

## 指令映射

| 原指令 | 新指令 |
|--------|--------|
| `/steamprice …` | `steam价格 …` |
| `/steamprice history …` | `steam价格 历史 …` |
| `/steamprice regions …` | `steam价格 区价 …` |
| `/steamprice info …` | `steam价格 资料 …` |
| `/steamprice detailed_info …` | `steam价格 详细资料 …` |
| （仅入口无参数） | `steam价格` → 帮助文本 |

别名入口（可选）：`小黑盒查价`、`steam查价`（行为同 `steam价格`）。

LLM 名称校正：本版不接 AstrBot LLM，使用内置别名即可。

---

### 功能 1：帮助

**触发**：`steam价格`（无后续参数）

**对话流**：

用户: steam价格  
Bot:  【Steam 价格查询】  
      steam价格 [-地区] [--] <游戏名|appid|Steam链接>  
      steam价格 历史 [-地区] [--] <目标>  
      …

---

### 功能 2：默认查价

用户: steam价格 艾尔登法环  
Bot:  （当前价 / 史低 / 促销摘要 …）

---

### 功能 3–6：历史 / 区价 / 资料 / 详细资料

用户: steam价格 历史 -CN 1245620  
Bot:  （促销历史）

用户: steam价格 区价 艾尔登法环  
Bot:  （全球区价）

用户: steam价格 资料 Stardew Valley  
Bot:  （基础资料）

用户: steam价格 详细资料 2277560  
Bot:  （基础 + 详细资料）
