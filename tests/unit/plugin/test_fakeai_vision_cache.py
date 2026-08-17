"""FakeAi 图片识别缓存单测。"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def fakeai_memory():
    """独立加载 memory.py，避免触发 FakeAi 包级导入的框架循环。"""
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "plugins"))
    spec = importlib.util.spec_from_file_location(
        "fakeai_memory_under_test",
        ROOT / "plugins" / "FakeAi" / "memory.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest_asyncio.fixture
async def vision_db(tmp_path, monkeypatch, fakeai_memory):
    """使用临时 SQLite 的 MemoryManager，只验证 vision_cache 相关方法。"""
    monkeypatch.setenv("FAKEAI_MEMORY_DB", str(tmp_path / "vision.db"))
    mgr = fakeai_memory.MemoryManager()

    async def _no_migrations():
        return None

    mgr._run_migrations = _no_migrations
    await mgr.init_db()
    yield mgr
    await mgr.close()
    mgr._database_path = None


class TestVisionCacheKey:
    def test_qq_file_is_stable(self, fakeai_memory):
        """VC-01: QQ file 字段作为图片识别缓存键。"""
        key = fakeai_memory.vision_cache_key("E2B74F40A66AB49C36D6E22A9D729FFE.jpg")
        assert key == "E2B74F40A66AB49C36D6E22A9D729FFE.jpg"

    def test_local_path_uses_basename(self, fakeai_memory):
        """VC-02: 本地路径只保留文件名，避免目录差异造成重复缓存。"""
        assert fakeai_memory.vision_cache_key("file:///tmp/abc.gif") == "abc.gif"
        assert fakeai_memory.vision_cache_key("C:/tmp/abc.gif") == "abc.gif"

    def test_empty_or_url_file_returns_empty(self, fakeai_memory):
        """VC-03: 没有稳定 file 时不启用缓存，避免过期 URL 误命中。"""
        assert fakeai_memory.vision_cache_key("") == ""
        assert fakeai_memory.vision_cache_key("https://example.com/a.jpg?rkey=1") == ""


class TestVisionCacheDb:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_save_and_get(self, vision_db, fakeai_memory):
        """VC-04: 保存后可命中同一图片，且不丢失描述。"""
        key = fakeai_memory.vision_cache_key("abc.jpg")
        assert await vision_db.get_vision_cache(key) is None

        await vision_db.save_vision_cache(
            key,
            {"content": "crying cat", "model": "vision"},
            prompt_key="brief",
        )
        hit = await vision_db.get_vision_cache(key)
        assert hit is not None
        assert hit["content"] == "crying cat"
        assert hit["cached"] is True

    @pytest.mark.asyncio(loop_scope="function")
    async def test_prompt_key_is_isolated(self, vision_db, fakeai_memory):
        """VC-05: 聊天简述与手动详细测试使用不同缓存场景。"""
        key = fakeai_memory.vision_cache_key("abc.jpg")
        await vision_db.save_vision_cache(
            key,
            {"content": "brief"},
            prompt_key="brief",
        )
        await vision_db.save_vision_cache(
            key,
            {"content": "detail"},
            prompt_key="detail",
        )
        assert (await vision_db.get_vision_cache(key, "brief"))["content"] == "brief"
        assert (await vision_db.get_vision_cache(key, "detail"))["content"] == "detail"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_cleanup_removes_old_entries(self, vision_db, fakeai_memory):
        """VC-06: 超过 3 天未再出现的记录会被清理。"""
        key = fakeai_memory.vision_cache_key("abc.jpg")
        await vision_db.save_vision_cache(
            key,
            {"content": "old"},
            prompt_key="brief",
        )
        old_ts = int(time.time()) - 4 * 86400
        await vision_db._db.execute(
            "UPDATE vision_cache SET last_seen_at=? WHERE image_key=?",
            (old_ts, key),
        )
        await vision_db._db.commit()

        assert await vision_db.cleanup_vision_cache(3) == 1
        assert await vision_db.get_vision_cache(key) is None
