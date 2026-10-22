"""TodayWaifu 已移除敏感命令的回归测试。"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins"))

from TodayWaifu.commands import handlers
from TodayWaifu.config import KEYWORD_ROUTES
from TodayWaifu.TodayWaifu import TodayWaifu


def _plugin(keyword_trigger_enabled: bool = True):
    return SimpleNamespace(
        cfg_dict=lambda: {
            "keyword_trigger_enabled": keyword_trigger_enabled,
            "keyword_trigger_mode": "exact",
        }
    )


def test_rape_command_is_not_registered():
    assert "强奸" not in KEYWORD_ROUTES
    assert not hasattr(handlers, "cmd_rape_marry")


@pytest.mark.parametrize("enabled", [True, False])
def test_rape_command_is_not_resolved(enabled: bool):
    plugin = _plugin(enabled)
    assert TodayWaifu._resolve_action(plugin, "强奸 @1211330825") is None
