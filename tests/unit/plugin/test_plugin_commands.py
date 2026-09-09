"""插件命令帮助匹配测试。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins"))

from common.utils.plugin_commands import is_help_message  # noqa: E402


def test_bare_help_does_not_match_any_plugin():
    """PC-01: 裸「帮助」和「help」不应匹配任何插件帮助。"""
    assert not is_help_message("帮助", command_names=("钓鱼",))
    assert not is_help_message("help", command_names=("钓鱼",))


def test_help_requires_a_declared_command_prefix():
    """PC-02: 帮助必须带有当前插件声明的命令前缀。"""
    assert is_help_message("钓鱼帮助", command_names=("钓鱼",))
    assert is_help_message("钓鱼 help", command_names=("钓鱼",))
    assert is_help_message("钓鱼 帮助", command_names=("钓鱼",))
    assert not is_help_message("抽老婆帮助", command_names=("钓鱼",))


def test_help_matching_remains_exact():
    """PC-03: 帮助命令不接受额外文本或空命令名。"""
    assert not is_help_message("钓鱼帮助 现在", command_names=("钓鱼",))
    assert not is_help_message("帮助", command_names=("",))
