"""init 模板插件生成单元测试。

规范:
  CX-21: 数字开头的用户名生成的模板插件入口类名合法且可编译加载
  CX-22: _to_class_name 始终产出合法标识符且不重复 Plugin 后缀
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from ncatbot.cli.commands.init import _generate_template_plugin, _to_class_name


def test_numeric_username_generates_loadable_plugin(tmp_path: Path):
    """CX-21: 数字开头的用户名生成的模板插件入口类名合法且可编译加载。"""
    with patch("ncatbot.cli.commands.init.getpass.getuser", return_value="35921"):
        _generate_template_plugin(tmp_path)

    plugin_dirs = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert len(plugin_dirs) == 1
    pdir = plugin_dirs[0]

    # 生成的 plugin.py 必须是合法 Python（数字开头用户名曾导致 SyntaxError）
    source = (pdir / "plugin.py").read_text(encoding="utf-8")
    compile(source, "plugin.py", "exec")  # 不应抛 SyntaxError

    # manifest 的 entry_class 必须是合法标识符，且与 plugin.py 中的类名一致
    manifest = tomllib.loads((pdir / "manifest.toml").read_text(encoding="utf-8"))
    entry_class = manifest["entry_class"]
    assert entry_class.isidentifier()
    assert f"class {entry_class}(" in source


@pytest.mark.parametrize(
    "plugin_name, expected",
    [
        ("_35921_plugin", "_35921Plugin"),  # 纯数字用户名
        ("john_plugin", "JohnPlugin"),  # 普通用户名：无重复 Plugin 后缀
        ("_1234abc_plugin", "_1234abcPlugin"),  # 数字开头混合
        ("alice_dev_plugin", "AliceDevPlugin"),
    ],
)
def test_to_class_name_is_valid_identifier(plugin_name: str, expected: str):
    """CX-22: _to_class_name 始终产出合法标识符且不重复 Plugin 后缀。"""
    result = _to_class_name(plugin_name)
    assert result == expected
    assert result.isidentifier()
    assert not result.endswith("PluginPlugin")
