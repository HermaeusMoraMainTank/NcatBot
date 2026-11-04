"""Tests for the PrivateReminder tasks page in the unified admin backend."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from ncatbot.admin.server import AdminServer  # noqa: E402


TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 4, 20, 0, tzinfo=TZ)


class FakeReminderPlugin:
    def __init__(self):
        self.data = {
            "reminders": [
                {
                    "id": 1,
                    "user_id": "100",
                    "content": "收鱼",
                    "due_at": (NOW + timedelta(minutes=30)).isoformat(),
                    "created_at": NOW.isoformat(),
                    "attempts": 0,
                },
                {
                    "id": 2,
                    "user_id": "200",
                    "content": "休息",
                    "due_at": (NOW - timedelta(minutes=5)).isoformat(),
                    "created_at": NOW.isoformat(),
                    "attempts": 3,
                    "failed_at": NOW.isoformat(),
                    "last_error": "offline",
                },
            ]
        }
        self._now = lambda: NOW
        self._reminder_lock = asyncio.Lock()
        self.saved = False

    def _save_data(self):
        self.saved = True


def _server(plugin: FakeReminderPlugin) -> AdminServer:
    bot = SimpleNamespace(
        config=SimpleNamespace(
            admin=SimpleNamespace(
                enabled=True,
                host="127.0.0.1",
                port=0,
                allow_remote=False,
                token="secret",
            )
        ),
        plugin_loader=SimpleNamespace(plugins={"PrivateReminder": plugin}),
    )
    server = AdminServer(bot)
    server.token = "secret"
    return server


@pytest.mark.asyncio
async def test_private_reminder_admin_lists_tasks_and_cancels():
    plugin = FakeReminderPlugin()
    server = _server(plugin)
    app = server._create_app()

    async with TestClient(
        TestServer(app)
    ) as client:
        response = await client.get(
            "/api/private-reminder/tasks",
            headers={"Authorization": "Bearer secret"},
        )
        assert response.status == 200
        body = await response.json()
        assert body["ok"] is True
        data = body["data"]
        assert data["pending"] == 1
        assert data["failed"] == 1
        assert data["items"][0]["id"] == 2
        assert data["items"][0]["status"] == "failed"
        assert data["items"][1]["due_display"] == "2026-11-04 20:30"

        response = await client.delete(
            "/api/private-reminder/tasks/1",
            headers={"Authorization": "Bearer secret"},
        )
        assert response.status == 200
        assert plugin.saved is True
        assert [item["id"] for item in plugin.data["reminders"]] == [2]


def test_admin_static_includes_reminder_tasks_view():
    path = ROOT / "ncatbot" / "admin" / "static" / "index.html"
    text = path.read_text(encoding="utf-8")
    assert 'id="view-reminders"' in text
    assert "预约提醒" in text
    assert "/api/private-reminder/tasks" in text
    assert "reminders: {title:" in text
