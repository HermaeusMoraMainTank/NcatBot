"""
NapCat 配置管理

负责 NapCat 的 OneBot11、WebUI 等配置文件管理。
"""

import json
import shutil
from pathlib import Path
from typing import Optional, TYPE_CHECKING
from urllib.parse import urlparse

from ncatbot.utils import get_log, get_config_manager, confirm
from ncatbot.utils.config.security import strong_password_check, generate_strong_token
from .default_webui_config import config as default_webui_config
from .platform import PlatformOps

_DEFAULT_WEBUI_TOKEN = "napcat_webui"
_DEFAULT_WS_TOKEN = "napcat_ws"

if TYPE_CHECKING:
    from ncatbot.utils.config.models import NapCatConfig
    from .webui_client import WebUIClient

LOG = get_log("NapCatConfigManager")


class NapCatConfigManager:
    """NapCat 配置管理器

    Parameters
    ----------
    platform_ops:
        平台操作实例。
    napcat_config:
        NapCatConfig 实例，由 NapCatAdapter 注入。
    bot_uin:
        目标 QQ 号。
    """

    def __init__(
        self,
        platform_ops: Optional[PlatformOps] = None,
        napcat_config: Optional["NapCatConfig"] = None,
        bot_uin: str = "",
        webui_client: Optional["WebUIClient"] = None,
    ):
        self._platform = platform_ops or PlatformOps.create()
        self._napcat_dir = self._platform.napcat_dir
        self._config_dir = self._platform.config_dir
        self._napcat_config = napcat_config
        self._bot_uin = bot_uin
        self._webui_client = webui_client

    @property
    def uin(self) -> str:
        return self._bot_uin

    # ==================== 路径属性 ====================

    @property
    def onebot_config_path(self) -> Path:
        return self._config_dir / f"onebot11_{self.uin}.json"

    @property
    def webui_config_path(self) -> Path:
        return self._config_dir / "webui.json"

    @property
    def quick_login_script_path(self) -> Path:
        return self._napcat_dir / f"{self.uin}_quickLogin.bat"

    @property
    def plugins_config_path(self) -> Path:
        return self._config_dir / "plugins.json"

    # ==================== JSON 文件操作 ====================

    @staticmethod
    def _read_json(path: Path) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _write_json(path: Path, data: dict) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

    # ==================== WebUI Token 安全强制 ====================

    def _enforce_webui_token_security(self) -> None:
        """确保 webui_token 满足强密码策略，不满足则替换并写回 config.yaml。"""
        nc = self._napcat_config
        token = nc.webui_token

        if strong_password_check(token):
            return

        is_default = token == _DEFAULT_WEBUI_TOKEN

        if not is_default:
            if not confirm(
                f"WebUI 令牌 '{token}' 强度不足, 是否替换为自动生成的强密钥?",
                default=True,
            ):
                LOG.warning("WebUI 令牌强度不足, 存在安全风险, 继续使用现有令牌")
                return

        new_token = generate_strong_token()
        nc.webui_token = new_token

        try:
            mgr = get_config_manager()
            entry = mgr.get_adapter_config("napcat")
            if entry is not None:
                entry.config["webui_token"] = new_token
                mgr.save()
        except Exception as e:
            LOG.warning(f"写回 config.yaml 失败: {e}")

        if is_default:
            LOG.warning(f"WebUI 令牌为默认弱密钥, 已自动替换, 新令牌: {new_token}")
        else:
            LOG.warning(f"WebUI 令牌已替换为强密钥, 新令牌: {new_token}")

    # ==================== WS Token 安全强制 ====================

    def _enforce_ws_token_security(self) -> None:
        """当 ws_listen_ip 非本地时，确保 ws_token 满足强密码策略。"""
        nc = self._napcat_config

        if nc.ws_listen_ip in ("localhost", "127.0.0.1", "::1"):
            return

        token = nc.ws_token
        if strong_password_check(token):
            return

        is_default = token == _DEFAULT_WS_TOKEN

        if not is_default:
            if not confirm(
                f"WS 令牌 '{token}' 强度不足 (ws_listen_ip={nc.ws_listen_ip}), "
                f"是否替换为自动生成的强密钥?",
                default=True,
            ):
                LOG.warning(
                    "WS 令牌强度不足且监听非本地地址, 存在安全风险, 继续使用现有令牌"
                )
                return

        LOG.info("WS_TOKEN 为默认弱密钥且监听非本地地址, 将自动替换为强密钥")
        new_token = generate_strong_token()
        nc.ws_token = new_token

        try:
            mgr = get_config_manager()
            entry = mgr.get_adapter_config("napcat")
            if entry is not None:
                entry.config["ws_token"] = new_token
                mgr.save()
        except Exception as e:
            LOG.warning(f"写回 config.yaml 失败: {e}")

        if is_default:
            LOG.warning(f"WS 令牌为默认弱密钥, 已自动替换, 新令牌: {new_token}")
        else:
            LOG.warning(f"WS 令牌已替换为强密钥, 新令牌: {new_token}")

    # ==================== OneBot11 配置 ====================

    def _build_expected_ws_server(self) -> dict:
        nc = self._napcat_config
        ws_port = urlparse(nc.ws_uri).port or 3001
        ws_token = nc.ws_token

        return {
            "name": "WsServer",
            "enable": True,
            "host": nc.ws_listen_ip,
            "port": int(ws_port),
            "messagePostFormat": "array",
            "reportSelfMessage": False,
            "token": str(ws_token) if ws_token else "",
            "enableForcePushEvent": True,
            "debug": False,
            "heartInterval": 30000,
        }

    def _get_default_onebot_config(self) -> dict:
        return {
            "network": {"websocketServers": []},
            "musicSignUrl": "",
            "enableLocalFile2Url": False,
            "parseMultMsg": True,
        }

    def configure_onebot(self) -> None:
        """通过文件写入配置 OneBot11（NapCat 启动前调用）。"""
        if self.onebot_config_path.exists():
            config = self._read_json(self.onebot_config_path)
        else:
            config = self._get_default_onebot_config()

        if "network" not in config:
            config["network"] = {"websocketServers": []}
        if "websocketServers" not in config["network"]:
            config["network"]["websocketServers"] = []

        expected_server = self._build_expected_ws_server()
        servers = config["network"]["websocketServers"]

        if expected_server not in servers:
            target_port = expected_server["port"]
            for server in servers[:]:
                if server.get("port") == target_port:
                    if confirm(
                        f"端口 {target_port} 已存在配置, 是否强制覆盖?", default=True
                    ):
                        servers.remove(server)
                    else:
                        raise ValueError(f"端口 {target_port} 已存在, 请更改端口")
            servers.append(expected_server)

        try:
            self._write_json(self.onebot_config_path, config)
        except Exception as e:
            LOG.error(f"配置 OneBot 失败: {e}")
            raise

    def configure_onebot_http(self, webui_client: "WebUIClient") -> None:
        """通过 WebUI HTTP API 配置 OneBot11（NapCat 运行中调用）。"""
        resp = webui_client.api_call("/api/OB11Config/GetConfig")
        config_str = resp.get("data", {}).get("config", "{}")
        config = json.loads(config_str) if isinstance(config_str, str) else config_str

        if "network" not in config:
            config["network"] = {"websocketServers": []}
        if "websocketServers" not in config["network"]:
            config["network"]["websocketServers"] = []

        expected_server = self._build_expected_ws_server()
        servers = config["network"]["websocketServers"]

        if expected_server not in servers:
            target_port = expected_server["port"]
            for server in servers[:]:
                if server.get("port") == target_port:
                    servers.remove(server)
            servers.append(expected_server)

        webui_client.api_call(
            "/api/OB11Config/SetConfig",
            payload={"config": json.dumps(config, ensure_ascii=False)},
        )
        LOG.info("通过 HTTP API 配置 OneBot11 成功")

    # ==================== WebUI 配置 ====================

    def configure_webui(self) -> None:
        nc = self._napcat_config

        if not self.webui_config_path.exists():
            LOG.warning("第一次运行 WebUI, 将创建配置文件")
            config = dict(default_webui_config)
            config["port"] = nc.webui_port if nc.enable_webui else 0
            config["token"] = nc.webui_token
            config["wsListenIp"] = nc.ws_listen_ip
            self._write_json(self.webui_config_path, config)
            return

        config = self._read_json(self.webui_config_path)
        updates = {}

        if not nc.enable_webui:
            LOG.warning("WebUI 已禁用")
            if config.get("port", 0) != 0:
                updates["port"] = 0
        else:
            checks = [
                ("token", nc.webui_token, "WebUI 令牌"),
                ("port", nc.webui_port, "WebUI 端口"),
                ("wsListenIp", nc.ws_listen_ip, "WebUI 监听 IP"),
            ]
            for key, expected, name in checks:
                if config.get(key) != expected:
                    LOG.warning(f"{name}不匹配, 将修改为: {expected}")
                    updates[key] = expected

        if updates:
            config.update(updates)
            self._write_json(self.webui_config_path, config)

    # ==================== 快速登录脚本 ====================

    def configure_quick_login(self) -> None:
        template = self._napcat_dir / "quickLoginExample.bat"
        if template.exists():
            shutil.copy(template, self.quick_login_script_path)

    # ==================== NapCat 内置插件配置 ====================

    def configure_plugins(self) -> None:
        """配置 NapCat 内置插件开关（plugins.json）。"""
        nc = self._napcat_config
        expected = {"napcat-plugin-builtin": nc.enable_napcat_builtin_plugins}

        if self.plugins_config_path.exists():
            config = self._read_json(self.plugins_config_path)
            if config.get("napcat-plugin-builtin") == nc.enable_napcat_builtin_plugins:
                return
            LOG.warning(
                f"NapCat 内置插件开关不匹配, "
                f"将修改为: {nc.enable_napcat_builtin_plugins}"
            )

        self._write_json(self.plugins_config_path, expected)

    # ==================== 主配置方法 ====================

    def configure_all(self) -> None:
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._enforce_webui_token_security()
        self._enforce_ws_token_security()
        self.configure_onebot()
        self.configure_quick_login()
        self.configure_webui()
        self.configure_plugins()
