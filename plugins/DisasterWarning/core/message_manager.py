"""
消息推送管理器
实现优化的报数控制、拆分过滤器和改进的去重逻辑 - NcatBot 版本
"""

import asyncio
import os
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from ncatbot.types import MessageArray, PlainText, Image
from ncatbot.utils import get_log

from ..models.data_source_config import (
    get_intensity_based_sources,
    get_scale_based_sources,
)
from ..models.models import (
    DataSource,
    DisasterEvent,
    EarthquakeData,
    TsunamiData,
    WeatherAlarmData,
)
from ..utils.formatters import (
    BaseMessageFormatter,
    format_earthquake_message,
    format_tsunami_message,
    format_weather_message,
)
from ..utils.formatters.base import normalize_map_provider, static_map_image_url
from ..utils.formatters.earthquake import GlobalQuakeFormatter
from common.utils.CommonUtil import CommonUtil
from .event_deduplicator import EventDeduplicator
from .gq_card_renderer import render_global_quake_card_png
from .weather_card_renderer import render_weather_card_png
from .filters import (
    GlobalQuakeFilter,
    IntensityFilter,
    LocalIntensityFilter,
    RegionalRestrictionFilter,
    ReportCountController,
    ScaleFilter,
    USGSFilter,
    WeatherFilter,
)

_log = get_log()


class MessagePushManager:
    """消息推送管理器"""

    def __init__(self, config: dict[str, Any], api):
        self.config = config
        self.api = api
        self.plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # 初始化数据存储目录
        self.storage_dir = os.path.join(self.plugin_root, "data")
        self.temp_dir = os.path.join(self.storage_dir, "temp")
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir, exist_ok=True)

        self.data_dir = self.plugin_root

        # 初始化过滤器
        earthquake_filters = config.get("earthquake_filters", {})

        # 烈度过滤器配置
        intensity_filter_config = earthquake_filters.get("intensity_filter", {})
        self.intensity_filter = IntensityFilter(
            enabled=intensity_filter_config.get("enabled", True),
            min_magnitude=intensity_filter_config.get("min_magnitude", 2.0),
            min_intensity=intensity_filter_config.get("min_intensity", 4.0),
        )

        # 震度过滤器配置
        scale_filter_config = earthquake_filters.get("scale_filter", {})
        self.scale_filter = ScaleFilter(
            enabled=scale_filter_config.get("enabled", True),
            min_magnitude=scale_filter_config.get("min_magnitude", 2.0),
            min_scale=scale_filter_config.get("min_scale", 1.0),
        )

        # USGS过滤器配置
        magnitude_only_filter_config = earthquake_filters.get(
            "magnitude_only_filter", {}
        )
        self.usgs_filter = USGSFilter(
            enabled=magnitude_only_filter_config.get("enabled", True),
            min_magnitude=magnitude_only_filter_config.get("min_magnitude", 4.5),
        )

        # Global Quake过滤器配置
        global_quake_filter_config = earthquake_filters.get("global_quake_filter", {})
        self.global_quake_filter = GlobalQuakeFilter(
            enabled=global_quake_filter_config.get("enabled", True),
            min_magnitude=global_quake_filter_config.get("min_magnitude", 4.5),
            min_intensity=global_quake_filter_config.get("min_intensity", 5.0),
        )

        # 初始化报数控制器
        push_config = config.get("push_frequency_control", {})
        self.report_controller = ReportCountController(
            cea_cwa_report_n=push_config.get("cea_cwa_report_n", 1),
            jma_report_n=push_config.get("jma_report_n", 3),
            gq_report_n=push_config.get("gq_report_n", 5),
            final_report_always_push=push_config.get("final_report_always_push", True),
            ignore_non_final_reports=push_config.get("ignore_non_final_reports", False),
        )

        # 初始化去重器
        self.deduplicator = EventDeduplicator(
            time_window_minutes=config.get("event_deduplication", {}).get(
                "time_window_minutes", 1
            ),
            location_tolerance_km=config.get("event_deduplication", {}).get(
                "location_tolerance_km", 20.0
            ),
            magnitude_tolerance=config.get("event_deduplication", {}).get(
                "magnitude_tolerance", 0.5
            ),
        )

        # 目标会话
        self.target_groups = config.get("target_groups", [])

        # 初始化本地监控过滤器
        self.local_monitor = LocalIntensityFilter(config.get("local_monitoring", {}))

        # 初始化气象预警过滤器
        weather_config = config.get("weather_config", {})
        weather_filter_config = weather_config.get("weather_filter", {})
        self.weather_filter = WeatherFilter(weather_filter_config)

        self.regional = RegionalRestrictionFilter(
            config.get("regional_restriction", {})
        )

    def should_push_event(self, event: DisasterEvent) -> bool:
        """判断是否应该推送事件"""
        # 1. 时间检查
        event_time_aware = self._get_event_time(event)

        if event_time_aware:
            current_time_utc = datetime.now(timezone.utc)
            time_diff = (current_time_utc - event_time_aware).total_seconds() / 3600

            if time_diff > 1:
                _log.info(f"[灾害预警] 事件时间过早（{time_diff:.1f}小时前），过滤")
                return False

        # 2. 区域限制（地震 / 海啸 / 气象）
        if isinstance(event.data, EarthquakeData):
            if not self.regional.allows_earthquake(event.data):
                _log.info(
                    "[灾害预警] 区域限制：地震不在深圳范围（关键词与边界框均未命中），已过滤"
                )
                return False
        elif isinstance(event.data, TsunamiData):
            if not self.regional.allows_tsunami(event.data):
                _log.info("[灾害预警] 区域限制：海啸报文未涉及深圳关键词，已过滤")
                return False
            return True
        elif isinstance(event.data, WeatherAlarmData):
            if not self.regional.allows_weather(event.data):
                _log.info("[灾害预警] 区域限制：气象预警与深圳无关，已过滤")
                return False
            headline = event.data.headline or event.data.title or ""
            if self.weather_filter.should_filter(headline):
                return False
            return True

        # 3. 地震事件专用过滤逻辑
        earthquake = event.data
        source_id = self._get_source_id(event)

        # 数据源专用过滤器
        if source_id == "global_quake":
            if self.global_quake_filter.should_filter(earthquake):
                _log.info(f"[灾害预警] 事件被Global Quake过滤器过滤: {source_id}")
                return False
        elif source_id in get_intensity_based_sources():
            if self.intensity_filter.should_filter(earthquake):
                _log.info(f"[灾害预警] 事件被烈度过滤器过滤: {source_id}")
                return False
        elif source_id in get_scale_based_sources():
            if self.scale_filter.should_filter(earthquake):
                _log.info(f"[灾害预警] 事件被震度过滤器过滤: {source_id}")
                return False
        elif source_id == "usgs_fanstudio":
            if self.usgs_filter.should_filter(earthquake):
                _log.info(f"[灾害预警] 事件被USGS过滤器过滤: {source_id}")
                return False

        # 报数控制
        if not self.report_controller.should_push_report(event):
            _log.info(f"[灾害预警] 事件被报数控制器过滤: {source_id}")
            return False

        # 本地烈度过滤与注入
        result = self.local_monitor.inject_local_estimation(earthquake)
        if result is not None and not result.get("is_allowed", True):
            return False

        return True

    def _get_event_time(self, event: DisasterEvent) -> datetime | None:
        """获取灾害事件的带时区时间"""
        raw_time = None
        if isinstance(event.data, EarthquakeData):
            raw_time = event.data.shock_time
        elif isinstance(event.data, TsunamiData):
            raw_time = event.data.issue_time
        elif isinstance(event.data, WeatherAlarmData):
            raw_time = event.data.effective_time or event.data.issue_time

        if not raw_time:
            return None

        if raw_time.tzinfo is not None:
            return raw_time

        source_id = event.source_id or self._get_source_id(event)

        tz_jst = timezone(timedelta(hours=9))
        tz_cst = timezone(timedelta(hours=8))
        tz_utc = timezone.utc

        if (
            "jma" in source_id
            or "p2p" in source_id
            or source_id == "wolfx_jma_eew"
            or source_id == "wolfx_jma_eq"
        ):
            return raw_time.replace(tzinfo=tz_jst)

        if "global_quake" in source_id:
            return raw_time.replace(tzinfo=tz_utc)

        return raw_time.replace(tzinfo=tz_cst)

    def _get_source_id(self, event: DisasterEvent) -> str:
        """获取事件的数据源ID"""
        source_mapping = {
            DataSource.FAN_STUDIO_CEA.value: "cea_fanstudio",
            DataSource.WOLFX_CENC_EEW.value: "cea_wolfx",
            DataSource.FAN_STUDIO_CWA.value: "cwa_fanstudio",
            DataSource.WOLFX_CWA_EEW.value: "cwa_wolfx",
            DataSource.FAN_STUDIO_JMA.value: "jma_fanstudio",
            DataSource.P2P_EEW.value: "jma_p2p",
            DataSource.WOLFX_JMA_EEW.value: "jma_wolfx",
            DataSource.FAN_STUDIO_CENC.value: "cenc_fanstudio",
            DataSource.WOLFX_CENC_EQ.value: "cenc_wolfx",
            DataSource.P2P_EARTHQUAKE.value: "jma_p2p_info",
            DataSource.WOLFX_JMA_EQ.value: "jma_wolfx_info",
            DataSource.FAN_STUDIO_USGS.value: "usgs_fanstudio",
            DataSource.GLOBAL_QUAKE.value: "global_quake",
            DataSource.FAN_STUDIO_WEATHER.value: "china_weather_fanstudio",
            DataSource.FAN_STUDIO_TSUNAMI.value: "china_tsunami_fanstudio",
            DataSource.P2P_TSUNAMI.value: "jma_tsunami_p2p",
        }

        return source_mapping.get(event.source.value, event.source.value)

    async def push_event(self, event: DisasterEvent) -> bool:
        """推送事件"""
        _log.debug(f"[灾害预警] 处理事件推送: {event.id}")

        # 1. 先去重检查
        if not self.deduplicator.should_push_event(event):
            _log.debug(f"[灾害预警] 事件 {event.id} 被去重器过滤")
            return False

        # 2. 推送条件检查
        if not self.should_push_event(event):
            _log.debug(f"[灾害预警] 事件 {event.id} 未通过推送条件检查")
            return False

        try:
            # 3. 构建消息
            message = await self._build_message_array_async(event)
            _log.debug("[灾害预警] 消息构建完成")

            # 4. 获取目标群
            if not self.target_groups:
                _log.warning("[灾害预警] 没有配置目标群，无法推送消息")
                return False

            # 5. 推送消息
            push_success_count = 0
            send_tasks = [
                self._send_message(group_id, message) for group_id in self.target_groups
            ]
            results = await asyncio.gather(*send_tasks, return_exceptions=True)
            for group_id, result in zip(self.target_groups, results):
                if isinstance(result, Exception):
                    _log.error(f"[灾害预警] 推送到群 {group_id} 失败: {result}")
                else:
                    _log.info(f"[灾害预警] 消息已推送到群 {group_id}")
                    push_success_count += 1

            # 6. 记录推送
            _log.info(
                f"[灾害预警] 事件 {event.id} 推送完成，成功推送到 {push_success_count} 个群"
            )
            return push_success_count > 0

        except Exception as e:
            _log.error(f"[灾害预警] 推送事件失败: {e}")
            return False

    def _build_message_text(
        self,
        event: DisasterEvent,
        source_id: str,
        *,
        append_map_link: bool,
        map_provider: str,
        map_zoom_level: int,
        detailed_jma: bool,
    ) -> str:
        """构建纯文本消息（可选末尾地图链接）"""
        if isinstance(event.data, WeatherAlarmData):
            weather_config = self.config.get("weather_config", {})
            options = {
                "max_description_length": weather_config.get(
                    "max_description_length", 384
                )
            }
            message_text = format_weather_message(source_id, event.data, options)
        elif isinstance(event.data, TsunamiData):
            message_text = format_tsunami_message(source_id, event.data)
        elif isinstance(event.data, EarthquakeData):
            options = {"detailed_jma_intensity": detailed_jma}
            message_text = format_earthquake_message(source_id, event.data, options)
        else:
            _log.warning(f"[灾害预警] 未知事件类型: {type(event.data)}")
            message_text = f"🚨[未知事件]\n📋事件ID：{event.id}\n⏰时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        if append_map_link and isinstance(event.data, EarthquakeData):
            if event.data.latitude is not None and event.data.longitude is not None:
                map_url = BaseMessageFormatter.get_map_link(
                    event.data.latitude,
                    event.data.longitude,
                    map_provider,
                    map_zoom_level,
                    magnitude=event.data.magnitude,
                    place_name=event.data.place_name,
                )
                if map_url:
                    zero_width_space = "\u200b"
                    encoded_map_url = urllib.parse.quote(map_url, safe=":/?&=+")
                    message_text += f"{zero_width_space}\n🗺️地图链接:{zero_width_space} {encoded_map_url}"

        return message_text

    async def _download_static_map(self, url: str) -> Path | None:
        """下载静态地图示意图为本地临时文件"""
        path = Path(self.temp_dir) / f"eq_map_{uuid.uuid4().hex}.png"
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                content = response.content
            await asyncio.to_thread(path.write_bytes, content)
        except Exception as e:
            _log.warning(f"[灾害预警] 静态地图下载失败: {e}")
            return None
        if path.is_file() and path.stat().st_size > 0:
            return path
        return None

    async def _build_message_array_async(self, event: DisasterEvent) -> MessageArray:
        """构建消息链（文本 + 可选 Global Quake 卡片图 + 可选震中地图图）"""
        source_id = self._get_source_id(event)
        mf = self.config.get("message_format", {})
        map_provider = normalize_map_provider(
            mf.get("map_source") or mf.get("map_provider")
        )
        map_zoom = int(mf.get("map_zoom_level", 5))
        detailed_jma = mf.get("detailed_jma_intensity", False)

        # 气象预警：优先 PIL 卡片；生成失败时回退纯文本。
        if isinstance(event.data, WeatherAlarmData):
            wc = self.config.get("weather_config", {})
            use_card = wc.get("render_card", True)
            chain = MessageArray()
            if use_card:
                png = await asyncio.to_thread(
                    render_weather_card_png,
                    event.data,
                    Path(self.temp_dir),
                    wc,
                )
                if png:
                    chain.add_segment(Image(file=str(png)))
                    return chain

            text = self._build_message_text(
                event,
                source_id,
                append_map_link=False,
                map_provider=map_provider,
                map_zoom_level=map_zoom,
                detailed_jma=detailed_jma,
            )
            chain.add_segment(PlainText(text=text))
            return chain

        include_map_image = mf.get("include_map_image", False)
        include_map_link = mf.get("include_map_link", False)
        legacy_include_map = mf.get("include_map", False)
        if legacy_include_map and not include_map_image:
            include_map_link = True

        use_gq_card = (
            mf.get("use_global_quake_card", False)
            and source_id == "global_quake"
            and isinstance(event.data, EarthquakeData)
            and event.data.latitude is not None
            and event.data.longitude is not None
        )

        chain = MessageArray()

        if use_gq_card:
            ctx = GlobalQuakeFormatter.get_render_context(event.data)
            card_root = Path(self.plugin_root) / "resources" / "card_templates"
            png = await render_global_quake_card_png(
                context=ctx,
                card_templates_root=card_root,
                template_name=str(mf.get("global_quake_template", "Aurora")),
                out_dir=Path(self.temp_dir),
                playwright_mode=str(mf.get("playwright_mode", "local")),
                playwright_server_url=str(mf.get("playwright_server_url", "") or ""),
            )
            if png:
                caption = str(
                    mf.get("global_quake_caption", "🚨 [地震预警] Global Quake")
                ).strip()
                if caption:
                    chain.add_segment(PlainText(text=caption))
                chain.add_segment(Image(file=str(png)))
            else:
                text = self._build_message_text(
                    event,
                    source_id,
                    append_map_link=include_map_link and not include_map_image,
                    map_provider=map_provider,
                    map_zoom_level=map_zoom,
                    detailed_jma=detailed_jma,
                )
                chain.add_segment(PlainText(text=text))
        else:
            text = self._build_message_text(
                event,
                source_id,
                append_map_link=include_map_link and not include_map_image,
                map_provider=map_provider,
                map_zoom_level=map_zoom,
                detailed_jma=detailed_jma,
            )
            chain.add_segment(PlainText(text=text))

        if include_map_image and isinstance(event.data, EarthquakeData):
            if event.data.latitude is not None and event.data.longitude is not None:
                map_url = static_map_image_url(
                    event.data.latitude,
                    event.data.longitude,
                    zoom=map_zoom,
                )
                local_map = await self._download_static_map(map_url)
                if local_map:
                    chain.add_segment(Image(file=str(local_map)))

        return chain

    async def _send_message(self, group_id: int, message: MessageArray):
        """发送消息到指定群"""
        await self.api.qq.post_group_array_msg(group_id=group_id, msg=message)

    def cleanup_temp_files(self, max_age_seconds: float = 86400) -> int:
        """清理 temp 目录中过期的卡片/地图图片（默认保留 24 小时）。"""
        removed = CommonUtil.cleanup_old_files(
            self.temp_dir, max_age_seconds=max_age_seconds
        )
        if removed:
            _log.info(f"[灾害预警] 已清理 {removed} 个过期临时文件")
        return removed

    def cleanup_old_records(self):
        """清理旧记录"""
        self.deduplicator.cleanup_old_events()
        self.cleanup_temp_files()
