"""One-shot builder: convert astrbot tataru_main.py -> engine.py + service helpers."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
src = (ROOT / "legacy" / "tataru_main.py").read_text(encoding="utf-8")

marker = '@register(\n    "astrbot_plugin_tataru",'
idx = src.find(marker)
if idx < 0:
    idx = src.find("class TataruPlugin(Star):")
assert idx > 0, "TataruPlugin not found"
core_src = src[:idx]
plugin_body = src[idx:]

m = re.search(r"^PLUGIN_DIR = Path\(__file__\)\.resolve\(\)\.parent", core_src, re.M)
assert m, "PLUGIN_DIR not found"
rest = core_src[m.start() :]
rest = rest.replace("AstrMessageEvent", "EventLike")

rest = re.sub(
    r"def debug_command\(command_name: str\):.*?return decorator\n",
    '''def debug_command(command_name: str):
    """No-op decorator in NcatBot port."""

    def decorator(func):
        return func

    return decorator

''',
    rest,
    count=1,
    flags=re.S,
)

engine = '''"""Tataru core engine — ported from astrbot_plugin_tataru (MIT)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from functools import wraps
import html
import ipaddress
import itertools
import json
import logging
import random
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import aiohttp
from curl_cffi import requests as curl_requests
from icalendar import Calendar
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("Tataru")


class EventLike(Protocol):
    def get_platform_id(self) -> str: ...
    def get_sender_id(self) -> str: ...
    def is_private_chat(self) -> bool: ...


@dataclass
class SimpleEvent:
    platform_id: str
    sender_id: str
    private: bool
    message_str: str = ""

    def get_platform_id(self) -> str:
        return self.platform_id

    def get_sender_id(self) -> str:
        return self.sender_id

    def is_private_chat(self) -> bool:
        return self.private


def page_json_response(data, *, status_code: int = 200):
    return data


def page_error_response(message: str, *, status_code: int = 400):
    return {"status": "error", "message": message}


async def get_page_request_json() -> object:
    return {}

''' + rest

(ROOT / "engine.py").write_text(engine, encoding="utf-8")
print("engine.py lines:", len(engine.splitlines()))
print("astrbot left:", "astrbot" in engine)
print("Comp left:", "Comp." in engine)

# Save raw plugin methods section for reference
(ROOT / "legacy" / "plugin_body.py").write_text(plugin_body, encoding="utf-8")
print("plugin_body saved, lines:", len(plugin_body.splitlines()))
