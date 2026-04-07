"""
NapCat Setup 模块单元测试

覆盖:
  S-01: configure_all() 自动创建 config 目录
  S-02: configure_onebot() 写入正确的 OneBot11 配置
  S-03: configure_onebot() 合并已有配置
  S-04: _install_linux() 不再要求 root 权限
  S-05: LinuxOps.napcat_dir rootless fallback
  S-06: LinuxOps 使用 napcat CLI（不带 sudo/bash）
  S-10: _enforce_ws_token_security
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ==================== Fixtures ====================


@pytest.fixture
def tmp_napcat_dir(tmp_path):
    """创建临时 napcat 目录结构"""
    napcat_dir = tmp_path / "napcat"
    napcat_dir.mkdir()
    return napcat_dir


@pytest.fixture
def mock_napcat_config():
    """模拟 NapCatConfig"""
    cfg = MagicMock()
    cfg.ws_uri = "ws://127.0.0.1:3001"
    cfg.ws_token = "test_token"
    cfg.ws_listen_ip = "0.0.0.0"
    cfg.enable_webui = False
    cfg.webui_port = 6099
    cfg.webui_token = "webui_token"
    cfg.enable_napcat_builtin_plugins = False
    return cfg


@pytest.fixture
def config_manager(tmp_napcat_dir, mock_napcat_config):
    """创建使用临时目录的 NapCatConfigManager"""
    from ncatbot.adapter.napcat.setup.config import NapCatConfigManager

    platform_ops = MagicMock()
    platform_ops.napcat_dir = tmp_napcat_dir
    platform_ops.config_dir = tmp_napcat_dir / "config"

    return NapCatConfigManager(
        platform_ops=platform_ops,
        napcat_config=mock_napcat_config,
        bot_uin="1550507358",
    )


# ==================== S-01: configure_all 自动创建 config 目录 ====================


class TestConfigureAll:
    @patch(
        "ncatbot.adapter.napcat.setup.config.NapCatConfigManager"
        "._enforce_webui_token_security"
    )
    def test_creates_config_dir_when_missing(
        self, _mock_sec, config_manager, tmp_napcat_dir
    ):
        """S-01: config 目录不存在时 configure_all 自动创建"""
        config_dir = tmp_napcat_dir / "config"
        assert not config_dir.exists()

        config_manager.configure_all()

        assert config_dir.exists()
        assert config_dir.is_dir()

    @patch(
        "ncatbot.adapter.napcat.setup.config.NapCatConfigManager"
        "._enforce_webui_token_security"
    )
    def test_succeeds_when_config_dir_exists(
        self, _mock_sec, config_manager, tmp_napcat_dir
    ):
        """S-01: config 目录已存在时不报错"""
        config_dir = tmp_napcat_dir / "config"
        config_dir.mkdir()

        config_manager.configure_all()

        assert config_dir.exists()


# ==================== S-02: configure_onebot 写入正确配置 ====================


class TestConfigureOnebot:
    def test_creates_new_config(self, config_manager, tmp_napcat_dir):
        """S-02: 首次运行时生成正确的 OneBot11 配置"""
        config_dir = tmp_napcat_dir / "config"
        config_dir.mkdir()

        config_manager.configure_onebot()

        config_path = config_dir / "onebot11_1550507358.json"
        assert config_path.exists()

        config = json.loads(config_path.read_text(encoding="utf-8"))
        servers = config["network"]["websocketServers"]
        assert len(servers) == 1
        assert servers[0]["port"] == 3001
        assert servers[0]["token"] == "test_token"
        assert servers[0]["host"] == "0.0.0.0"
        assert servers[0]["enable"] is True

    def test_merges_existing_config(self, config_manager, tmp_napcat_dir):
        """S-03: 已有配置时追加 WebSocket 服务器"""
        config_dir = tmp_napcat_dir / "config"
        config_dir.mkdir()

        existing = {
            "network": {"websocketServers": []},
            "musicSignUrl": "https://example.com",
        }
        config_path = config_dir / "onebot11_1550507358.json"
        config_path.write_text(json.dumps(existing), encoding="utf-8")

        config_manager.configure_onebot()

        config = json.loads(config_path.read_text(encoding="utf-8"))
        assert config["musicSignUrl"] == "https://example.com"
        assert len(config["network"]["websocketServers"]) == 1


# ==================== S-04: _install_linux 不再要求 root ====================


class TestInstallLinux:
    @patch("ncatbot.adapter.napcat.setup.installer.subprocess.Popen")
    @patch(
        "ncatbot.adapter.napcat.setup.installer.PlatformOps._confirm_action",
        return_value=True,
    )
    def test_no_root_check(self, mock_confirm, mock_popen):
        """S-04: _install_linux 不调用 _check_root, 不使用 sudo"""
        from ncatbot.adapter.napcat.setup.installer import NapCatInstaller
        from ncatbot.adapter.napcat.setup.platform import LinuxOps

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.wait = MagicMock()
        mock_popen.return_value = mock_process

        platform_ops = MagicMock(spec=LinuxOps)
        installer = NapCatInstaller(platform_ops)

        result = installer._install_linux("install")

        assert result is True
        # 验证调用的命令不包含 sudo
        cmd = mock_popen.call_args[0][0]
        assert "sudo" not in cmd

    @patch("ncatbot.adapter.napcat.setup.installer.subprocess.Popen")
    def test_skip_confirm_skips_prompt(self, mock_popen):
        """S-07: skip_confirm=True 时跳过确认直接安装"""
        from ncatbot.adapter.napcat.setup.installer import NapCatInstaller
        from ncatbot.adapter.napcat.setup.platform import LinuxOps

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.wait = MagicMock()
        mock_popen.return_value = mock_process

        platform_ops = MagicMock(spec=LinuxOps)
        installer = NapCatInstaller(platform_ops)

        result = installer._install_linux("install", skip_confirm=True)

        assert result is True
        mock_popen.assert_called_once()

    @patch("ncatbot.adapter.napcat.setup.installer.subprocess.Popen")
    @patch(
        "ncatbot.adapter.napcat.setup.installer.PlatformOps._confirm_action",
        return_value=False,
    )
    def test_confirm_declined_returns_false(self, mock_confirm, mock_popen):
        """S-07: 用户拒绝确认时返回 False"""
        from ncatbot.adapter.napcat.setup.installer import NapCatInstaller
        from ncatbot.adapter.napcat.setup.platform import LinuxOps

        platform_ops = MagicMock(spec=LinuxOps)
        installer = NapCatInstaller(platform_ops)

        result = installer._install_linux("install")

        assert result is False
        mock_popen.assert_not_called()


# ==================== S-07: install() 公开方法 ====================


class TestInstallerPublicInstall:
    @patch("ncatbot.adapter.napcat.setup.installer.subprocess.Popen")
    def test_install_delegates_with_skip_confirm(self, mock_popen):
        """S-07: install(skip_confirm=True) 传递到 _install_linux"""
        from ncatbot.adapter.napcat.setup.installer import NapCatInstaller
        from ncatbot.adapter.napcat.setup.platform import LinuxOps

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.wait = MagicMock()
        mock_popen.return_value = mock_process

        platform_ops = MagicMock(spec=LinuxOps)
        platform_ops.is_napcat_installed.return_value = False
        installer = NapCatInstaller(platform_ops)

        result = installer.install(skip_confirm=True)

        assert result is True


# ==================== S-05: LinuxOps.napcat_dir rootless fallback ====================


class TestLinuxOpsNapcatDir:
    @patch("ncatbot.adapter.napcat.setup.platform.Path")
    def test_rootless_fallback(self, mock_path_cls):
        """S-05: /opt/QQ 路径不存在时 fallback 到 ~/Napcat/..."""
        from ncatbot.adapter.napcat.setup.platform import LinuxOps

        mock_root_path = MagicMock()
        mock_root_path.exists.return_value = False
        mock_path_cls.return_value = mock_root_path
        mock_path_cls.home.return_value = Path("/home/testuser")

        ops = LinuxOps()
        result = ops.napcat_dir

        result_posix = result.as_posix()
        assert "Napcat" in result_posix
        assert "app_launcher/napcat" in result_posix


# ==================== S-06: LinuxOps 使用 napcat CLI ======================


class TestLinuxOpsNapcatCLI:
    @patch(
        "ncatbot.adapter.napcat.setup.platform.LinuxOps._has_napcat_cli",
        return_value=True,
    )
    @patch("ncatbot.adapter.napcat.setup.platform.subprocess.Popen")
    def test_is_napcat_running_uses_cli(self, mock_popen, _mock_cli):
        """S-06: is_napcat_running 使用 'napcat status'（无 bash/sudo）"""
        from ncatbot.adapter.napcat.setup.platform import LinuxOps

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b"PID: 12345"
        mock_process.stdout = mock_stdout
        mock_process.wait = MagicMock()
        mock_popen.return_value = mock_process

        ops = LinuxOps()
        result = ops.is_napcat_running()

        cmd = mock_popen.call_args[0][0]
        assert cmd == ["napcat", "status"]
        assert "sudo" not in cmd
        assert "bash" not in cmd
        assert result is True

    @patch(
        "ncatbot.adapter.napcat.setup.platform.LinuxOps._has_napcat_cli",
        return_value=True,
    )
    @patch("ncatbot.adapter.napcat.setup.platform.time.sleep")
    @patch("ncatbot.adapter.napcat.setup.platform.subprocess.Popen")
    def test_start_napcat_uses_cli(self, mock_popen, mock_sleep, _mock_cli):
        """S-06: start_napcat 使用 'napcat start <uin>'（无 sudo）"""
        from ncatbot.adapter.napcat.setup.platform import LinuxOps

        def make_status_process(output: bytes):
            p = MagicMock()
            p.returncode = 0
            p.stdout = MagicMock()
            p.stdout.read.return_value = output
            p.stderr = MagicMock()
            p.wait = MagicMock()
            return p

        # First call: is_napcat_running(uin) -> not running
        # Second call: is_napcat_running() -> not running
        # Third call: start -> success
        mock_start_process = MagicMock()
        mock_start_process.returncode = 0
        mock_start_process.stdout = MagicMock()
        mock_start_process.wait = MagicMock()

        # Fourth call: is_napcat_running(uin) -> running
        mock_popen.side_effect = [
            make_status_process(b"No running"),
            make_status_process(b"No running"),
            mock_start_process,
            make_status_process(b"PID: 12345, UIN: 123456"),
        ]

        ops = LinuxOps()
        ops.start_napcat("123456")

        # Verify the start call (third Popen call)
        start_call = mock_popen.call_args_list[2]
        cmd = start_call[0][0]
        assert cmd == ["napcat", "start", "123456"]
        assert "sudo" not in cmd

    @patch(
        "ncatbot.adapter.napcat.setup.platform.LinuxOps._has_napcat_cli",
        return_value=True,
    )
    @patch("ncatbot.adapter.napcat.setup.platform.subprocess.Popen")
    def test_stop_napcat_uses_cli(self, mock_popen, _mock_cli):
        """S-06: stop_napcat 使用 'napcat stop'（无 bash/sudo）"""
        from ncatbot.adapter.napcat.setup.platform import LinuxOps

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = MagicMock()
        mock_process.wait = MagicMock()
        mock_popen.return_value = mock_process

        ops = LinuxOps()
        ops.stop_napcat()

        cmd = mock_popen.call_args[0][0]
        assert cmd == ["napcat", "stop"]
        assert "sudo" not in cmd
        assert "bash" not in cmd


# ==================== S-08: _enforce_webui_token_security ====================


class TestEnforceWebuiTokenSecurity:
    """WebUI token 安全强制检查。"""

    def _make_manager(self, tmp_path, webui_token="napcat_webui"):
        from ncatbot.adapter.napcat.setup.config import NapCatConfigManager

        cfg = MagicMock()
        cfg.ws_uri = "ws://127.0.0.1:3001"
        cfg.ws_token = "test_token"
        cfg.ws_listen_ip = "0.0.0.0"
        cfg.enable_webui = True
        cfg.webui_port = 6099
        cfg.webui_token = webui_token
        cfg.enable_napcat_builtin_plugins = False

        platform_ops = MagicMock()
        platform_ops.napcat_dir = tmp_path / "napcat"
        platform_ops.config_dir = tmp_path / "napcat" / "config"

        mgr = NapCatConfigManager(
            platform_ops=platform_ops,
            napcat_config=cfg,
            bot_uin="123456",
        )
        return mgr, cfg

    @patch("ncatbot.adapter.napcat.setup.config.get_config_manager")
    def test_default_weak_token_replaced(self, mock_gcm, tmp_path):
        """S-08a: 默认弱密钥自动替换为强密钥"""
        mock_entry = MagicMock()
        mock_entry.config = {"webui_token": "napcat_webui"}
        mock_gcm.return_value.get_adapter_config.return_value = mock_entry

        mgr, cfg = self._make_manager(tmp_path, webui_token="napcat_webui")
        mgr._enforce_webui_token_security()

        # token 已被替换
        assert cfg.webui_token != "napcat_webui"
        # config.yaml entry 也已更新
        assert mock_entry.config["webui_token"] == cfg.webui_token
        mock_gcm.return_value.save.assert_called_once()

    def test_strong_token_unchanged(self, tmp_path):
        """S-08b: 强密钥不做任何修改"""
        from ncatbot.utils.config.security import generate_strong_token

        strong = generate_strong_token()
        mgr, cfg = self._make_manager(tmp_path, webui_token=strong)
        mgr._enforce_webui_token_security()

        assert cfg.webui_token == strong

    @patch("ncatbot.adapter.napcat.setup.config.confirm", return_value=True)
    @patch("ncatbot.adapter.napcat.setup.config.get_config_manager")
    def test_non_default_weak_token_confirm_yes(self, mock_gcm, mock_confirm, tmp_path):
        """S-08c: 非默认弱密钥 + 确认替换 → 替换"""
        mock_entry = MagicMock()
        mock_entry.config = {"webui_token": "myweakpw"}
        mock_gcm.return_value.get_adapter_config.return_value = mock_entry

        mgr, cfg = self._make_manager(tmp_path, webui_token="myweakpw")
        mgr._enforce_webui_token_security()

        assert cfg.webui_token != "myweakpw"
        mock_gcm.return_value.save.assert_called_once()
        mock_confirm.assert_called_once()

    @patch("ncatbot.adapter.napcat.setup.config.confirm", return_value=False)
    def test_non_default_weak_token_confirm_no(self, mock_confirm, tmp_path):
        """S-08d: 非默认弱密钥 + 拒绝替换 → 保持原样"""
        mgr, cfg = self._make_manager(tmp_path, webui_token="myweakpw")
        mgr._enforce_webui_token_security()

        assert cfg.webui_token == "myweakpw"
        mock_confirm.assert_called_once()


# ==================== S-10: _enforce_ws_token_security ====================


class TestEnforceWsTokenSecurity:
    """WS token 安全强制检查（ws_listen_ip 非本地时）。"""

    def _make_manager(self, tmp_path, ws_token="napcat_ws", ws_listen_ip="0.0.0.0"):
        from ncatbot.adapter.napcat.setup.config import NapCatConfigManager

        cfg = MagicMock()
        cfg.ws_uri = "ws://127.0.0.1:3001"
        cfg.ws_token = ws_token
        cfg.ws_listen_ip = ws_listen_ip
        cfg.enable_webui = False
        cfg.webui_port = 6099
        cfg.webui_token = "webui_token"
        cfg.enable_napcat_builtin_plugins = False

        platform_ops = MagicMock()
        platform_ops.napcat_dir = tmp_path / "napcat"
        platform_ops.config_dir = tmp_path / "napcat" / "config"

        mgr = NapCatConfigManager(
            platform_ops=platform_ops,
            napcat_config=cfg,
            bot_uin="123456",
        )
        return mgr, cfg

    @patch("ncatbot.adapter.napcat.setup.config.get_config_manager")
    def test_default_weak_token_replaced(self, mock_gcm, tmp_path):
        """S-10a: 0.0.0.0 + 默认弱密钥 → 自动替换"""
        mock_entry = MagicMock()
        mock_entry.config = {"ws_token": "napcat_ws"}
        mock_gcm.return_value.get_adapter_config.return_value = mock_entry

        mgr, cfg = self._make_manager(tmp_path, ws_token="napcat_ws")
        mgr._enforce_ws_token_security()

        assert cfg.ws_token != "napcat_ws"
        assert mock_entry.config["ws_token"] == cfg.ws_token
        mock_gcm.return_value.save.assert_called_once()

    def test_strong_token_unchanged(self, tmp_path):
        """S-10b: 强密钥不做任何修改"""
        from ncatbot.utils.config.security import generate_strong_token

        strong = generate_strong_token()
        mgr, cfg = self._make_manager(tmp_path, ws_token=strong)
        mgr._enforce_ws_token_security()

        assert cfg.ws_token == strong

    def test_localhost_skips_check(self, tmp_path):
        """S-10c: ws_listen_ip=localhost → 跳过检查，弱密钥保留"""
        mgr, cfg = self._make_manager(
            tmp_path, ws_token="napcat_ws", ws_listen_ip="localhost"
        )
        mgr._enforce_ws_token_security()

        assert cfg.ws_token == "napcat_ws"

    def test_127_skips_check(self, tmp_path):
        """S-10d: ws_listen_ip=127.0.0.1 → 跳过检查"""
        mgr, cfg = self._make_manager(
            tmp_path, ws_token="napcat_ws", ws_listen_ip="127.0.0.1"
        )
        mgr._enforce_ws_token_security()

        assert cfg.ws_token == "napcat_ws"

    @patch("ncatbot.adapter.napcat.setup.config.confirm", return_value=True)
    @patch("ncatbot.adapter.napcat.setup.config.get_config_manager")
    def test_non_default_weak_token_confirm_yes(self, mock_gcm, mock_confirm, tmp_path):
        """S-10e: 非默认弱密钥 + 确认替换 → 替换"""
        mock_entry = MagicMock()
        mock_entry.config = {"ws_token": "myweakpw"}
        mock_gcm.return_value.get_adapter_config.return_value = mock_entry

        mgr, cfg = self._make_manager(tmp_path, ws_token="myweakpw")
        mgr._enforce_ws_token_security()

        assert cfg.ws_token != "myweakpw"
        mock_gcm.return_value.save.assert_called_once()
        mock_confirm.assert_called_once()

    @patch("ncatbot.adapter.napcat.setup.config.confirm", return_value=False)
    def test_non_default_weak_token_confirm_no(self, mock_confirm, tmp_path):
        """S-10f: 非默认弱密钥 + 拒绝替换 → 保持原样"""
        mgr, cfg = self._make_manager(tmp_path, ws_token="myweakpw")
        mgr._enforce_ws_token_security()

        assert cfg.ws_token == "myweakpw"
        mock_confirm.assert_called_once()


# ==================== S-09: configure_plugins ====================


class TestConfigurePlugins:
    """NapCat 内置插件开关配置。"""

    def _make_manager(self, tmp_path, enable_builtin=False):
        from ncatbot.adapter.napcat.setup.config import NapCatConfigManager

        cfg = MagicMock()
        cfg.ws_uri = "ws://127.0.0.1:3001"
        cfg.ws_token = "test_token"
        cfg.ws_listen_ip = "0.0.0.0"
        cfg.enable_webui = False
        cfg.webui_port = 6099
        cfg.webui_token = "webui_token"
        cfg.enable_napcat_builtin_plugins = enable_builtin

        platform_ops = MagicMock()
        napcat_dir = tmp_path / "napcat"
        napcat_dir.mkdir(parents=True, exist_ok=True)
        config_dir = napcat_dir / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        platform_ops.napcat_dir = napcat_dir
        platform_ops.config_dir = config_dir

        mgr = NapCatConfigManager(
            platform_ops=platform_ops,
            napcat_config=cfg,
            bot_uin="123456",
        )
        return mgr, config_dir

    def test_creates_plugins_json_default_off(self, tmp_path):
        """S-09a: 默认关闭内置插件 → 写入 false"""
        mgr, config_dir = self._make_manager(tmp_path, enable_builtin=False)
        mgr.configure_plugins()

        plugins_path = config_dir / "plugins.json"
        assert plugins_path.exists()
        data = json.loads(plugins_path.read_text(encoding="utf-8"))
        assert data == {"napcat-plugin-builtin": False}

    def test_creates_plugins_json_enabled(self, tmp_path):
        """S-09b: 开启内置插件 → 写入 true"""
        mgr, config_dir = self._make_manager(tmp_path, enable_builtin=True)
        mgr.configure_plugins()

        plugins_path = config_dir / "plugins.json"
        data = json.loads(plugins_path.read_text(encoding="utf-8"))
        assert data == {"napcat-plugin-builtin": True}

    def test_updates_existing_plugins_json(self, tmp_path):
        """S-09c: 已有 plugins.json 且值不匹配 → 覆写"""
        mgr, config_dir = self._make_manager(tmp_path, enable_builtin=False)
        plugins_path = config_dir / "plugins.json"
        plugins_path.write_text(
            json.dumps({"napcat-plugin-builtin": True}), encoding="utf-8"
        )

        mgr.configure_plugins()

        data = json.loads(plugins_path.read_text(encoding="utf-8"))
        assert data == {"napcat-plugin-builtin": False}

    def test_skips_write_when_matching(self, tmp_path):
        """S-09d: 已有 plugins.json 且值匹配 → 不写入"""
        mgr, config_dir = self._make_manager(tmp_path, enable_builtin=False)
        plugins_path = config_dir / "plugins.json"
        plugins_path.write_text(
            json.dumps({"napcat-plugin-builtin": False}), encoding="utf-8"
        )
        mtime_before = plugins_path.stat().st_mtime

        mgr.configure_plugins()

        assert plugins_path.stat().st_mtime == mtime_before
