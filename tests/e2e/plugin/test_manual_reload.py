"""
手动热重载与加载/卸载 E2E（落盘探测插件）

规范:
  PL-MR-01: reload_plugin 后群回复随磁盘 main.py 更新
  PL-MR-02: 仅改盘不 reload 时仍为旧回复
  PL-MR-03: unload 后 ping 不再触发探测插件回复
  PL-MR-04: load_plugin 恢复行为；私聊 !reload 改盘后生效
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Final

import pytest

import ncatbot.utils.config.manager as config_manager_mod
from ncatbot.core.registry.registrar import _pending_handlers
from ncatbot.testing import PluginTestHarness
from ncatbot.testing.assertions import extract_text
from ncatbot.testing.factories.qq import group_message, private_message
from ncatbot.utils.config.manager import get_config_manager

PLUGIN_NAME: Final[str] = "manual_reload_probe"
PLUGIN_FOLDER: Final[str] = "manual_reload_probe"

REPLY_V1 = "MR_PROBE_V1"
REPLY_V2 = "MR_PROBE_V2"


def _write_minimal_e2e_config(cfg_path: Path) -> None:
    cfg_path.write_text(
        "bot_uin: '111111'\n"
        "root: '10001'\n"
        "adapters:\n"
        "  - type: napcat\n"
        "    platform: qq\n"
        "    enabled: true\n"
        "    config: {}\n"
        "plugin:\n"
        "  enable_builtin_commands: true\n"
        "  builtin_commands_group_allowed: false\n",
        encoding="utf-8",
    )


def _write_manual_reload_probe_plugin(plugins_root: Path, reply_text: str) -> Path:
    plugins_root = Path(plugins_root).resolve()
    plugins_dir = plugins_root / PLUGIN_FOLDER
    plugins_dir.mkdir(parents=True, exist_ok=True)

    manifest = (
        f'name = "{PLUGIN_NAME}"\n'
        "version = '1.0.0'\n"
        "main = 'main.py'\n"
        "entry_class = 'ManualReloadProbePlugin'\n"
        "author = 'Test'\n"
        "description = 'manual reload E2E probe'\n"
    )
    (plugins_dir / "manifest.toml").write_text(manifest, encoding="utf-8")

    main_py = plugins_dir / "main.py"
    main_py.write_text(
        "from ncatbot.core import registrar\n"
        "from ncatbot.event.qq import GroupMessageEvent\n"
        "from ncatbot.plugin import NcatBotPlugin\n"
        "\n"
        "\n"
        "class ManualReloadProbePlugin(NcatBotPlugin):\n"
        f'    name = "{PLUGIN_NAME}"\n'
        "    version = '1.0.0'\n"
        "    author = 'Test'\n"
        "    description = 'probe'\n"
        "\n"
        "    @registrar.qq.on_group_command('ping', ignore_case=True)\n"
        "    async def on_ping(self, event: GroupMessageEvent):\n"
        f"        await self.api.qq.post_group_msg(event.group_id, text={reply_text!r})\n",
        encoding="utf-8",
    )
    return main_py


def _touch_main_py_for_mtime(main_py: Path) -> None:
    time.sleep(0.02)


@pytest.fixture(autouse=True)
def reset_config_singleton(monkeypatch):
    config_manager_mod._default_manager = None
    yield
    config_manager_mod._default_manager = None


@pytest.fixture(autouse=True)
def clean_pending():
    _pending_handlers.clear()
    yield
    _pending_handlers.clear()


def _apply_e2e_config(tmp_path, monkeypatch) -> None:
    p = tmp_path / "cfg.yaml"
    _write_minimal_e2e_config(p)
    monkeypatch.setenv("NCATBOT_CONFIG_PATH", str(p))
    get_config_manager(str(p))


def _last_send_group_text(h: PluginTestHarness) -> str:
    calls = h.mock_api.get_calls("send_group_msg")
    assert calls, "expected send_group_msg"
    return extract_text(calls[-1])


# ---- PL-MR-01 ----


async def test_reload_plugin_updates_reply_from_disk(tmp_path, monkeypatch):
    """PL-MR-01: 覆写 main.py 后 reload_plugin，群 ping 使用新回复文本。"""
    _apply_e2e_config(tmp_path, monkeypatch)
    plugins_root = tmp_path / "plugins"
    main_py = _write_manual_reload_probe_plugin(plugins_root, REPLY_V1)

    async with PluginTestHarness([PLUGIN_NAME], plugins_root, skip_builtin=True) as h:
        await h.inject(group_message("ping", group_id="7001", user_id="99999"))
        await h.settle(0.15)
        assert REPLY_V1 in _last_send_group_text(h)

        _write_manual_reload_probe_plugin(plugins_root, REPLY_V2)
        _touch_main_py_for_mtime(main_py)

        ok = await h.reload_plugin(PLUGIN_NAME)
        assert ok is True

        h.reset_api()
        await h.inject(group_message("ping", group_id="7001", user_id="99999"))
        await h.settle(0.15)
        h.assert_api("send_group_msg").called()
        assert REPLY_V2 in _last_send_group_text(h)


# ---- PL-MR-02 ----


async def test_disk_edit_without_reload_keeps_old_reply(tmp_path, monkeypatch):
    """PL-MR-02: 仅改盘、不调用 reload 时行为不变。"""
    _apply_e2e_config(tmp_path, monkeypatch)
    plugins_root = tmp_path / "plugins"
    main_py = _write_manual_reload_probe_plugin(plugins_root, REPLY_V1)

    async with PluginTestHarness([PLUGIN_NAME], plugins_root, skip_builtin=True) as h:
        await h.inject(group_message("ping", group_id="7002", user_id="99999"))
        await h.settle(0.15)
        assert REPLY_V1 in _last_send_group_text(h)

        _write_manual_reload_probe_plugin(plugins_root, REPLY_V2)
        _touch_main_py_for_mtime(main_py)

        h.reset_api()
        await h.inject(group_message("ping", group_id="7002", user_id="99999"))
        await h.settle(0.15)
        assert REPLY_V1 in _last_send_group_text(h)


# ---- PL-MR-03 / load ----


async def test_unload_then_ping_has_no_probe_reply(tmp_path, monkeypatch):
    """PL-MR-03: unload 后探测插件不再响应 ping。"""
    _apply_e2e_config(tmp_path, monkeypatch)
    plugins_root = tmp_path / "plugins"
    _write_manual_reload_probe_plugin(plugins_root, REPLY_V1)

    async with PluginTestHarness([PLUGIN_NAME], plugins_root, skip_builtin=True) as h:
        await h.bot.plugin_loader.unload_plugin(PLUGIN_NAME)
        assert PLUGIN_NAME not in h.loaded_plugins

        h.reset_api()
        await h.inject(group_message("ping", group_id="7003", user_id="99999"))
        await h.settle(0.15)
        h.assert_api("send_group_msg").not_called()


async def test_load_after_unload_restores_reply(tmp_path, monkeypatch):
    """PL-MR-03b: unload 后再 load_plugin，行为恢复。"""
    _apply_e2e_config(tmp_path, monkeypatch)
    plugins_root = tmp_path / "plugins"
    _write_manual_reload_probe_plugin(plugins_root, REPLY_V1)

    async with PluginTestHarness([PLUGIN_NAME], plugins_root, skip_builtin=True) as h:
        await h.bot.plugin_loader.unload_plugin(PLUGIN_NAME)
        plugin = await h.bot.plugin_loader.load_plugin(PLUGIN_NAME)
        assert plugin is not None

        h.reset_api()
        await h.inject(group_message("ping", group_id="7004", user_id="99999"))
        await h.settle(0.15)
        assert REPLY_V1 in _last_send_group_text(h)


# ---- PL-MR-04 ----


async def test_bang_reload_after_disk_edit(tmp_path, monkeypatch):
    """PL-MR-04: root 私聊 !reload <name> 后，新 main.py 生效。"""
    _apply_e2e_config(tmp_path, monkeypatch)
    plugins_root = tmp_path / "plugins"
    main_py = _write_manual_reload_probe_plugin(plugins_root, REPLY_V1)

    async with PluginTestHarness([PLUGIN_NAME], plugins_root, skip_builtin=False) as h:
        await h.inject(group_message("ping", group_id="7005", user_id="99999"))
        await h.settle(0.15)
        assert REPLY_V1 in _last_send_group_text(h)

        _write_manual_reload_probe_plugin(plugins_root, REPLY_V2)
        _touch_main_py_for_mtime(main_py)

        h.reset_api()
        await h.inject(private_message(f"!reload {PLUGIN_NAME}", user_id="10001"))
        await h.settle(0.2)

        assert h.mock_api.called("send_private_msg")
        ack = extract_text(h.mock_api.get_calls("send_private_msg")[-1])
        assert "重载" in ack or PLUGIN_NAME in ack

        h.reset_api()
        await h.inject(group_message("ping", group_id="7005", user_id="99999"))
        await h.settle(0.15)
        assert REPLY_V2 in _last_send_group_text(h)
