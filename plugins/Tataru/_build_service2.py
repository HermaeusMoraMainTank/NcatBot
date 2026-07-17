"""Build clean service.py from plugin_body methods with reliable transforms."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
body = (ROOT / "legacy" / "plugin_body.py").read_text(encoding="utf-8")

# Take methods: from render_text_image through end of create_nuannuan_result (exclude terminate)
start = body.find("    def render_text_image")
term = body.find("    async def terminate")
chunk = body[start:term]

chunk = re.sub(r"\n    @filter\.command\([^\n]+\)", "", chunk)
chunk = re.sub(r"\n    @debug_command\([^\n]+\)", "", chunk)
chunk = chunk.replace("AstrMessageEvent", "SimpleEvent")

# Convert Comp.*
chunk = chunk.replace("Comp.Image.fromURL(", "ReplyPart.image_url(")
chunk = chunk.replace("Comp.Image.fromFileSystem(", "ReplyPart.image(")
chunk = chunk.replace("Comp.Plain(", "ReplyPart.text(")

# Convert yield forms to parts collection model
# Strategy: wrap each former-generator method body with parts list

# First, rewrite yield statements
chunk = re.sub(
    r"yield event\.plain_result\((.+?)\)\s*$",
    r"parts.append(ReplyPart.text(\1))",
    chunk,
    flags=re.M,
)
chunk = re.sub(
    r"yield event\.image_result\((.+?)\)\s*$",
    r"parts.append(ReplyPart.image(\1))",
    chunk,
    flags=re.M,
)
chunk = re.sub(
    r"yield event\.chain_result\((.+?)\)\s*$",
    r"parts.extend(\1 if isinstance(\1, list) else [\1])",
    chunk,
    flags=re.M,
)
# Fix broken extends for chain - the regex with \1 twice won't work for isinstance
# redo chain_result simpler:
chunk = re.sub(
    r"parts\.extend\((.+?) if isinstance\(.+?",
    "BROKEN",
    chunk,
)

# Start over for chain - read current and fix properly
chunk = (ROOT / "legacy" / "plugin_body.py").read_text(encoding="utf-8")[start:term]
chunk = re.sub(r"\n    @filter\.command\([^\n]+\)", "", chunk)
chunk = re.sub(r"\n    @debug_command\([^\n]+\)", "", chunk)
chunk = chunk.replace("AstrMessageEvent", "SimpleEvent")
chunk = chunk.replace("Comp.Image.fromURL(", "ReplyPart.image_url(")
chunk = chunk.replace("Comp.Image.fromFileSystem(", "ReplyPart.image(")
chunk = chunk.replace("Comp.Plain(", "ReplyPart.text(")

def repl_plain(m):
    return f"parts.append(ReplyPart.text({m.group(1)}))"

def repl_image(m):
    return f"parts.append(ReplyPart.image({m.group(1)}))"

def repl_chain(m):
    return f"parts.extend({m.group(1)})"

chunk = re.sub(r"yield event\.plain_result\((.+)\)\s*$", repl_plain, chunk, flags=re.M)
chunk = re.sub(r"yield event\.image_result\((.+)\)\s*$", repl_image, chunk, flags=re.M)
chunk = re.sub(r"yield event\.chain_result\((.+)\)\s*$", repl_chain, chunk, flags=re.M)

# yield result / yield item
chunk = re.sub(
    r"^\s*yield result\s*$",
    "        parts.extend(result if isinstance(result, list) else [result])",
    chunk,
    flags=re.M,
)
chunk = re.sub(
    r"^\s*async for result in self\.create_house_result\(event, \"房子\"\):\s*\n\s*yield result\s*$",
    '        return await self.create_house_result(event, "房子")',
    chunk,
    flags=re.M,
)
chunk = re.sub(
    r"^\s*async for result in self\.create_house_result\(event, \"房屋\"\):\s*\n\s*yield result\s*$",
    '        return await self.create_house_result(event, "房屋")',
    chunk,
    flags=re.M,
)
chunk = re.sub(
    r"^\s*result = self\.create_tarot_result\(event\)\s*\n\s*async for item in result:\s*\n\s*yield item\s*$",
    "        return await self.create_tarot_result(event)",
    chunk,
    flags=re.M,
)

# nuannuan: result = await create...; parts.extend
chunk = chunk.replace(
    """    async def nuannuan(self, event: SimpleEvent):
        \"\"\"本周时尚品鉴作业。\"\"\"
        result = await self.create_nuannuan_result(event)
        parts.extend(result if isinstance(result, list) else [result])
""",
    """    async def nuannuan(self, event: SimpleEvent):
        \"\"\"本周时尚品鉴作业。\"\"\"
        return await self.create_nuannuan_result(event)
""",
)

# Fix create_nuannuan_result returns
chunk = chunk.replace(
    "return event.image_result(str(image_path))",
    "return [ReplyPart.image(str(image_path))]",
)
chunk = re.sub(
    r"return event\.plain_result\((.+)\)\s*$",
    r"return [ReplyPart.text(\1)]",
    chunk,
    flags=re.M,
)

# Inject parts = [] and return parts into async methods that use parts.append/extend
METHOD_NAMES = [
    "help", "precious", "lottery", "calendar", "dungeon_note",
    "risingstones_posts", "party_finder", "ff_weibo", "item", "market",
    "create_house_result", "logs_dps", "character_logs", "create_tarot_result",
]

lines = chunk.splitlines(keepends=True)
out = []
i = 0
while i < len(lines):
    line = lines[i]
    m = re.match(r"    async def (\w+)\(", line)
    if m and m.group(1) in METHOD_NAMES:
        name = m.group(1)
        out.append(line)
        i += 1
        # copy signature continuation and docstring
        while i < len(lines) and (lines[i].startswith("        ") or lines[i].strip() == ""):
            if lines[i].strip().startswith('"""') or lines[i].strip().startswith("'''"):
                out.append(lines[i])
                i += 1
                # consume docstring
                if not (lines[i-1].strip().count('"""') >= 2 or lines[i-1].strip().count("'''") >= 2):
                    while i < len(lines):
                        out.append(lines[i])
                        if '"""' in lines[i] or "'''" in lines[i]:
                            i += 1
                            break
                        i += 1
                break
            if lines[i].strip().startswith("parts:") or lines[i].startswith("        " + ("async " if False else "")):
                # if we hit body without docstring
                if not lines[i].strip().startswith('"""'):
                    break
            # signature multi-line
            if lines[i].strip().endswith("):") or lines[i].rstrip().endswith(":"):
                out.append(lines[i])
                i += 1
                # docstring after
                if i < len(lines) and ('"""' in lines[i] or "'''" in lines[i]):
                    out.append(lines[i])
                    i += 1
                    if lines[i-1].count('"""') < 2 and lines[i-1].count("'''") < 2:
                        while i < len(lines):
                            out.append(lines[i])
                            if '"""' in lines[i] or "'''" in lines[i]:
                                i += 1
                                break
                            i += 1
                break
            out.append(lines[i])
            i += 1
        # inject parts
        out.append("        parts: list[ReplyPart] = []\n")
        # copy rest of method until next def at indent 4
        while i < len(lines):
            if re.match(r"    (async )?def ", lines[i]):
                break
            # convert bare return to return parts
            if re.match(r"        return\s*$", lines[i]):
                out.append("        return parts\n")
                i += 1
                continue
            out.append(lines[i])
            i += 1
        # ensure ends with return parts
        # look back
        j = len(out) - 1
        while j >= 0 and out[j].strip() == "":
            j -= 1
        if j >= 0 and "return parts" not in out[j] and not out[j].strip().startswith("return ["):
            out.append("        return parts\n")
        out.append("\n")
        continue
    out.append(line)
    i += 1

chunk = "".join(out)

# Fix house / tarot wrappers that shouldn't have parts injection incorrectly
# Fix create_house_result: already has parts for listing - but also uses parts.extend for components which are ReplyParts - good
# components list already has ReplyPart.image from Comp replacement - parts.extend(components) works

# Fix async for leftovers if any
if "async for" in chunk or "yield " in chunk:
    print("WARN leftovers async for/yield:")
    for n, L in enumerate(chunk.splitlines(), 1):
        if "async for" in L or L.strip().startswith("yield "):
            print(n, L)

HEADER = r'''"""Tataru service — command logic ported from astrbot_plugin_tataru."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .engine import (
    ADMIN_DB_PATH,
    CALENDAR_SOURCES,
    PARTY_CATEGORY_LABELS,
    PARTY_FINDER_CARDS_PER_IMAGE,
    PLUGIN_DIR,
    PLUGIN_VERSION,
    QQ_DOC_URL,
    RISINGSTONES_DB_PATH,
    RISINGSTONES_TIMEZONE,
    Calendar,
    PluginAdminStore,
    RisingstonesAccountStore,
    RisingstonesGlamourResponse,
    SimpleEvent,
    aiohttp_get,
    choose_tarot,
    command_args,
    configure_network_settings,
    configured_risingstones_credentials,
    create_character_logs_text,
    create_help_text,
    create_house_text,
    create_item_info,
    create_logs_text,
    create_market_text,
    debug_log,
    feature_enabled,
    fetch_risingstones_posts,
    fetch_risingstones_recruits,
    format_calendar_item,
    format_risingstones_glamour_message,
    format_risingstones_guilds,
    format_risingstones_notifications,
    format_risingstones_posts,
    format_risingstones_profile,
    format_risingstones_recruits,
    format_risingstones_statistics,
    get_bili_detail,
    get_bili_url,
    get_current_period,
    get_dungeon_note,
    get_ff_weibo_text,
    get_party_finder_entries,
    is_risingstones_private_event,
    load_tarot,
    logger,
    normalize_calendar_date,
    normalize_calendar_server,
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
    render_party_finder_cards,
    resolve_party_duty_ids,
    resolve_party_world,
    risingstones_account_key,
    risingstones_account_request,
    risingstones_binding_guide,
    risingstones_checkin,
    risingstones_feature_for_query,
    risingstones_glamour_rows,
    risingstones_guild_rows,
    risingstones_statistics,
    risingstones_verify_credential,
    text_to_image,
)


@dataclass
class ReplyPart:
    kind: str
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


class TataruService:
    def __init__(self, config: dict | None = None):
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

    async def initialize(self):
        debug_log("plugin.initialize", version=PLUGIN_VERSION)
        self.tarot_dict = load_tarot()
        self.cache_dir.mkdir(exist_ok=True)
        self.risingstones_accounts.initialize()
        self.admin_store.initialize()
        saved_owner_curl = self.admin_store.get_setting("risingstones_owner_curl")
        if saved_owner_curl and not self.config.get("risingstones_owner_curl"):
            self.config["risingstones_owner_curl"] = saved_owner_curl
        elif self.config.get("risingstones_owner_curl"):
            self.admin_store.set_setting(
                "risingstones_owner_curl",
                str(self.config["risingstones_owner_curl"]).strip(),
            )
        self.calendar_task = asyncio.create_task(self.download_calendar_loop())
        self.risingstones_checkin_task = asyncio.create_task(
            self.risingstones_checkin_loop()
        )
        logger.info("Tataru service initialized.")

    async def terminate(self):
        if self.calendar_task:
            self.calendar_task.cancel()
        if self.risingstones_checkin_task:
            self.risingstones_checkin_task.cancel()
        debug_log("plugin.terminate")
        logger.info("Tataru service terminated.")

    def default_calendar_server(self) -> str:
        return (
            "国际服" if bool(self.config.get("use_global_calendar", False)) else "国服"
        )

    def weibo_cookie(self) -> str:
        return str(self.config.get("weibo_cookie", "") or "").strip()

    def fflogs_client_id(self) -> str:
        return str(self.config.get("fflogs_client_id", "") or "").strip()

    def fflogs_client_secret(self) -> str:
        return str(self.config.get("fflogs_client_secret", "") or "").strip()

    def default_logs_cn_source(self) -> bool:
        return not bool(self.config.get("use_global_fflogs", False))

    def configured_font_path(self) -> str:
        return str(self.config.get("font_path", "") or "").strip()

    def ffxiv_icon_font_path(self) -> str:
        return str(self.config.get("ffxiv_icon_font_path", "") or "").strip()

    def risingstones_owner_credentials(self):
        return configured_risingstones_credentials(self.config)

    def risingstones_checkin_hour(self) -> int:
        try:
            hour = int(self.config.get("risingstones_checkin_hour", 8))
        except (TypeError, ValueError):
            hour = 8
        return hour if 0 <= hour <= 23 else 8

'''

# Fix house methods if still broken
chunk2 = chunk
# If house still has async for, replace manually
chunk2 = re.sub(
    r"    async def house\(self, event: SimpleEvent\):[\s\S]*?(?=\n    async def house_alias)",
    '''    async def house(self, event: SimpleEvent):
        """查询指定服务器空房。"""
        return await self.create_house_result(event, "房子")

''',
    chunk2,
    count=1,
)
chunk2 = re.sub(
    r"    async def house_alias\(self, event: SimpleEvent\):[\s\S]*?(?=\n    async def create_house_result)",
    '''    async def house_alias(self, event: SimpleEvent):
        """查询指定服务器空房。"""
        return await self.create_house_result(event, "房屋")

''',
    chunk2,
    count=1,
)
chunk2 = re.sub(
    r"    async def tarot\(self, event: SimpleEvent\):[\s\S]*?(?=\n    async def create_tarot_result)",
    '''    async def tarot(self, event: SimpleEvent):
        """随机抽取一张FF14塔罗牌。"""
        return await self.create_tarot_result(event)

''',
    chunk2,
    count=1,
)
chunk2 = re.sub(
    r"    async def nuannuan\(self, event: SimpleEvent\):[\s\S]*?(?=\n    async def dungeon_note)",
    '''    async def nuannuan(self, event: SimpleEvent):
        """本周时尚品鉴作业。"""
        return await self.create_nuannuan_result(event)

''',
    chunk2,
    count=1,
)

# create_tarot_result should be async def (already is)
# Fix help to append 玩家 line
chunk2 = chunk2.replace(
    "parts.append(ReplyPart.text(create_help_text()))",
    'parts.append(ReplyPart.text(create_help_text() + "[石之家 玩家 角色名 (服务器)] 查询角色信息卡（现有绘图）\\n"))',
)

text = HEADER + chunk2
(ROOT / "service.py").write_text(text, encoding="utf-8")

import ast
try:
    ast.parse(text)
    print("SYNTAX OK", len(text.splitlines()), "lines")
except SyntaxError as e:
    print("SYNTAX ERROR", e)
    lines = text.splitlines()
    for n in range(max(1, e.lineno - 3), min(len(lines), e.lineno + 3) + 1):
        print(f"{n}: {lines[n-1]}")

print("yield left", "yield " in text)
print("Comp left", "Comp." in text)
print("async for left", "async for" in text)
