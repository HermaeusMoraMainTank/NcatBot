"""异步 I/O 辅助：避免在事件循环中执行阻塞读写与 HTTP。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Mapping, Optional

import aiohttp
import yaml


async def load_json(path: str | Path, *, encoding: str = "utf-8") -> Any:
    def _read() -> Any:
        with open(path, encoding=encoding) as f:
            return json.load(f)

    return await asyncio.to_thread(_read)


async def load_yaml(path: str | Path, *, encoding: str = "utf-8") -> Any:
    def _read() -> Any:
        with open(path, encoding=encoding) as f:
            return yaml.safe_load(f)

    return await asyncio.to_thread(_read)


async def http_get_bytes(
    url: str,
    *,
    headers: Optional[Mapping[str, str]] = None,
    timeout: float = 30,
    verify_ssl: bool = True,
) -> tuple[int, bytes]:
    connector = aiohttp.TCPConnector(ssl=verify_ssl)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.get(
            url,
            headers=dict(headers or {}),
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            return resp.status, await resp.read()


async def http_post_bytes(
    url: str,
    *,
    headers: Optional[Mapping[str, str]] = None,
    data: Any = None,
    json_data: Any = None,
    timeout: float = 30,
    verify_ssl: bool = True,
) -> tuple[int, bytes]:
    connector = aiohttp.TCPConnector(ssl=verify_ssl)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.post(
            url,
            headers=dict(headers or {}),
            data=data,
            json=json_data,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            return resp.status, await resp.read()
