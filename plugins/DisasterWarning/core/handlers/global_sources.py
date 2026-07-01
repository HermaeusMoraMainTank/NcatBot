"""
全球及其他地震资源处理器
包含 USGS 和 GlobalQuake 相关处理器
"""

import json
from datetime import datetime, timezone
from typing import Any

from ncatbot.utils import get_log

from ...models.models import (
    DataSource,
    DisasterEvent,
    DisasterType,
    EarthquakeData,
)
from ...utils.fe_regions import translate_place_name
from .base import BaseDataHandler

_log = get_log()


class GlobalQuakeHandler(BaseDataHandler):
    """Global Quake处理器 - 适配新的WebSocket JSON消息格式"""

    # 罗马数字到阿拉伯数字的映射
    ROMAN_TO_INT = {
        "I": 1,
        "II": 2,
        "III": 3,
        "IV": 4,
        "V": 5,
        "VI": 6,
        "VII": 7,
        "VIII": 8,
        "IX": 9,
        "X": 10,
        "XI": 11,
        "XII": 12,
    }

    def __init__(self, message_logger=None):
        super().__init__("global_quake", message_logger)

    def parse_message(self, message: str) -> DisasterEvent | None:
        """解析Global Quake消息"""
        try:
            data = json.loads(message)

            # 检查消息类型
            msg_type = data.get("type")
            action = data.get("action")

            if msg_type == "earthquake":
                _log.debug(
                    f"[灾害预警] {self.source_id} 收到地震消息，action: {action}"
                )
                return self._parse_earthquake_data(data)
            else:
                _log.debug(f"[灾害预警] {self.source_id} 忽略消息类型: {msg_type}")
                return None

        except json.JSONDecodeError as e:
            _log.error(f"[灾害预警] {self.source_id} JSON解析失败: {e}")
            return None
        except Exception as e:
            _log.error(f"[灾害预警] {self.source_id} 消息处理失败: {e}")
            return None

    def _parse_earthquake_data(self, data: dict[str, Any]) -> DisasterEvent | None:
        """解析地震数据 - 适配新的GlobalQuake Monitor格式"""
        try:
            # 获取实际地震数据
            eq_data = data.get("data", {})
            if not eq_data:
                _log.warning(f"[灾害预警] {self.source_id} 消息中没有data字段")
                return None

            # 解析震源时间
            shock_time = None
            origin_time_iso = eq_data.get("originTimeIso")
            if origin_time_iso:
                shock_time = self._parse_datetime(origin_time_iso)
            elif eq_data.get("originTimeMs"):
                # 从毫秒时间戳解析
                shock_time = datetime.fromtimestamp(
                    eq_data["originTimeMs"] / 1000, tz=timezone.utc
                )

            # 解析烈度（从罗马数字转换）
            intensity = None
            intensity_str = eq_data.get("intensity", "")
            if intensity_str and intensity_str in self.ROMAN_TO_INT:
                intensity = float(self.ROMAN_TO_INT[intensity_str])

            # 获取坐标
            latitude = eq_data.get("latitude", 0)
            longitude = eq_data.get("longitude", 0)

            # 格式化震级和深度 - 保留1位小数，与其他数据源保持一致
            magnitude_raw = eq_data.get("magnitude")
            if magnitude_raw is not None:
                try:
                    magnitude = round(float(magnitude_raw), 1)
                except (ValueError, TypeError):
                    magnitude = None
            else:
                magnitude = None

            depth_raw = eq_data.get("depth")
            if depth_raw is not None:
                try:
                    depth = round(float(depth_raw), 1)
                except (ValueError, TypeError):
                    depth = None
            else:
                depth = None

            # 翻译地名（使用FE Regions，类似USGS处理）
            original_region = eq_data.get("region", "未知地点")
            place_name = translate_place_name(
                original_region, latitude, longitude, fallback_to_original=True
            )

            # 获取最大加速度和测站信息
            max_pga = eq_data.get("maxPGA")
            station_count = eq_data.get("stationCount")

            # 创建地震数据对象
            earthquake = EarthquakeData(
                id=eq_data.get("id", ""),
                event_id=eq_data.get("id", ""),
                source=DataSource.GLOBAL_QUAKE,
                disaster_type=DisasterType.EARTHQUAKE,
                shock_time=shock_time or datetime.now(),
                latitude=latitude,
                longitude=longitude,
                depth=depth,
                magnitude=magnitude,
                intensity=intensity,
                place_name=place_name,
                updates=eq_data.get("revisionId", 1),
                raw_data=data,
                max_pga=max_pga,
                stations=station_count,
            )

            _log.info(
                f"[灾害预警] Global Quake地震解析成功: {earthquake.place_name} "
                f"(M {earthquake.magnitude:.1f}), 烈度: {intensity_str}, "
                f"时间: {earthquake.shock_time}"
            )

            return DisasterEvent(
                id=earthquake.id,
                data=earthquake,
                source=earthquake.source,
                disaster_type=earthquake.disaster_type,
            )
        except Exception as e:
            _log.error(f"[灾害预警] {self.source_id} 解析地震数据失败: {e}")
            return None

    def _parse_text_message(self, message: str) -> DisasterEvent | None:
        """解析文本消息 - 保留向后兼容"""
        _log.debug(f"[灾害预警] {self.source_id} 文本消息: {message}")
        return None

    def _parse_data(self, data: dict[str, Any]) -> DisasterEvent | None:
        """实现基类抽象方法"""
        return self._parse_earthquake_data(data)


class USGSEarthquakeHandler(BaseDataHandler):
    """美国地质调查局地震情报处理器"""

    def __init__(self, message_logger=None):
        super().__init__("usgs_fanstudio", message_logger)

    def _parse_data(self, data: dict[str, Any]) -> DisasterEvent | None:
        """解析USGS地震数据"""
        try:
            # 获取实际数据 - 兼容多种格式
            msg_data = data.get("Data", {}) or data.get("data", {}) or data
            if not msg_data:
                _log.debug(f"[灾害预警] {self.source_id} 消息中没有有效数据")
                return None

            # 记录数据获取情况用于调试
            if "Data" in data:
                _log.debug(f"[灾害预警] {self.source_id} 使用Data字段获取数据")
            elif "data" in data:
                _log.debug(f"[灾害预警] {self.source_id} 使用data字段获取数据")
            else:
                _log.debug(f"[灾害预警] {self.source_id} 使用整个消息作为数据")

            # 心跳包检测 - 在详细处理前进行快速过滤
            if self._is_heartbeat_message(msg_data):
                return None

            # 检查关键字段 - 兼容大小写（仅记录警告，不阻止处理）
            required_fields = ["id", "magnitude", "latitude", "longitude", "shockTime"]
            missing_fields = []
            for field in required_fields:
                # 检查小写和大写版本
                if field not in msg_data and field.capitalize() not in msg_data:
                    missing_fields.append(field)
                elif field in msg_data and msg_data[field] is None:
                    missing_fields.append(field)
                elif (
                    field.capitalize() in msg_data
                    and msg_data[field.capitalize()] is None
                ):
                    missing_fields.append(field)

            if missing_fields:
                _log.debug(
                    f"[灾害预警] {self.source_id} 数据缺少部分字段: {missing_fields}，继续处理..."
                )

            # 优化USGS数据精度 - 四舍五入到1位小数
            def get_field(data, field_name):
                """获取字段值，兼容大小写"""
                return data.get(field_name) or data.get(field_name.capitalize())

            magnitude_raw = get_field(msg_data, "magnitude")
            if magnitude_raw is not None:
                try:
                    magnitude = round(float(magnitude_raw), 1)
                except (ValueError, TypeError):
                    magnitude = None
            else:
                magnitude = None

            depth_raw = get_field(msg_data, "depth")
            if depth_raw is not None:
                try:
                    depth = round(float(depth_raw), 1)
                except (ValueError, TypeError):
                    depth = None
            else:
                depth = None

            # 验证关键字段 - 如果缺少关键信息，不创建地震对象
            usgs_id = get_field(msg_data, "id") or ""
            usgs_latitude = float(get_field(msg_data, "latitude") or 0)
            usgs_longitude = float(get_field(msg_data, "longitude") or 0)
            usgs_place_name_en = get_field(msg_data, "placeName") or ""

            if not usgs_id:
                # 只有在非心跳包情况下才记录警告，且避免重复警告
                if not self._is_heartbeat_message(msg_data):
                    warning_msg = f"[灾害预警] {self.source_id} 缺少地震ID，跳过处理"
                    if self._should_log_warning("missing_usgs_id", warning_msg):
                        _log.warning(warning_msg)
                return None

            if usgs_latitude == 0 and usgs_longitude == 0:
                # 心跳包检测已经处理了这种情况，这里不再重复记录
                return None

            if not usgs_place_name_en and not magnitude:
                # 只有在非心跳包情况下才记录警告，且避免重复警告
                if not self._is_heartbeat_message(msg_data):
                    warning_msg = (
                        f"[灾害预警] {self.source_id} 缺少地点名称和震级信息，跳过处理"
                    )
                    if self._should_log_warning(
                        "missing_usgs_place_magnitude", warning_msg
                    ):
                        _log.warning(warning_msg)
                return None

            # 🌏 FE Regions 中文翻译
            # 将 USGS 英文地名翻译为中文（基于 F-E 地震区划）
            usgs_place_name = translate_place_name(
                usgs_place_name_en,
                usgs_latitude,
                usgs_longitude,
                fallback_to_original=True,  # 翻译失败时保留英文
            )

            # 记录翻译结果（仅在翻译成功时）
            if usgs_place_name != usgs_place_name_en:
                _log.debug(
                    f"[灾害预警] {self.source_id} FE翻译: '{usgs_place_name_en}' → '{usgs_place_name}'"
                )

            earthquake = EarthquakeData(
                id=usgs_id,
                event_id=usgs_id,
                source=DataSource.FAN_STUDIO_USGS,
                disaster_type=DisasterType.EARTHQUAKE,
                shock_time=self._parse_datetime(get_field(msg_data, "shockTime")),
                update_time=self._parse_datetime(get_field(msg_data, "updateTime")),
                latitude=usgs_latitude,
                longitude=usgs_longitude,
                depth=depth,
                magnitude=magnitude,
                place_name=usgs_place_name,
                info_type=get_field(msg_data, "infoTypeName") or "",
                raw_data=msg_data,
            )

            _log.info(
                f"[灾害预警] 地震数据解析成功: {earthquake.place_name} (M {earthquake.magnitude}), 时间: {earthquake.shock_time}"
            )

            return DisasterEvent(
                id=earthquake.id,
                data=earthquake,
                source=earthquake.source,
                disaster_type=earthquake.disaster_type,
            )
        except Exception as e:
            _log.error(f"[灾害预警] {self.source_id} 解析数据失败: {e}")
            return None
