"""
WebSocket消息处理器注册中心
负责创建和注册各种数据源的WebSocket消息处理器 - NcatBot 版本
"""

import json

from ncatbot.utils import get_log

from .websocket_manager import WebSocketManager

_log = get_log()


class WebSocketHandlerRegistry:
    """WebSocket消息处理器注册中心"""

    def __init__(self, service):
        """
        初始化注册中心
        :param service: DisasterWarningService 实例
        """
        self.service = service

    def register_all(self, ws_manager: WebSocketManager):
        """注册所有处理器"""
        ws_manager.register_handler("fan_studio", self._create_fan_studio_handler())
        ws_manager.register_handler("p2p", self._create_p2p_handler())
        ws_manager.register_handler("wolfx", self._create_wolfx_handler())
        ws_manager.register_handler("global_quake", self._create_global_quake_handler())

    def _create_fan_studio_handler(self):
        """创建 FAN Studio WebSocket 处理器"""

        async def fan_studio_handler(
            message, connection_name=None, connection_info=None
        ):
            if connection_info:
                _log.debug(
                    f"[灾害预警] FAN Studio处理器收到消息 - 连接: {connection_name}"
                )

            try:
                try:
                    data = json.loads(message)
                except json.JSONDecodeError as e:
                    _log.error(f"[灾害预警] JSON解析失败: {e}")
                    return None

                # 定义源映射关系
                source_map = {
                    "weatheralarm": ("china_weather_alarm", "china_weather_fanstudio"),
                    "tsunami": ("china_tsunami", "china_tsunami_fanstudio"),
                    "cenc": ("china_cenc_earthquake", "cenc_fanstudio"),
                    "cea": ("china_earthquake_warning", "cea_fanstudio"),
                    "jma": ("japan_jma_eew", "jma_fanstudio"),
                    "cwa": ("taiwan_cwa_earthquake", "cwa_fanstudio"),
                    "usgs": ("usgs_earthquake", "usgs_fanstudio"),
                }

                messages_to_process = []
                msg_type = data.get("type")

                # 处理 initial_all
                if msg_type == "initial_all":
                    for key, value in data.items():
                        if key in source_map and isinstance(value, dict):
                            messages_to_process.append((key, value))

                # 处理 update
                elif msg_type == "update":
                    source = data.get("source")
                    if source and source in source_map:
                        messages_to_process.append((source, data))

                # 兜底：特征识别
                source_id = data.get("source")
                if not messages_to_process and not source_id:
                    msg_data = data
                    depth = 0
                    while (
                        isinstance(msg_data, dict)
                        and ("Data" in msg_data or "data" in msg_data)
                        and depth < 3
                    ):
                        msg_data = msg_data.get("Data") or msg_data.get("data")
                        depth += 1

                    if isinstance(msg_data, dict):
                        detected_source = None
                        if "headline" in msg_data and "type" in msg_data:
                            detected_source = "weatheralarm"
                        elif "warningInfo" in msg_data and "code" in msg_data:
                            detected_source = "tsunami"
                        elif "infoTypeName" in msg_data and (
                            "[正式测定]" in msg_data.get("infoTypeName", "")
                            or "[自动测定]" in msg_data.get("infoTypeName", "")
                        ):
                            detected_source = "cenc"
                        elif (
                            "infoTypeName" in msg_data
                            and "final" in msg_data
                            and isinstance(msg_data.get("epiIntensity"), str)
                        ):
                            detected_source = "jma"
                        elif (
                            "epiIntensity" in msg_data
                            and "createTime" in msg_data
                            and "shockTime" in msg_data
                            and "infoTypeName" not in msg_data
                        ):
                            detected_source = "cwa"
                        elif (
                            "epiIntensity" in msg_data
                            and "eventId" in msg_data
                            and "updates" in msg_data
                        ):
                            detected_source = "cea"
                        elif "url" in msg_data and "usgs.gov" in msg_data.get(
                            "url", ""
                        ):
                            detected_source = "usgs"

                        if detected_source:
                            messages_to_process.append((detected_source, data))

                # 遍历处理
                processed_count = 0
                for source, payload in messages_to_process:
                    config_key, handler_id = source_map[source]

                    if not self.service.is_fan_studio_source_enabled(config_key):
                        _log.debug(
                            f"[灾害预警] 数据源 {config_key} ({source}) 未启用，忽略"
                        )
                        continue

                    handler = self.service.handlers.get(handler_id)
                    if handler:
                        _log.info(f"[灾害预警] 处理 {source} 数据 ({config_key})")
                        event = handler.parse_message(json.dumps(payload))

                        if event:
                            if (
                                connection_info
                                and hasattr(event, "raw_data")
                                and isinstance(event.raw_data, dict)
                            ):
                                event.raw_data["connection_info"] = {
                                    "connection_name": connection_name,
                                    "uri": connection_info.get("uri"),
                                    "source_channel": source,
                                }

                            _log.debug(f"[灾害预警] {source} 解析成功: {event.id}")
                            await self.service._handle_disaster_event(event)
                            processed_count += 1
                    else:
                        _log.warning(f"[灾害预警] 未找到处理器: {handler_id}")

                return None

            except Exception as e:
                _log.error(
                    f"[灾害预警] FAN Studio处理器解析消息失败 - 连接: {connection_name}, 错误: {e}"
                )
                raise

        return fan_studio_handler

    def _create_p2p_handler(self):
        """创建 P2P Quake WebSocket 处理器"""

        async def p2p_handler(message, connection_name=None, connection_info=None):
            _log.debug(
                f"[灾害预警] P2P处理器收到消息 - 连接: {connection_name}, 长度: {len(message)}"
            )

            try:
                data = json.loads(message)
                code = data.get("code")
                if code == 556:
                    _log.info(
                        "[灾害预警] P2P处理器收到紧急地震速报(code:556)，准备解析..."
                    )
            except (json.JSONDecodeError, AttributeError, TypeError):
                pass

            # 尝试EEW处理器
            eew_handler = self.service.handlers.get("jma_p2p")
            if eew_handler:
                try:
                    event = eew_handler.parse_message(message)
                    if event:
                        if (
                            connection_info
                            and hasattr(event, "raw_data")
                            and isinstance(event.raw_data, dict)
                        ):
                            event.raw_data["connection_info"] = {
                                "connection_name": connection_name,
                                "uri": connection_info.get("uri"),
                            }

                        _log.debug(f"[灾害预警] P2P EEW处理器解析成功: {event.id}")
                        await self.service._handle_disaster_event(event)
                        return
                except Exception as e:
                    _log.error(
                        f"[灾害预警] P2P EEW处理器解析失败 - 连接: {connection_name}, 错误: {e}"
                    )

            # 尝试地震情報处理器
            info_handler = self.service.handlers.get("jma_p2p_info")
            if info_handler:
                try:
                    event = info_handler.parse_message(message)
                    if event:
                        if (
                            connection_info
                            and hasattr(event, "raw_data")
                            and isinstance(event.raw_data, dict)
                        ):
                            event.raw_data["connection_info"] = {
                                "connection_name": connection_name,
                                "uri": connection_info.get("uri"),
                            }

                        _log.debug(
                            f"[灾害预警] P2P地震情報处理器解析成功: {event.id}"
                        )
                        await self.service._handle_disaster_event(event)
                        return
                except Exception as e:
                    _log.error(
                        f"[灾害预警] P2P地震情報处理器解析失败 - 连接: {connection_name}, 错误: {e}"
                    )

            _log.debug("[灾害预警] P2P处理器返回None，无有效事件")

        return p2p_handler

    def _create_wolfx_handler(self):
        """创建 Wolfx WebSocket 处理器"""

        async def wolfx_handler(message, connection_name=None, connection_info=None):
            _log.debug(
                f"[灾害预警] Wolfx处理器收到消息 - 连接: {connection_name}"
            )

            if connection_name:
                source_mapping = {
                    "wolfx_japan_jma_eew": "jma_wolfx",
                    "wolfx_china_cenc_eew": "cea_wolfx",
                    "wolfx_taiwan_cwa_eew": "cwa_wolfx",
                    "wolfx_china_cenc_earthquake": "cenc_wolfx",
                    "wolfx_japan_jma_earthquake": "jma_wolfx_info",
                }

                target_source = source_mapping.get(connection_name)
                if target_source and target_source in self.service.handlers:
                    handler = self.service.handlers[target_source]
                    _log.debug(f"[灾害预警] 使用Wolfx处理器: {target_source}")

                    try:
                        event = handler.parse_message(message)
                        if event:
                            if (
                                connection_info
                                and hasattr(event, "raw_data")
                                and isinstance(event.raw_data, dict)
                            ):
                                event.raw_data["connection_info"] = {
                                    "connection_name": connection_name,
                                    "uri": connection_info.get("uri"),
                                }

                            _log.debug(f"[灾害预警] Wolfx处理器解析成功: {event.id}")
                            await self.service._handle_disaster_event(event)
                            return
                    except Exception as e:
                        _log.error(
                            f"[灾害预警] Wolfx处理器解析消息失败 - 连接: {connection_name}, 错误: {e}"
                        )
                        return
                else:
                    _log.warning(
                        f"[灾害预警] 无法识别Wolfx连接名称: {connection_name}"
                    )
                    return
            else:
                _log.warning("[灾害预警] Wolfx处理器未收到连接名称")
                return

        return wolfx_handler

    def _create_global_quake_handler(self):
        """创建 Global Quake WebSocket 处理器"""

        async def global_quake_handler(
            message, connection_name=None, connection_info=None
        ):
            _log.debug(
                f"[灾害预警] Global Quake处理器收到消息 - 连接: {connection_name}"
            )

            handler = self.service.handlers.get("global_quake")
            if handler:
                try:
                    event = handler.parse_message(message)
                    if event:
                        if (
                            connection_info
                            and hasattr(event, "raw_data")
                            and isinstance(event.raw_data, dict)
                        ):
                            event.raw_data["connection_info"] = {
                                "connection_name": connection_name,
                                "uri": connection_info.get("uri"),
                            }

                        _log.debug(
                            f"[灾害预警] Global Quake处理器解析成功: {event.id}"
                        )
                        await self.service._handle_disaster_event(event)
                except Exception as e:
                    _log.error(
                        f"[灾害预警] Global Quake处理器解析消息失败 - 连接: {connection_name}, 错误: {e}"
                    )
            else:
                _log.warning("[灾害预警] 未找到Global Quake处理器")

        return global_quake_handler


