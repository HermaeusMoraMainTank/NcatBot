"""
DataMixin 规范测试

规范:
  M-10: _mixin_load() 从 JSON 加载数据
  M-11: _mixin_unload() 将数据保存到 JSON
  M-12: 数据文件不存在时优雅返回空字典
"""

import json

import pytest

from ncatbot.plugin.mixin.data_mixin import DataMixin


class FakePlugin(DataMixin):
    """最小数据 mixin 实例"""

    def __init__(self, workspace):
        self.name = "test_plugin"
        self.workspace = workspace
        self.data = {}


@pytest.fixture
def plugin(tmp_path):
    return FakePlugin(tmp_path)


# ---- M-10: _mixin_load 加载 JSON ----


def test_mixin_load_from_json(tmp_path):
    """M-10: 从 data.json 加载数据"""
    data_path = tmp_path / "data.json"
    data_path.write_text('{"counter": 5, "items": [1, 2]}', encoding="utf-8")

    p = FakePlugin(tmp_path)
    p._mixin_load()

    assert p.data == {"counter": 5, "items": [1, 2]}


# ---- M-11: _mixin_unload 保存 JSON ----


def test_mixin_unload_saves(plugin):
    """M-11: _mixin_unload() 将 data 保存到 data.json"""
    plugin.data = {"key": "value", "count": 42}
    plugin._mixin_unload()

    raw = json.loads(plugin._data_path.read_text(encoding="utf-8"))
    assert raw == {"key": "value", "count": 42}


def test_roundtrip(tmp_path):
    """M-10+M-11: 加载 → 修改 → 保存 → 重载数据一致"""
    p = FakePlugin(tmp_path)
    p.data = {"original": True}
    p._mixin_unload()

    p2 = FakePlugin(tmp_path)
    p2._mixin_load()
    assert p2.data == {"original": True}


# ---- M-12: 文件不存在优雅处理 ----


def test_missing_file_returns_empty(plugin):
    """M-12: 数据文件不存在 → 返回空字典"""
    plugin._mixin_load()
    assert plugin.data == {}


def test_non_dict_json_returns_empty(tmp_path):
    """M-12 补充: JSON 内容不是字典 → 返回空字典"""
    data_path = tmp_path / "data.json"
    data_path.write_text("[1, 2, 3]", encoding="utf-8")

    p = FakePlugin(tmp_path)
    p._mixin_load()
    assert p.data == {}
