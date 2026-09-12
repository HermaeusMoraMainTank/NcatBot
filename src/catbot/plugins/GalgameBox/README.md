# Galgame 百宝盒

移植自 [astrbot_plugin_galgame_box](https://github.com/PyuraMazo/astrbot_plugin_galgame_box)（AGPL-3.0）。

指令前缀统一为 **`gal`**（不再使用 `/旮旯`）。

## 依赖

```bash
pip install aiohttp beautifulsoup4 curl_cffi jinja2 pillow pydantic playwright
playwright install chromium
```

TouchGal 建议安装 `curl_cffi` 以绕过 Cloudflare。NSFW / 详情页需在配置中填写 TouchGal Token。

## 指令

| 功能 | 指令 |
|------|------|
| 帮助 | `gal` |
| 作品 | `gal 作品 <名>` |
| 角色 | `gal 角色 <名>` |
| 厂商 | `gal 厂商 <名>` |
| ID | `gal ID <VNDB ID>` |
| 简讯 | `gal 简讯` |
| 随机 | `gal 随机` |
| 推荐 | `gal 推荐 <标签…>` |
| 下载 | `gal 下载 <内容>` |
| 出处 | `gal 出处`（可附图/引用/链接） |

推荐会话内回复 `换一个` / `结束`。

## 配置

通过插件 ConfigMixin 配置嵌套段：`basicSetting` / `safetySetting` / `scheduleSetting` 等，字段含义见上游 `_conf_schema.json`。
