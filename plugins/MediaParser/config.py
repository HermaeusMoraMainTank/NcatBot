"""MediaParser 插件配置"""

# 默认配置
DEFAULT_CONFIG = {
    # 启用的平台
    "enable_platforms": [
        "A站",
        "B站",
        "微博",
        "小红书",
        "抖音",
        "快手",
        "NGA",
        "TikTok",
        "Instagram",
        "推特",
        "油管",
        "网易云",
    ],
    # 禁用的会话列表
    "disabled_sessions": [],
    # 防抖秒数
    "debounce_interval": 300,
    # 资源最大大小 (MB)
    "source_max_size": 90,
    # 资源最大时长 (分钟)
    "source_max_minute": 15,
    # 音频以文件形式上传
    "audio_to_file": True,
    # 单条重媒体仍渲染卡片
    "single_heavy_render_card": False,
    # 转发阈值
    "forward_threshold": 2,
    # 提示下载失败项
    "show_download_fail_tip": True,
    # 下载超时
    "download_timeout": 280,
    # 普通请求超时
    "common_timeout": 15,
    # B站 Cookies
    "bili_ck": "",
    # 抖音 Cookies
    "douyin_ck": "",
    # B站视频编码
    "bili_video_codecs": "AVC",
    # B站视频分辨率
    "bili_video_quality": "_720P",
    # YouTube Cookies
    "ytb_ck": "",
    # Instagram Cookies
    "ig_ck": "",
    # 代理地址
    "proxy": "",
    # Emoji CDN
    "emoji_cdn": "https://cdn.jsdelivr.net/npm/emoji-datasource-facebook@14.0.0/img/facebook/64/",
    # Emoji 样式
    "emoji_style": "FACEBOOK",
    # 自动清理缓存周期
    "clean_cron": "30 2 * * *",
}


class ConfigWrapper:
    """配置包装器，兼容 astrbot 的配置访问方式"""

    def __init__(self, config_dict: dict):
        self._config = config_dict

    def __getitem__(self, key):
        return self._config.get(key)

    def get(self, key, default=None):
        return self._config.get(key, default)

    def __setitem__(self, key, value):
        self._config[key] = value

    def __contains__(self, key):
        return key in self._config

    def save_config(self):
        """保存配置（暂不实现持久化）"""
        pass

