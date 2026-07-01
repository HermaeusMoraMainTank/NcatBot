"""
灾害预警核心服务
整合所有重构的组件 - NcatBot 版本
"""

import asyncio
import json
import traceback
from datetime import datetime
from typing import Any

from ncatbot.utils import get_log

from ..models.models import (
    DataSource,
    DisasterEvent,
    DisasterType,
    EarthquakeData,
    TsunamiData,
    WeatherAlarmData,
)
from .handler_registry import WebSocketHandlerRegistry
from .handlers import DATA_HANDLERS
from .message_logger import MessageLogger
from .message_manager import MessagePushManager
from .statistics_manager import StatisticsManager
from .websocket_manager import HTTPDataFetcher, WebSocketManager

_log = get_log()


class DisasterWarningService:
    """灾害预警核心服务"""

    def __init__(self, config: dict[str, Any], api, bot_id: int):
        self.config = config
        self.api = api
        self.bot_id = bot_id
        self.running = False

        # 初始化消息记录器
        self.message_logger = MessageLogger(config, "disaster_warning")

        # 初始化统计管理器
        self.statistics_manager = StatisticsManager()

        # 初始化组件
        self.ws_manager = WebSocketManager(
            config.get("websocket_config", {}), self.message_logger
        )
        self.http_fetcher: HTTPDataFetcher | None = None
        self.message_manager = MessagePushManager(config, api)

        # 数据处理器
        self.handlers = {}
        self._initialize_handlers()

        # 连接配置
        self.connections = {}
        self.connection_tasks = []

        # 定时任务
        self.scheduled_tasks = []

    def _initialize_handlers(self):
        """初始化数据处理器"""
        for source_id, handler_class in DATA_HANDLERS.items():
            self.handlers[source_id] = handler_class(self.message_logger)

    async def initialize(self):
        """初始化服务"""
        try:
            _log.info("[灾害预警] 正在初始化灾害预警服务...")

            # 初始化HTTP获取器
            self.http_fetcher = HTTPDataFetcher(self.config)

            # 注册WebSocket消息处理器
            self._register_handlers()

            # 配置连接
            self._configure_connections()

            _log.info("[灾害预警] 灾害预警服务初始化完成")

        except Exception as e:
            _log.error(f"[灾害预警] 初始化服务失败: {e}")
            raise

    def _register_handlers(self):
        """注册消息处理器"""
        registry = WebSocketHandlerRegistry(self)
        registry.register_all(self.ws_manager)

    def _configure_connections(self):
        """配置连接 - 适配数据源配置"""
        data_sources = self.config.get("data_sources", {})

        # FAN Studio连接配置
        fan_studio_config = data_sources.get("fan_studio", {})
        if isinstance(fan_studio_config, dict) and fan_studio_config.get(
            "enabled", True
        ):
            primary_server = "wss://ws.fanstudio.tech"
            backup_server = "wss://ws.fanstudio.hk"

            fan_sub_sources = [
                "china_earthquake_warning",
                "taiwan_cwa_earthquake",
                "china_cenc_earthquake",
                "usgs_earthquake",
                "china_weather_alarm",
                "china_tsunami",
                "japan_jma_eew",
            ]

            any_fan_source_enabled = any(
                fan_studio_config.get(source, True) for source in fan_sub_sources
            )

            if any_fan_source_enabled:
                self.connections["fan_studio_all"] = {
                    "url": f"{primary_server}/all",
                    "backup_url": f"{backup_server}/all",
                    "handler": "fan_studio",
                }
                _log.info("[灾害预警] 已配置 FAN Studio 全量数据连接 (/all)")

        # P2P连接配置
        p2p_config = data_sources.get("p2p_earthquake", {})
        if isinstance(p2p_config, dict) and p2p_config.get("enabled", True):
            p2p_enabled = False
            if p2p_config.get("japan_jma_eew", True):
                p2p_enabled = True
            if p2p_config.get("japan_jma_earthquake", True):
                p2p_enabled = True
            if p2p_config.get("japan_jma_tsunami", True):
                p2p_enabled = True

            if p2p_enabled:
                self.connections["p2p_main"] = {
                    "url": "wss://api.p2pquake.net/v2/ws",
                    "handler": "p2p",
                }

        # Wolfx连接配置
        wolfx_config = data_sources.get("wolfx", {})
        if isinstance(wolfx_config, dict) and wolfx_config.get("enabled", True):
            wolfx_sources = [
                ("japan_jma_eew", "wss://ws-api.wolfx.jp/jma_eew"),
                ("china_cenc_eew", "wss://ws-api.wolfx.jp/cenc_eew"),
                ("taiwan_cwa_eew", "wss://ws-api.wolfx.jp/cwa_eew"),
                ("japan_jma_earthquake", "wss://ws-api.wolfx.jp/jma_eqlist"),
                ("china_cenc_earthquake", "wss://ws-api.wolfx.jp/cenc_eqlist"),
            ]

            for source_key, url in wolfx_sources:
                if wolfx_config.get(source_key, True):
                    conn_name = f"wolfx_{source_key}"
                    self.connections[conn_name] = {"url": url, "handler": "wolfx"}

        # Global Quake连接配置
        global_quake_config = data_sources.get("global_quake", {})
        if isinstance(global_quake_config, dict) and global_quake_config.get(
            "enabled", False
        ):
            global_quake_url = "wss://gqm.aloys233.top/ws"
            self.connections["global_quake"] = {
                "url": global_quake_url,
                "handler": "global_quake",
            }
            _log.info("[灾害预警] Global Quake 数据源已启用")

    async def start(self):
        """启动服务"""
        if self.running:
            return

        try:
            self.running = True
            self.start_time = datetime.now()
            _log.info("[灾害预警] 正在启动灾害预警服务...")

            # 启动WebSocket管理器
            await self.ws_manager.start()

            # 建立WebSocket连接
            await self._establish_websocket_connections()

            # 启动定时HTTP数据获取
            await self._start_scheduled_http_fetch()

            # 启动时清理上次运行遗留的临时文件
            self.message_manager.cleanup_temp_files()

            # 启动清理任务
            await self._start_cleanup_task()

            # 检查并提示日志记录器状态
            if self.message_logger.enabled:
                _log.info(
                    f"[灾害预警] 原始消息日志记录已启用，日志文件: {self.message_logger.log_file_path}"
                )
            else:
                _log.info(
                    "[灾害预警] 原始消息日志记录未启用。如需调试或记录原始数据，请使用命令 '灾害预警日志开关' 启用。"
                )

            _log.info("[灾害预警] 灾害预警服务已启动")

        except Exception as e:
            _log.error(f"[灾害预警] 启动服务失败: {e}")
            self.running = False
            raise

    async def stop(self):
        """停止服务"""
        if not self.running:
            return

        try:
            self.running = False
            _log.info("[灾害预警] 正在停止灾害预警服务...")

            # 取消所有任务
            for task in self.connection_tasks:
                task.cancel()

            for task in self.scheduled_tasks:
                task.cancel()

            # 停止WebSocket管理器
            await self.ws_manager.stop()

            # 关闭HTTP获取器
            if self.http_fetcher:
                await self.http_fetcher.__aexit__(None, None, None)

            _log.info("[灾害预警] 灾害预警服务已停止")

        except Exception as e:
            _log.error(f"[灾害预警] 停止服务时出错: {e}")

    async def _establish_websocket_connections(self):
        """建立WebSocket连接"""
        for conn_name, conn_config in self.connections.items():
            if conn_config["handler"] in ["fan_studio", "p2p", "wolfx", "global_quake"]:
                connection_info = {
                    "connection_name": conn_name,
                    "handler_type": conn_config["handler"],
                    "data_source": self._get_data_source_from_connection(conn_name),
                    "established_time": None,
                    "backup_url": conn_config.get("backup_url"),
                }

                task = asyncio.create_task(
                    self.ws_manager.connect(
                        name=conn_name,
                        uri=conn_config["url"],
                        connection_info=connection_info,
                    )
                )
                self.connection_tasks.append(task)

                backup_info = (
                    f", 备用: {conn_config.get('backup_url')}"
                    if conn_config.get("backup_url")
                    else ""
                )
                _log.info(
                    f"[灾害预警] 已启动WebSocket连接任务: {conn_name} (数据源: {connection_info['data_source']}{backup_info})"
                )

    def _get_data_source_from_connection(self, connection_name: str) -> str:
        """从连接名称获取数据源ID"""
        connection_mapping = {
            "fan_studio_all": "fan_studio_mixed",
            "p2p_main": "jma_p2p",
            "wolfx_japan_jma_eew": "jma_wolfx",
            "wolfx_china_cenc_eew": "cea_wolfx",
            "wolfx_taiwan_cwa_eew": "cwa_wolfx",
            "wolfx_china_cenc_earthquake": "cenc_wolfx",
            "wolfx_japan_jma_earthquake": "jma_wolfx_info",
            "global_quake": "global_quake",
        }

        return connection_mapping.get(connection_name, "unknown")

    def is_fan_studio_source_enabled(self, source_key: str) -> bool:
        """检查特定的 FAN Studio 数据源是否启用"""
        data_sources = self.config.get("data_sources", {})
        fan_studio_config = data_sources.get("fan_studio", {})

        if not isinstance(fan_studio_config, dict) or not fan_studio_config.get(
            "enabled", True
        ):
            return False

        return fan_studio_config.get(source_key, True)

    async def _start_scheduled_http_fetch(self):
        """启动定时HTTP数据获取"""

        async def fetch_wolfx_data():
            while self.running:
                try:
                    await asyncio.sleep(300)  # 5分钟获取一次

                    async with self.http_fetcher as fetcher:
                        # 获取中国地震台网地震列表
                        cenc_data = await fetcher.fetch_json(
                            "https://api.wolfx.jp/cenc_eqlist.json"
                        )
                        if cenc_data:
                            handler = self.handlers.get("cenc_wolfx")
                            if handler:
                                event = handler.parse_message(json.dumps(cenc_data))
                                if event:
                                    await self._handle_disaster_event(event)

                        # 获取日本气象厅地震列表
                        jma_data = await fetcher.fetch_json(
                            "https://api.wolfx.jp/jma_eqlist.json"
                        )
                        if jma_data:
                            handler = self.handlers.get("jma_wolfx_info")
                            if handler:
                                event = handler.parse_message(json.dumps(jma_data))
                                if event:
                                    await self._handle_disaster_event(event)

                except Exception as e:
                    _log.error(f"[灾害预警] 定时HTTP数据获取失败: {e}")

        task = asyncio.create_task(fetch_wolfx_data())
        self.scheduled_tasks.append(task)

    async def _start_cleanup_task(self):
        """启动清理任务"""

        async def cleanup():
            while self.running:
                try:
                    await asyncio.sleep(86400)  # 每天清理一次
                    self.message_manager.cleanup_old_records()
                except Exception as e:
                    _log.error(f"[灾害预警] 清理任务失败: {e}")

        task = asyncio.create_task(cleanup())
        self.scheduled_tasks.append(task)

    def is_in_silence_period(self) -> bool:
        """检查是否处于启动后的静默期"""
        if not hasattr(self, "start_time"):
            return False

        debug_config = self.config.get("debug_config", {})
        silence_duration = debug_config.get("startup_silence_duration", 0)

        if silence_duration <= 0:
            return False

        elapsed = (datetime.now() - self.start_time).total_seconds()
        return elapsed < silence_duration

    async def _handle_disaster_event(self, event: DisasterEvent):
        """处理灾害事件"""
        # 检查静默期
        if self.is_in_silence_period():
            debug_config = self.config.get("debug_config", {})
            silence_duration = debug_config.get("startup_silence_duration", 0)
            elapsed = (datetime.now() - self.start_time).total_seconds()
            _log.debug(
                f"[灾害预警] 处于启动静默期 (剩余 {silence_duration - elapsed:.1f}s)，忽略事件: {event.id}"
            )
            return

        try:
            _log.debug(f"[灾害预警] 处理灾害事件: {event.id}")
            self._log_event(event)

            # 记录统计数据
            self.statistics_manager.record_push(event)

            # 推送消息
            push_result = await self.message_manager.push_event(event)
            if push_result:
                _log.debug(f"[灾害预警] ✅ 事件推送成功: {event.id}")
            else:
                _log.debug(f"[灾害预警] 事件推送被过滤: {event.id}")

        except Exception as e:
            _log.error(f"[灾害预警] 处理灾害事件失败: {e}")
            _log.error(
                f"[灾害预警] 失败的事件ID: {event.id if hasattr(event, 'id') else 'unknown'}"
            )
            _log.error(f"[灾害预警] 异常堆栈: {traceback.format_exc()}")

    def _log_event(self, event: DisasterEvent):
        """记录事件日志"""
        try:
            if isinstance(event.data, EarthquakeData):
                earthquake = event.data
                log_info = f"地震事件 - 震级: M{earthquake.magnitude}, 位置: {earthquake.place_name}, 时间: {earthquake.shock_time}, 数据源: {event.source.value}"
            elif isinstance(event.data, TsunamiData):
                tsunami = event.data
                log_info = f"海啸事件 - 级别: {tsunami.level}, 标题: {tsunami.title}, 数据源: {event.source.value}"
            elif isinstance(event.data, WeatherAlarmData):
                weather = event.data
                log_info = (
                    f"气象事件 - 标题: {weather.headline}, 数据源: {event.source.value}"
                )
            else:
                log_info = (
                    f"未知事件类型 - ID: {event.id}, 数据源: {event.source.value}"
                )

            _log.debug(f"[灾害预警] 事件详情: {log_info}")
        except Exception:
            _log.debug(
                f"[灾害预警] 事件详情: ID={event.id}, 类型={event.disaster_type.value}, 数据源={event.source.value}"
            )

    def get_service_status(self) -> dict[str, Any]:
        """获取服务状态"""
        connection_status = self.ws_manager.get_all_connections_status()

        active_websocket_connections = sum(
            1 for status in connection_status.values() if status["connected"]
        )

        global_quake_connected = any(
            "global_quake" in task.get_name() if hasattr(task, "get_name") else False
            for task in self.connection_tasks
        )

        return {
            "running": self.running,
            "active_websocket_connections": active_websocket_connections,
            "global_quake_connected": global_quake_connected,
            "total_connections": len(connection_status),
            "connection_details": connection_status,
            "statistics_summary": self.statistics_manager.get_summary(),
            "data_sources": self._get_active_data_sources(),
            "message_logger_enabled": self.message_logger.enabled
            if self.message_logger
            else False,
            "uptime": self._get_uptime(),
        }

    def _get_uptime(self) -> str:
        """获取服务运行时间"""
        if not self.running or not hasattr(self, "start_time"):
            return "未运行"

        delta = datetime.now() - self.start_time
        days = delta.days
        hours, remainder = divmod(delta.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        parts = []
        if days > 0:
            parts.append(f"{days}天")
        if hours > 0:
            parts.append(f"{hours}小时")
        if minutes > 0:
            parts.append(f"{minutes}分")
        parts.append(f"{seconds}秒")

        return "".join(parts)

    def _get_active_data_sources(self) -> list[str]:
        """获取活跃的数据源"""
        active_sources = []
        data_sources = self.config.get("data_sources", {})

        for service_name, service_config in data_sources.items():
            if isinstance(service_config, dict) and service_config.get(
                "enabled", False
            ):
                for source_name, enabled in service_config.items():
                    if (
                        source_name != "enabled"
                        and isinstance(enabled, bool)
                        and enabled
                    ):
                        active_sources.append(f"{service_name}.{source_name}")

        return active_sources

    async def test_push(
        self, target_group: int, disaster_type: str = "earthquake", test_type: str = None
    ):
        """测试推送功能"""
        try:
            test_configs = {
                "earthquake": {
                    "china_eew": {
                        "source_id": "cea_fanstudio",
                        "magnitude": 5.5,
                        "depth": 10.0,
                        "intensity": 6.0,
                        "place_name": "测试地名",
                        "latitude": 31.2,
                        "longitude": 103.8,
                        "updates": 1,
                        "is_final": False,
                    },
                    "japan_eew": {
                        "source_id": "jma_wolfx",
                        "magnitude": 6.2,
                        "depth": 35.0,
                        "scale": 5.0,
                        "place_name": "测试地名",
                        "latitude": 37.5,
                        "longitude": 141.8,
                        "updates": 2,
                        "is_final": False,
                        "raw_data": {
                            "areas": [
                                {"name": "测试区域1", "scaleFrom": 50, "kindCode": "10"},
                                {"name": "测试区域2", "scaleFrom": 45, "kindCode": "11"},
                            ]
                        },
                    },
                    "usgs_info": {
                        "source_id": "usgs_fanstudio",
                        "magnitude": 4.8,
                        "depth": 15.5,
                        "place_name": "测试地名",
                        "latitude": 34.1,
                        "longitude": -118.2,
                        "info_type": "automatic",
                    },
                },
                "tsunami": {
                    "japan_tsunami": {
                        "source_id": "jma_tsunami_p2p",
                        "title": "津波注意報",
                        "level": "Watch",
                        "org_unit": "日本气象厅",
                        "forecasts": [
                            {
                                "name": "测试地点 1",
                                "grade": "Watch",
                                "immediate": False,
                                "firstHeight": {
                                    "arrivalTime": "2023-12-12T13:15:00",
                                    "condition": "津波到達中と推測",
                                },
                                "maxHeight": {"description": "１ｍ", "value": 1},
                            },
                        ],
                        "subtitle": "三陸沖を震源とする地震により、津波注意報が発表されています。",
                    },
                },
                "weather": {
                    "china_weather": {
                        "source_id": "china_weather_fanstudio",
                        "headline": "大风黄色预警信号",
                        "title": "大风黄色预警信号",
                        "description": "气象台发布大风黄色预警信号",
                        "type": "wind",
                        "effective_time": datetime.now(),
                        "longitude": 116.0,
                        "latitude": 39.0,
                    }
                },
            }

            # 选择配置
            if disaster_type == "earthquake":
                if test_type == "china" or test_type is None:
                    test_config = test_configs["earthquake"]["china_eew"]
                elif test_type == "japan":
                    test_config = test_configs["earthquake"]["japan_eew"]
                elif test_type == "usgs":
                    test_config = test_configs["earthquake"]["usgs_info"]
                else:
                    test_config = test_configs["earthquake"]["china_eew"]
            elif disaster_type == "tsunami":
                test_config = test_configs["tsunami"]["japan_tsunami"]
            elif disaster_type == "weather":
                test_config = test_configs["weather"]["china_weather"]
            else:
                test_config = test_configs["earthquake"]["china_eew"]

            # 创建测试事件
            test_event = self._create_simple_test_event(disaster_type, test_config)

            _log.info(
                f"[灾害预警] 创建测试事件: {test_event.id} (类型: {disaster_type}, 配置: {test_config['source_id']})"
            )

            # 注入本地预估信息
            if disaster_type == "earthquake" and self.message_manager.local_monitor:
                self.message_manager.local_monitor.inject_local_estimation(
                    test_event.data
                )

            # 构建消息并推送
            message = await self.message_manager._build_message_async(test_event)
            await self.message_manager._send_message(target_group, message)

            _log.info(f"[灾害预警] 测试推送成功: {test_event.id}")

            from ..models.data_source_config import get_data_source_config

            config = get_data_source_config(test_config["source_id"])
            source_name = config.display_name if config else test_config["source_id"]
            return f"✅ 测试推送成功\n📡 数据源: {source_name}\n🎯 消息链路畅通"

        except Exception as e:
            _log.error(f"[灾害预警] 测试推送失败: {e}")
            return f"❌ 测试推送失败: {str(e)}"

    def _create_simple_test_event(
        self, disaster_type: str, test_config: dict
    ) -> DisasterEvent:
        """创建简化的测试事件"""
        from ..models.models import (
            DisasterEvent,
            EarthquakeData,
            TsunamiData,
            WeatherAlarmData,
        )

        source_id = test_config["source_id"]

        source_enum_mapping = {
            "cea_fanstudio": DataSource.FAN_STUDIO_CEA,
            "jma_wolfx": DataSource.WOLFX_JMA_EEW,
            "usgs_fanstudio": DataSource.FAN_STUDIO_USGS,
            "china_tsunami_fanstudio": DataSource.FAN_STUDIO_TSUNAMI,
            "jma_tsunami_p2p": DataSource.P2P_TSUNAMI,
            "china_weather_fanstudio": DataSource.FAN_STUDIO_WEATHER,
        }
        source_enum = source_enum_mapping.get(source_id, DataSource.FAN_STUDIO_CEA)

        if disaster_type == "earthquake":
            test_data = EarthquakeData(
                id=f"test_{source_id}_{int(datetime.now().timestamp())}",
                event_id=f"test_event_{source_id}",
                source=source_enum,
                disaster_type=DisasterType.EARTHQUAKE,
                shock_time=datetime.now(),
                latitude=test_config.get("latitude", 35.0),
                longitude=test_config.get("longitude", 105.0),
                magnitude=test_config.get("magnitude", 5.5),
                depth=test_config.get("depth", 10.0),
                intensity=test_config.get("intensity"),
                scale=test_config.get("scale"),
                place_name=test_config.get("place_name", "测试地震地点"),
                raw_data={
                    **{"test": True, "source_id": source_id},
                    **test_config.get("raw_data", {}),
                },
                info_type=test_config.get("info_type"),
                updates=test_config.get("updates", 1),
                is_final=test_config.get("is_final", False),
            )
            disaster_type_enum = DisasterType.EARTHQUAKE

        elif disaster_type == "tsunami":
            test_data = TsunamiData(
                id=f"test_{source_id}_{int(datetime.now().timestamp())}",
                code=f"test_tsunami_{source_id}",
                source=source_enum,
                title=test_config.get("title", "海啸警报测试"),
                level=test_config.get("level", "Warning"),
                org_unit=test_config.get("org_unit", "测试海啸预警中心"),
                forecasts=test_config.get("forecasts", []),
                raw_data={
                    **{"test": True, "source_id": source_id},
                    **test_config.get("raw_data", {}),
                },
                issue_time=datetime.now(),
                subtitle=test_config.get("subtitle", "测试震源信息"),
            )
            disaster_type_enum = DisasterType.TSUNAMI

        elif disaster_type == "weather":
            test_data = WeatherAlarmData(
                id=f"test_{source_id}_{int(datetime.now().timestamp())}",
                source=source_enum,
                headline=test_config.get("headline", "气象预警测试"),
                title=test_config.get("title", "测试预警"),
                description=test_config.get("description", "测试描述"),
                type=test_config.get("type", "unknown"),
                effective_time=test_config.get("effective_time", datetime.now()),
                longitude=test_config.get("longitude", 116.0),
                latitude=test_config.get("latitude", 39.0),
                raw_data={
                    **{"test": True, "source_id": source_id},
                    **test_config.get("raw_data", {}),
                },
                issue_time=datetime.now(),
            )
            disaster_type_enum = DisasterType.WEATHER_ALARM

        else:
            return self._create_simple_test_event("earthquake", test_config)

        return DisasterEvent(
            id=test_data.id,
            data=test_data,
            source=test_data.source,
            disaster_type=disaster_type_enum,
        )


# 服务实例
_disaster_service: DisasterWarningService | None = None


async def get_disaster_service(
    config: dict[str, Any], api, bot_id: int
) -> DisasterWarningService:
    """获取灾害预警服务实例"""
    global _disaster_service

    if _disaster_service is None:
        _disaster_service = DisasterWarningService(config, api, bot_id)
        await _disaster_service.initialize()

    return _disaster_service


async def stop_disaster_service():
    """停止灾害预警服务"""
    global _disaster_service

    if _disaster_service:
        await _disaster_service.stop()
        _disaster_service = None


