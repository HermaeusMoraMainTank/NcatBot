"""
兼容层 - 替换 astrbot 的 API

提供与 astrbot.api 兼容的接口，使核心代码无需大量修改即可运行
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

# 使用 ncatbot 的日志（模块级 _log）
try:
    from ncatbot.utils import get_log

    _log = get_log()
except ImportError:
    # 回退到标准 logging
    _log = logging.getLogger("MediaParser")
    _log.setLevel(logging.DEBUG)
    if not _log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "[%(asctime)s] %(levelname)s %(name)s | %(message)s", datefmt="%H:%M:%S"
            )
        )
        _log.addHandler(handler)


class ConfigWrapper:
    """配置包装器，兼容 astrbot 的 AstrBotConfig 访问方式"""

    def __init__(self, config_dict: Optional[Dict[str, Any]] = None):
        self._config = config_dict or {}

    def __getitem__(self, key: str) -> Any:
        return self._config.get(key)

    def get(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    def __setitem__(self, key: str, value: Any):
        self._config[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self._config

    def save_config(self):
        """保存配置（暂不实现持久化）"""
        pass

    def update(self, data: Dict[str, Any]):
        """更新配置"""
        self._config.update(data)


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
    # YouTube Cookies（浏览器 Cookie 字符串或 Netscape 文件路径）
    "ytb_ck": "",
    # 从浏览器读取 YouTube Cookie，如 chrome / edge / firefox（与 ytb_ck 二选一）
    "ytb_cookies_from_browser": "",
    # YouTube cookie 文件路径（空则不用；有 ytb_ck 时由解析器写入）
    "ytb_cookies_file": "",
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
    # 解析发送成功后，延迟多少秒删除本次用到的缓存文件（视频/语音/预览图等，仅删 cache_dir 内）
    # 0 表示关闭，仅依赖每日 clean_cron 全量清理
    "parsed_media_delete_after_sec": 1800,
    # 数据目录
    "data_dir": "",
    # 缓存目录
    "cache_dir": "",
}


def create_config(data_dir: Path, cache_dir: Path) -> ConfigWrapper:
    """创建配置对象"""
    config = ConfigWrapper(DEFAULT_CONFIG.copy())
    config["data_dir"] = str(data_dir)
    config["cache_dir"] = str(cache_dir)
    return config
