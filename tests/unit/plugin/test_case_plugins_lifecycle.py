"""开箱插件生命周期（CM-02）：合并后的 CaseOpening 能真实跑通。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins"))

from CaseOpening import CaseOpening


class _FakeServices:
    def get(self, name):
        return None


class _FakeEconomyStore:
    @staticmethod
    def change_balance(user_id, delta, reason=""):
        return {"ok": True, "balance": 1000}


class _FakeEconomyPlugin:
    store = _FakeEconomyStore()


class _FakeLoader:
    def get_plugin(self, name):
        return _FakeEconomyPlugin() if name == "GroupEconomy" else None


class _FakeQQ:
    async def post_group_msg(self, **kwargs):
        return None


class _FakeApi:
    qq = _FakeQQ()


@pytest.mark.asyncio
async def test_case_opening_on_load(tmp_path: Path):
    plugin = CaseOpening()
    plugin.warm_images_on_load = False  # 单测不打网络，图片预热另测
    plugin.workspace = tmp_path / "CaseOpening"
    plugin.config = {}
    plugin.data = {}
    plugin.services = _FakeServices()
    plugin.api = _FakeApi()
    plugin._plugin_loader = _FakeLoader()
    plugin._manifest = None
    await plugin.on_load()
    try:
        assert len(plugin.cs2.cases) == 52
        assert len(plugin.dota.treasures) == 13
        assert plugin.cs2.store.count("1") == 0
        assert plugin.dota.store.count("1") == 0
        assert plugin.loan.status("1")["debt"] == 0
    finally:
        await plugin.on_close()
