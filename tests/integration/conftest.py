"""
集成测试公共 fixtures
"""

import pytest_asyncio

from ncatbot.testing import TestHarness


@pytest_asyncio.fixture
async def harness():
    h = TestHarness()
    async with h:
        yield h
