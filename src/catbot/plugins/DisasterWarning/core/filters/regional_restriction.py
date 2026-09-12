"""
区域推送限制：按关键词与可选经纬度矩形过滤（默认深圳市范围）。
"""

from __future__ import annotations

from typing import Any

from ncatbot.utils import get_log

from ...models.models import EarthquakeData, TsunamiData, WeatherAlarmData

_log = get_log()

# 深圳市及近海大致外接矩形（WGS84），可与关键词匹配二选一或组合
_DEFAULT_SHENZHEN_BOX: dict[str, float] = {
    "min_lat": 22.40,
    "max_lat": 22.90,
    "min_lon": 113.70,
    "max_lon": 114.70,
}


class RegionalRestrictionFilter:
    """启用后，仅推送与配置区域相关的事件。"""

    def __init__(self, config: dict[str, Any]):
        self.enabled = bool(config.get("enabled", False))
        raw_kw = config.get("keywords", [])
        self.keywords = [str(k).strip() for k in raw_kw if k and str(k).strip()]
        self.use_bbox = bool(config.get("use_bounding_box", True))
        bb = {**_DEFAULT_SHENZHEN_BOX, **(config.get("bounding_box") or {})}
        self.min_lat = float(bb["min_lat"])
        self.max_lat = float(bb["max_lat"])
        self.min_lon = float(bb["min_lon"])
        self.max_lon = float(bb["max_lon"])

        if self.enabled:
            if not self.keywords and not self.use_bbox:
                _log.warning(
                    "[灾害预警] regional_restriction 已启用但未配置 keywords 且关闭 "
                    "use_bounding_box，区域限制将不生效"
                )
            _log.info(
                "[灾害预警] 区域限制已启用：关键词="
                f"{self.keywords or '(无)'}；"
                f"边界框={'开' if self.use_bbox else '关'}"
            )

    def _keyword_hit(self, text: str) -> bool:
        if not self.keywords:
            return False
        return any(kw in text for kw in self.keywords)

    def _in_bbox(self, lat: float, lon: float) -> bool:
        return (
            self.min_lat <= lat <= self.max_lat and self.min_lon <= lon <= self.max_lon
        )

    def allows_earthquake(self, eq: EarthquakeData) -> bool:
        if not self.enabled:
            return True
        if not self.keywords and not self.use_bbox:
            return True
        text = f"{eq.place_name or ''}{eq.province or ''}"
        if self._keyword_hit(text):
            return True
        if self.use_bbox:
            try:
                lat = float(eq.latitude)
                lon = float(eq.longitude)
            except (TypeError, ValueError):
                return False
            if self._in_bbox(lat, lon):
                return True
        return False

    def allows_tsunami(self, ts: TsunamiData) -> bool:
        if not self.enabled:
            return True
        if not self.keywords:
            # 海啸报文通常无稳定坐标，未配关键词时不做区域拦截
            return True
        parts: list[str] = [ts.title or "", ts.subtitle or "", ts.level or ""]
        for item in ts.forecasts or []:
            if isinstance(item, dict):
                parts.extend(str(v) for v in item.values())
            else:
                parts.append(str(item))
        text = "".join(parts)
        return self._keyword_hit(text)

    def allows_weather(self, w: WeatherAlarmData) -> bool:
        if not self.enabled:
            return True
        if not self.keywords and not self.use_bbox:
            return True
        parts: list[str] = [
            w.headline or "",
            w.title or "",
            w.description or "",
            *(w.affected_areas or []),
        ]
        text = "".join(parts)
        # 对气象预警，若配置了关键词则严格以关键词命中为准，避免外接矩形误放行邻省边界区域。
        if self.keywords:
            return self._keyword_hit(text)
        if self.use_bbox and w.latitude is not None and w.longitude is not None:
            try:
                if self._in_bbox(float(w.latitude), float(w.longitude)):
                    return True
            except (TypeError, ValueError):
                pass
        return False
