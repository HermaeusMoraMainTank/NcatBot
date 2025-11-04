# BilibiliAnalysis 插件

这是一个适配 NcatBot 框架的 Bilibili 链接解析插件，基于 nonebot_plugin_analysis_bilibili 插件进行重构。

## 功能特性

- 🎬 **视频解析**: 支持 av/BV 号视频链接解析
- 📺 **番剧解析**: 支持番剧、电影等 PGC 内容解析
- 📡 **直播解析**: 支持直播间信息解析
- 📝 **文章解析**: 支持专栏文章解析
- 💬 **动态解析**: 支持用户动态解析
- 🔗 **短链接**: 支持 b23.tv 等短链接解析
- 🔍 **搜索功能**: 支持通过标题搜索视频（可选）
- 🖼️ **图片显示**: 支持封面图片显示和大小调整
- ⚡ **缓存机制**: 避免重复解析同一链接
- 🛡️ **权限控制**: 支持黑白名单和群组权限控制

## 使用方法

### 基本使用

在群聊或私聊中发送 Bilibili 链接，机器人会自动解析并返回详细信息：

- 视频链接: `https://www.bilibili.com/video/BV1xx411c7mD`
- 番剧链接: `https://www.bilibili.com/bangumi/play/ep123456`
- 直播链接: `https://live.bilibili.com/123456`
- 文章链接: `https://www.bilibili.com/read/cv123456`
- 动态链接: `https://t.bilibili.com/123456`
- 短链接: `https://b23.tv/abc123`

### 搜索功能（可选）

如果启用了搜索功能，可以使用以下命令：

```
搜视频 关键词
查询视频 关键词
搜索视频 关键词
```

## 配置说明

插件支持通过 `config.yaml` 文件进行配置：

### 图片显示配置

```yaml
analysis_display_image: true  # 是否显示封面图片
analysis_display_image_list: ["video", "bangumi", "live", "article", "dynamic"]  # 哪些类型需要显示封面
```

### 图片大小调整

```yaml
analysis_images_size: ""  # 图片大小调整，如: "100h", "100w", "100h_100w"
analysis_cover_images_size: ""  # 封面图大小调整
```

### 权限控制

```yaml
# 白名单（优先级高于黑名单）
analysis_whitelist: []  # 用户白名单
analysis_group_whitelist: []  # 群组白名单

# 黑名单
analysis_blacklist: []  # 用户黑名单
analysis_group_blacklist: []  # 群组黑名单
```

### 其他配置

```yaml
analysis_desc_blacklist: []  # 不显示简介的群组
analysis_reanalysis_time: 0  # 重新解析时间间隔（秒）
analysis_enable_search: false  # 是否启用搜索功能
analysis_trust_env: false  # 是否使用系统代理
```

## 安装说明

1. 将 `BilibiliAnalysis` 文件夹复制到 `plugins` 目录
2. 确保已安装必要的依赖：
   - `aiohttp`
   - `ncatbot`
3. 重启机器人即可使用

## 依赖说明

- `aiohttp`: 用于 HTTP 请求
- `ncatbot`: NcatBot 框架核心

## 注意事项

1. 插件会自动处理 Bilibili 的反爬机制
2. 支持图片大小调整，避免发送过大的图片
3. 内置缓存机制，避免重复解析
4. 支持权限控制，可以限制特定用户或群组的使用

## 更新日志

### v1.0
- 初始版本
- 支持视频、番剧、直播、文章、动态解析
- 支持短链接解析
- 支持搜索功能
- 支持图片显示和大小调整
- 支持权限控制
- 支持缓存机制

## 许可证

基于原 nonebot_plugin_analysis_bilibili 插件进行适配，遵循相应的开源许可证。
