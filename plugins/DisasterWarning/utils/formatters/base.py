"""
基础消息格式化器
"""

from datetime import datetime, timedelta, timezone
from typing import Any

# map_source / map_provider 兼容：中文名与英文 ID
_MAP_SOURCE_ALIASES: dict[str, str] = {
    "amap": "amap",
    "高德": "amap",
    "高德地图": "amap",
    "baidu": "baidu",
    "百度": "baidu",
    "百度地图": "baidu",
    "google": "google",
    "openstreetmap": "openstreetmap",
    "osm": "openstreetmap",
    "petallight": "openstreetmap",
    "petaldark": "openstreetmap",
    "petalmap矢量图亮": "openstreetmap",
    "petalmap矢量图暗": "openstreetmap",
    "PetalMap矢量图亮": "openstreetmap",
    "PetalMap矢量图暗": "openstreetmap",
    "arcwi": "openstreetmap",
    "arcwob": "openstreetmap",
    "arcwh": "openstreetmap",
    "geovis": "openstreetmap",
    "arcgis卫星影像": "openstreetmap",
    "arcgis地形图": "openstreetmap",
    "arcgis山影图": "openstreetmap",
    "中科星图卫星影像": "openstreetmap",
}


def normalize_map_provider(map_source: str | None) -> str:
    """将配置中的 map_source / map_provider 规范为 get_map_link 使用的 provider 名。"""
    if not map_source or not str(map_source).strip():
        return "baidu"
    raw = str(map_source).strip()
    if raw in _MAP_SOURCE_ALIASES:
        return _MAP_SOURCE_ALIASES[raw]
    key = raw.lower()
    if key in _MAP_SOURCE_ALIASES:
        return _MAP_SOURCE_ALIASES[key]
    return raw if raw in ("baidu", "amap", "google", "openstreetmap") else "baidu"


def static_map_image_url(
    latitude: float,
    longitude: float,
    zoom: int = 5,
    width: int = 640,
    height: int = 480,
) -> str:
    """
    生成 OpenStreetMap 静态示意图 URL（无需 key，用于消息中的地图图片）。
    与 map_source 瓦片样式无关；文字地图链接仍用 get_map_link。
    """
    z = max(0, min(18, int(zoom)))
    w = max(100, min(1024, int(width)))
    h = max(100, min(1024, int(height)))
    return (
        f"https://staticmap.openstreetmap.de/staticmap.php?"
        f"center={latitude},{longitude}&zoom={z}&size={w}x{h}&maptype=mapnik"
    )


class BaseMessageFormatter:
    """基础消息格式化器"""

    @staticmethod
    def format_coordinates(latitude: float, longitude: float) -> str:
        """格式化坐标显示"""
        lat_dir = "N" if latitude >= 0 else "S"
        lon_dir = "E" if longitude >= 0 else "W"
        return f"{abs(latitude):.2f}°{lat_dir}, {abs(longitude):.2f}°{lon_dir}"

    @staticmethod
    def format_time(dt: datetime, target_timezone: str = "UTC+8") -> str:
        """格式化时间显示 - 支持时区转换"""
        if not dt:
            return "未知时间"

        # 解析目标时区
        tz_offsets = {
            "UTC+0": timezone.utc,
            "UTC+8": timezone(timedelta(hours=8)),  # 北京时间
            "UTC+9": timezone(timedelta(hours=9)),  # 日本时间
        }
        target_tz = tz_offsets.get(target_timezone, timezone(timedelta(hours=8)))

        # 如果datetime带有时区信息，进行时区转换
        if dt.tzinfo is not None:
            dt = dt.astimezone(target_tz)

        return f"{dt.strftime('%Y年%m月%d日 %H时%M分%S秒')} ({target_timezone})"

    @staticmethod
    def get_map_link(
        latitude: float,
        longitude: float,
        provider: str = "baidu",
        zoom: int = 5,
        magnitude: float = None,
        place_name: str = None,
    ) -> str:
        """生成地图链接"""
        if latitude is None or longitude is None:
            return ""

        # 构建震中信息（简化版，减少URL长度）
        magnitude_info = f"M{magnitude:.1f}" if magnitude is not None else "地震"
        location_info = place_name if place_name else "震中位置"

        if provider == "openstreetmap":
            # OpenStreetMap 简洁格式
            return f"https://www.openstreetmap.org/?mlat={latitude}&mlon={longitude}&zoom={zoom}"

        elif provider == "google":
            # Google Maps 简洁格式
            return f"https://maps.google.com/maps?q={latitude},{longitude}&z={zoom}"

        elif provider == "baidu":
            # 百度地图直接使用WGS84坐标
            # 增加 coord_type=wgs84 提高精度
            # 确保 zoom 参数正确传递
            baidu_map_url = f"https://api.map.baidu.com/marker?location={latitude},{longitude}&zoom={zoom}&title={magnitude_info}+Epicenter&content={location_info[:32]}&coord_type=wgs84&output=html"
            return baidu_map_url

        elif provider == "amap":
            # 高德地图简洁格式
            # 高德Web端URI API可能不支持zoom参数，但尝试传递z参数
            return f"https://uri.amap.com/marker?position={longitude},{latitude}&name=震中位置&src=disaster_warning&coordinate=wgs84&callnative=0"

        # 默认返回百度地图
        return f"https://api.map.baidu.com/marker?location={latitude},{longitude}&zoom={zoom}&title={magnitude_info}+Epicenter&content={location_info[:32]}&coord_type=wgs84&output=html"

    @staticmethod
    def format_message(data: Any) -> str:
        """默认消息格式化"""
        lines = [f"🚨[{data.disaster_type.value}] 灾害预警 (基础格式)"]
        if hasattr(data, "id"):
            lines.append(f"📋ID: {data.id}")
        if hasattr(data, "shock_time") and data.shock_time:
            lines.append(f"⏰发震时间: {data.shock_time}")
        if hasattr(data, "place_name") and data.place_name:
            lines.append(f"📍地点: {data.place_name}")
        if hasattr(data, "raw_data") and data.raw_data:
            lines.append(f"📝数据: {data.raw_data}")
        return "\n".join(lines)

