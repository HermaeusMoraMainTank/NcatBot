"""TodayWaifu 受限「强奸」命令测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins"))

from ncatbot.types import At  # noqa: E402
from TodayWaifu.commands import handlers  # noqa: E402


def _event(user_id: str, target_id: str):
    return SimpleNamespace(
        sender=SimpleNamespace(user_id=user_id),
        message=[At(user_id=target_id)],
    )


@pytest.mark.asyncio
async def test_rape_command_only_delegates_for_exact_user_and_target():
    plugin = SimpleNamespace()
    with patch.object(handlers, "cmd_force_marry", new=AsyncMock()) as force_marry:
        await handlers.cmd_rape_marry(plugin, _event("794383252", "1211330825"))
        force_marry.assert_awaited_once()


@pytest.mark.asyncio
async def test_rape_command_rejects_other_user_or_target():
    plugin = SimpleNamespace()
    with patch.object(handlers, "cmd_force_marry", new=AsyncMock()) as force_marry:
        await handlers.cmd_rape_marry(plugin, _event("794383252", "123"))
        await handlers.cmd_rape_marry(plugin, _event("123", "1211330825"))
        force_marry.assert_not_awaited()
