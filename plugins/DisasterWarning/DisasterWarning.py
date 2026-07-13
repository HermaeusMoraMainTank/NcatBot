"""
灾害预警插件主入口 - NcatBot 版本
支持多数据源灾害预警：地震、海啸、气象预警
"""

import asyncio
import json
import os
from datetime import datetime

import yaml

from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.plugin import NcatBotPlugin
from ncatbot.core import registrar
from ncatbot.utils import get_log

from .core.disaster_service import get_disaster_service, stop_disaster_service
from .models.models import (
    DATA_SOURCE_MAPPING,
    DisasterEvent,
    DisasterType,
    EarthquakeData,
    get_data_source_from_id,
)
from .utils.fe_regions import translate_place_name

_log = get_log()


class DisasterWarning(NcatBotPlugin):
    """多数据源灾害预警插件，支持地震、海啸、气象预警"""

    name = "DisasterWarning"
    version = "1.0"

    # 成员变量（类属性）
    config = {}
    disaster_service = None
    _service_task = None
    admin_users = []

    async def on_load(self):
        """加载插件"""
        try:
            _log.info("[灾害预警] 正在初始化灾害预警插件...")

            # 加载配置
            self._load_config()

            # 检查插件是否启用
            if not self.config.get("enabled", True):
                _log.info("[灾害预警] 插件已禁用，跳过初始化")
                return

            # 获取机器人 ID
            bot_id = int(self.api.self_id) if hasattr(self.api, "self_id") else 0

            # 获取灾害预警服务
            self.disaster_service = await get_disaster_service(
                self.config, self.api, bot_id
            )

            # 启动服务
            self._service_task = asyncio.create_task(self.disaster_service.start())

            _log.info("[灾害预警] 灾害预警插件初始化完成")

        except Exception as e:
            _log.error(f"[灾害预警] 插件初始化失败: {e}")
            import traceback

            _log.error(traceback.format_exc())

    async def on_unload(self):
        """卸载插件"""
        try:
            _log.info("[灾害预警] 正在停止灾害预警插件...")

            # 停止服务任务
            if self._service_task:
                self._service_task.cancel()
                try:
                    await self._service_task
                except asyncio.CancelledError:
                    pass

            # 停止灾害预警服务
            await stop_disaster_service()

            _log.info("[灾害预警] 灾害预警插件已停止")

        except Exception as e:
            _log.error(f"[灾害预警] 插件停止时出错: {e}")

    def _load_config(self):
        """加载配置文件"""
        # 支持 YAML 和 JSON 两种格式
        yaml_config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
        json_config_path = os.path.join(os.path.dirname(__file__), "config.json")

        # 默认配置
        default_config = {
            "enabled": True,
            "admin_users": [],
            "target_groups": [],  # 推送目标群列表
            "data_sources": {
                "fan_studio": {
                    "enabled": True,
                    "china_earthquake_warning": True,
                    "taiwan_cwa_earthquake": True,
                    "china_cenc_earthquake": True,
                    "japan_jma_eew": True,
                    "usgs_earthquake": True,
                    "china_weather_alarm": True,
                    "china_tsunami": True,
                },
                "p2p_earthquake": {
                    "enabled": True,
                    "japan_jma_eew": True,
                    "japan_jma_earthquake": True,
                    "japan_jma_tsunami": True,
                },
                "wolfx": {
                    "enabled": True,
                    "japan_jma_eew": True,
                    "china_cenc_eew": True,
                    "taiwan_cwa_eew": True,
                    "japan_jma_earthquake": True,
                    "china_cenc_earthquake": True,
                },
                "global_quake": {"enabled": True},
            },
            "earthquake_filters": {
                "intensity_filter": {
                    "enabled": True,
                    "min_magnitude": 2.0,
                    "min_intensity": 4.0,
                },
                "scale_filter": {
                    "enabled": True,
                    "min_magnitude": 2.0,
                    "min_scale": 1.0,
                },
                "magnitude_only_filter": {"enabled": True, "min_magnitude": 4.5},
                "global_quake_filter": {
                    "enabled": True,
                    "min_magnitude": 4.5,
                    "min_intensity": 5.0,
                },
            },
            "push_frequency_control": {
                "cea_cwa_report_n": 1,
                "jma_report_n": 3,
                "gq_report_n": 5,
                "final_report_always_push": True,
                "ignore_non_final_reports": False,
            },
            "event_deduplication": {
                "time_window_minutes": 1,
                "location_tolerance_km": 20.0,
                "magnitude_tolerance": 0.5,
            },
            "local_monitoring": {
                "enabled": False,
                "latitude": 0.0,
                "longitude": 0.0,
                "intensity_threshold": 3.0,
                "strict_mode": False,
                "place_name": "本地",
            },
            "regional_restriction": {
                "enabled": True,
                "keywords": ["深圳"],
                "use_bounding_box": True,
                "bounding_box": {
                    "min_lat": 22.40,
                    "max_lat": 22.90,
                    "min_lon": 113.70,
                    "max_lon": 114.70,
                },
            },
            "weather_config": {
                "max_description_length": 384,
                "render_card": True,
                "card_width": 880,
                "card_padding": 28,
                "card_max_body_lines": 16,
                "font_title_size": 26,
                "font_body_size": 20,
                "font_label_size": 17,
                "font_paths": [],
                "weather_filter": {
                    "enabled": True,
                    "provinces": [],
                    "keywords": ["深圳"],
                    "min_color_level": "白色",
                },
            },
            "message_format": {
                "include_map": False,
                "include_map_image": False,
                "include_map_link": False,
                "map_provider": "baidu",
                "map_source": "",
                "map_zoom_level": 5,
                "detailed_jma_intensity": False,
                "use_global_quake_card": False,
                "global_quake_template": "Aurora",
                "global_quake_caption": "🚨 [地震预警] Global Quake",
                "playwright_mode": "local",
                "playwright_server_url": "",
                "browser_pool_size": 2,
            },
            "websocket_config": {
                "heartbeat_interval": 120,
                "connection_timeout": 15,
                "max_message_size": 1048576,
                "max_reconnect_retries": 3,
                "reconnect_interval": 10,
                "fallback_retry_enabled": True,
                "fallback_retry_interval": 1800,
                "fallback_retry_max_count": -1,
            },
            "debug_config": {
                "enable_raw_message_logging": False,
                "startup_silence_duration": 0,
            },
        }

        # 优先加载 YAML 配置
        if os.path.exists(yaml_config_path):
            try:
                with open(yaml_config_path, "r", encoding="utf-8") as f:
                    self.config = yaml.safe_load(f) or {}
                # 合并默认配置
                self.config = self._merge_config(default_config, self.config)
                _log.info("[灾害预警] YAML 配置加载成功")
            except Exception as e:
                _log.warning(f"[灾害预警] YAML 配置加载失败，尝试 JSON 配置: {e}")
                self.config = default_config
        elif os.path.exists(json_config_path):
            try:
                with open(json_config_path, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
                # 合并默认配置
                self.config = self._merge_config(default_config, self.config)
                _log.info("[灾害预警] JSON 配置加载成功")
            except Exception as e:
                _log.warning(f"[灾害预警] JSON 配置加载失败，使用默认配置: {e}")
                self.config = default_config
        else:
            self.config = default_config
            # 保存默认配置
            self._save_config()
            _log.info("[灾害预警] 已创建默认配置文件")

        self.admin_users = self.config.get("admin_users", [])

    def _merge_config(self, default: dict, user: dict) -> dict:
        """递归合并配置"""
        result = default.copy()
        for key, value in user.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = self._merge_config(result[key], value)
            else:
                result[key] = value
        return result

    def _save_config(self):
        """保存配置"""
        yaml_config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
        try:
            with open(yaml_config_path, "w", encoding="utf-8") as f:
                yaml.dump(
                    self.config,
                    f,
                    allow_unicode=True,
                    default_flow_style=False,
                    sort_keys=False,
                )
        except Exception as e:
            _log.error(f"[灾害预警] 保存配置失败: {e}")

    def is_admin(self, user_id) -> bool:
        """检查用户是否为管理员"""
        return str(user_id) in [str(u) for u in self.admin_users]

    @registrar.qq.on_group_message()
    async def handle_message(self, input: GroupMessage) -> None:
        """处理群消息"""
        raw_message = input.raw_message.strip()

        # 同时支持 "灾难" 和 "灾害" 两种叫法
        cmd = raw_message.replace("灾难", "灾害")

        # 命令路由
        if cmd == "灾害预警":
            await self._handle_help(input)
        elif cmd == "灾害预警状态":
            await self._handle_status(input)
        elif cmd == "灾害预警统计":
            await self._handle_stats(input)
        elif cmd.startswith("灾害预警测试"):
            await self._handle_test(input, cmd)
        elif cmd.startswith("灾害预警模拟"):
            await self._handle_simulate(input, cmd)
        elif cmd == "灾害预警配置 查看":
            await self._handle_config_view(input)
        elif cmd == "灾害预警日志":
            await self._handle_logs(input)
        elif cmd == "灾害预警日志开关":
            await self._handle_toggle_logging(input)
        elif cmd == "灾害预警日志清除":
            await self._handle_clear_logs(input)
        elif cmd == "灾害预警统计清除":
            await self._handle_clear_stats(input)

    async def _handle_help(self, input: GroupMessage):
        """显示帮助信息"""
        help_text = """🚨 灾害预警插件使用说明

📋 可用命令：
• 灾害预警 - 显示此帮助信息
• 灾害预警状态 - 查看服务运行状态
• 灾害预警统计 - 查看详细的事件统计报告
• 灾害预警统计清除 - 清除所有统计信息
• 灾害预警测试 [群号] [灾害类型] [格式] - 测试推送功能
• 灾害预警模拟 <纬度> <经度> <震级> [深度] [数据源] - 模拟地震事件
• 灾害预警配置 查看 - 查看当前配置摘要
• 灾害预警日志 - 查看原始消息日志统计摘要
• 灾害预警日志开关 - 开关原始消息日志记录
• 灾害预警日志清除 - 清除所有原始消息日志

更多信息可参考 README 文档"""

        await self.api.qq.post_group_msg(group_id=input.group_id, text=help_text)

    async def _handle_status(self, input: GroupMessage):
        """查看服务状态"""
        if not self.disaster_service:
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text="❌ 灾害预警服务未启动"
            )
            return

        try:
            status = self.disaster_service.get_service_status()

            # 基础状态
            running_state = "🟢 运行中" if status["running"] else "🔴 已停止"
            uptime = status.get("uptime", "未知")

            status_text = [
                "📊 灾害预警服务状态\n",
                "\n",
                f"🔄 运行状态：{running_state} (已运行 {uptime})\n",
                f"🔗 活跃连接：{status['active_websocket_connections']} / {status['total_connections']}\n",
            ]

            # 连接详情
            conn_details = status.get("connection_details", {})
            if conn_details:
                status_text.append("\n")
                status_text.append("📡 连接详情：\n")
                for name, detail in conn_details.items():
                    state_icon = "🟢" if detail.get("connected") else "🔴"
                    uri = detail.get("uri", "未知地址")
                    if len(uri) > 30:
                        uri = uri[:27] + "..."
                    retry = detail.get("retry_count", 0)
                    retry_text = f" (重试: {retry})" if retry > 0 else ""
                    status_text.append(f"  {state_icon} {name}: {uri}{retry_text}\n")

            # 活跃数据源
            active_sources = status.get("data_sources", [])
            if active_sources:
                status_text.append("\n")
                status_text.append("📡 数据源详情：\n")

                service_groups = {}
                for source in active_sources:
                    parts = source.split(".", 1)
                    service = parts[0]
                    name = parts[1] if len(parts) > 1 else source
                    if service not in service_groups:
                        service_groups[service] = []
                    service_groups[service].append(name)

                service_names = {
                    "fan_studio": "FAN Studio",
                    "p2p_earthquake": "P2P地震情报",
                    "wolfx": "Wolfx",
                    "global_quake": "Global Quake",
                }

                for service, sources in service_groups.items():
                    display_name = service_names.get(service, service)
                    sources_str = ", ".join(sources)
                    status_text.append(f"  • {display_name}: {sources_str}\n")

            await self.api.qq.post_group_msg(
                group_id=input.group_id, text="".join(status_text)
            )

        except Exception as e:
            _log.error(f"[灾害预警] 获取服务状态失败: {e}")
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text=f"❌ 获取服务状态失败: {str(e)}"
            )

    async def _handle_stats(self, input: GroupMessage):
        """查看统计信息"""
        if not self.disaster_service:
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text="❌ 灾害预警服务未启动"
            )
            return

        try:
            status = self.disaster_service.get_service_status()
            stats_summary = status.get("statistics_summary", "❌ 暂无统计数据")

            # 附加过滤统计信息
            if self.disaster_service.message_logger:
                filter_stats = self.disaster_service.message_logger.filter_stats
                if filter_stats and filter_stats.get("total_filtered", 0) > 0:
                    stats_summary += "\n\n🛡️ 日志过滤拦截统计:\n"
                    stats_summary += f"• 重复数据拦截: {filter_stats.get('duplicate_events_filtered', 0)}\n"
                    stats_summary += (
                        f"• 心跳包过滤: {filter_stats.get('heartbeat_filtered', 0)}\n"
                    )
                    stats_summary += (
                        f"• P2P节点状态: {filter_stats.get('p2p_areas_filtered', 0)}\n"
                    )
                    stats_summary += f"• 连接状态过滤: {filter_stats.get('connection_status_filtered', 0)}\n"
                    stats_summary += (
                        f"📊 总计拦截: {filter_stats.get('total_filtered', 0)}"
                    )

            await self.api.qq.post_group_msg(
                group_id=input.group_id, text=stats_summary
            )

        except Exception as e:
            _log.error(f"[灾害预警] 获取统计信息失败: {e}")
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text=f"❌ 获取统计信息失败: {str(e)}"
            )

    async def _handle_test(self, input: GroupMessage, raw_message: str):
        """测试推送功能"""
        if not self.disaster_service:
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text="❌ 灾害预警服务未启动"
            )
            return

        try:
            # 解析参数
            parts = raw_message.split()[1:] if len(raw_message.split()) > 1 else []

            target_group = input.group_id
            disaster_type = "earthquake"
            test_type = None

            type_mapping = {
                "地震": "earthquake",
                "海啸": "tsunami",
                "气象": "weather",
                "earthquake": "earthquake",
                "tsunami": "tsunami",
                "weather": "weather",
            }

            format_mapping = {
                "中国": "china",
                "日本": "japan",
                "美国": "usgs",
                "china": "china",
                "japan": "japan",
                "usgs": "usgs",
            }

            if len(parts) >= 1:
                if parts[0] in type_mapping:
                    disaster_type = type_mapping[parts[0]]
                elif parts[0] in format_mapping:
                    test_type = format_mapping[parts[0]]
                elif parts[0].isdigit():
                    target_group = int(parts[0])

            if len(parts) >= 2:
                if parts[1] in type_mapping:
                    disaster_type = type_mapping[parts[1]]
                elif parts[1] in format_mapping:
                    test_type = format_mapping[parts[1]]

            if len(parts) >= 3:
                if parts[2] in format_mapping:
                    test_type = format_mapping[parts[2]]

            _log.info(
                f"[灾害预警] 开始{disaster_type}测试推送到群 {target_group} (格式: {test_type or '默认'})"
            )

            test_result = await self.disaster_service.test_push(
                target_group, disaster_type, test_type
            )

            if test_result and "✅" in test_result:
                await self.api.qq.post_group_msg(
                    group_id=input.group_id, text=test_result
                )
            else:
                await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text=test_result or "❌ 测试推送失败，请检查日志",
                )

        except Exception as e:
            _log.error(f"[灾害预警] 测试推送失败: {e}")
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text=f"❌ 测试推送失败: {str(e)}"
            )

    async def _handle_simulate(self, input: GroupMessage, raw_message: str):
        """模拟地震事件"""
        if not self.disaster_service or not self.disaster_service.message_manager:
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text="❌ 服务未启动"
            )
            return

        try:
            # 解析参数: 灾害预警模拟 <纬度> <经度> <震级> [深度] [数据源]
            parts = raw_message.split()[1:]
            if len(parts) < 3:
                await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text="❌ 参数不足\n格式：灾害预警模拟 <纬度> <经度> <震级> [深度] [数据源]",
                )
                return

            lat = float(parts[0])
            lon = float(parts[1])
            magnitude = float(parts[2])
            depth = float(parts[3]) if len(parts) > 3 else 10.0
            source = parts[4] if len(parts) > 4 else "cea_fanstudio"

            # 获取数据源
            data_source = get_data_source_from_id(source)
            if not data_source:
                valid_sources = ", ".join(DATA_SOURCE_MAPPING.keys())
                await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text=f"❌ 无效的数据源: {source}\n可用数据源: {valid_sources}",
                )
                return

            # 自动生成地名
            final_place_name = translate_place_name("模拟震中", lat, lon)

            earthquake = EarthquakeData(
                id=f"sim_{int(datetime.now().timestamp())}",
                event_id=f"sim_{int(datetime.now().timestamp())}",
                source=data_source,
                disaster_type=DisasterType.EARTHQUAKE,
                shock_time=datetime.now(),
                latitude=lat,
                longitude=lon,
                depth=depth,
                magnitude=magnitude,
                place_name=final_place_name,
                source_id=source,
                raw_data={"test": True, "source_id": source},
            )

            # 特定数据源的特殊处理
            if source == "usgs_fanstudio":
                earthquake.update_time = datetime.now()

            if source in ["jma_p2p", "jma_wolfx", "jma_p2p_info"]:
                earthquake.max_scale = max(0, min(7, int(magnitude - 2)))
                earthquake.scale = earthquake.max_scale

            disaster_event = DisasterEvent(
                id=f"sim_evt_{int(datetime.now().timestamp())}",
                data=earthquake,
                source=data_source,
                disaster_type=DisasterType.EARTHQUAKE,
                source_id=source,
            )

            manager = self.disaster_service.message_manager

            report_lines = [
                "🧪 **灾害预警模拟报告**",
                f"Input: M{magnitude} @ ({lat}, {lon}), Depth {depth}km\n",
            ]

            # 检查全局过滤器
            global_pass = True
            if manager.intensity_filter:
                if manager.intensity_filter.should_filter(earthquake):
                    global_pass = False
                    report_lines.append("❌ 全局过滤: 拦截 (不满足最小震级/烈度要求)")
                else:
                    report_lines.append("✅ 全局过滤: 通过")

            # 检查本地监控
            local_pass = True
            if manager.local_monitor:
                result = manager.local_monitor.inject_local_estimation(earthquake)

                if result is None:
                    report_lines.append("ℹ️ 本地监控: 未启用")
                else:
                    allowed = result.get("is_allowed", True)
                    dist = result.get("distance")
                    inte = result.get("intensity")

                    if allowed:
                        report_lines.append("✅ 本地监控: 触发")
                    else:
                        local_pass = False
                        report_lines.append("❌ 本地监控: 拦截 (严格模式生效中)")

                    report_lines.append(
                        f"   ⦁ 严格模式: {'开启' if manager.local_monitor.strict_mode else '关闭'}"
                    )

                    dist_str = f"{dist:.1f} km" if dist is not None else "未知"
                    inte_str = f"{inte:.1f}" if inte is not None else "未知"
                    report_lines.extend(
                        [
                            f"   ⦁ 距本地: {dist_str}",
                            f"   ⦁ 预估最大本地烈度: {inte_str}",
                            f"   ⦁ 本地烈度阈值: {manager.local_monitor.threshold}",
                        ]
                    )
            else:
                report_lines.append("ℹ️ 本地监控: 未配置")

            # 发送报告
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text="\n".join(report_lines)
            )

            await asyncio.sleep(1)

            # 模拟消息构建
            if global_pass and local_pass:
                try:
                    _log.info("[灾害预警] 开始构建模拟预警消息...")
                    msg_text = await manager._build_message_async(disaster_event)
                    _log.info("[灾害预警] 消息构建成功")

                    await manager._send_message(input.group_id, msg_text)

                except Exception as build_e:
                    import traceback

                    _log.error(
                        f"[灾害预警] 消息构建失败: {build_e}\n{traceback.format_exc()}"
                    )
                    await self.api.qq.post_group_msg(
                        group_id=input.group_id, text=f"❌ 消息构建失败: {build_e}"
                    )
            else:
                await self.api.qq.post_group_msg(
                    group_id=input.group_id, text="\n⛔ 结论: 该事件不会触发预警推送。"
                )

        except Exception as e:
            import traceback

            error_trace = traceback.format_exc()
            _log.error(f"[灾害预警] 模拟测试失败: {e}\n{error_trace}")
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text=f"❌ 模拟失败: {e}"
            )

    async def _handle_config_view(self, input: GroupMessage):
        """查看配置"""
        if not self.is_admin(input.sender.user_id):
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text="🚫 权限不足：此命令仅限管理员使用。"
            )
            return

        try:
            config_str = json.dumps(self.config, indent=2, ensure_ascii=False)
            # 限制长度避免消息过长
            if len(config_str) > 2000:
                config_str = config_str[:2000] + "\n... (配置过长已截断)"
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text=f"🔧 当前配置详情：\n{config_str}"
            )

        except Exception as e:
            _log.error(f"[灾害预警] 获取配置详情失败: {e}")
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text=f"❌ 获取配置详情失败: {str(e)}"
            )

    async def _handle_logs(self, input: GroupMessage):
        """查看日志信息"""
        if not self.is_admin(input.sender.user_id):
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text="🚫 权限不足：此命令仅限管理员使用。"
            )
            return

        if not self.disaster_service or not self.disaster_service.message_logger:
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text="❌ 日志功能不可用"
            )
            return

        try:
            log_summary = self.disaster_service.message_logger.get_log_summary()

            if not log_summary["enabled"]:
                await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text="📋 原始消息日志功能未启用\n\n使用 灾害预警日志开关 启用日志记录",
                )
                return

            if not log_summary["log_exists"]:
                await self.api.qq.post_group_msg(
                    group_id=input.group_id,
                    text="📋 暂无日志记录\n\n当日志功能启用后，所有接收到的原始消息将被记录。",
                )
                return

            log_info = f"""📊 原始消息日志统计

📁 日志文件：{log_summary["log_file"]}
📈 总条目数：{log_summary["total_entries"]}
📦 文件大小：{log_summary.get("file_size_mb", 0):.2f} MB
📅 时间范围：{log_summary["date_range"]["start"]} 至 {log_summary["date_range"]["end"]}

📡 数据源统计："""

            for source in log_summary["data_sources"]:
                log_info += f"\n  • {source}"

            log_info += "\n\n💡 提示：使用 灾害预警日志开关 可以关闭日志记录"

            await self.api.qq.post_group_msg(group_id=input.group_id, text=log_info)

        except Exception as e:
            _log.error(f"[灾害预警] 获取日志信息失败: {e}")
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text=f"❌ 获取日志信息失败: {str(e)}"
            )

    async def _handle_toggle_logging(self, input: GroupMessage):
        """切换日志开关"""
        if not self.is_admin(input.sender.user_id):
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text="🚫 权限不足：此命令仅限管理员使用。"
            )
            return

        if not self.disaster_service or not self.disaster_service.message_logger:
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text="❌ 日志功能不可用"
            )
            return

        try:
            current_state = self.disaster_service.message_logger.enabled
            new_state = not current_state

            self.config["debug_config"]["enable_raw_message_logging"] = new_state
            self.disaster_service.message_logger.enabled = new_state

            self._save_config()

            status = "启用" if new_state else "禁用"
            action = "开始" if new_state else "停止"

            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                text=f"✅ 原始消息日志记录已{status}\n\n插件将{action}记录所有数据源的原始消息格式。",
            )

        except Exception as e:
            _log.error(f"[灾害预警] 切换日志状态失败: {e}")
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text=f"❌ 切换日志状态失败: {str(e)}"
            )

    async def _handle_clear_logs(self, input: GroupMessage):
        """清除日志"""
        if not self.is_admin(input.sender.user_id):
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text="🚫 权限不足：此命令仅限管理员使用。"
            )
            return

        if not self.disaster_service or not self.disaster_service.message_logger:
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text="❌ 日志功能不可用"
            )
            return

        try:
            self.disaster_service.message_logger.clear_logs()
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                text="✅ 所有原始消息日志已清除\n\n日志文件已被删除，新的消息记录将重新开始。",
            )

        except Exception as e:
            _log.error(f"[灾害预警] 清除日志失败: {e}")
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text=f"❌ 清除日志失败: {str(e)}"
            )

    async def _handle_clear_stats(self, input: GroupMessage):
        """清除统计"""
        if not self.is_admin(input.sender.user_id):
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text="🚫 权限不足：此命令仅限管理员使用。"
            )
            return

        if not self.disaster_service or not self.disaster_service.statistics_manager:
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text="❌ 统计功能不可用"
            )
            return

        try:
            self.disaster_service.statistics_manager.reset_stats()
            await self.api.qq.post_group_msg(
                group_id=input.group_id,
                text="✅ 统计数据已重置\n\n所有历史统计记录已被清除，新的统计将重新开始。",
            )

        except Exception as e:
            _log.error(f"[灾害预警] 清除统计失败: {e}")
            await self.api.qq.post_group_msg(
                group_id=input.group_id, text=f"❌ 清除统计失败: {str(e)}"
            )
