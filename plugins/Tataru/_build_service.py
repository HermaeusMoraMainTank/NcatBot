"""Convert AstrBot TataruPlugin body into NcatBot-friendly service.py."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
raw = (ROOT / "legacy" / "plugin_body.py").read_text(encoding="utf-8")

# Drop @register decorator and class header
raw = re.sub(r"^@register\([\s\S]*?\)\n", "", raw)
raw = raw.replace("class TataruPlugin(Star):", "class TataruService:")
raw = raw.replace("AstrMessageEvent", "SimpleEvent")
raw = raw.replace("Context", "object")

# Remove admin registration call and all admin methods (from _register_admin_web_apis through admin_activity)
# Keep render_text_image and onwards.
start_admin = raw.find("    def _register_admin_web_apis")
start_render = raw.find("    def render_text_image")
assert start_admin > 0 and start_render > start_admin
raw = raw[:start_admin] + raw[start_render:]

# Fix __init__
raw = raw.replace(
    """    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}
        configure_network_settings(self.config)
        self.started_at = datetime.now(RISINGSTONES_TIMEZONE)
        self.tarot_dict: dict | None = None
        self.cache_dir = PLUGIN_DIR / ".cache"
        self.calendar_task: asyncio.Task | None = None
        self.risingstones_checkin_task: asyncio.Task | None = None
        self.risingstones_accounts = RisingstonesAccountStore(RISINGSTONES_DB_PATH)
        self.admin_store = PluginAdminStore(ADMIN_DB_PATH)
        self.last_calendar_download_time: dict[str, datetime] = {}
        self._register_admin_web_apis(context)
""",
    """    def __init__(self, config: dict | None = None):
        self.config = config or {}
        configure_network_settings(self.config)
        self.started_at = datetime.now(RISINGSTONES_TIMEZONE)
        self.tarot_dict: dict | None = None
        self.cache_dir = PLUGIN_DIR / ".cache"
        self.calendar_task: asyncio.Task | None = None
        self.risingstones_checkin_task: asyncio.Task | None = None
        self.risingstones_accounts = RisingstonesAccountStore(RISINGSTONES_DB_PATH)
        self.admin_store = PluginAdminStore(ADMIN_DB_PATH)
        self.last_calendar_download_time: dict[str, datetime] = {}
""",
)

# The above replace may fail due to Context already replaced - try alternate
if "super().__init__" in raw:
    raw = raw.replace(
        """    def __init__(self, context: object, config=None):
        super().__init__(context)
        self.config = config or {}
        configure_network_settings(self.config)
        self.started_at = datetime.now(RISINGSTONES_TIMEZONE)
        self.tarot_dict: dict | None = None
        self.cache_dir = PLUGIN_DIR / ".cache"
        self.calendar_task: asyncio.Task | None = None
        self.risingstones_checkin_task: asyncio.Task | None = None
        self.risingstones_accounts = RisingstonesAccountStore(RISINGSTONES_DB_PATH)
        self.admin_store = PluginAdminStore(ADMIN_DB_PATH)
        self.last_calendar_download_time: dict[str, datetime] = {}
        self._register_admin_web_apis(context)
""",
        """    def __init__(self, config: dict | None = None):
        self.config = config or {}
        configure_network_settings(self.config)
        self.started_at = datetime.now(RISINGSTONES_TIMEZONE)
        self.tarot_dict: dict | None = None
        self.cache_dir = PLUGIN_DIR / ".cache"
        self.calendar_task: asyncio.Task | None = None
        self.risingstones_checkin_task: asyncio.Task | None = None
        self.risingstones_accounts = RisingstonesAccountStore(RISINGSTONES_DB_PATH)
        self.admin_store = PluginAdminStore(ADMIN_DB_PATH)
        self.last_calendar_download_time: dict[str, datetime] = {}
""",
    )

raw = raw.replace(
    'logger.info("Tataru AstrBot plugin initialized.")',
    'logger.info("Tataru service initialized.")',
)
raw = raw.replace(
    'logger.info("Tataru AstrBot plugin terminated.")',
    'logger.info("Tataru service terminated.")',
)

# Convert event.message_str usage - SimpleEvent has message_str field; handlers take event
# Convert yields: transform async generator command handlers into list collectors


def convert_async_gen_method(text: str) -> str:
    """Convert methods that yield event.*_result into methods returning list[ReplyPart]."""
    # Replace yield event.plain_result(X) with parts.append(ReplyPart.text(X))
    text = re.sub(
        r"yield event\.plain_result\((.+?)\)",
        r"parts.append(ReplyPart.text(\1))",
        text,
    )
    text = re.sub(
        r"yield event\.image_result\((.+?)\)",
        r"parts.append(ReplyPart.image(\1))",
        text,
    )
    text = re.sub(
        r"yield event\.chain_result\((.+?)\)",
        r"parts.extend(_chain_to_parts(\1))",
        text,
    )
    text = re.sub(
        r"yield result\b",
        r"parts.extend(_coerce_result(result))",
        text,
    )
    text = re.sub(
        r"yield item\b",
        r"parts.extend(_coerce_result(item))",
        text,
    )
    return text


raw = convert_async_gen_method(raw)

# For methods that used to be async generators, inject `parts: list[ReplyPart] = []` at start
# and `return parts` before bare returns that were just `return` after yields.
# Identify command-like async methods by checking if they contain parts.append

HEADER = '''"""Tataru service layer — command logic without AstrBot / NcatBot coupling."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .engine import (
    ADMIN_DB_PATH,
    CALENDAR_SOURCES,
    PLUGIN_DIR,
    PLUGIN_VERSION,
    PARTY_CATEGORY_LABELS,
    PARTY_FINDER_CARDS_PER_IMAGE,
    QQ_DOC_URL,
    RISINGSTONES_DB_PATH,
    RISINGSTONES_TIMEZONE,
    RisingstonesAccountStore,
    RisingstonesCredentials,
    RisingstonesGlamourResponse,
    PluginAdminStore,
    SimpleEvent,
    Calendar,
    command_args,
    configure_network_settings,
    create_help_text,
    create_character_logs_text,
    create_item_info,
    create_logs_text,
    create_market_text,
    create_house_text,
    choose_tarot,
    debug_log,
    feature_enabled,
    fetch_risingstones_posts,
    fetch_risingstones_recruits,
    format_risingstones_posts,
    format_risingstones_recruits,
    format_risingstones_glamour_message,
    format_risingstones_guilds,
    format_risingstones_notifications,
    format_risingstones_profile,
    format_risingstones_statistics,
    format_calendar_item,
    get_bili_detail,
    get_bili_url,
    get_current_period,
    get_dungeon_note,
    get_ff_weibo_text,
    get_party_finder_entries,
    load_tarot,
    logger,
    normalize_calendar_date,
    normalize_calendar_server,
    parse_dungeon_query,
    parse_character_logs_query,
    parse_house_query,
    parse_logs_query,
    parse_market_query,
    parse_party_finder_query,
    parse_risingstones_binding,
    parse_risingstones_glamour_query,
    parse_risingstones_guild_query,
    parse_risingstones_posts_query,
    parse_risingstones_recruit_query,
    parse_risingstones_stat_kind,
    random_left_right,
    random_lottery,
    risingstones_account_key,
    risingstones_account_request,
    risingstones_binding_guide,
    risingstones_checkin,
    risingstones_feature_for_query,
    risingstones_glamour_rows,
    risingstones_guild_rows,
    risingstones_statistics,
    risingstones_verify_credential,
    resolve_party_duty_ids,
    resolve_party_world,
    render_party_finder_cards,
    text_to_image,
    aiohttp_get,
    configured_risingstones_credentials,
    is_risingstones_private_event,
)


@dataclass
class ReplyPart:
    kind: str  # text | image | image_url
    value: str

    @classmethod
    def text(cls, value: str) -> "ReplyPart":
        return cls("text", value)

    @classmethod
    def image(cls, value: str) -> "ReplyPart":
        return cls("image", str(value))

    @classmethod
    def image_url(cls, value: str) -> "ReplyPart":
        return cls("image_url", value)


def _chain_to_parts(components) -> list[ReplyPart]:
    """Best-effort convert AstrBot Comp list leftovers / mixed objects."""
    parts: list[ReplyPart] = []
    for item in components or []:
        if isinstance(item, ReplyPart):
            parts.append(item)
            continue
        # Already converted Comp.Image may appear as duck-typed objects
        file_path = getattr(item, "file", None) or getattr(item, "path", None)
        url = getattr(item, "url", None)
        text = getattr(item, "text", None)
        if file_path:
            parts.append(ReplyPart.image(str(file_path)))
        elif url:
            parts.append(ReplyPart.image_url(str(url)))
        elif text is not None:
            parts.append(ReplyPart.text(str(text)))
        elif isinstance(item, str):
            parts.append(ReplyPart.text(item))
        elif isinstance(item, Path):
            parts.append(ReplyPart.image(str(item)))
    return parts


def _coerce_result(result) -> list[ReplyPart]:
    if result is None:
        return []
    if isinstance(result, ReplyPart):
        return [result]
    if isinstance(result, list):
        return _chain_to_parts(result)
    # AstrBot MessageEvent result duck typing
    if hasattr(result, "chain") or hasattr(result, "__iter__"):
        try:
            return _chain_to_parts(list(result))
        except TypeError:
            pass
    return [ReplyPart.text(str(result))]

'''

# Fix create_nuannuan_result / create_tarot_result / create_house_result which return event.*_result
raw = raw.replace(
    "return event.image_result(str(image_path))",
    "return [ReplyPart.image(str(image_path))]",
)
raw = raw.replace(
    "return event.plain_result(",
    "return [ReplyPart.text(",
)
# Fix mismatched parens from above - plain_result(...) became [ReplyPart.text(...  need extra ]
# e.g. return [ReplyPart.text("msg")  -> return [ReplyPart.text("msg")]
# Only fix lines that were return [ReplyPart.text( ... ) without closing ]
raw = re.sub(
    r"return \[ReplyPart\.text\((.+?)\)\s*$",
    r"return [ReplyPart.text(\1)]",
    raw,
    flags=re.M,
)

# Fix Comp.Image / Comp.Plain that still appear in risingstones / item / party_finder / tarot
raw = raw.replace("Comp.Image.fromURL(", "ReplyPart.image_url(")
raw = raw.replace("Comp.Image.fromFileSystem(", "ReplyPart.image(")
raw = raw.replace("Comp.Plain(", "ReplyPart.text(")

# Methods that collect parts need initialization. Inject after docstring or first line of each async cmd.
COMMAND_METHODS = [
    "help",
    "precious",
    "lottery",
    "calendar",
    "nuannuan",
    "dungeon_note",
    "risingstones_posts",
    "party_finder",
    "ff_weibo",
    "item",
    "market",
    "house",
    "house_alias",
    "create_house_result",
    "logs_dps",
    "character_logs",
    "tarot",
    "create_tarot_result",
]

for name in COMMAND_METHODS:
    pattern = rf"(    async def {name}\([\s\S]*?\n)(        \"\"\"[\s\S]*?\"\"\"\n)?"

    def inject(m, _name=name):
        head = m.group(1)
        doc = m.group(2) or ""
        if "parts: list[ReplyPart] = []" in head + doc:
            return m.group(0)
        return head + doc + "        parts: list[ReplyPart] = []\n"

    raw2 = re.sub(pattern, inject, raw, count=1)
    if raw2 == raw:
        print("WARN: inject failed for", name)
    raw = raw2

# Ensure command methods return parts at the end when they fall through
# Replace bare `return` after parts.append with `return parts`
# and add `return parts` at end of methods that don't return.

# Simpler heuristic: after each method that uses parts.append, replace trailing structure.
# Change early `return` that means stop (after append) to `return parts`
lines = raw.splitlines(True)
out_lines = []
in_parts_method = False
indent_stack_name = None
for i, line in enumerate(lines):
    if re.match(r"    async def (\w+)\(", line):
        name = re.match(r"    async def (\w+)\(", line).group(1)
        in_parts_method = name in COMMAND_METHODS
    elif re.match(r"    def |\n    async def ", line) or (
        line.startswith("    def ") or line.startswith("    async def ")
    ):
        if re.match(r"    (async )?def ", line):
            name_m = re.match(r"    (?:async )?def (\w+)\(", line)
            if name_m:
                in_parts_method = name_m.group(1) in COMMAND_METHODS

    if in_parts_method and re.match(r"        return\s*$", line):
        out_lines.append("        return parts\n")
        continue
    out_lines.append(line)

raw = "".join(out_lines)

# Add return parts at end of methods that fall off - hard; fix common cases:
# Methods that end with parts.append without return - append return parts before next method
# Do a second pass: if a COMMAND method body ends without return parts, add it.


def ensure_return_parts(text: str) -> str:
    for name in COMMAND_METHODS:
        # Find method and next method at same indent
        m = re.search(
            rf"(    async def {name}\([\s\S]*?)(?=\n    (?:async )?def |\Z)", text
        )
        if not m:
            continue
        body = m.group(1)
        if "parts: list[ReplyPart] = []" not in body:
            continue
        if (
            re.search(r"\n        return parts\s*\n\s*$", body)
            or "return parts" in body[-80:]
        ):
            continue
        # If body has return [ReplyPart already somewhere as sole returns ok
        if "return [ReplyPart" in body and "parts.append" not in body:
            continue
        if "parts.append" in body or "parts.extend" in body:
            new_body = body.rstrip() + "\n        return parts\n\n"
            text = text[: m.start()] + new_body + text[m.end() :]
    return text


raw = ensure_return_parts(raw)

# Fix risingstones glamour Comp conversion leftovers: components.append(ReplyPart...) then chain_result
# Already handled via parts.extend(_chain_to_parts(components))

# Fix create_house_result that calls yield via result
# Fix nested `return [ReplyPart.text(...)]` with missing bracket - already regexed

# Fix event.message_str -> event.message_str (SimpleEvent has it) OK
# command_args(event.message_str, ...) OK

# feature gate helper that used to be in debug_command:
# Handlers don't check feature anymore with no-op decorator - add optional checks in NcatBot layer later.

# _persist_plugin_config may still be referenced - stub it
# Remove _persist if admin methods gone - check
if "_persist_plugin_config" in raw:
    # add stub method at end of class if missing
    if "def _persist_plugin_config" not in raw:
        raw = (
            raw.rstrip()
            + "\n\n    def _persist_plugin_config(self) -> None:\n        return\n"
        )

# Fix image_components.append(ReplyPart.image(...)) then parts.extend(_chain_to_parts(image_components))
# Good.

service = HEADER + "\n" + raw
# Duplicate dataclass import from engine Calendar - engine imports Calendar from icalendar, we re-export in from .engine import Calendar - need to export Calendar from engine (it's imported there so ok if we add to import - Calendar is not in engine.__all__ but name exists in engine module)

(ROOT / "service.py").write_text(service, encoding="utf-8")
print("service.py lines:", len(service.splitlines()))
print("Comp left:", "Comp." in service)
print("yield left:", "yield " in service)
print("Star left:", "Star" in service)
print("Astr left:", "Astr" in service)
