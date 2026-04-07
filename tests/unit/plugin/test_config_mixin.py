"""
ConfigMixin 规范测试

规范:
  M-01: _mixin_load() 从插件源码目录加载默认配置
  M-02: set_config() 持久化到全局 config_manager
  M-04: remove_config() 返回 bool
  M-05: update_config() 批量更新
  M-06: 源码 config.yaml 不存在时优雅返回空字典
  M-07: 全局覆盖优先级高于源码默认值
  M-08: init_defaults() 仅补充缺失键、不持久化
  M-09: _mixin_unload() 为 no-op
"""

from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ncatbot.plugin.mixin.config_mixin import ConfigMixin


@dataclass
class FakeManifest:
    """模拟 PluginManifest"""

    name: str = "test_plugin"
    version: str = "1.0.0"
    main: str = "main.py"
    plugin_path: Path = field(default_factory=lambda: Path("."))
    folder_name: str = "test_plugin"


class FakePlugin(ConfigMixin):
    """最小配置 mixin 实例"""

    def __init__(self, workspace, *, source_dir=None, manifest=None):
        self.name = "test_plugin"
        self.workspace = workspace
        self.config = {}
        if manifest is not None:
            self._manifest = manifest
        else:
            m = FakeManifest()
            # source_dir 对应真实的 plugin_path（manifest.toml 所在目录）
            m.plugin_path = (
                source_dir if source_dir is not None else (workspace / "_src")
            )
            self._manifest = m


def _make_fake_mgr(plugin_configs=None):
    """创建 mock ConfigManager"""
    mgr = MagicMock()
    mgr.config.plugin.plugin_configs = (
        plugin_configs if plugin_configs is not None else {}
    )
    return mgr


@pytest.fixture
def source_dir(tmp_path):
    """创建插件源码目录"""
    d = tmp_path / "_src" / "test_plugin"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def plugin(tmp_path, source_dir):
    return FakePlugin(tmp_path, source_dir=source_dir)


# ---- M-01: _mixin_load 从源码目录加载 ----


@patch("ncatbot.plugin.mixin.config_mixin.get_config_manager")
def test_mixin_load_from_source_yaml(mock_gcm, tmp_path, source_dir):
    """M-01: 从插件源码目录的 config.yaml 加载默认配置"""
    mock_gcm.return_value = _make_fake_mgr()
    config_path = source_dir / "config.yaml"
    config_path.write_text("api_key: abc123\ntimeout: 30\n", encoding="utf-8")

    p = FakePlugin(tmp_path, source_dir=source_dir)
    p._mixin_load()

    assert p.config == {"api_key": "abc123", "timeout": 30}


@patch("ncatbot.plugin.mixin.config_mixin.get_config_manager")
def test_mixin_load_missing_source(mock_gcm, tmp_path):
    """M-06: 源码 config.yaml 不存在 → 返回空字典"""
    mock_gcm.return_value = _make_fake_mgr()
    p = FakePlugin(tmp_path)
    p._mixin_load()
    assert p.config == {}


# ---- M-02: set_config 持久化到全局 config ----


@patch("ncatbot.plugin.mixin.config_mixin.get_config_manager")
def test_set_config_persists_to_global(mock_gcm, plugin):
    """M-02: set_config() 写入全局 config_manager.plugin_configs"""
    mgr = _make_fake_mgr()
    mock_gcm.return_value = mgr

    plugin.config = {}
    plugin.set_config("key", "value")

    assert plugin.config["key"] == "value"
    assert mgr.config.plugin.plugin_configs["test_plugin"]["key"] == "value"
    mgr.save.assert_called_once()


# ---- M-04: remove_config ----


@patch("ncatbot.plugin.mixin.config_mixin.get_config_manager")
def test_remove_config_existing(mock_gcm, plugin):
    """M-04: 移除存在的 key → True"""
    mgr = _make_fake_mgr({"test_plugin": {"key": "value"}})
    mock_gcm.return_value = mgr

    plugin.config = {"key": "value"}
    result = plugin.remove_config("key")
    assert result is True
    assert "key" not in plugin.config


def test_remove_config_missing(plugin):
    """M-04: 移除不存在的 key → False"""
    plugin.config = {}
    result = plugin.remove_config("nonexistent")
    assert result is False


# ---- M-05: update_config ----


@patch("ncatbot.plugin.mixin.config_mixin.get_config_manager")
def test_update_config(mock_gcm, plugin):
    """M-05: update_config() 批量更新并持久化到全局 config"""
    mgr = _make_fake_mgr()
    mock_gcm.return_value = mgr

    plugin.config = {"a": 1}
    plugin.update_config({"b": 2, "c": 3})

    assert plugin.config == {"a": 1, "b": 2, "c": 3}
    assert mgr.config.plugin.plugin_configs["test_plugin"] == {"b": 2, "c": 3}
    mgr.save.assert_called_once()


# ---- M-06: 非字典内容优雅处理 ----


@patch("ncatbot.plugin.mixin.config_mixin.get_config_manager")
def test_load_non_dict_yaml(mock_gcm, tmp_path, source_dir):
    """M-06: 源码 YAML 内容不是字典时 → 返回空字典"""
    mock_gcm.return_value = _make_fake_mgr()
    config_path = source_dir / "config.yaml"
    config_path.write_text("- item1\n- item2\n", encoding="utf-8")

    p = FakePlugin(tmp_path, source_dir=source_dir)
    p._mixin_load()
    assert p.config == {}


# ---- M-07: 全局覆盖优先级 > 源码默认值 ----


@patch("ncatbot.plugin.mixin.config_mixin.get_config_manager")
def test_global_overrides_source_defaults(mock_gcm, tmp_path, source_dir):
    """M-07: 全局 plugin_configs 覆盖源码默认值"""
    config_path = source_dir / "config.yaml"
    config_path.write_text("api_key: default\ntimeout: 30\n", encoding="utf-8")

    mock_gcm.return_value = _make_fake_mgr({"test_plugin": {"api_key": "overridden"}})

    p = FakePlugin(tmp_path, source_dir=source_dir)
    p._mixin_load()

    assert p.config["api_key"] == "overridden"
    assert p.config["timeout"] == 30


# ---- M-08: init_defaults ----


def test_init_defaults_fills_missing(plugin):
    """M-08: init_defaults() 仅补充缺失键"""
    plugin.config = {"existing": "keep"}
    plugin.init_defaults({"existing": "ignored", "new_key": "added"})

    assert plugin.config["existing"] == "keep"
    assert plugin.config["new_key"] == "added"


@patch("ncatbot.plugin.mixin.config_mixin.get_config_manager")
def test_init_defaults_no_persist(mock_gcm, plugin):
    """M-08: init_defaults() 不触发持久化"""
    mgr = _make_fake_mgr()
    mock_gcm.return_value = mgr

    plugin.config = {}
    plugin.init_defaults({"key": "value"})

    mgr.save.assert_not_called()


# ---- M-09: _mixin_unload 为 no-op ----


def test_mixin_unload_noop(plugin):
    """M-10: _mixin_unload() 不写入任何文件"""
    plugin.config = {"saved": True}
    plugin._mixin_unload()

    config_path = plugin.workspace / "config.yaml"
    assert not config_path.exists()


# ---- _apply_global_overrides 不因 name 缺失崩溃 ----


def test_apply_global_overrides_with_name(tmp_path, source_dir):
    """_apply_global_overrides 在 name 已设置时不抛异常"""
    p = FakePlugin(tmp_path, source_dir=source_dir)
    # 应正常执行（get_config_manager 可能不可用，但 exception 应被捕获）
    p._apply_global_overrides()
    # 不崩溃即通过
