import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from functools import wraps
import html
import ipaddress
import itertools
import json
import random
import re
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import aiohttp
from curl_cffi import requests as curl_requests
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
import astrbot.api.message_components as Comp
from astrbot.api.star import Context, Star, register
from icalendar import Calendar
from PIL import Image, ImageDraw, ImageFont

try:
    from astrbot.api.web import error_response as page_error_response
    from astrbot.api.web import json_response as page_json_response
    from astrbot.api.web import request as page_request

    async def get_page_request_json() -> object:
        return await page_request.json(default={})

except ModuleNotFoundError:
    try:
        from quart import jsonify, request as page_request

        def page_json_response(data: object, *, status_code: int = 200):
            return jsonify(data), status_code

        def page_error_response(message: str, *, status_code: int = 400):
            return jsonify({"status": "error", "message": message}), status_code

        async def get_page_request_json() -> object:
            return await page_request.get_json(silent=True) or {}

    except ModuleNotFoundError:

        def page_json_response(data: object, *, status_code: int = 200):
            return data

        def page_error_response(message: str, *, status_code: int = 400):
            return {"status": "error", "message": message}

        async def get_page_request_json() -> object:
            return {}


PLUGIN_DIR = Path(__file__).resolve().parent
PLUGIN_NAME = "astrbot_plugin_tataru"
PLUGIN_AUTHOR = "jawwe"
PLUGIN_CONTACT = "lyjawwe@gmail.com"


def load_plugin_version() -> str:
    metadata_path = PLUGIN_DIR / "metadata.yaml"
    try:
        metadata_text = metadata_path.read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    match = re.search(r"^version:\s*v?(.+?)\s*$", metadata_text, flags=re.M)
    return match.group(1) if match else "unknown"


PLUGIN_VERSION = load_plugin_version()
PLUGIN_USER_AGENT = (
    f"{PLUGIN_NAME} {PLUGIN_VERSION} / {PLUGIN_AUTHOR} <{PLUGIN_CONTACT}>"
)
BROWSER_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
]


@dataclass(frozen=True)
class NetworkSettings:
    debug_mode: bool = False
    proxy_enabled: bool = False
    proxy_url: str | None = None
    proxy_auth: aiohttp.BasicAuth | None = None
    proxy_error: str | None = None


@dataclass(frozen=True)
class HttpResponse:
    status: int
    payload: object | None = None


class ProxyConfigurationError(RuntimeError):
    """Raised when proxy mode is enabled with incomplete settings."""


NETWORK_SETTINGS = NetworkSettings()
HTTP_REQUEST_COUNTER = itertools.count(1)
SENSITIVE_DEBUG_KEYWORDS = (
    "token",
    "cookie",
    "authorization",
    "secret",
    "password",
    "credential",
    "curl",
    "api_key",
    "apikey",
    "proxy_auth",
)


def is_sensitive_debug_key(key: str) -> bool:
    normalized_key = key.lower().replace("-", "_")
    return any(keyword in normalized_key for keyword in SENSITIVE_DEBUG_KEYWORDS)


def mask_debug_secret(value: object) -> str:
    text = str(value or "")
    if len(text) <= 4:
        return "*" * max(4, len(text))
    return text[:2] + "*" * (len(text) - 4) + text[-2:]


def mask_admin_display_secret(value: object) -> str:
    """Keep Page summaries readable without exposing full sensitive values."""
    text = str(value or "")
    if len(text) <= 6:
        return "*" * max(3, len(text))
    return text[:3] + "*" * min(10, max(3, len(text) - 6)) + text[-3:]


def sanitize_debug_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        if not parsed.query:
            return url
        query = urlencode(
            [
                (
                    key,
                    mask_debug_secret(value) if is_sensitive_debug_key(key) else value,
                )
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            ],
            doseq=True,
            safe="*",
        )
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment)
        )
    except Exception:
        return "<invalid-url>"


def sanitize_debug_value(value: object, key: str = "") -> object:
    if is_sensitive_debug_key(key):
        return mask_debug_secret(value)
    if key.lower() == "url" and isinstance(value, str):
        return sanitize_debug_url(value)
    if isinstance(value, dict):
        return {
            str(item_key): sanitize_debug_value(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [sanitize_debug_value(item) for item in value]
    return value


def debug_log(event: str, *, proxy_used: bool = False, **fields: object) -> None:
    if not NETWORK_SETTINGS.debug_mode:
        return
    prefix = "[Tataru Debug]"
    if proxy_used:
        prefix += " [Proxy]"
    details = " ".join(
        f"{key}={sanitize_debug_value(value, key)!r}" for key, value in fields.items()
    )
    logger.info(f"{prefix} event={event}" + (f" {details}" if details else ""))


def configure_network_settings(config: dict | None) -> None:
    global NETWORK_SETTINGS

    config = config or {}
    debug_mode = bool(config.get("debug_mode", False))
    proxy_enabled = bool(config.get("proxy_enabled", False))
    if not proxy_enabled:
        NETWORK_SETTINGS = NetworkSettings(debug_mode=debug_mode)
        debug_log("proxy.configuration", enabled=False)
        return

    host = str(config.get("proxy_host", "") or "").strip()
    username = str(config.get("proxy_username", "") or "").strip()
    password = str(config.get("proxy_password", "") or "")
    try:
        port = int(config.get("proxy_port", 0) or 0)
    except (TypeError, ValueError):
        port = 0

    error = None
    if not host:
        error = "代理已启用，但未填写代理地址"
    elif "://" in host or "/" in host:
        error = "代理地址仅填写 IP 或主机名，不要包含协议或路径"
    else:
        proxy_host = host.strip("[]")
        try:
            ipaddress.IPv6Address(proxy_host)
        except ValueError:
            if ":" in proxy_host:
                error = "代理地址仅填写 IP 或主机名，端口请填写在单独的端口字段中"
    if not error and not 1 <= port <= 65535:
        error = "代理端口必须在 1 到 65535 之间"
    elif not error and bool(username) != bool(password):
        error = "代理用户名和密码需要同时填写或同时留空"

    if error:
        NETWORK_SETTINGS = NetworkSettings(
            debug_mode=debug_mode,
            proxy_enabled=True,
            proxy_error=error,
        )
        logger.warning(f"[Proxy] {error}")
        debug_log(
            "proxy.configuration", proxy_used=True, enabled=True, status="invalid"
        )
        return

    proxy_host = host.strip("[]")
    if ":" in proxy_host:
        proxy_host = f"[{proxy_host}]"
    NETWORK_SETTINGS = NetworkSettings(
        debug_mode=debug_mode,
        proxy_enabled=True,
        proxy_url=f"http://{proxy_host}:{port}",
        proxy_auth=aiohttp.BasicAuth(username, password) if username else None,
    )
    debug_log(
        "proxy.configuration",
        proxy_used=True,
        enabled=True,
        host=host,
        port=port,
        authentication=bool(username),
    )


def proxy_request_options() -> dict:
    if not NETWORK_SETTINGS.proxy_enabled:
        return {}
    if NETWORK_SETTINGS.proxy_error:
        raise ProxyConfigurationError(NETWORK_SETTINGS.proxy_error)
    options = {"proxy": NETWORK_SETTINGS.proxy_url}
    if NETWORK_SETTINGS.proxy_auth:
        options["proxy_auth"] = NETWORK_SETTINGS.proxy_auth
    return options


def debug_command(command_name: str):
    """Log command lifecycle events when plugin debug mode is enabled."""

    def decorator(handler):
        @wraps(handler)
        async def wrapper(self, *args, **kwargs):
            feature = admin_feature_for_command(command_name)
            admin_store = getattr(self, "admin_store", None)
            if feature and admin_store and not feature_enabled(admin_store, feature):
                event = args[0] if args else kwargs.get("event")
                if event is not None:
                    yield event.plain_result("该功能已在塔塔露管理台中停用。")
                return
            debug_log("command.start", command=command_name)
            try:
                async for result in handler(self, *args, **kwargs):
                    yield result
            except Exception as exc:
                debug_log(
                    "command.error",
                    command=command_name,
                    error_type=type(exc).__name__,
                )
                raise
            finally:
                debug_log("command.finish", command=command_name)

        return wrapper

    return decorator


DATA_DIR = PLUGIN_DIR / "data"
TAROT_DIR = DATA_DIR / "TarotImages"
TAROT_JSON = TAROT_DIR / "ff14_tarot.json"
JOB_JSON = DATA_DIR / "job.json"
BOSS_JSON = DATA_DIR / "boss.json"
DEFAULT_FONT_PATHS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.otf",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/msyh.ttc",
]
FFXIV_ICON_FONT_START = 0xE020
FFXIV_ICON_FONT_END = 0xE0DB
PARTY_FINDER_CARDS_PER_IMAGE = 5
DEFAULT_CN_CALENDAR_PATH = DATA_DIR / "calendar.ics"
CALENDAR_SOURCES = {
    "国服": {
        "primary": (
            "https://calendar.google.com/calendar/ical/"
            "up88drvlnnh2t77hbpqq8v33i2cngfh7%40import.calendar.google.com/public/basic.ics"
        ),
        "fallback": (
            "https://p66-caldav.icloud.com/published/2/"
            "MTAyMTk3MTMxMjExMDIxOXsjasy7WUO0EcKVz7qGEuVjjTlRkgd6"
            "WOZM171uxP_u-QM51M24lHzRlAQir-oodDRRTzZeusSLbw0snkZoqI4"
        ),
        "bundled": DEFAULT_CN_CALENDAR_PATH,
    },
    "国际服": {
        "primary": (
            "https://calendar.google.com/calendar/ical/"
            "1gpnler51bgs1ajti10ao946ou367bf6%40import.calendar.google.com/public/basic.ics"
        ),
        "fallback": (
            "https://p66-caldav.icloud.com/published/2/"
            "MTAyMTk3MTMxMjExMDIxOXsjasy7WUO0EcKVz7qGEuVzSK8L9ZRQYf1sxUFeH1A1a22"
            "GJLf6nfk2-CZNYMv5iOxCNlUR-umbJKFWWAUVRp8"
        ),
        "bundled": None,
    },
}
QQ_DOC_URL = "https://docs.qq.com/sheet/DY2lCeEpwemZESm5q?tab=dewveu&c=A1A0A0"
BILI_USER_ID = 15503317
WEIBO_UID = "1797798792"
WEIBO_API_BASE = "https://m.weibo.cn/api/container/getIndex"
WEIBO_MOBILE_BASE = "https://m.weibo.cn"
WEIBO_WEB_BASE = "https://weibo.com"
WEIBO_WEB_TIMELINE_API = "https://weibo.com/ajax/statuses/mymblog"
FFLOGS_HOSTS = {False: "https://www.fflogs.com", True: "https://cn.fflogs.com"}
FFLOGS_PERCENTILES = [10, 25, 50, 75, 95, 99, 100]
FFLOGS_STAT_AGGREGATES = ["persecondamount", "amount", "persecond", "raw", "normalized"]
FFLOGS_METADATA_QUERY = """
query {
  worldData {
    zones {
      id
      name
      frozen
      difficulties { id name }
      partitions { id name compactName default }
      encounters { id name }
    }
  }
}
"""
FFLOGS_METADATA_CACHE: dict[str, dict] = {}
DUNGEON_NOTE_URL = "https://ff14.org/duty"
GARLAND_BASE_URL = "https://garlandtools.cn"
PARTY_FINDER_URL = "https://xivpf.littlenightmare.top/listings"
PARTY_FINDER_API_V1_URL = "https://xivpf.littlenightmare.top/api/listings"
PARTY_FINDER_API_V2_URL = "https://xivpf.littlenightmare.top/api/v2/listings"
XIVAPI_BASE_URL = "https://xivapi-v2.xivcdn.com/api"
HOUSE_API_URL = "https://house.ffxiv.cyou/api/sales"
RISINGSTONES_API_BASE = "https://apiff14risingstones.web.sdo.com/api"
RISINGSTONES_WEB_BASE = "https://ff14risingstones.web.sdo.com/pc/index.html"
RISINGSTONES_DEFAULT_LIMIT = 10
RISINGSTONES_MAX_LIMIT = 20
RISINGSTONES_DB_PATH = DATA_DIR / "risingstones.sqlite3"
ADMIN_DB_PATH = DATA_DIR / "admin.sqlite3"
RISINGSTONES_TIMEZONE = ZoneInfo("Asia/Shanghai")
RISINGSTONES_IMPERSONATE = "chrome124"
RISINGSTONES_BINDING_SEPARATOR = " | "
RISINGSTONES_CONSOLE_SCRIPT = """fetch(
  'https://apiff14risingstones.web.sdo.com/api/home/userInfo/getUserInfo',
  { credentials: 'include' },
)
  .then((response) => response.text())
  .then(() => console.log('请求已发出：请在 Network 中筛选 getUserInfo，右键该请求并选择 Copy > Copy as cURL (bash)。'))
  .catch((error) => console.error('请求触发失败：', error));"""
DATA_CENTRES = ["陆行鸟", "莫古力", "猫小胖", "豆豆柴"]
CN_WORLD_DATA_CENTRES = set(DATA_CENTRES)
CN_WORLD_NAME_CACHE: dict[str, dict] | None = None
HOUSE_DEFAULT_LISTINGS = 10
HOUSE_MAX_LISTINGS = 40
HOUSE_SERVER_IDS = {
    "红玉海": 1167,
    "神意之地": 1081,
    "拉诺西亚": 1042,
    "幻影群岛": 1044,
    "萌芽池": 1060,
    "宇宙和音": 1173,
    "沃仙曦染": 1174,
    "晨曦王座": 1175,
    "白银乡": 1172,
    "白金幻象": 1076,
    "神拳痕": 1171,
    "潮风亭": 1170,
    "旅人栈桥": 1113,
    "拂晓之间": 1121,
    "龙巢神殿": 1166,
    "梦羽宝境": 1176,
    "紫水栈桥": 1043,
    "延夏": 1169,
    "静语庄园": 1106,
    "摩杜纳": 1045,
    "海猫茶屋": 1177,
    "柔风海湾": 1178,
    "琥珀原": 1179,
    "水晶塔": 1192,
    "银泪湖": 1183,
    "太阳海岸": 1180,
    "伊修加德": 1186,
    "红茶川": 1201,
}
HOUSE_AREA_NAMES = ["海都", "森都", "沙都", "白银", "雪都"]
HOUSE_AREA_ALIASES = {
    "海雾村": "海都",
    "海雾": "海都",
    "薰衣草苗圃": "森都",
    "薰衣草": "森都",
    "高脚孤丘": "沙都",
    "高脚": "沙都",
    "白银乡": "白银",
    "穹顶皓天": "雪都",
    "穹顶": "雪都",
}
HOUSE_SIZE_NAMES = ["S", "M", "L"]
HOUSE_PURCHASE_TYPES = {
    0: "不可购买",
    1: "先到先得",
    2: "抽签",
}
HOUSE_REGION_TYPES = {
    0: "保留",
    1: "部队",
    2: "个人",
}
MARKET_DEFAULT_SCOPE = "全大区"
MARKET_DEFAULT_LISTINGS = 10
MARKET_MAX_LISTINGS = 40
MARKET_SCOPE_ALIASES = {
    "鸟": "陆行鸟",
    "猪": "莫古力",
    "猫": "猫小胖",
    "狗": "豆豆柴",
    "柔风": "柔风海湾",
}
GARLAND_CORE_DATA: dict | None = None
NODE_NAME_BY_TYPE = {
    0: "矿脉",
    1: "石场",
    2: "良材",
    3: "草场",
    4: "鱼影",
    5: "鱼影",
}
PARTY_CATEGORY_LABELS = {
    "DutyRoulette": "随机任务",
    "Dungeons": "迷宫挑战",
    "Guildhests": "行会令",
    "Trials": "讨伐歼灭战",
    "Raids": "大型任务",
    "HighEndDuty": "高难任务",
    "Pvp": "PvP",
    "GoldSaucer": "金碟",
    "Fates": "危命任务",
    "TreasureHunt": "寻宝",
    "TheHunt": "狩猎",
    "GatheringForays": "采集",
    "DeepDungeons": "深层迷宫",
    "AdventuringForays": "特殊场景探索",
    "V&C Dungeon Finder": "多变迷宫",
    "None": "其他",
}
PARTY_CATEGORY_IDS = {
    "None": 0,
    "DutyRoulette": 2,
    "Dungeons": 4,
    "Guildhests": 8,
    "Trials": 16,
    "Raids": 32,
    "HighEndDuty": 64,
    "Pvp": 128,
    "GoldSaucer": 256,
    "Fates": 512,
    "TreasureHunt": 1024,
    "TheHunt": 2048,
    "GatheringForays": 4096,
    "DeepDungeons": 8192,
    "AdventuringForays": 16384,
    "V&C Dungeon Finder": 32768,
}
PARTY_CATEGORY_ID_LABELS = {
    category_id: PARTY_CATEGORY_LABELS[category]
    for category, category_id in PARTY_CATEGORY_IDS.items()
    if category in PARTY_CATEGORY_LABELS
}
PARTY_DUTY_ALIAS_IDS = {
    "妖星乱舞绝境战": [1094],
    "绝妖星乱舞": [1094],
    "绝妖星": [1094],
    "绝妖": [1094],
    "妖星": [1094],
}
PARTY_DUTY_ID_NAME_OVERRIDES = {
    1094: "妖星乱舞绝境战",
}
PARTY_DUTY_ID_CACHE: dict[str, list[int]] = {}
PARTY_CATEGORY_ALIASES = {
    "随机任务": "DutyRoulette",
    "随机": "DutyRoulette",
    "roulette": "DutyRoulette",
    "dutyroulette": "DutyRoulette",
    "迷宫挑战": "Dungeons",
    "迷宫": "Dungeons",
    "dungeons": "Dungeons",
    "行会令": "Guildhests",
    "guildhests": "Guildhests",
    "讨伐歼灭战": "Trials",
    "讨伐": "Trials",
    "歼灭": "Trials",
    "trials": "Trials",
    "大型任务": "Raids",
    "大型": "Raids",
    "raids": "Raids",
    "高难任务": "HighEndDuty",
    "高难度任务": "HighEndDuty",
    "高难度": "HighEndDuty",
    "高难本": "HighEndDuty",
    "高难": "HighEndDuty",
    "零式": "HighEndDuty",
    "绝": "HighEndDuty",
    "highendduty": "HighEndDuty",
    "high-end": "HighEndDuty",
    "high-endduty": "HighEndDuty",
    "pvp": "Pvp",
    "金碟": "GoldSaucer",
    "goldsaucer": "GoldSaucer",
    "危命任务": "Fates",
    "危命": "Fates",
    "fate": "Fates",
    "fates": "Fates",
    "寻宝": "TreasureHunt",
    "宝物库": "TreasureHunt",
    "treasurehunt": "TreasureHunt",
    "狩猎": "TheHunt",
    "恶名精英": "TheHunt",
    "hunt": "TheHunt",
    "thehunt": "TheHunt",
    "采集": "GatheringForays",
    "gatheringforays": "GatheringForays",
    "深层迷宫": "DeepDungeons",
    "死宫": "DeepDungeons",
    "天宫": "DeepDungeons",
    "deepdungeons": "DeepDungeons",
    "特殊场景探索": "AdventuringForays",
    "特殊战场": "AdventuringForays",
    "adventuringforays": "AdventuringForays",
    "fieldoperations": "AdventuringForays",
    "多变迷宫": "V&C Dungeon Finder",
    "异闻": "V&C Dungeon Finder",
    "vc": "V&C Dungeon Finder",
    "v&c": "V&C Dungeon Finder",
    "v&cdungeonfinder": "V&C Dungeon Finder",
    "其他": "None",
    "无": "None",
    "none": "None",
}
PARTY_JOB_ALIASES = {
    "骑士": 19,
    "骑": 19,
    "pld": 19,
    "武僧": 20,
    "僧": 20,
    "mnk": 20,
    "战士": 21,
    "战": 21,
    "war": 21,
    "龙骑": 22,
    "龙骑士": 22,
    "龙": 22,
    "drg": 22,
    "诗人": 23,
    "吟游诗人": 23,
    "诗": 23,
    "brd": 23,
    "白魔": 24,
    "白魔法师": 24,
    "白": 24,
    "whm": 24,
    "黑魔": 25,
    "黑魔法师": 25,
    "黑": 25,
    "blm": 25,
    "召唤": 27,
    "召唤师": 27,
    "召": 27,
    "smn": 27,
    "学者": 28,
    "学": 28,
    "sch": 28,
    "忍者": 30,
    "忍": 30,
    "nin": 30,
    "机工": 31,
    "机工士": 31,
    "机": 31,
    "mch": 31,
    "暗黑": 32,
    "暗黑骑士": 32,
    "暗骑": 32,
    "drk": 32,
    "占星": 33,
    "占星术士": 33,
    "占": 33,
    "ast": 33,
    "武士": 34,
    "侍": 34,
    "sam": 34,
    "赤魔": 35,
    "赤魔法师": 35,
    "赤": 35,
    "rdm": 35,
    "青魔": 36,
    "青魔法师": 36,
    "青": 36,
    "blu": 36,
    "绝枪": 37,
    "绝枪战士": 37,
    "枪刃": 37,
    "gnb": 37,
    "舞者": 38,
    "舞": 38,
    "dnc": 38,
    "钐镰": 39,
    "钐镰客": 39,
    "镰刀": 39,
    "镰": 39,
    "rpr": 39,
    "贤者": 40,
    "贤": 40,
    "sge": 40,
    "蝰蛇": 41,
    "蝰蛇剑士": 41,
    "蝰": 41,
    "vpr": 41,
    "绘灵": 42,
    "绘灵法师": 42,
    "绘": 42,
    "pct": 42,
    "魔兽": 43,
    "魔兽使": 43,
    "bst": 43,
}


@dataclass
class PartyFinderQuery:
    data_centre: str | None
    category: str | None
    search_terms: list[str]
    job_ids: list[int]
    limit: int


@dataclass
class MarketQuery:
    scope_name: str
    scope_type: str
    item_name: str | None
    hq: bool
    limit: int


@dataclass
class HouseQuery:
    server_name: str | None
    area_name: str | None
    size_name: str | None
    ward: int | None
    plot_id: int | None
    limit: int


@dataclass
class RisingstonesPostsQuery:
    kind: str
    keyword: str | None
    page: int
    limit: int


@dataclass
class RisingstonesRecruitQuery:
    kind: str
    keyword: str | None
    page: int
    limit: int


@dataclass
class RisingstonesGlamourQuery:
    mode: str
    value: str | None
    limit: int


@dataclass
class RisingstonesGuildQuery:
    mode: str
    value: str | None
    limit: int


@dataclass(frozen=True)
class RisingstonesCredentials:
    cookie: str
    user_agent: str


@dataclass(frozen=True)
class RisingstonesGlamourMessage:
    text: str
    image_url: str | None


@dataclass(frozen=True)
class RisingstonesGlamourResponse:
    messages: list[RisingstonesGlamourMessage]


class RisingstonesAccountStore:
    """Small per-user SQLite store for private Rising Stones credentials."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS risingstones_accounts (
                    account_key TEXT PRIMARY KEY,
                    credential TEXT NOT NULL,
                    user_agent TEXT NOT NULL DEFAULT '',
                    auto_checkin INTEGER NOT NULL DEFAULT 0,
                    last_checkin_date TEXT,
                    last_attempt_date TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )

            columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(risingstones_accounts)"
                )
            }
            if "user_agent" not in columns:
                connection.execute(
                    "ALTER TABLE risingstones_accounts ADD COLUMN user_agent TEXT NOT NULL DEFAULT ''"
                )

    def set_credential(
        self, account_key: str, credentials: RisingstonesCredentials
    ) -> None:
        now = datetime.now(RISINGSTONES_TIMEZONE).isoformat(timespec="seconds")
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO risingstones_accounts (
                    account_key, credential, user_agent, updated_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(account_key) DO UPDATE SET
                    credential = excluded.credential,
                    user_agent = excluded.user_agent,
                    updated_at = excluded.updated_at
                """,
                (account_key, credentials.cookie, credentials.user_agent, now),
            )

    def get_credential(self, account_key: str) -> str | None:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT credential FROM risingstones_accounts WHERE account_key = ?",
                (account_key,),
            ).fetchone()
        return str(row[0]) if row else None

    def get_credentials(self, account_key: str) -> RisingstonesCredentials | None:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                """
                SELECT credential, user_agent
                FROM risingstones_accounts
                WHERE account_key = ?
                """,
                (account_key,),
            ).fetchone()
        if not row or not row[0] or not row[1]:
            return None
        return RisingstonesCredentials(cookie=str(row[0]), user_agent=str(row[1]))

    def remove(self, account_key: str) -> bool:
        with sqlite3.connect(self.path) as connection:
            cursor = connection.execute(
                "DELETE FROM risingstones_accounts WHERE account_key = ?",
                (account_key,),
            )
        return cursor.rowcount > 0

    def set_auto_checkin(self, account_key: str, enabled: bool) -> bool:
        with sqlite3.connect(self.path) as connection:
            cursor = connection.execute(
                "UPDATE risingstones_accounts SET auto_checkin = ? WHERE account_key = ?",
                (int(enabled), account_key),
            )
        return cursor.rowcount > 0

    def due_auto_checkins(self, day: str) -> list[tuple[str, RisingstonesCredentials]]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                """
                SELECT account_key, credential, user_agent
                FROM risingstones_accounts
                WHERE auto_checkin = 1
                  AND (last_attempt_date IS NULL OR last_attempt_date != ?)
                """,
                (day,),
            ).fetchall()
        return [
            (
                str(account_key),
                RisingstonesCredentials(cookie=str(cookie), user_agent=str(user_agent)),
            )
            for account_key, cookie, user_agent in rows
            if cookie and user_agent
        ]

    def mark_attempt(self, account_key: str, day: str) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "UPDATE risingstones_accounts SET last_attempt_date = ? WHERE account_key = ?",
                (day, account_key),
            )

    def mark_checkin(self, account_key: str, day: str) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "UPDATE risingstones_accounts SET last_checkin_date = ? WHERE account_key = ?",
                (day, account_key),
            )

    def summary(self) -> dict[str, int]:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(auto_checkin), 0)
                FROM risingstones_accounts
                """
            ).fetchone()
        return {"accounts": int(row[0]), "auto_checkin_accounts": int(row[1])}

    def admin_rows(self, limit: int = 100) -> list[dict[str, object]]:
        limit = max(1, min(limit, 100))
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                """
                SELECT account_key, auto_checkin, last_checkin_date, last_attempt_date,
                       updated_at
                FROM risingstones_accounts
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "account": mask_admin_display_secret(account_key),
                "auto_checkin": bool(auto_checkin),
                "last_checkin_date": last_checkin_date or "",
                "last_attempt_date": last_attempt_date or "",
                "updated_at": updated_at,
            }
            for account_key, auto_checkin, last_checkin_date, last_attempt_date, updated_at in rows
        ]


ADMIN_FEATURE_DEFAULTS = {
    "help": True,
    "precious": True,
    "lottery": True,
    "calendar": True,
    "nuannuan": True,
    "dungeon_note": True,
    "party_finder": True,
    "weibo": True,
    "item": True,
    "market": True,
    "house": True,
    "logs_dps": True,
    "character_logs": True,
    "tarot": True,
    "risingstones_content": True,
    "risingstones_recruit": True,
    "risingstones_binding": True,
    "risingstones_profile": True,
    "risingstones_checkin": True,
    "risingstones_glamour": True,
    "risingstones_guild": True,
}

LEGACY_ADMIN_FEATURE_MEMBERS = {
    "core": (
        "help",
        "precious",
        "lottery",
        "calendar",
        "nuannuan",
        "dungeon_note",
        "tarot",
    ),
    "party_finder": ("party_finder",),
    "market": ("item", "market", "house"),
    "fflogs": ("logs_dps", "character_logs"),
    "weibo": ("weibo",),
    "risingstones": (
        "risingstones_content",
        "risingstones_recruit",
        "risingstones_binding",
        "risingstones_profile",
        "risingstones_checkin",
        "risingstones_glamour",
        "risingstones_guild",
    ),
}

ADMIN_COMMAND_FEATURES = {
    "帮帮忙": "help",
    "选门": "precious",
    "仙人彩": "lottery",
    "日历": "calendar",
    "暖暖": "nuannuan",
    "攻略": "dungeon_note",
    "招募": "party_finder",
    "看看微博": "weibo",
    "物品": "item",
    "价格": "market",
    "房子": "house",
    "房屋": "house",
    "输出": "logs_dps",
    "logs": "character_logs",
    "抽卡": "tarot",
}

ADMIN_CONFIG_BOOLEAN_FIELDS = (
    "debug_mode",
    "proxy_enabled",
    "use_global_calendar",
    "use_global_fflogs",
)
ADMIN_CONFIG_INTEGER_LIMITS = {
    "proxy_port": (0, 65535),
    "risingstones_checkin_hour": (0, 23),
}
ADMIN_CONFIG_TEXT_FIELDS = (
    "proxy_host",
    "proxy_username",
    "font_path",
    "ffxiv_icon_font_path",
)
ADMIN_CONFIG_SECRET_FIELDS = (
    "proxy_password",
    "weibo_cookie",
    "fflogs_client_id",
    "fflogs_client_secret",
)


def admin_feature_for_command(command_name: str) -> str | None:
    """Return the feature switch that controls a public plugin command."""
    return ADMIN_COMMAND_FEATURES.get(command_name)


def plugin_cache_size(cache_dir: Path) -> int:
    """Sum only files below the plugin cache directory."""
    if not cache_dir.exists():
        return 0
    return sum(path.stat().st_size for path in cache_dir.rglob("*") if path.is_file())


def sanitize_admin_activity_detail(value: object) -> str:
    """Store short operational context without persisting credentials."""
    text = str(value or "").strip()
    text = re.sub(
        r"(?i)\b(cookie|token|authorization|secret|password|curl)\s*[:=]\s*([^\s;]+)",
        lambda match: f"{match.group(1)}={mask_debug_secret(match.group(2))}",
        text,
    )
    return text[:240]


class PluginAdminStore:
    """Private operational state for the authenticated plugin Page."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS fflogs_tracking_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform_id TEXT NOT NULL,
                    sender_key TEXT NOT NULL,
                    character_name TEXT NOT NULL,
                    world_name TEXT NOT NULL,
                    region TEXT NOT NULL DEFAULT 'CN',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_synced_at TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(platform_id, sender_key, character_name, world_name)
                )
                """
            )

    def get_feature_flags(self) -> dict[str, bool]:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT setting_value FROM admin_settings WHERE setting_key = 'feature_flags'"
            ).fetchone()
        if not row:
            return dict(ADMIN_FEATURE_DEFAULTS)
        try:
            saved = json.loads(str(row[0]))
        except (TypeError, ValueError, json.JSONDecodeError):
            saved = {}
        flags = dict(ADMIN_FEATURE_DEFAULTS)
        for legacy_key, members in LEGACY_ADMIN_FEATURE_MEMBERS.items():
            if legacy_key in saved:
                for member in members:
                    flags[member] = bool(saved[legacy_key])
        for key in ADMIN_FEATURE_DEFAULTS:
            if key in saved:
                flags[key] = bool(saved[key])
        return flags

    def set_feature_flags(self, flags: dict[str, bool]) -> dict[str, bool]:
        resolved = self.get_feature_flags()
        for key, value in flags.items():
            if key in ADMIN_FEATURE_DEFAULTS:
                resolved[key] = bool(value)
        now = datetime.now(RISINGSTONES_TIMEZONE).isoformat(timespec="seconds")
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO admin_settings (setting_key, setting_value, updated_at)
                VALUES ('feature_flags', ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = excluded.setting_value,
                    updated_at = excluded.updated_at
                """,
                (json.dumps(resolved, ensure_ascii=False), now),
            )
        return resolved

    def get_setting(self, key: str) -> str:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT setting_value FROM admin_settings WHERE setting_key = ?", (key,)
            ).fetchone()
        return str(row[0]) if row else ""

    def set_setting(self, key: str, value: str) -> None:
        now = datetime.now(RISINGSTONES_TIMEZONE).isoformat(timespec="seconds")
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO admin_settings (setting_key, setting_value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = excluded.setting_value,
                    updated_at = excluded.updated_at
                """,
                (key, value, now),
            )

    def record_activity(self, source: str, status: str, detail: object = "") -> None:
        now = datetime.now(RISINGSTONES_TIMEZONE).isoformat(timespec="seconds")
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO admin_activity (created_at, source, status, detail)
                VALUES (?, ?, ?, ?)
                """,
                (now, source[:64], status[:32], sanitize_admin_activity_detail(detail)),
            )

    def recent_activity(self, limit: int = 20) -> list[dict[str, str]]:
        limit = max(1, min(limit, 100))
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                """
                SELECT created_at, source, status, detail
                FROM admin_activity
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "created_at": str(created_at),
                "source": str(source),
                "status": str(status),
                "detail": str(detail),
            }
            for created_at, source, status, detail in rows
        ]

    def database_summary(self) -> dict[str, object]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
            table_names = [
                str(row[0]) for row in rows if not str(row[0]).startswith("sqlite_")
            ]
            row_counts = {
                table_name: int(
                    connection.execute(
                        f'SELECT COUNT(*) FROM "{table_name.replace(chr(34), chr(34) * 2)}"'
                    ).fetchone()[0]
                )
                for table_name in table_names
            }
        return {
            "path_name": self.path.name,
            "size": self.path.stat().st_size if self.path.exists() else 0,
            "tables": table_names,
            "row_counts": row_counts,
        }


def feature_enabled(store: PluginAdminStore, feature: str) -> bool:
    """Resolve one page-managed feature flag with a safe enabled default."""
    return store.get_feature_flags().get(feature, True)


def risingstones_feature_for_query(raw_query: str) -> str:
    """Resolve the detailed Rising Stones switch for one command invocation."""
    action = raw_query.strip().split(maxsplit=1)[0] if raw_query.strip() else ""
    if action == "招募":
        return "risingstones_recruit"
    if action in {"绑定", "解绑"}:
        return "risingstones_binding"
    if action in {"我的", "通知", "统计"}:
        return "risingstones_profile"
    if action in {"签到", "自动签到"}:
        return "risingstones_checkin"
    if action == "幻化":
        return "risingstones_glamour"
    if action == "部队":
        return "risingstones_guild"
    return "risingstones_content"


def admin_settings_public_view(config: dict | None) -> dict[str, object]:
    """Expose editable configuration while returning only secret presence flags."""
    config = config or {}
    settings: dict[str, object] = {
        key: bool(config.get(key, False)) for key in ADMIN_CONFIG_BOOLEAN_FIELDS
    }
    settings.update(
        {key: int(config.get(key, 0) or 0) for key in ADMIN_CONFIG_INTEGER_LIMITS}
    )
    settings.update(
        {
            key: str(config.get(key, "") or "").strip()
            for key in ADMIN_CONFIG_TEXT_FIELDS
        }
    )
    settings.update(
        {
            f"{key}_set": bool(str(config.get(key, "") or "").strip())
            for key in ADMIN_CONFIG_SECRET_FIELDS
        }
    )
    return settings


def validate_admin_settings_update(payload: object) -> dict[str, object]:
    """Validate one Page settings update and return the changed config values."""
    if not isinstance(payload, dict) or not isinstance(payload.get("settings"), dict):
        raise ValueError("插件设置必须是 JSON 对象。")
    settings = payload["settings"]
    updates: dict[str, object] = {}
    for key in ADMIN_CONFIG_BOOLEAN_FIELDS:
        if key in settings:
            if not isinstance(settings[key], bool):
                raise ValueError(f"{key} 必须是布尔值。")
            updates[key] = settings[key]
    for key, (minimum, maximum) in ADMIN_CONFIG_INTEGER_LIMITS.items():
        if key in settings:
            value = settings[key]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{key} 必须是整数。")
            if not minimum <= value <= maximum:
                raise ValueError(f"{key} 必须在 {minimum} 到 {maximum} 之间。")
            updates[key] = value
    for key in ADMIN_CONFIG_TEXT_FIELDS:
        if key in settings:
            value = settings[key]
            if not isinstance(value, str):
                raise ValueError(f"{key} 必须是字符串。")
            updates[key] = value.strip()

    clear_secrets = payload.get("clear_secrets", [])
    if not isinstance(clear_secrets, list) or any(
        key not in ADMIN_CONFIG_SECRET_FIELDS for key in clear_secrets
    ):
        raise ValueError("清除字段包含不支持的敏感配置。")
    for key in ADMIN_CONFIG_SECRET_FIELDS:
        if key in clear_secrets:
            updates[key] = ""
            continue
        if key not in settings:
            continue
        value = settings[key]
        if not isinstance(value, str):
            raise ValueError(f"{key} 必须是字符串。")
        if value.strip():
            updates[key] = value.strip()
    return updates


def build_admin_overview(
    store: PluginAdminStore,
    *,
    version: str,
    started_at: datetime,
    risingstones_accounts: int,
    auto_checkin_accounts: int,
) -> dict:
    now = datetime.now(RISINGSTONES_TIMEZONE)
    database_size = store.path.stat().st_size if store.path.exists() else 0
    return {
        "version": version,
        "uptime_seconds": max(0, int((now - started_at).total_seconds())),
        "database_size": database_size,
        "risingstones_accounts": risingstones_accounts,
        "auto_checkin_accounts": auto_checkin_accounts,
        "feature_flags": store.get_feature_flags(),
        "activity": store.recent_activity(limit=8),
    }


def sanitize_admin_test_result(result: dict[str, object]) -> dict[str, object]:
    """Remove request secrets before returning a Page test result."""
    return {
        key: value for key, value in result.items() if not is_sensitive_debug_key(key)
    }


def validate_owner_curl_for_admin(value: str) -> bool:
    """Accept only a parseable Chrome cURL (bash) request for getUserInfo."""
    return "getUserInfo" in value and parse_risingstones_curl_binding(value) is not None


@dataclass
class LogsQuery:
    boss_name: str | None
    job_name: str | None
    cn_source: bool
    dps_type: str
    day: int


@dataclass
class CharacterLogsQuery:
    character_name: str | None
    server_name: str | None
    cn_source: bool | None


LOGS_SERVER_TOKENS = {
    "国际服": False,
    "国际": False,
    "global": False,
    "intl": False,
    "international": False,
    "国服": True,
    "国": True,
    "cn": True,
    "china": True,
}
LOGS_DPS_TYPES = {"rdps", "adps", "pdps", "ndps", "cdps"}
FFLOGS_GLOBAL_CHARACTER_REGIONS = ["JP", "NA", "EU", "OC"]
FFLOGS_CHARACTER_BASE_ZONES = [
    {
        "key": "arcadion_light",
        "zone_id": 62,
        "difficulty": 101,
        "type": "savage",
        "version": "7.x",
        "order": 70,
    },
    {
        "key": "arcadion_cruiser",
        "zone_id": 68,
        "difficulty": 101,
        "type": "savage",
        "version": "7.x",
        "order": 70,
    },
    {
        "key": "arcadion_heavy",
        "zone_id": 73,
        "difficulty": 101,
        "type": "savage",
        "version": "7.x",
        "order": 70,
    },
    {
        "key": "ultimate_70_future",
        "zone_id": 65,
        "difficulty": None,
        "type": "ultimate",
        "version": "7.x",
        "order": 70,
    },
    {
        "key": "ultimate_70_legacy",
        "zone_id": 59,
        "difficulty": None,
        "type": "ultimate",
        "version": "7.x",
        "order": 70,
    },
    {
        "key": "ultimate_60_top",
        "zone_id": 53,
        "difficulty": None,
        "type": "ultimate",
        "version": "6.x",
        "order": 60,
    },
    {
        "key": "ultimate_60_dsr",
        "zone_id": 45,
        "difficulty": None,
        "type": "ultimate",
        "version": "6.x",
        "order": 60,
    },
    {
        "key": "ultimate_60_legacy",
        "zone_id": 43,
        "difficulty": None,
        "type": "ultimate",
        "version": "6.x",
        "order": 60,
    },
    {
        "key": "ultimate_50_tea",
        "zone_id": 32,
        "difficulty": None,
        "type": "ultimate",
        "version": "5.x",
        "order": 50,
    },
    {
        "key": "ultimate_50_legacy",
        "zone_id": 30,
        "difficulty": None,
        "type": "ultimate",
        "version": "5.x",
        "order": 50,
    },
    {
        "key": "ultimate_40_uwu",
        "zone_id": 23,
        "difficulty": None,
        "type": "ultimate",
        "version": "4.x",
        "order": 40,
    },
    {
        "key": "ultimate_40_ucob",
        "zone_id": 19,
        "difficulty": None,
        "type": "ultimate",
        "version": "4.x",
        "order": 40,
    },
]
FFLOGS_CHARACTER_ZONE_REQUESTS = [dict(item) for item in FFLOGS_CHARACTER_BASE_ZONES]
FFLOGS_CHARACTER_QUERY_BATCH_SIZE = 8
FFLOGS_CHARACTER_QUERY_CONCURRENCY = 3
FFLOGS_CHARACTER_PARTITION_VERSION_OVERRIDES = {
    (30, 3): "5.4",
}
FFLOGS_CHARACTER_SHORT_LABELS = {
    93: "M1S",
    94: "M2S",
    95: "M3S",
    96: "M4S",
    97: "M5S",
    98: "M6S",
    99: "M7S",
    100: "M8S",
    101: "M9S",
    102: "M10S",
    103: "M11S",
    104: "M12S门神",
    105: "M12S本体",
    78: "P1S",
    79: "P2S",
    80: "P3S",
    81: "P4S门神",
    82: "P4S本体",
    83: "P5S",
    84: "P6S",
    85: "P7S",
    86: "P8S门神",
    87: "P8S本体",
    88: "P9S",
    89: "P10S",
    90: "P11S",
    91: "P12S门神",
    92: "P12S本体",
    1079: "绝伊甸",
    1077: "绝欧",
    1068: "绝欧",
    1076: "绝龙诗",
    1065: "绝龙诗",
    1075: "绝亚",
    1062: "绝亚",
    1050: "绝亚",
    1074: "绝神兵",
    1061: "绝神兵",
    1048: "绝神兵",
    1042: "绝神兵",
    1073: "绝巴哈",
    1060: "绝巴哈",
    1047: "绝巴哈",
    1039: "绝巴哈",
}
FFLOGS_CHARACTER_GROUPS = [
    (
        "绝境战",
        [1079, 1077, 1076, 1075, 1074, 1073],
    ),
    (
        "7.0 阿卡狄亚零式",
        [105, 104, 103, 102, 101, 100, 99, 98, 97, 96, 95, 94, 93],
    ),
]
LOGS_BOSS_PHASE_GROUPS = [
    {
        "aliases": [
            "m12s",
            "m12s全部",
            "林德布鲁姆",
            "阿卡狄亚零式登天斗技场重量级4",
            "阿卡狄亚登天斗技场零式重量级4",
            "阿卡狄亚零式重量级4",
            "重量级4零式",
            "零式重量级4",
            "lindwurm",
        ],
        "phase_aliases": [
            ["门神", "前半", "p1", "phase1", "一阶段", "第一阶段"],
            ["本体", "后半", "p2", "phase2", "二阶段", "第二阶段", "ii"],
        ],
        "members": [(104, 101), (105, 101)],
    },
    {
        "aliases": [
            "m12",
            "m12全部",
            "阿卡狄亚登天斗技场重量级4",
            "阿卡狄亚重量级4",
            "重量级4",
        ],
        "phase_aliases": [
            ["门神", "前半", "p1", "phase1", "一阶段", "第一阶段"],
            ["本体", "后半", "p2", "phase2", "二阶段", "第二阶段", "ii"],
        ],
        "members": [(104, 100), (105, 100)],
    },
]


def api_request_headers(headers: dict | None = None) -> dict:
    request_headers = {
        "Connection": "close",
        "User-Agent": PLUGIN_USER_AGENT,
    }
    if headers:
        request_headers.update(headers)
    for key in list(request_headers):
        if key.lower() == "user-agent":
            del request_headers[key]
    request_headers["User-Agent"] = PLUGIN_USER_AGENT
    return request_headers


def browser_request_headers(headers: dict | None = None) -> dict:
    request_headers = {
        "Connection": "close",
        "User-Agent": random.choice(BROWSER_USER_AGENTS),
    }
    if headers:
        request_headers.update(headers)
    return request_headers


async def aiohttp_get(
    url: str,
    res_type: str = "json",
    timeout_seconds: int = 15,
    headers: dict | None = None,
    use_api_user_agent: bool = False,
):
    result = await aiohttp_request(
        "GET",
        url,
        res_type=res_type,
        timeout_seconds=timeout_seconds,
        headers=headers,
        use_api_user_agent=use_api_user_agent,
    )
    return result.payload if result.status == 200 else None


async def aiohttp_request(
    method: str,
    url: str,
    *,
    res_type: str = "json",
    timeout_seconds: int = 15,
    headers: dict | None = None,
    use_api_user_agent: bool = False,
    data: dict | None = None,
    json_data: dict | None = None,
    auth: aiohttp.BasicAuth | None = None,
) -> HttpResponse:
    request_headers = (
        api_request_headers(headers)
        if use_api_user_agent
        else browser_request_headers(headers)
    )
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    request_options = proxy_request_options()
    proxy_used = bool(request_options)
    request_id = next(HTTP_REQUEST_COUNTER)
    start_time = time.monotonic()
    debug_log(
        "http.request.start",
        proxy_used=proxy_used,
        request_id=request_id,
        method=method.upper(),
        url=url,
        timeout_seconds=timeout_seconds,
        header_names=sorted(request_headers),
        has_body=bool(data or json_data),
        has_auth=auth is not None,
    )
    try:
        async with aiohttp.ClientSession(
            timeout=timeout, headers=request_headers
        ) as session:
            async with session.request(
                method,
                url,
                data=data,
                json=json_data,
                auth=auth,
                **request_options,
            ) as response:
                payload = None
                if response.status == 200:
                    if res_type == "bytes":
                        payload = await response.read()
                    elif res_type == "text":
                        payload = await response.text()
                    else:
                        try:
                            payload = await response.json(content_type=None)
                        except Exception as exc:
                            logger.warning(
                                f"JSON响应解析失败: {sanitize_debug_url(url)}, {exc}"
                            )
                            debug_log(
                                "http.response.parse_error",
                                proxy_used=proxy_used,
                                request_id=request_id,
                                error_type=type(exc).__name__,
                            )
                debug_log(
                    "http.request.finish",
                    proxy_used=proxy_used,
                    request_id=request_id,
                    status=response.status,
                    elapsed_ms=round((time.monotonic() - start_time) * 1000),
                    content_length=response.content_length,
                )
                return HttpResponse(response.status, payload)
    except Exception as exc:
        debug_log(
            "http.request.error",
            proxy_used=proxy_used,
            request_id=request_id,
            error_type=type(exc).__name__,
            elapsed_ms=round((time.monotonic() - start_time) * 1000),
        )
        raise


async def safe_aiohttp_get(url: str, context: str, **kwargs):
    try:
        return await aiohttp_get(url, **kwargs)
    except Exception as exc:
        logger.warning(f"{context}请求失败: {exc}")
        return None


def random_left_right() -> str:
    result = []
    for _ in range(5):
        result.append("右" if random.random() > 0.5 else "左")
    return " ".join(result)


def random_lottery() -> str:
    last_num_list = []
    lottery_list = []
    for _ in range(3):
        while True:
            last_num = random.randint(0, 9)
            if last_num not in last_num_list:
                last_num_list.append(last_num)
                break
        lottery_list.append(
            f"{random.randint(0, 9)} {random.randint(0, 9)} "
            f"{random.randint(0, 9)} {last_num}"
        )
    return "\n".join(lottery_list)


def resolve_text_font(configured_font_path: str | None = None) -> str | None:
    candidates = []
    if configured_font_path:
        candidates.append(configured_font_path)
    candidates.extend(DEFAULT_FONT_PATHS)
    for candidate in candidates:
        font_path = Path(candidate).expanduser()
        if font_path.exists() and font_path.is_file():
            return str(font_path)
    return None


def text_to_image(
    text: str,
    output_path: Path,
    width_now: int = 20,
    font_path: str | None = None,
) -> None:
    font_size = 20
    resolved_font = resolve_text_font(font_path)
    if resolved_font:
        font = ImageFont.truetype(resolved_font, size=font_size)
    else:
        logger.warning("未找到可用中文字体，文本转图片将使用 PIL 默认字体。")
        font = ImageFont.load_default()

    cursor = 0
    wrapped = ""
    for char in text:
        if char == "\n":
            if len(wrapped) > 1:
                wrapped += char
            cursor = 0
            continue
        cursor += 2 if len(char.encode()) > 1 else 1
        wrapped += char
        if cursor >= 2 * width_now:
            wrapped += "\n"
            cursor = 0

    lines = wrapped.split("\n")
    image = Image.new(
        "RGB",
        (int(width_now * 22), (len(lines) + 1) * (font_size + 2)),
        (255, 255, 255),
    )
    draw = ImageDraw.Draw(image)
    draw.text((10, 10), wrapped, font=font, fill=(0, 0, 0))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="JPEG")


def load_render_font(font_path: str | None, font_size: int):
    resolved_font = resolve_text_font(font_path)
    if resolved_font:
        try:
            return ImageFont.truetype(resolved_font, size=font_size)
        except OSError as exc:
            logger.warning(f"Configured font load failed: {resolved_font}: {exc}")
    return ImageFont.load_default()


def load_optional_font(font_path: str | None, font_size: int):
    if not font_path:
        return None
    font_file = Path(font_path).expanduser()
    if not font_file.exists() or not font_file.is_file():
        logger.warning(f"FFXIV icon font path is not a file: {font_path}")
        return None
    try:
        return ImageFont.truetype(str(font_file), size=font_size)
    except OSError as exc:
        logger.warning(f"FFXIV icon font load failed: {font_path}: {exc}")
        return None


def is_ffxiv_icon_char(char: str) -> bool:
    return len(char) == 1 and FFXIV_ICON_FONT_START <= ord(char) <= FFXIV_ICON_FONT_END


def mixed_char_font(char: str, text_font, icon_font):
    if icon_font and is_ffxiv_icon_char(char):
        return icon_font
    return text_font


def text_bbox_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    if not text:
        return 0, 0
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def mixed_text_width(draw: ImageDraw.ImageDraw, text: str, text_font, icon_font) -> int:
    return sum(
        text_bbox_size(draw, char, mixed_char_font(char, text_font, icon_font))[0]
        for char in text
    )


def draw_mixed_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    text_font,
    icon_font,
    fill: tuple[int, int, int],
) -> int:
    x, y = xy
    for char in text:
        font = mixed_char_font(char, text_font, icon_font)
        draw.text((x, y), char, font=font, fill=fill)
        x += text_bbox_size(draw, char, font)[0]
    return x


def wrap_mixed_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    text_font,
    icon_font,
    max_lines: int,
) -> list[str]:
    text = re.sub(r"[ \t\r\f\v]+", " ", text or "").strip()
    if not text:
        return []

    lines: list[str] = []
    line = ""
    truncated = False
    for char in text:
        if char == "\n":
            lines.append(line)
            line = ""
        else:
            candidate = line + char
            if (
                line
                and mixed_text_width(draw, candidate, text_font, icon_font) > max_width
            ):
                lines.append(line)
                line = char
            else:
                line = candidate
        if len(lines) >= max_lines:
            truncated = bool(line) or char != text[-1]
            line = ""
            break
    if line and len(lines) < max_lines:
        lines.append(line)

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True
    if truncated and lines:
        while (
            lines[-1]
            and mixed_text_width(draw, lines[-1] + "...", text_font, icon_font)
            > max_width
        ):
            lines[-1] = lines[-1][:-1]
        lines[-1] += "..."
    return lines


def draw_pill(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font,
    fill: tuple[int, int, int],
    text_fill: tuple[int, int, int],
    padding_x: int = 12,
    padding_y: int = 5,
) -> tuple[int, int, int, int]:
    x, y = xy
    text_w, text_h = text_bbox_size(draw, text, font)
    rect = (x, y, x + text_w + padding_x * 2, y + text_h + padding_y * 2)
    draw.rounded_rectangle(rect, radius=7, fill=fill)
    draw.text((x + padding_x, y + padding_y - 1), text, font=font, fill=text_fill)
    return rect


def format_party_card_time_left(seconds_value) -> str:
    try:
        seconds = int(float(seconds_value))
    except (TypeError, ValueError):
        return party_optional_text(seconds_value)
    if seconds <= 0:
        return "已过期"
    minutes = seconds // 60
    rest_seconds = seconds % 60
    if minutes < 60:
        return f"{minutes}分钟{rest_seconds:02d}秒"
    hours = minutes // 60
    rest_minutes = minutes % 60
    if rest_minutes:
        return f"{hours}小时{rest_minutes}分钟"
    return f"{hours}小时"


def get_party_category_label(listing: dict) -> str:
    category_now = party_optional_text(listing.get("category"))
    category_id = listing.get("category_id")
    if not category_now and category_id is not None:
        try:
            return PARTY_CATEGORY_ID_LABELS.get(
                int(category_id), f"分类ID {category_id}"
            )
        except (TypeError, ValueError):
            return f"分类ID {category_id}"
    return PARTY_CATEGORY_LABELS.get(category_now, category_now or "未知分类")


def get_party_people_text(listing: dict) -> str:
    total_text = party_optional_text(listing.get("total_text") or listing.get("total"))
    if total_text:
        return total_text

    filled = listing.get("slots_filled")
    available = listing.get("slots_available")
    if filled is not None and available is not None:
        try:
            return f"{int(filled)}/{int(available)}"
        except (TypeError, ValueError):
            return f"{filled}/{available}"
    return ""


def normalize_party_finder_entry(index: int, listing: dict) -> dict:
    home_world = party_optional_text(
        listing.get("home_world")
        or listing.get("world")
        or listing.get("created_world")
    )
    created_world = party_optional_text(listing.get("created_world") or home_world)
    if not home_world:
        world_id = listing.get("home_world_id") or listing.get("created_world_id")
        home_world = f"世界ID {world_id}" if world_id else "未知世界"
    if not created_world:
        world_id = listing.get("created_world_id") or listing.get("home_world_id")
        created_world = f"世界ID {world_id}" if world_id else home_world

    datacenter = party_optional_text(
        listing.get("datacenter")
        or listing.get("data_centre")
        or listing.get("data_center")
    )
    item_level = party_optional_text(listing.get("min_item_level"))
    duty = party_optional_text(listing.get("duty")) or "无"
    if item_level and item_level != "0":
        duty = f"{duty}  IL {item_level}"

    return {
        "index": index,
        "creator": party_optional_text(
            listing.get("name") or listing.get("player_name")
        )
        or "未知",
        "home_world": home_world,
        "created_world": created_world,
        "datacenter": datacenter or "未知大区",
        "category": get_party_category_label(listing),
        "duty": duty,
        "description": party_optional_text(listing.get("description")) or "无招募说明",
        "people": get_party_people_text(listing) or "?/?",
        "time_left": format_party_card_time_left(
            listing.get("time_left") or listing.get("time_left_seconds")
        ),
        "updated": format_party_updated_at(
            party_optional_text(listing.get("updated_at"))
        ),
        "is_cross_world": bool(
            created_world and home_world and created_world != home_world
        ),
    }


def draw_party_card_row(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    label: str,
    value: str,
    label_font,
    value_font,
    icon_font,
    badge: str | None = None,
) -> None:
    label_color = (88, 88, 94)
    value_color = (45, 45, 52)
    draw_mixed_text(draw, (x, y), label, label_font, icon_font, label_color)
    label_w = mixed_text_width(draw, label, label_font, icon_font)
    end_x = draw_mixed_text(
        draw, (x + label_w + 8, y), value, value_font, icon_font, value_color
    )
    if badge:
        draw_pill(
            draw,
            (end_x + 14, y - 3),
            badge,
            value_font,
            (242, 114, 57),
            (255, 255, 255),
            9,
            4,
        )


def render_party_finder_cards(
    entries: list[dict],
    output_path: Path,
    font_path: str | None = None,
    icon_font_path: str | None = None,
) -> None:
    width = 920
    card_x = 24
    card_w = width - card_x * 2
    card_h = 390
    gap = 20
    padding_y = 24
    height = padding_y * 2 + len(entries) * card_h + max(0, len(entries) - 1) * gap

    image = Image.new("RGB", (width, height), (246, 247, 250))
    draw = ImageDraw.Draw(image)
    title_font = load_render_font(font_path, 30)
    label_font = load_render_font(font_path, 23)
    body_font = load_render_font(font_path, 23)
    small_font = load_render_font(font_path, 18)
    people_font = load_render_font(font_path, 36)
    icon_font = load_optional_font(icon_font_path, 23)

    for card_index, entry in enumerate(entries):
        y = padding_y + card_index * (card_h + gap)
        shadow = (card_x + 4, y + 6, card_x + card_w + 4, y + card_h + 6)
        card = (card_x, y, card_x + card_w, y + card_h)
        draw.rounded_rectangle(shadow, radius=14, fill=(224, 226, 232))
        draw.rounded_rectangle(card, radius=14, fill=(255, 255, 255))
        draw.rounded_rectangle(
            (card_x, y, card_x + card_w, y + 66), radius=14, fill=(91, 96, 216)
        )
        draw.rectangle((card_x, y + 48, card_x + card_w, y + 66), fill=(91, 96, 216))

        draw_mixed_text(
            draw,
            (card_x + 24, y + 18),
            entry["creator"],
            title_font,
            icon_font,
            (255, 255, 255),
        )
        time_text = entry.get("time_left") or ""
        if time_text:
            badge_w = text_bbox_size(draw, time_text, body_font)[0] + 30
            draw_pill(
                draw,
                (card_x + card_w - badge_w - 20, y + 14),
                time_text,
                body_font,
                (125, 130, 226),
                (255, 255, 255),
                14,
                6,
            )

        body_x = card_x + 24
        row_y = y + 96
        row_gap = 32
        draw_party_card_row(
            draw,
            body_x,
            row_y,
            "所属服务器：",
            entry["home_world"],
            label_font,
            body_font,
            icon_font,
        )
        draw_party_card_row(
            draw,
            body_x,
            row_y + row_gap,
            "创建服务器：",
            entry["created_world"],
            label_font,
            body_font,
            icon_font,
            "跨服招募" if entry.get("is_cross_world") else None,
        )
        draw_party_card_row(
            draw,
            body_x,
            row_y + row_gap * 2,
            "大区：",
            entry["datacenter"],
            label_font,
            body_font,
            icon_font,
        )
        draw_party_card_row(
            draw,
            body_x,
            row_y + row_gap * 3,
            "类别：",
            entry["category"],
            label_font,
            body_font,
            icon_font,
        )
        draw_party_card_row(
            draw,
            body_x,
            row_y + row_gap * 4,
            "任务：",
            entry["duty"],
            label_font,
            body_font,
            icon_font,
        )

        people_text = entry.get("people") or "?/?"
        people_x = card_x + card_w - 124
        draw.text(
            (people_x + 10, y + 148), people_text, font=people_font, fill=(42, 42, 48)
        )
        draw.text((people_x + 28, y + 193), "人数", font=small_font, fill=(88, 88, 94))

        divider_y = y + 254
        draw.line(
            (card_x + 24, divider_y, card_x + card_w - 24, divider_y),
            fill=(226, 226, 230),
            width=1,
        )
        draw.text((body_x, y + 272), "招募说明：", font=label_font, fill=(91, 96, 216))
        desc_lines = wrap_mixed_text(
            draw, entry.get("description") or "", card_w - 48, body_font, icon_font, 2
        )
        for line_index, line in enumerate(desc_lines):
            draw_mixed_text(
                draw,
                (body_x, y + 306 + line_index * 28),
                line,
                body_font,
                icon_font,
                (102, 102, 108),
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="JPEG", quality=90)


def load_tarot() -> dict:
    with TAROT_JSON.open("r", encoding="utf-8") as tarot_file:
        return json.load(tarot_file)


def load_json_list(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig") as json_file:
        data = json.load(json_file)
    return data if isinstance(data, list) else []


def choose_tarot(tarot_dict: dict) -> tuple[str, Path]:
    info_now = tarot_dict["_塔罗牌堆"][random.randint(0, 43)]
    text_now = info_now.split("[", 1)[0]
    file_name = info_now.rsplit("/", 1)[-1][:-1].replace(" ", "_")
    return text_now, TAROT_DIR / file_name


def create_help_text() -> str:
    return """【塔塔露现有指令】
()括号表示可选的参数
[暖暖] 本周时尚品鉴作业
[选门] 帮你选藏宝洞的门
[仙人彩] 帮你选每周仙人仙彩数字
[日历 (国服/国际服)] 获取FF近期活动日历
[攻略 (副本等级) 副本名关键字 (文本)] 查简单副本攻略
[石之家 (帖子/攻略/招募) (关键词) (数量)] 查石之家公开内容
[石之家 幻化/部队] 使用主人登录信息或私聊绑定查询登录态内容
[石之家 绑定] 私聊获取 Chrome 控制台绑定脚本
[石之家 我的/通知/统计/签到/自动签到 开启/关闭/解绑] 仅私聊个人账号管理
[招募 大区名 (分类) (数量)] 获取指定大区招募板信息
[看看微博] 获取FF官方微博新闻
[物品 物品名] 查询物品信息
[价格 (大区/服务器) 物品名 (HQ) (数量)] 查询市场物价
[房子 服务器名 主城名 房子大小] 查询空房
[输出 boss名 职业名 (国服) (rdps) (day2)] 查询FFLogs输出分段
[logs 角色名 服务器名 (国服/国际服)] 查询角色FFLogs战绩
[抽卡] 随机抽取一张FF14塔罗牌
"""


def format_calendar_item(info_item: list) -> str:
    end_info = (
        str(info_item[0]).strip().split("+", 1)[0].rsplit(":", 1)[0].replace("-", ".")
    )
    start_info = (
        str(info_item[1]).strip().split("+", 1)[0].rsplit(":", 1)[0].replace("-", ".")
    )
    summary_info = str(info_item[2]).strip()
    return "* " + summary_info + "\n  " + start_info + " 至 " + end_info


def normalize_calendar_date(value) -> tuple[date, date | datetime]:
    if isinstance(value, datetime):
        return value.date(), value
    return value, value


def normalize_calendar_server(value: str | None, default_server: str = "国服") -> str:
    if not value:
        return default_server
    value = value.strip().lower()
    if value in {
        "国际服",
        "国际",
        "global",
        "intl",
        "international",
        "gaia",
        "mana",
        "elemental",
    }:
        return "国际服"
    if value in {"国服", "国", "cn", "china", "陆行鸟", "莫古力", "猫小胖", "豆豆柴"}:
        return "国服"
    return default_server


def command_args(message: str, command: str) -> str:
    message = message.strip()
    if message == command:
        return ""
    if message.startswith(command):
        return message[len(command) :].strip()
    return message


def parse_dungeon_query(dungeon_info: str) -> tuple[str | None, str | None, bool]:
    parts = dungeon_info.split()
    if not parts:
        return None, None, False

    dungeon_level = None
    is_text = False
    if "文本" in parts:
        is_text = True
        parts = [part for part in parts if part != "文本"]

    if parts and parts[0].isdigit():
        dungeon_level = parts[0]
        parts = parts[1:]

    if not parts:
        return dungeon_level, None, is_text
    return dungeon_level, parts[0], is_text


def strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<.*?>", "", text)
    return html.unescape(text).strip()


def parse_risingstones_posts_query(value: str) -> RisingstonesPostsQuery:
    """Parse the public Rising Stones post and strategy lookup syntax."""
    parts = value.split()
    kind = "post"
    if parts:
        kind_aliases = {
            "帖子": "post",
            "社区": "post",
            "post": "post",
            "攻略": "strat",
            "战略": "strat",
            "strat": "strat",
        }
        resolved_kind = kind_aliases.get(parts[0].lower())
        if resolved_kind:
            kind = resolved_kind
            parts = parts[1:]

    limit = RISINGSTONES_DEFAULT_LIMIT
    if parts and parts[-1].isdigit():
        limit = max(1, min(int(parts.pop()), RISINGSTONES_MAX_LIMIT))
    keyword = " ".join(parts).strip() or None
    return RisingstonesPostsQuery(kind=kind, keyword=keyword, page=1, limit=limit)


def risingstones_content_url(kind: str, post_id: object) -> str:
    route = "strat" if kind == "strat" else "post"
    return f"{RISINGSTONES_WEB_BASE}#/{route}/detail/{quote(str(post_id or ''))}"


async def fetch_risingstones_posts(query: RisingstonesPostsQuery) -> list[dict]:
    """Fetch public Rising Stones posts using the website's browser request shape."""
    content_type = "2" if query.kind == "strat" else "1"
    if query.keyword:
        endpoint = "/common/search"
        params = {
            "type": content_type,
            "keywords": query.keyword,
            "page": query.page,
            "limit": query.limit,
            "part_id": "",
            "orderBy": "comment",
            "pageTime": "",
        }
    else:
        endpoint = "/home/posts/postsList"
        params = {
            "type": content_type,
            "page": query.page,
            "limit": query.limit,
            "is_top": "0",
            "is_refine": "0",
            "part_id": "",
            "hotType": "postsHotNow" if query.kind == "post" else "",
            "order": "",
        }
    response = await aiohttp_request(
        "GET",
        f"{RISINGSTONES_API_BASE}{endpoint}?{urlencode(params)}",
        timeout_seconds=20,
        headers={
            "Accept": "application/json, text/plain, */*",
            "Referer": RISINGSTONES_WEB_BASE,
        },
    )
    if response.status != 200 or not isinstance(response.payload, dict):
        raise RuntimeError(f"石之家请求失败: HTTP {response.status}")
    payload = response.payload
    if payload.get("code") not in {0, 10000}:
        raise RuntimeError(f"石之家接口返回异常: {payload.get('msg') or '未知错误'}")
    data = payload.get("data")
    rows = data.get("rows") if isinstance(data, dict) else None
    return (
        [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    )


def risingstones_number(value: object) -> int | None:
    try:
        return int(value) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None


def format_risingstones_posts(query: RisingstonesPostsQuery, rows: list[dict]) -> str:
    """Format public community content as a bounded chat response."""
    kind_label = "攻略" if query.kind == "strat" else "帖子"
    keyword_label = f" 关键词：{query.keyword}" if query.keyword else ""
    lines = [f"【石之家{kind_label}】{keyword_label} 数量：{len(rows)}"]
    for index, row in enumerate(rows[: query.limit], start=1):
        post_id = row.get("posts_id") or row.get("id")
        title = str(row.get("title") or "未命名内容").strip()
        category = str(row.get("part_name") or "未分类").strip()
        author = str(row.get("character_name") or "未知作者").strip()
        server = str(row.get("area_name") or "未知服务器").strip()
        created_at = str(row.get("created_at") or "未知时间").strip()
        metrics = []
        for label, field in (
            ("浏览", "read_count"),
            ("评论", "comment_count"),
            ("点赞", "like_count"),
        ):
            number = risingstones_number(row.get(field))
            if number is not None:
                metrics.append(f"{label}：{number}")
        lines.extend(
            [
                f"\n{index:02d}. {title}",
                f"[{category}] {author} @ {server}",
                " | ".join([*metrics, created_at]),
                risingstones_content_url(query.kind, post_id),
            ]
        )
    return "\n".join(lines)


def parse_risingstones_recruit_query(value: str) -> RisingstonesRecruitQuery:
    """Parse public Rising Stones recruitment lookup syntax."""
    parts = value.split()
    kind = "party"
    if parts:
        kind_aliases = {
            "副本": "party",
            "队伍": "party",
            "party": "party",
            "萌新": "beginner",
            "新手": "beginner",
            "beginner": "beginner",
            "其他": "other",
            "other": "other",
            "rp": "rp",
            "角色扮演": "rp",
        }
        resolved_kind = kind_aliases.get(parts[0].lower())
        if resolved_kind:
            kind = resolved_kind
            parts = parts[1:]

    limit = RISINGSTONES_DEFAULT_LIMIT
    if parts and parts[-1].isdigit():
        limit = max(1, min(int(parts.pop()), RISINGSTONES_MAX_LIMIT))
    keyword = " ".join(parts).strip() or None
    return RisingstonesRecruitQuery(kind=kind, keyword=keyword, page=1, limit=limit)


def risingstones_recruit_list_spec(kind: str) -> tuple[str, str | None]:
    specs = {
        "party": ("/home/recruit/recruitFbList", "fb_name"),
        "beginner": ("/home/recruit/recruitNeList", None),
        "other": ("/home/recruit/recruitOtherList", None),
        "rp": ("/home/recruit/recruitRpList", "rp_name"),
    }
    return specs[kind]


async def fetch_risingstones_recruits(query: RisingstonesRecruitQuery) -> list[dict]:
    """Fetch one public Rising Stones recruitment list with browser headers."""
    endpoint, keyword_field = risingstones_recruit_list_spec(query.kind)
    params: dict[str, str | int] = {"page": query.page, "limit": query.limit}
    if keyword_field and query.keyword:
        params[keyword_field] = query.keyword
    response = await aiohttp_request(
        "GET",
        f"{RISINGSTONES_API_BASE}{endpoint}?{urlencode(params)}",
        timeout_seconds=20,
        headers={
            "Accept": "application/json, text/plain, */*",
            "Referer": RISINGSTONES_WEB_BASE,
        },
    )
    if response.status != 200 or not isinstance(response.payload, dict):
        raise RuntimeError(f"石之家招募请求失败: HTTP {response.status}")
    payload = response.payload
    if payload.get("code") not in {0, 10000}:
        raise RuntimeError(
            f"石之家招募接口返回异常: {payload.get('msg') or '未知错误'}"
        )
    data = payload.get("data")
    rows = data.get("rows") if isinstance(data, dict) else None
    result = (
        [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    )
    if query.keyword and not keyword_field:
        keyword = query.keyword.lower()
        result = [
            row
            for row in result
            if keyword
            in " ".join(
                str(row.get(field) or "")
                for field in ("title", "detail_mask", "profile", "category_name")
            ).lower()
        ]
    return result


def risingstones_recruit_url(kind: str, recruit_id: object) -> str:
    route = {
        "party": "party",
        "beginner": "beginner",
        "other": "others",
        "rp": "roleplay",
    }[kind]
    return f"{RISINGSTONES_WEB_BASE}#/recruit/{route}?id={quote(str(recruit_id or ''))}"


def risingstones_recruit_text(kind: str, row: dict) -> tuple[str, str, str]:
    if kind == "party":
        title = str(row.get("fb_name") or "未命名副本招募").strip()
        category = str(row.get("fb_type") or "副本招募").strip()
        job_values = [
            str(item.get("value")).strip()
            for item in row.get("jobInfo", [])
            if isinstance(item, dict) and item.get("value")
        ]
        detail = " | ".join(
            text
            for text in (
                str(row.get("fb_time") or "").strip(),
                str(row.get("progress") or "").strip(),
                str(row.get("strategy") or "").strip(),
                f"需求：{'、'.join(job_values)}" if job_values else "",
            )
            if text
        )
    elif kind == "beginner":
        title = str(row.get("title") or "未命名萌新招待").strip()
        styles = [
            str(item.get("name")).strip()
            for item in row.get("styleInfo", [])
            if isinstance(item, dict) and item.get("name")
        ]
        category = " / ".join(styles) or str(row.get("identity") or "萌新招待")
        detail = " | ".join(
            text
            for text in (
                str(row.get("weekday_time") or "").strip(),
                str(row.get("weekend_time") or "").strip(),
                strip_html(str(row.get("detail_mask") or "")),
            )
            if text
        )
    elif kind == "other":
        title = str(row.get("title") or "未命名其他招募").strip()
        category = str(row.get("category_name") or "其他招募").strip()
        detail = strip_html(str(row.get("detail_mask") or ""))
    else:
        title = str(row.get("rp_name") or "未命名 RP 招募").strip()
        category = str(row.get("rp_type") or "RP 招募").strip()
        detail = " | ".join(
            text
            for text in (
                str(row.get("open_time") or "").strip(),
                strip_html(str(row.get("profile") or "")),
            )
            if text
        )
    return title, category, detail


def format_risingstones_recruits(
    query: RisingstonesRecruitQuery, rows: list[dict]
) -> str:
    """Format public Rising Stones recruitment results for a chat response."""
    kind_labels = {"party": "副本", "beginner": "萌新", "other": "其他", "rp": "RP"}
    keyword_label = f" 关键词：{query.keyword}" if query.keyword else ""
    lines = [
        f"【石之家招募】类型：{kind_labels[query.kind]}{keyword_label} 数量：{len(rows)}"
    ]
    for index, row in enumerate(rows[: query.limit], start=1):
        title, category, detail = risingstones_recruit_text(query.kind, row)
        author = str(row.get("character_name") or "未知发布者").strip()
        server = "@".join(
            part
            for part in (
                str(row.get("area_name") or "").strip(),
                str(row.get("group_name") or "").strip(),
            )
            if part
        )
        updated_at = str(
            row.get("updated_at") or row.get("sort_updated_time") or ""
        ).strip()
        lines.extend(
            [
                f"\n{index:02d}. [{category}] {title}",
                f"{author}{f' @ {server}' if server else ''}",
                detail[:500] if detail else "暂无说明",
                updated_at,
                risingstones_recruit_url(query.kind, row.get("id")),
            ]
        )
    return "\n".join(lines)


def normalize_risingstones_cookie(value: str) -> str:
    """Keep only the session cookie required by the Rising Stones API."""
    text = value.strip()
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()
    for item in text.split(";"):
        candidate = item.strip()
        if candidate.startswith("ff14risingstones="):
            return candidate
    return ""


def parse_risingstones_binding(value: str) -> RisingstonesCredentials | None:
    """Parse a legacy cookie/UA pair or Chrome's copied cURL request."""
    curl_credentials = parse_risingstones_curl_binding(value)
    if curl_credentials:
        return curl_credentials
    cookie_text, separator, user_agent = value.partition(RISINGSTONES_BINDING_SEPARATOR)
    cookie = normalize_risingstones_cookie(cookie_text)
    user_agent = user_agent.strip() if separator else ""
    if not cookie or not user_agent:
        return None
    return RisingstonesCredentials(cookie=cookie, user_agent=user_agent)


def parse_risingstones_curl_binding(value: str) -> RisingstonesCredentials | None:
    """Extract cookie and UA from Chrome DevTools' Copy as cURL output."""
    # Chrome's Windows command format escapes shell-sensitive characters with
    # carets and puts the Cookie in `-b` instead of a request header.
    normalized = re.sub(r"\^([\"%&])", r"\1", value)
    headers = re.findall(r"(?:-H|--header)\s+['\"]([^'\"]+)['\"]", normalized)
    values: dict[str, str] = {}
    for header in headers:
        name, separator, header_value = header.partition(":")
        if separator:
            values[name.strip().lower()] = header_value.strip()
    cookie_match = re.search(r"(?:-b|--cookie)\s+['\"]([^'\"]+)['\"]", normalized)
    cookie_value = values.get("cookie", "")
    if cookie_match:
        cookie_value = cookie_match.group(1)
    cookie_value = cookie_value.replace("^", "")
    cookie = normalize_risingstones_cookie(cookie_value)
    user_agent = values.get("user-agent", "").strip()
    if not cookie or not user_agent:
        return None
    return RisingstonesCredentials(cookie=cookie, user_agent=user_agent)


def risingstones_binding_guide() -> str:
    return (
        "石之家 Cookie 不允许网页脚本直接读取，因此请按以下步骤获取登录信息：\n"
        "1. 保持石之家网页登录，按 F12 打开开发者工具并切到 Console。\n"
        "2. 粘贴并运行下方脚本，再切到 Network。\n"
        "3. 筛选 `getUserInfo`，右键该请求，选择 Copy > Copy as cURL (bash)。\n"
        "4. 将完整 cURL 内容私聊发送：石之家 绑定 <完整 cURL>\n\n"
        "```javascript\n"
        f"{RISINGSTONES_CONSOLE_SCRIPT}\n"
        "```\n"
        "如果 Chrome 阻止粘贴，请先在 Console 输入 allow pasting。"
    )


def configured_risingstones_credentials(
    config: dict | None,
) -> RisingstonesCredentials | None:
    """Read operator credentials from the full Chrome cURL configuration."""
    config = config or {}
    curl_value = str(config.get("risingstones_owner_curl", "") or "").strip()
    if curl_value:
        if "getUserInfo" not in curl_value:
            return None
        return parse_risingstones_curl_binding(curl_value)

    # Keep legacy paired values functional for existing installations. New
    # configuration uses `risingstones_owner_curl` exclusively.
    cookie = normalize_risingstones_cookie(
        str(config.get("risingstones_cookie", "") or "")
    )
    user_agent = str(config.get("risingstones_user_agent", "") or "").strip()
    if not cookie or not user_agent:
        return None
    return RisingstonesCredentials(cookie=cookie, user_agent=user_agent)


def risingstones_proxy_url() -> str | None:
    """Convert the shared proxy configuration into curl_cffi's proxy URL."""
    options = proxy_request_options()
    proxy_url = options.get("proxy")
    proxy_auth = options.get("proxy_auth")
    if not proxy_url:
        return None
    if not proxy_auth:
        return str(proxy_url)
    parsed = urlsplit(str(proxy_url))
    username = quote(str(proxy_auth.login), safe="")
    password = quote(str(proxy_auth.password), safe="")
    return urlunsplit(
        (parsed.scheme, f"{username}:{password}@{parsed.netloc}", parsed.path, "", "")
    )


def risingstones_account_key(event: AstrMessageEvent) -> str | None:
    platform_id = str(event.get_platform_id() or "").strip()
    sender_id = str(event.get_sender_id() or "").strip()
    if not platform_id or not sender_id:
        return None
    return f"{platform_id}:{sender_id}"


def is_risingstones_private_event(event: AstrMessageEvent) -> bool:
    checker = getattr(event, "is_private_chat", None)
    return bool(checker and checker())


async def risingstones_account_request(
    credentials: RisingstonesCredentials,
    method: str,
    endpoint: str,
    *,
    params: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
) -> dict:
    """Call a private API with the login UA and Chrome-like TLS fingerprint."""
    proxy_url = risingstones_proxy_url()
    request_id = next(HTTP_REQUEST_COUNTER)
    debug_log(
        "risingstones.request.start",
        proxy_used=bool(proxy_url),
        request_id=request_id,
        method=method.upper(),
        endpoint=endpoint,
        has_body=bool(data),
    )
    request_kwargs = {
        "params": params,
        "data": data,
        "timeout": 20,
    }
    if proxy_url:
        request_kwargs["proxy"] = proxy_url
    try:
        async with curl_requests.AsyncSession(
            impersonate=RISINGSTONES_IMPERSONATE,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": RISINGSTONES_WEB_BASE,
                "User-Agent": credentials.user_agent,
                "Cookie": credentials.cookie,
            },
        ) as session:
            response = await session.request(
                method, f"{RISINGSTONES_API_BASE}{endpoint}", **request_kwargs
            )
    except Exception as exc:
        debug_log(
            "risingstones.request.error",
            proxy_used=bool(proxy_url),
            request_id=request_id,
            error_type=type(exc).__name__,
        )
        raise RuntimeError("石之家请求连接失败") from exc
    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError(f"石之家响应解析失败: HTTP {response.status_code}") from exc
    debug_log(
        "risingstones.request.finish",
        proxy_used=bool(proxy_url),
        request_id=request_id,
        status=response.status_code,
    )
    if response.status_code != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"石之家请求失败: HTTP {response.status_code}")
    if payload.get("code") not in {0, 10000, 10001}:
        message = str(payload.get("msg") or "未知错误")
        if (
            payload.get("code") == 10403
            or "登录" in message
            or "token" in message.lower()
        ):
            raise RuntimeError("石之家凭据已失效，请在私聊中重新绑定")
        raise RuntimeError(f"石之家接口返回异常: {message}")
    return payload


async def risingstones_verify_credential(credentials: RisingstonesCredentials) -> dict:
    payload = await risingstones_account_request(
        credentials, "GET", "/home/userInfo/getUserInfo"
    )
    data = payload.get("data")
    if not isinstance(data, dict) or not data.get("character_name"):
        raise RuntimeError("石之家凭据有效，但账号未绑定角色")
    return data


async def risingstones_checkin(
    credentials: RisingstonesCredentials,
) -> tuple[bool, str]:
    tempsuid = str(uuid.uuid4())
    payload = await risingstones_account_request(
        credentials,
        "POST",
        "/home/sign/signIn",
        params={"tempsuid": tempsuid},
        data={"tempsuid": tempsuid},
    )
    if payload.get("code") == 10000:
        return True, str(payload.get("msg") or "签到成功")
    return True, str(payload.get("msg") or "今日已签到")


def format_risingstones_profile(data: dict) -> str:
    """Format the currently bound Rising Stones character profile."""
    character_name = str(data.get("character_name") or "未知角色").strip()
    server = "@".join(
        part
        for part in (
            str(data.get("area_name") or "").strip(),
            str(data.get("group_name") or "").strip(),
        )
        if part
    )
    details = data.get("characterDetail")
    detail = details[0] if isinstance(details, list) and details else {}
    follows = (
        data.get("followFansiNum")
        if isinstance(data.get("followFansiNum"), dict)
        else {}
    )
    lines = [f"【石之家档案】{character_name}{f' @ {server}' if server else ''}"]
    metrics = (
        ("经验", data.get("experience")),
        ("关注", follows.get("followNum")),
        ("粉丝", follows.get("fansNum")),
        ("获赞", data.get("beLikedNum")),
    )
    lines.extend(
        f"{label}：{value}" for label, value in metrics if value not in {None, ""}
    )
    for label, field in (
        ("创建时间", "create_time"),
        ("上次登录", "last_login_time"),
        ("游戏时长", "play_time"),
    ):
        value = detail.get(field) if isinstance(detail, dict) else None
        if value not in {None, ""}:
            lines.append(f"{label}：{value}")
    return "\n".join(lines)


RISINGSTONES_NOTIFICATION_FIELDS = (
    ("系统消息", "sysNum"),
    ("@ 我的", "atMsgNum"),
    ("评论", "commentMsgNum"),
    ("赞和收藏", "beLikedMsgNum"),
    ("我的招募", "recruitTip"),
    ("副本招募", "recruitFbTip"),
    ("部队招待", "recruitGuildTip"),
    ("萌新招待", "recruitNeTip"),
    ("其他招募", "recruitOtherTip"),
    ("新粉丝", "newFensNum"),
)

RISINGSTONES_STAT_KIND_ALIASES = {
    "全部": "all",
    "all": "all",
    "战场": "frontline",
    "frontline": "frontline",
    "绝境": "ultimate",
    "ultimate": "ultimate",
    "钓鱼": "fishing",
    "fishing": "fishing",
    "零式": "savage",
    "savage": "savage",
    "幻化": "glamour",
    "glamour": "glamour",
    "蜃景": "occult",
    "occult": "occult",
    "深层": "deepdungeon",
    "深层迷宫": "deepdungeon",
    "deepdungeon": "deepdungeon",
}
RISINGSTONES_STAT_LABELS = {
    "frontline": "战场数据",
    "ultimate": "绝境战数据",
    "fishing": "钓鱼数据",
    "savage": "零式数据",
    "glamour": "幻化/武具投影数据",
    "occult": "蜃景幻界数据",
    "deepdungeon": "深层迷宫数据",
}


def parse_risingstones_stat_kind(value: str) -> str:
    return RISINGSTONES_STAT_KIND_ALIASES.get(value.strip().lower(), "all")


def risingstones_first_data_row(data: object) -> dict | None:
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        return next((item for item in data if isinstance(item, dict)), None)
    return None


def risingstones_stat_lines(kind: str, data: object) -> list[str]:
    """Normalize the verified statistic summary fields without inventing values."""
    row = risingstones_first_data_row(data)
    if kind == "ultimate" and isinstance(data, list):
        lines = []
        for item in data:
            if not isinstance(item, dict):
                continue
            territory = str(
                item.get("territory_name") or item.get("territory_type") or "绝境战"
            )
            clear_times = item.get("clear_times")
            dead_times = item.get("dead_times")
            job_name = item.get("job_name")
            parts = []
            if clear_times not in {None, ""}:
                parts.append(f"通关：{clear_times}")
            if dead_times not in {None, ""}:
                parts.append(f"倒地：{dead_times}")
            if job_name:
                parts.append(str(job_name))
            if parts:
                lines.append(f"{territory}：{' | '.join(parts)}")
        return lines
    if not row:
        return []

    fields = {
        "frontline": (
            ("参战次数", "fight_times", "次"),
            ("胜利次数", "win_times", "次"),
            ("胜率", "win_rate", "%"),
            ("KDA", "kda", ""),
            ("击倒数", "kill_times", "次"),
            ("助攻数", "assist_times", "次"),
            ("倒地数", "dead_times", "次"),
        ),
        "fishing": (
            ("钓鱼次数", "total_times", "次"),
            ("成功率", "succ_rate", "%"),
            ("出海次数", "sea_times", "次"),
            ("最高出海分数", "max_sea_score", "分"),
        ),
        "savage": (
            ("已记录副本数", "territory_num", "个"),
            ("进入次数", "enter_num", "次"),
            ("完成次数", "finish_times", "次"),
            ("累计耗时", "elapsed_time", "分钟"),
        ),
        "glamour": (
            ("使用幻想药次数", "washing_num", "次"),
            ("已套装幻影化数量", "set_num", "套"),
            ("装备使用染剂数", "color_times", "种"),
            ("武具投影次数", "vanity_times", "次"),
        ),
        "occult": (
            ("当前等级", "now_level", "级"),
            ("FATE 完成次数", "fate_times", "次"),
            ("CE 完成次数", "ce_times", "次"),
        ),
        "deepdungeon": (
            ("通关次数", "clear_times", "次"),
            ("魔器武器等级", "weapon_level", ""),
            ("魔器防具等级", "armor_level", ""),
            ("强化等级", "enchantedLevel", ""),
        ),
    }
    return [
        f"{label}：{row[field]}{unit}"
        for label, field, unit in fields.get(kind, ())
        if row.get(field) not in {None, ""}
    ]


async def risingstones_statistics(
    credentials: RisingstonesCredentials, kind: str
) -> dict[str, list[str]]:
    endpoints = {
        "frontline": ("/home/dataCenter/frontline1TotalNew", None),
        "ultimate": ("/home/dataCenter/gaoNanFirst1", None),
        "fishing": ("/home/dataCenter/fishTotal1", None),
        "savage": ("/home/dataCenter/getLingShiTotal", None),
        "glamour": ("/home/dataCenter/getDressTotal7", None),
        "occult": ("/home/dataCenter/getMKDTotal1", None),
        "deepdungeon": ("/home/dataCenter/getDDTerr1", {"dd_type": "dd4"}),
    }
    kinds = list(endpoints) if kind == "all" else [kind]
    result: dict[str, list[str]] = {}
    for current_kind in kinds:
        endpoint, params = endpoints[current_kind]
        try:
            payload = await risingstones_account_request(
                credentials, "GET", endpoint, params=params
            )
        except RuntimeError:
            if kind != "all":
                raise
            continue
        lines = risingstones_stat_lines(current_kind, payload.get("data"))
        if lines:
            result[current_kind] = lines
    return result


def format_risingstones_statistics(statistics: dict[str, list[str]]) -> str:
    lines = ["【石之家统计】"]
    for kind, values in statistics.items():
        lines.append(f"\n[{RISINGSTONES_STAT_LABELS[kind]}]")
        lines.extend(values)
    return "\n".join(lines)


def parse_risingstones_glamour_query(value: str) -> RisingstonesGlamourQuery:
    parts = value.split()
    mode = "list"
    if parts and parts[0] in {"详情", "detail"}:
        mode = "detail"
        parts = parts[1:]
    elif parts and parts[0] in {"装备", "equipment"}:
        mode = "equipment"
        parts = parts[1:]
    limit = RISINGSTONES_DEFAULT_LIMIT
    if mode != "detail" and parts and parts[-1].isdigit():
        limit = max(1, min(int(parts.pop()), RISINGSTONES_MAX_LIMIT))
    return RisingstonesGlamourQuery(
        mode=mode, value=" ".join(parts).strip() or None, limit=limit
    )


def risingstones_glamour_url(glamour_id: object) -> str:
    return f"{RISINGSTONES_WEB_BASE}#/glamour/detail/{quote(str(glamour_id or ''))}"


async def risingstones_glamour_detail(
    credentials: RisingstonesCredentials, row: dict
) -> dict:
    glamour_id = str(row.get("id") or "").strip()
    if not glamour_id.isdigit():
        return row
    try:
        payload = await risingstones_account_request(
            credentials,
            "GET",
            "/home/glamour/glamourDetail",
            params={"id": glamour_id},
        )
    except RuntimeError:
        return row
    data = payload.get("data")
    return data if isinstance(data, dict) else row


async def enrich_risingstones_glamour_rows(
    credentials: RisingstonesCredentials, rows: list[dict]
) -> list[dict]:
    """Fetch enough detail for each result's main image and equipment list."""
    semaphore = asyncio.Semaphore(4)

    async def fetch_detail(row: dict) -> dict:
        async with semaphore:
            return await risingstones_glamour_detail(credentials, row)

    return list(await asyncio.gather(*(fetch_detail(row) for row in rows)))


async def risingstones_glamour_rows(
    credentials: RisingstonesCredentials, query: RisingstonesGlamourQuery
) -> list[dict]:
    if query.mode == "detail":
        if not query.value or not query.value.isdigit():
            raise ValueError("幻化详情需要数字 ID")
        payload = await risingstones_account_request(
            credentials,
            "GET",
            "/home/glamour/glamourDetail",
            params={"id": query.value},
        )
        data = payload.get("data")
        return [data] if isinstance(data, dict) else []

    if query.mode == "equipment":
        if not query.value:
            raise ValueError("装备检索需要装备名称")
        equipment_payload = await risingstones_account_request(
            credentials,
            "GET",
            "/home/gameData/searchEquip",
            params={"page": "1", "limit": "10", "name": query.value},
        )
        equipment_data = equipment_payload.get("data")
        equipment_rows = (
            equipment_data.get("rows")
            if isinstance(equipment_data, dict)
            else equipment_data
        )
        equipment_id = next(
            (
                str(item.get("id") or item.get("equipment_id"))
                for item in equipment_rows or []
                if isinstance(item, dict)
                and (item.get("id") or item.get("equipment_id"))
            ),
            None,
        )
        if not equipment_id:
            return []
        payload = await risingstones_account_request(
            credentials,
            "GET",
            "/common/search",
            params={
                "type": "7",
                "keywords": equipment_id,
                "searchByEquipment": "1",
                "page": "1",
                "limit": str(query.limit),
            },
        )
    elif query.value:
        payload = await risingstones_account_request(
            credentials,
            "GET",
            "/common/search",
            params={
                "type": "7",
                "keywords": query.value,
                "page": "1",
                "limit": str(query.limit),
            },
        )
    else:
        payload = await risingstones_account_request(
            credentials,
            "GET",
            "/home/glamour/glamoursList",
            params={"page": "1", "limit": str(query.limit)},
        )
    data = payload.get("data")
    rows = data.get("rows") if isinstance(data, dict) else None
    result = (
        [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    )
    return await enrich_risingstones_glamour_rows(credentials, result)


RISINGSTONES_EQUIPMENT_SLOT_LABELS = {
    "MAIN_HAND": "主手",
    "OFF_HAND": "副手",
    "HEAD": "头部",
    "BODY": "上衣",
    "GLOVES": "手部",
    "LEGS": "腿部",
    "FEET": "脚部",
    "EARRINGS": "耳坠",
    "NECKLACE": "项链",
    "BRACELETS": "手镯",
    "LEFT_RING": "左戒指",
    "RIGHT_RING": "右戒指",
}


def risingstones_glamour_main_image(row: dict) -> str | None:
    """Return the detail page's main image, falling back to its image list."""
    main_image = str(row.get("main_image") or "").strip()
    if main_image.startswith(("http://", "https://")):
        return main_image

    images = row.get("images")
    if isinstance(images, str):
        try:
            images = json.loads(images)
        except json.JSONDecodeError:
            images = []
    if isinstance(images, list):
        for image in images:
            image_url = str(image or "").strip()
            if image_url.startswith(("http://", "https://")):
                return image_url
    return None


def risingstones_glamour_equipment_lines(row: dict) -> list[str]:
    equipments = row.get("equipments")
    if not isinstance(equipments, list):
        return []

    lines = []
    for equipment in equipments:
        if not isinstance(equipment, dict):
            continue
        name = str(equipment.get("name") or "").strip()
        if not name:
            continue
        slot = str(equipment.get("slot") or "").strip()
        label = RISINGSTONES_EQUIPMENT_SLOT_LABELS.get(slot, slot or "装备")
        dyes = equipment.get("dyes")
        dye_names = (
            [
                str(dye.get("name") or "").strip()
                for dye in dyes
                if isinstance(dye, dict) and str(dye.get("name") or "").strip()
            ]
            if isinstance(dyes, list)
            else []
        )
        dye_text = f"（{' / '.join(dye_names)}）" if dye_names else ""
        lines.append(f"{label}：{name}{dye_text}")
    return lines


def format_risingstones_glamour_message(
    query: RisingstonesGlamourQuery, row: dict, index: int, total: int
) -> RisingstonesGlamourMessage:
    labels = {"list": "投稿", "equipment": "装备检索", "detail": "投稿详情"}
    title = str(row.get("title") or "未命名幻化").strip()
    author = str(
        row.get("character_name")
        or (row.get("userInfo") or {}).get("character_name")
        or row.get("nickname")
        or "未知投稿者"
    ).strip()
    server = "@".join(
        part
        for part in (
            str(
                row.get("area_name")
                or (row.get("userInfo") or {}).get("area_name")
                or ""
            ).strip(),
            str(
                row.get("group_name")
                or (row.get("userInfo") or {}).get("group_name")
                or ""
            ).strip(),
        )
        if part
    )
    description = strip_html(str(row.get("desc") or row.get("description") or ""))
    metrics = []
    for label, field in (("点赞", "likes"), ("收藏", "favorites")):
        number = risingstones_number(row.get(field))
        if number is not None:
            metrics.append(f"{label}：{number}")
    equipment_lines = risingstones_glamour_equipment_lines(row)
    lines = [
        f"【石之家幻化】{labels[query.mode]} {index}/{total}",
        title,
        f"{author}{f' @ {server}' if server else ''}",
    ]
    if description:
        lines.append(description[:500])
    if equipment_lines:
        lines.extend(["装备：", *equipment_lines])
    if metrics:
        lines.append(" | ".join(metrics))
    lines.append(risingstones_glamour_url(row.get("id")))
    return RisingstonesGlamourMessage(
        text="\n".join(lines), image_url=risingstones_glamour_main_image(row)
    )


def format_risingstones_glamour(
    query: RisingstonesGlamourQuery, rows: list[dict]
) -> str:
    labels = {"list": "投稿", "equipment": "装备检索", "detail": "投稿详情"}
    lines = [f"【石之家幻化】{labels[query.mode]} 数量：{len(rows)}"]
    for index, row in enumerate(rows[: query.limit], start=1):
        title = str(row.get("title") or "未命名幻化").strip()
        author = str(
            row.get("character_name")
            or (row.get("userInfo") or {}).get("character_name")
            or row.get("nickname")
            or "未知投稿者"
        ).strip()
        server = "@".join(
            part
            for part in (
                str(
                    row.get("area_name")
                    or (row.get("userInfo") or {}).get("area_name")
                    or ""
                ).strip(),
                str(
                    row.get("group_name")
                    or (row.get("userInfo") or {}).get("group_name")
                    or ""
                ).strip(),
            )
            if part
        )
        description = strip_html(str(row.get("desc") or row.get("description") or ""))
        metrics = []
        for label, field in (("点赞", "likes"), ("收藏", "favorites")):
            number = risingstones_number(row.get(field))
            if number is not None:
                metrics.append(f"{label}：{number}")
        lines.extend(
            [
                f"\n{index:02d}. {title}",
                f"{author}{f' @ {server}' if server else ''}",
                description[:500] if description else "暂无说明",
                " | ".join(metrics) if metrics else "",
                risingstones_glamour_url(row.get("id")),
            ]
        )
    return "\n".join(line for line in lines if line != "")


def parse_risingstones_guild_query(value: str) -> RisingstonesGuildQuery:
    parts = value.split()
    mode = "list"
    if parts and parts[0] in {"详情", "detail"}:
        mode = "detail"
        parts = parts[1:]
    limit = RISINGSTONES_DEFAULT_LIMIT
    if mode != "detail" and parts and parts[-1].isdigit():
        limit = max(1, min(int(parts.pop()), RISINGSTONES_MAX_LIMIT))
    return RisingstonesGuildQuery(
        mode=mode, value=" ".join(parts).strip() or None, limit=limit
    )


async def risingstones_guild_rows(
    credentials: RisingstonesCredentials, query: RisingstonesGuildQuery
) -> list[dict]:
    if query.mode == "detail":
        if not query.value or not query.value.isdigit():
            raise ValueError("部队详情需要数字 ID")
        payload = await risingstones_account_request(
            credentials,
            "GET",
            "/home/recruit/getRecruitGuildDetail",
            params={"id": query.value},
        )
        data = payload.get("data")
        return [data] if isinstance(data, dict) else []

    params = {"page": "1", "limit": str(query.limit)}
    if query.value:
        params["guild_name"] = query.value
    payload = await risingstones_account_request(
        credentials, "GET", "/home/recruit/recruitGuildList", params=params
    )
    data = payload.get("data")
    rows = data.get("rows") if isinstance(data, dict) else None
    return (
        [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    )


def risingstones_guild_url(guild_id: object) -> str:
    return f"{RISINGSTONES_WEB_BASE}#/recruit/guild/detail/{quote(str(guild_id or ''))}"


def format_risingstones_guilds(query: RisingstonesGuildQuery, rows: list[dict]) -> str:
    label = "详情" if query.mode == "detail" else "招待"
    lines = [f"【石之家部队{label}】数量：{len(rows)}"]
    for index, row in enumerate(rows[: query.limit], start=1):
        title = str(row.get("guild_name") or row.get("title") or "未命名部队").strip()
        author = str(row.get("character_name") or "未知发布者").strip()
        server = "@".join(
            part
            for part in (
                str(row.get("area_name") or "").strip(),
                str(row.get("group_name") or "").strip(),
            )
            if part
        )
        target_server = "@".join(
            part
            for part in (
                str(row.get("target_area_name") or "").strip(),
                str(row.get("target_group_name") or "").strip(),
            )
            if part
        )
        labels = [
            str(item.get("name")).strip()
            for item in row.get("labelInfo", [])
            if isinstance(item, dict) and item.get("name")
        ]
        schedule = " / ".join(
            text
            for text in (
                str(row.get("weekday_time") or "").strip(),
                str(row.get("weekend_time") or "").strip(),
            )
            if text
        )
        description = strip_html(
            str(row.get("detail_mask") or row.get("guild_address") or "")
        )
        details = [
            f"目标：{target_server}" if target_server else "",
            f"标签：{'、'.join(labels)}" if labels else "",
            f"活跃人数：{row['active_member_num']}"
            if row.get("active_member_num")
            else "",
            schedule,
        ]
        lines.extend(
            [
                f"\n{index:02d}. {title}",
                f"{author}{f' @ {server}' if server else ''}",
                " | ".join(detail for detail in details if detail),
                description[:500] if description else "暂无说明",
                risingstones_guild_url(row.get("id")),
            ]
        )
    return "\n".join(lines)


def format_risingstones_notifications(data: dict) -> str:
    """Format known unread counters while retaining unknown values safely."""
    lines = ["【石之家通知】"]
    for label, field in RISINGSTONES_NOTIFICATION_FIELDS:
        number = risingstones_number(data.get(field)) or 0
        lines.append(f"{label}：{number}")
    return "\n".join(lines)


def normalize_party_category(value: str | None) -> str | None:
    if not value:
        return None
    key = re.sub(r"\s+", "", value.strip().lower())
    return PARTY_CATEGORY_ALIASES.get(key)


def normalize_party_job(value: str | None) -> int | None:
    if not value:
        return None
    key = re.sub(r"\s+", "", value.strip().lower())
    return PARTY_JOB_ALIASES.get(key)


def normalize_party_duty_key(value: str | None) -> str:
    return re.sub(r"\s+", "", (value or "").strip().lower())


async def resolve_party_duty_ids(search_text: str | None) -> list[int]:
    key = normalize_party_duty_key(search_text)
    if not key:
        return []
    if key in PARTY_DUTY_ID_CACHE:
        return PARTY_DUTY_ID_CACHE[key]
    if key in PARTY_DUTY_ALIAS_IDS:
        PARTY_DUTY_ID_CACHE[key] = PARTY_DUTY_ALIAS_IDS[key]
        return PARTY_DUTY_ID_CACHE[key]

    query = f'Name~"{search_text}"'
    params = urlencode(
        {
            "query": query,
            "sheets": "ContentFinderCondition",
            "fields": "Name,ContentType.Name",
            "language": "chs",
            "limit": 20,
        }
    )
    try:
        payload = await aiohttp_get(
            f"{XIVAPI_BASE_URL}/search?{params}",
            timeout_seconds=20,
            use_api_user_agent=True,
        )
    except Exception as exc:
        logger.warning(f"招募副本名动态解析失败: {exc}")
        return []

    results = payload.get("results") if isinstance(payload, dict) else None
    duty_ids: list[int] = []
    if isinstance(results, list):
        for row in results:
            if not isinstance(row, dict):
                continue
            row_id = row.get("row_id")
            name = xivapi_field_text(row, "Name")
            if not row_id or not name:
                continue
            if key in normalize_party_duty_key(name):
                duty_ids.append(int(row_id))

    PARTY_DUTY_ID_CACHE[key] = duty_ids
    return duty_ids


def parse_party_finder_query(query: str) -> PartyFinderQuery:
    parts = query.split()
    if not parts:
        return PartyFinderQuery(None, None, [], [], 10)

    data_centre = parts[0] if parts[0] in DATA_CENTRES else None
    remain_parts = parts[1:] if data_centre else parts
    category = None
    search_terms = []
    job_ids = []
    limit = 10
    for part in remain_parts:
        if part.isdigit():
            limit = max(1, min(int(part), 40))
            continue
        normalized_category = normalize_party_category(part)
        if normalized_category:
            category = normalized_category
            continue
        job_id = normalize_party_job(part)
        if job_id:
            if job_id not in job_ids:
                job_ids.append(job_id)
            continue
        search_terms.append(part)
    return PartyFinderQuery(data_centre, category, search_terms, job_ids, limit)


def is_hq_token(value: str) -> bool:
    return value.strip().lower() in {"hq", "高品质", "高品", "hq品"}


def truncate_text(text: str, length: int = 80) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= length:
        return text
    return text[: length - 1] + "…"


def clean_weibo_title(text: str) -> str:
    title = strip_html(text)
    title = re.sub(r"全文$", "", title).strip()
    title = re.sub(r"#(?:最终幻想14|FF14)#", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return truncate_text(title or "微博内容为空", 90)


def is_pinned_weibo_card(card: dict, mblog: dict) -> bool:
    title = mblog.get("title")
    title_text = title.get("text") if isinstance(title, dict) else ""
    return any(
        [
            mblog.get("isTop"),
            mblog.get("is_top"),
            card.get("is_top"),
            mblog.get("top"),
            title_text == "置顶",
            "置顶" in str(card.get("title", "") or card.get("desc", "")),
        ]
    )


def weibo_cookie_value(cookie: str | None, name: str) -> str:
    if not cookie:
        return ""
    for part in cookie.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value.strip()
    return ""


def get_weibo_headers(cookie: str | None = None, uid: str = WEIBO_UID) -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 26_5_1 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "CriOS/150.0.7871.34 Mobile/15E148 Safari/604.1"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": f"{WEIBO_MOBILE_BASE}/u/{uid}",
        "MWeibo-Pwa": "1",
        "X-Requested-With": "XMLHttpRequest",
    }
    if cookie:
        headers["Cookie"] = cookie
        xsrf_token = weibo_cookie_value(cookie, "XSRF-TOKEN")
        if xsrf_token:
            headers["X-XSRF-TOKEN"] = xsrf_token
    return headers


def get_weibo_web_headers(cookie: str | None = None, uid: str = WEIBO_UID) -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": f"{WEIBO_WEB_BASE}/u/{uid}",
        "X-Requested-With": "XMLHttpRequest",
    }
    if cookie:
        headers["Cookie"] = cookie
        xsrf_token = weibo_cookie_value(cookie, "XSRF-TOKEN")
        if xsrf_token:
            headers["X-XSRF-TOKEN"] = xsrf_token
    return headers


async def fetch_weibo_cards(
    cookie: str | None = None, uid: str = WEIBO_UID
) -> list[dict]:
    params = urlencode({"type": "uid", "value": uid, "containerid": f"107603{uid}"})
    url = f"{WEIBO_API_BASE}?{params}"
    response = await aiohttp_request(
        "GET",
        url,
        timeout_seconds=20,
        headers=get_weibo_headers(cookie, uid),
    )
    if response.status != 200:
        logger.warning(f"微博接口请求失败，状态码：{response.status}")
        return []
    payload = response.payload
    if payload is None:
        return []

    if not isinstance(payload, dict) or payload.get("ok") != 1:
        logger.warning(
            f"微博接口返回状态异常: ok={payload.get('ok') if isinstance(payload, dict) else None} "
            f"msg={payload.get('msg') if isinstance(payload, dict) else None}"
        )
        return []
    cards = payload.get("data", {}).get("cards")
    return cards if isinstance(cards, list) else []


async def fetch_weibo_web_statuses(
    cookie: str | None = None, uid: str = WEIBO_UID
) -> list[dict]:
    params = urlencode({"uid": uid, "page": 1, "feature": 0})
    url = f"{WEIBO_WEB_TIMELINE_API}?{params}"
    response = await aiohttp_request(
        "GET",
        url,
        timeout_seconds=20,
        headers=get_weibo_web_headers(cookie, uid),
    )
    if response.status != 200:
        logger.warning(f"微博网页端接口请求失败，状态码：{response.status}")
        return []
    payload = response.payload
    if payload is None:
        return []

    if not isinstance(payload, dict) or payload.get("ok") != 1:
        if isinstance(payload, dict) and "login.php" in str(payload.get("url", "")):
            logger.warning("微博网页端接口需要登录，Cookie 可能未配置或已失效")
        else:
            logger.warning(
                f"微博网页端接口返回状态异常: ok={payload.get('ok') if isinstance(payload, dict) else None} "
                f"msg={payload.get('msg') if isinstance(payload, dict) else None}"
            )
        return []
    data = payload.get("data", {})
    if not isinstance(data, dict):
        return []
    statuses = data.get("list") or data.get("statuses")
    return statuses if isinstance(statuses, list) else []


def extract_valid_weibo_mblogs(cards: list[dict]) -> list[dict]:
    result = []
    for card in cards:
        if not isinstance(card, dict) or card.get("card_type") != 9:
            continue
        mblog = card.get("mblog")
        if not isinstance(mblog, dict):
            continue
        if is_pinned_weibo_card(card, mblog):
            continue
        if not mblog.get("bid"):
            continue
        result.append(mblog)
    return result


def extract_valid_weibo_web_statuses(statuses: list[dict]) -> list[dict]:
    result = []
    for status in statuses:
        if not isinstance(status, dict):
            continue
        title = status.get("title")
        title_text = title.get("text") if isinstance(title, dict) else ""
        if any(
            [
                status.get("isTop"),
                status.get("is_top"),
                status.get("top"),
                title_text == "置顶",
            ]
        ):
            continue
        if not (status.get("mblogid") or status.get("bid")):
            continue
        result.append(status)
    return result


def format_weibo_time(value: str) -> str:
    if not value:
        return "未知时间"
    try:
        parsed = parsedate_to_datetime(value)
        return f"{parsed.year}/{parsed.month}/{parsed.day} {parsed:%H:%M:%S}"
    except (TypeError, ValueError, IndexError):
        return value


def format_weibo_status(index: int, status: dict, uid: str = WEIBO_UID) -> str:
    bid = str(status.get("bid") or status.get("mblogid"))
    title = clean_weibo_title(str(status.get("text_raw") or status.get("text") or ""))
    created_at = format_weibo_time(str(status.get("created_at") or ""))
    weibo_url = f"{WEIBO_WEB_BASE}/{uid}/{bid}"
    return f"【{index}】{title} {created_at}\n{weibo_url}"


async def get_ff_weibo_text(cookie: str | None = None, limit: int = 5) -> str:
    cards = await fetch_weibo_cards(cookie)
    statuses = []
    for mblog in extract_valid_weibo_mblogs(cards):
        statuses.append(mblog)
        if len(statuses) >= limit:
            break

    if len(statuses) < limit and cookie:
        web_statuses = await fetch_weibo_web_statuses(cookie)
        seen_ids = {str(item.get("bid") or item.get("mblogid")) for item in statuses}
        for status in extract_valid_weibo_web_statuses(web_statuses):
            status_id = str(status.get("bid") or status.get("mblogid"))
            if status_id in seen_ids:
                continue
            statuses.append(status)
            seen_ids.add(status_id)
            if len(statuses) >= limit:
                break

    if not statuses:
        if cookie:
            return "没有获取到最新微博，可能是微博 Cookie 已失效或接口结构变化，请在插件设置里更新 weibo_cookie。"
        return "没有获取到最新微博，微博接口需要配置有效 Cookie，请在插件设置里填写 weibo_cookie。"
    return "\n".join(
        format_weibo_status(index, item)
        for index, item in enumerate(statuses[:limit], start=1)
    )


def find_bili_url_in_text(text: str) -> str | None:
    normalized = html.unescape(text)
    normalized = normalized.replace("\\/", "/")
    normalized = normalized.replace("\\u002F", "/").replace("\\u002f", "/")
    match = re.search(
        r"https?://(?:www\.)?bilibili\.com/video/(?:BV[0-9A-Za-z]+|av\d+)[^'\"\\\s<>]*",
        normalized,
    )
    if not match:
        return None
    return match.group(0).rstrip("),，。；;]")


def find_bili_url_in_obj(value) -> str | None:
    if isinstance(value, str):
        return find_bili_url_in_text(value)
    if isinstance(value, dict):
        for item in value.values():
            result = find_bili_url_in_obj(item)
            if result:
                return result
    if isinstance(value, list):
        for item in value:
            result = find_bili_url_in_obj(item)
            if result:
                return result
    return None


def get_current_period() -> int:
    base = datetime(2018, 10, 16, 16, 0, 0)
    current = datetime.now()
    if current < base:
        raise ValueError("当前系统时间早于2018年10月16日，请修正系统时间")
    return (current - base).days // 7 + 37


async def get_bili_url() -> str:
    table_id = "DY2lCeEpwemZESm5q"
    sheet_id = "dewveu"
    headers = {
        "referer": f"https://docs.qq.com/sheet/{table_id}?tab={sheet_id}",
        "authority": "docs.qq.com",
        "accept": "*/*",
    }
    docs_url = (
        "https://docs.qq.com/dop-api/opendoc?"
        f"tab={sheet_id}&id={table_id}&outformat=1&normal=1"
    )
    docs_json = await safe_aiohttp_get(docs_url, "腾讯文档接口", headers=headers)
    if docs_json:
        result = find_bili_url_in_obj(docs_json)
        if result:
            logger.info(f"从腾讯文档接口获取暖暖视频链接: {result}")
            return result

    docs_text = await safe_aiohttp_get(
        docs_url, "腾讯文档文本", res_type="text", headers=headers
    )
    if docs_text:
        result = find_bili_url_in_text(docs_text)
        if result:
            logger.info(f"从腾讯文档文本获取暖暖视频链接: {result}")
            return result

    docs_page = await safe_aiohttp_get(
        QQ_DOC_URL, "腾讯文档页面", res_type="text", headers=headers
    )
    if docs_page:
        result = find_bili_url_in_text(docs_page)
        if result:
            logger.info(f"从腾讯文档页面获取暖暖视频链接: {result}")
            return result

    period = get_current_period()
    prefix = f"【FF14/时尚品鉴】第{period}期"

    search_url = (
        "https://api.bilibili.com/x/web-interface/search/type?"
        f"search_type=video&keyword={quote(prefix)}&page=1"
    )
    search_data = await safe_aiohttp_get(
        search_url,
        "bilibili搜索接口",
        headers={"referer": "https://search.bilibili.com/"},
    )
    if search_data and search_data.get("code") == 0:
        videos = search_data.get("data", {}).get("result", [])
        for video in videos:
            title = re.sub(r"<.*?>", "", str(video.get("title", "")))
            author = str(video.get("author", ""))
            bvid = video.get("bvid")
            if (
                title.startswith(prefix)
                and bvid
                and ("游玩C哩酱" in author or not author)
            ):
                result = f"https://www.bilibili.com/video/{bvid}"
                logger.info(f"从bilibili搜索获取暖暖视频链接: {result}")
                return result
    elif search_data:
        logger.warning(
            f"bilibili搜索接口返回异常: code={search_data.get('code')} message={search_data.get('message')}"
        )

    api_url = (
        f"https://api.bilibili.com/x/space/arc/search?mid={BILI_USER_ID}&ps=10&pn=1"
    )
    data = await safe_aiohttp_get(
        api_url,
        "bilibili空间接口",
        headers={"referer": f"https://space.bilibili.com/{BILI_USER_ID}"},
    )
    if data and data.get("code") == 0:
        videos = data.get("data", {}).get("list", {}).get("vlist", [])
        for video in videos:
            if str(video.get("title", "")).startswith(prefix):
                result = f"https://www.bilibili.com/video/{video['bvid']}"
                logger.info(f"从bilibili空间接口获取暖暖视频链接: {result}")
                return result
    elif data:
        logger.warning(
            f"bilibili空间接口返回异常: code={data.get('code')} message={data.get('message')}"
        )

    raise ValueError("找不到最新一期bilibili视频链接")


async def get_bili_detail(bili_url: str) -> str:
    page = await aiohttp_get(
        bili_url, res_type="text", headers={"referer": "https://www.bilibili.com/"}
    )
    if not page:
        raise ValueError("获取bilibili页面失败")

    match = re.search(r'<span class="desc-info-text".*?</span>', page, flags=re.S)
    if match:
        result = match.group(0).split("\n", 1)[-1].replace("</span>", "")
        return html.unescape(re.sub(r"<.*?>", "", result)).strip()

    json_match = re.search(r'"desc"\s*:\s*"((?:\\.|[^"\\])*)"', page)
    if json_match:
        return html.unescape(json.loads(f'"{json_match.group(1)}"')).strip()

    raise ValueError("解析bilibili视频简介失败")


async def fetch_dungeon_notes() -> dict[str, dict[str, str]]:
    page = await aiohttp_get(DUNGEON_NOTE_URL, res_type="text")
    if not page:
        raise ValueError("获取攻略列表失败")

    note_dict: dict[str, dict[str, str]] = {}
    matches = re.findall(r"/duty/.*?</a>", page, flags=re.S)
    for line in matches[:-3]:
        try:
            page_id = line.split(".htm", 1)[0].replace("/duty/", "")
            dungeon_level = line.split("[", 1)[1].split("]", 1)[0]
            dungeon_name = line.split("] ", 1)[1].split("\n", 1)[0]
        except IndexError:
            continue
        note_dict.setdefault(dungeon_level, {})[html.unescape(dungeon_name)] = page_id
    return note_dict


async def get_dungeon_note(dungeon_info: str) -> tuple[str, bool]:
    dungeon_level, dungeon_name, is_text = parse_dungeon_query(dungeon_info)
    if not dungeon_name:
        return (
            "查攻略格式：攻略 (副本等级) 副本名关键字 (文本)。括号内为可选参数，默认输出图片攻略。",
            True,
        )

    note_dict = await fetch_dungeon_notes()
    page_matches = []
    for level_info, dungeon_items in note_dict.items():
        for name, page_id in dungeon_items.items():
            if dungeon_name in name:
                page_matches.append([level_info, name, page_id])

    if not page_matches:
        return "副本名没搜到鸭", True

    if len(page_matches) > 1 and dungeon_level:
        filtered_matches = [item for item in page_matches if dungeon_level == item[0]]
        if filtered_matches:
            page_matches = filtered_matches

    if len(page_matches) > 1:
        result = "是哪个副本呢？重新告诉我哦~\n"
        for page_match in page_matches:
            result += page_match[0] + " " + page_match[1] + "、"
        return result[:-1], True

    detail_page = await aiohttp_get(
        f"{DUNGEON_NOTE_URL}/{page_matches[0][-1]}.htm", res_type="text"
    )
    if not detail_page:
        return "攻略详情获取失败，请稍后再试", True

    blocks = re.findall(r"<p>.*?</p>|<h\d.*?</h\d>", detail_page, flags=re.S)
    result_text = ""
    for block in blocks:
        stripped = strip_html(block)
        if stripped:
            result_text += stripped + "\n"

    if not result_text.strip():
        return "攻略详情为空，请稍后再试", True
    return result_text, is_text


def garland_url(item_category: str, item_id: str | int) -> str:
    return f"{GARLAND_BASE_URL}/db/doc/{item_category}/chs/3/{item_id}.json"


def strip_xiv_tags(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    return re.sub(r"<.*?>", "", text).strip()


async def garland_core_value(path: str):
    global GARLAND_CORE_DATA
    if GARLAND_CORE_DATA is None:
        GARLAND_CORE_DATA = await aiohttp_get(
            garland_url("core", "data"), use_api_user_agent=True
        )
    value = GARLAND_CORE_DATA
    for part in path.split("."):
        value = value[part]
    return value


def garland_partials(payload: dict) -> dict[tuple[str, str], dict]:
    result = {}
    for partial in payload.get("partials", []):
        result[(str(partial.get("type")), str(partial.get("id")))] = partial.get(
            "obj", {}
        )
    return result


async def search_xivapi_item_id(name: str) -> tuple[int | None, str | None]:
    query = f'Name~"{name}"'
    params = urlencode(
        {
            "query": query,
            "sheets": "Item",
            "fields": "Name",
            "language": "chs",
            "limit": 8,
        }
    )
    payload = await aiohttp_get(
        f"{XIVAPI_BASE_URL}/search?{params}", use_api_user_agent=True
    )
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return None, None

    candidates = []
    for row in results:
        if not isinstance(row, dict):
            continue
        row_id = row.get("row_id")
        item_name = xivapi_field_text(row, "Name")
        if not row_id or not item_name:
            continue
        candidates.append((int(row_id), item_name))

    if not candidates:
        return None, None
    for row_id, item_name in candidates:
        if item_name == name:
            return row_id, item_name
    return candidates[0]


async def download_garland_item_icon(item: dict, output_path: Path) -> bool:
    icon = item.get("icon")
    if not icon:
        return False
    icon_path = str(icon)
    if not icon_path.startswith("t/"):
        icon_path = "t/" + icon_path
    image = await aiohttp_get(
        f"{GARLAND_BASE_URL}/files/icons/item/{icon_path}.png",
        res_type="bytes",
    )
    if not image:
        return False
    output_path.write_bytes(image)
    return True


def format_garland_node(node: dict) -> str:
    node_type = NODE_NAME_BY_TYPE.get(int(node.get("t", 0)), "采集点")
    coord = node.get("c") or []
    coord_text = f"({coord[0]}, {coord[1]})" if len(coord) >= 2 else ""
    time_text = ""
    if node.get("ti"):
        time_text = " ET " + " ".join(f"{item}时" for item in node["ti"])
    return f"{node.get('n', '')} {node.get('l', '')}级{node_type} {coord_text}{time_text}".strip()


async def parse_item_garland(item_id: int) -> tuple[str, dict]:
    payload = await aiohttp_get(garland_url("item", item_id), use_api_user_agent=True)
    if not isinstance(payload, dict) or "item" not in payload:
        raise ValueError("Garland 物品详情为空")

    item = payload["item"]
    partials = garland_partials(payload)
    lines = [
        item.get("name", f"物品 {item_id}"),
        await garland_core_value(f"item.categoryIndex.{item.get('category', 0)}.name"),
        f"物品等级 {item.get('ilvl', 0)}",
    ]
    if item.get("elvl"):
        lines.append(f"装备等级 {item['elvl']}")
    if item.get("jobCategories"):
        lines.append(str(item["jobCategories"]))
    if item.get("description"):
        lines.append(strip_xiv_tags(item["description"]))

    source_count = 0
    if item.get("nodes"):
        lines.append("·采集")
        for node_id in item["nodes"][:5]:
            node = partials.get(("node", str(node_id)))
            if node:
                location_name = await garland_core_value(
                    f"locationIndex.{node.get('z')}.name"
                )
                lines.append(f"  -- {location_name} {format_garland_node(node)}")
                source_count += 1

    if item.get("fishingSpots"):
        lines.append("·钓鱼")
        for spot_id in item["fishingSpots"][:5]:
            spot = partials.get(("fishing", str(spot_id)))
            if spot:
                location_name = await garland_core_value(
                    f"locationIndex.{spot.get('z')}.name"
                )
                coord = (
                    f"({spot.get('x')}, {spot.get('y')})"
                    if spot.get("x") is not None
                    else ""
                )
                lines.append(
                    f"  -- {location_name} {spot.get('n', '')} {spot.get('l', '')}级 {coord}"
                )
                source_count += 1

    if item.get("craft"):
        jobs = await garland_core_value("jobs")
        lines.append("·制作")
        for craft in item["craft"][:3]:
            job = jobs[craft["job"]]["name"]
            lines.append(f"  -- {job} {craft.get('lvl', '')}级")
            ingredients = []
            for ingredient in craft.get("ingredients", [])[:6]:
                if ingredient.get("id", 0) < 20:
                    continue
                ingredient_item = partials.get(("item", str(ingredient["id"])), {})
                ingredients.append(
                    f"{ingredient_item.get('n', ingredient['id'])}*{ingredient.get('amount', 1)}"
                )
            if ingredients:
                lines.append("     " + "、".join(ingredients))
            source_count += 1

    if item.get("vendors"):
        lines.append(f"·商店贩售 {item.get('price', 0)}金币")
        for vendor_id in item["vendors"][:5]:
            vendor = partials.get(("npc", str(vendor_id)))
            if vendor:
                location = ""
                if vendor.get("l"):
                    location = await garland_core_value(
                        f"locationIndex.{vendor['l']}.name"
                    )
                coord = vendor.get("c") or []
                coord_text = f"({coord[0]}, {coord[1]})" if len(coord) >= 2 else ""
                lines.append(
                    f"  -- {vendor.get('n', '')} {location} {coord_text}".strip()
                )
                source_count += 1
        if len(item["vendors"]) > 5:
            lines.append(f"  -- 等共计{len(item['vendors'])}个商人售卖")

    trades = item.get("tradeCurrency", []) + item.get("tradeShops", [])
    if trades:
        lines.append("·兑换")
        for trade in trades[:3]:
            shop_name = (
                "商店交易" if trade.get("shop") == "Shop" else trade.get("shop", "兑换")
            )
            lines.append(f"  -- {shop_name}")
            for listing in trade.get("listings", [])[:2]:
                currencies = []
                for currency in listing.get("currency", [])[:3]:
                    currency_item = partials.get(("item", str(currency.get("id"))), {})
                    currencies.append(
                        f"{currency_item.get('n', currency.get('id'))}*{currency.get('amount', 1)}"
                    )
                if currencies:
                    lines.append("     使用 " + "、".join(currencies))
            source_count += 1

    if item.get("drops"):
        lines.append("·怪物掉落")
        for mob_id in item["drops"][:5]:
            mob = partials.get(("mob", str(mob_id)))
            if mob:
                location = await garland_core_value(
                    f"locationIndex.{mob.get('z')}.name"
                )
                lines.append(f"  -- {mob.get('n', '')} {location}")
                source_count += 1

    if item.get("instances"):
        lines.append("·副本获取")
        for duty_id in item["instances"][:5]:
            duty = partials.get(("instance", str(duty_id)))
            if duty:
                lines.append(f"  -- {duty.get('min_lvl', '')}级 {duty.get('n', '')}")
                source_count += 1

    if item.get("quests"):
        lines.append("·任务奖励")
        for quest_id in item["quests"][:5]:
            quest = partials.get(("quest", str(quest_id)))
            if quest:
                lines.append(f"  -- {quest.get('n', '')}")
                source_count += 1

    if source_count == 0:
        lines.append("获取方式较麻烦/没查到，烦请打开网页查看！")

    status = []
    if item.get("unique"):
        status.append("独占")
    if "tradeable" in item:
        status.append("可交易" if item.get("tradeable") else "不可交易")
    if "unlistable" in item:
        status.append(
            "不可在市场上交易" if item.get("unlistable") else "可在市场上交易"
        )
    if item.get("storable"):
        status.append("可放入收藏柜")
    if status:
        lines.append(" ".join(status))

    lines.append(f"{GARLAND_BASE_URL}/db/#item/{item_id}")
    return "\n".join(line for line in lines if str(line).strip()), item


async def create_item_info(item_name: str, cache_dir: Path) -> tuple[str, Path | None]:
    if item_name.isdigit():
        item_id = int(item_name)
    else:
        item_id, found_name = await search_xivapi_item_id(item_name)
        if not item_id:
            return f'在最终幻想XIV中没有找到"{item_name}"', None
        if found_name and found_name != item_name:
            logger.info(f"物品搜索 {item_name} 匹配到 {found_name} ({item_id})")

    text, item = await parse_item_garland(item_id)
    icon_path = cache_dir / f"item_{item_id}.png"
    try:
        has_icon = await download_garland_item_icon(item, icon_path)
    except Exception as exc:
        logger.warning(f"物品图标下载失败: {exc}")
        has_icon = False
    return text, icon_path if has_icon else None


async def parse_market_query(query: str) -> MarketQuery:
    parts = query.split()
    scope_name = MARKET_DEFAULT_SCOPE
    scope_type = "all"
    item_parts = []
    hq = False
    limit = MARKET_DEFAULT_LISTINGS
    try:
        worlds = await load_cn_world_names()
    except Exception as exc:
        logger.warning(f"物价服务器名解析失败: {exc}")
        worlds = {}

    for part in parts:
        normalized_scope = MARKET_SCOPE_ALIASES.get(part, part)
        if part.isdigit():
            limit = max(1, min(int(part), MARKET_MAX_LISTINGS))
            continue
        if is_hq_token(part):
            hq = True
            continue
        if normalized_scope in DATA_CENTRES:
            scope_name = normalized_scope
            scope_type = "dc"
            continue
        if normalized_scope in worlds:
            scope_name = normalized_scope
            scope_type = "world"
            continue
        item_parts.append(part)

    return MarketQuery(
        scope_name=scope_name,
        scope_type=scope_type,
        item_name=" ".join(item_parts).strip() or None,
        hq=hq,
        limit=limit,
    )


async def fetch_market_listings(
    location: str, item_id: int, fetch_limit: int
) -> tuple[dict | None, str]:
    params = urlencode({"listings": fetch_limit, "entries": 0})
    url = f"https://universalis.app/api/v2/{quote(location)}/{item_id}?{params}"
    payload = await aiohttp_get(url, use_api_user_agent=True)
    if not isinstance(payload, dict):
        return None, location
    return payload, location


def format_market_time(timestamp_ms) -> str:
    try:
        return datetime.fromtimestamp(int(timestamp_ms) / 1000).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except (TypeError, ValueError, OSError):
        return "未知"


async def create_market_text(query: MarketQuery) -> str:
    if not query.item_name:
        return "查物价格式：价格 (大区/服务器) 物品名 (HQ) (数量)\n例：价格 陆行鸟 铁矿 HQ 10"

    item_id, real_name = await search_xivapi_item_id(query.item_name)
    if not item_id:
        return f'所查询物品"{query.item_name}"不存在'

    locations = DATA_CENTRES if query.scope_type == "all" else [query.scope_name]
    fetch_limit = min(max(query.limit * (5 if query.hq else 2), 20), 100)
    results = await asyncio.gather(
        *(
            fetch_market_listings(location, item_id, fetch_limit)
            for location in locations
        ),
        return_exceptions=True,
    )

    listings = []
    upload_times = []
    errors = []
    for result in results:
        if isinstance(result, Exception):
            errors.append(str(result))
            continue
        payload, location = result
        if not payload:
            errors.append(f"{location} 无返回")
            continue
        if payload.get("lastUploadTime"):
            upload_times.append(payload.get("lastUploadTime"))
        for listing in payload.get("listings", []):
            if query.hq and not listing.get("hq"):
                continue
            item = dict(listing)
            item["_scope"] = location
            listings.append(item)

    listings.sort(
        key=lambda item: (
            (
                item.get("pricePerUnit")
                if item.get("pricePerUnit") is not None
                else 10**18
            ),
            item.get("total") if item.get("total") is not None else 10**18,
        )
    )
    listings = listings[: query.limit]

    hq_label = "HQ" if query.hq else "全部"
    lines = [
        f"【{real_name or query.item_name} 价格】范围：{query.scope_name}  品质：{hq_label}  数量：{len(listings)}"
    ]
    if not listings:
        lines.append(
            "未查询到数据，可能是物品不可交易、暂时无人上架或 Universalis 暂时不可用。"
        )
        if errors:
            lines.append("接口错误：" + "；".join(errors[:3]))
        return "\n".join(lines)

    for index, listing in enumerate(listings, start=1):
        price = listing.get("pricePerUnit", 0)
        quantity = listing.get("quantity", 0)
        total = listing.get("total", price * quantity if price and quantity else 0)
        quality = "HQ" if listing.get("hq") else "NQ"
        world = listing.get("worldName") or listing.get("_scope", "")
        retainer = listing.get("retainerName") or "未知雇员"
        lines.append(
            f"{index:02d}. {price:,} x{quantity} = {total:,} {quality} @ {world} / {retainer}"
        )

    if upload_times:
        lines.append("更新时间：" + format_market_time(max(upload_times)))
    return "\n".join(lines)


def parse_logs_query(query: str, default_cn_source: bool = True) -> LogsQuery:
    parts = query.split()
    cn_source = default_cn_source
    dps_type = "rdps"
    day = -1
    content_parts = []
    for part in parts:
        lower = part.lower()
        if lower in LOGS_SERVER_TOKENS:
            cn_source = LOGS_SERVER_TOKENS[lower]
            continue
        if lower in LOGS_DPS_TYPES:
            dps_type = lower
            continue
        if lower.startswith("day"):
            try:
                day = int(lower.replace("day", "", 1)) - 1
            except ValueError:
                day = -2
            continue
        content_parts.append(part)

    boss_name = content_parts[0] if len(content_parts) > 0 else None
    job_name = content_parts[1] if len(content_parts) > 1 else None
    for index in range(len(content_parts) - 1, -1, -1):
        if find_logs_job(content_parts[index]):
            job_name = content_parts[index]
            boss_parts = content_parts[:index] + content_parts[index + 1 :]
            boss_name = " ".join(boss_parts) if boss_parts else None
            break
    return LogsQuery(boss_name, job_name, cn_source, dps_type, day)


def normalize_logs_lookup(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", "", str(value).strip()).casefold()


def find_logs_job(job_name: str | None) -> dict | None:
    if not job_name:
        return None
    query = normalize_logs_lookup(job_name)
    for item in load_json_list(JOB_JSON):
        aliases = item.get("nickname", [])
        names = [
            normalize_logs_lookup(name)
            for name in [item.get("name"), item.get("cn_name"), *aliases]
        ]
        if query in names:
            return item
    return None


def find_logs_boss(boss_name: str | None) -> dict | None:
    if not boss_name:
        return None
    query = normalize_logs_lookup(boss_name)
    if not query:
        return None
    candidates: list[tuple[dict, list[str]]] = []
    for item in load_json_list(BOSS_JSON):
        aliases = item.get("nickname", [])
        names = [
            normalize_logs_lookup(name)
            for name in [item.get("name"), item.get("cn_name"), *aliases]
            if isinstance(name, str) and name.strip()
        ]
        candidates.append((item, names))
        if query in names:
            return item
    for item, names in candidates:
        if any(query in name or name in query for name in names):
            return item
    return None


def find_logs_boss_by_key(pk: int, difficulty: int) -> dict | None:
    for item in load_json_list(BOSS_JSON):
        try:
            if int(item.get("pk")) == pk and int(item.get("savage")) == difficulty:
                return item
        except (TypeError, ValueError):
            continue
    return None


def find_logs_boss_group(boss_name: str | None) -> list[dict]:
    query = normalize_logs_lookup(boss_name)
    if not query:
        return []
    for group in LOGS_BOSS_PHASE_GROUPS:
        aliases = [normalize_logs_lookup(alias) for alias in group["aliases"]]
        if not any(alias and (query == alias or alias in query) for alias in aliases):
            continue
        selected_index = None
        for phase_index, phase_aliases in enumerate(group["phase_aliases"]):
            if any(normalize_logs_lookup(alias) in query for alias in phase_aliases):
                selected_index = phase_index
                break
        members = group["members"]
        if selected_index is not None:
            members = [members[selected_index]]
        bosses = [find_logs_boss_by_key(pk, difficulty) for pk, difficulty in members]
        return [boss for boss in bosses if boss]
    return []


def find_logs_bosses(boss_name: str | None) -> list[dict]:
    group = find_logs_boss_group(boss_name)
    if group:
        return group
    boss = find_logs_boss(boss_name)
    return [boss] if boss else []


def fflogs_region_entries(boss: dict, cn_source: bool) -> list[str]:
    regions = [
        region
        for region in boss.get("cn_region" if cn_source else "region", [])
        if isinstance(region, str) and "###" in region
    ]
    selected = []
    index = 0
    while index < len(regions):
        label = regions[index].split("###", 1)[0]
        group = []
        while index < len(regions) and regions[index].split("###", 1)[0] == label:
            group.append(regions[index])
            index += 1
        if len(group) >= 3:
            selected.append(group[1] if cn_source else group[0])
        elif len(group) == 2:
            selected.append(group[1] if cn_source else group[0])
        elif group:
            selected.append(group[0])
    return selected


async def get_fflogs_token(host: str, client_id: str, client_secret: str) -> str:
    auth = aiohttp.BasicAuth(client_id, client_secret)
    response = await aiohttp_request(
        "POST",
        f"{host}/oauth/token",
        timeout_seconds=20,
        use_api_user_agent=True,
        data={"grant_type": "client_credentials"},
        auth=auth,
    )
    if response.status != 200:
        raise RuntimeError(f"FFLogs token 请求失败：{response.status}")
    payload = response.payload
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not token:
        raise RuntimeError("FFLogs token 响应缺少 access_token")
    return token


async def fflogs_graphql(host: str, token: str, query: str, variables: dict) -> dict:
    headers = api_request_headers(
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    )
    response = await aiohttp_request(
        "POST",
        f"{host}/api/v2/client",
        timeout_seconds=25,
        headers=headers,
        use_api_user_agent=True,
        json_data={"query": query, "variables": variables},
    )
    if response.status != 200:
        raise RuntimeError(f"FFLogs GraphQL 请求失败：{response.status}")
    payload = response.payload
    if isinstance(payload, dict) and payload.get("errors"):
        raise RuntimeError("FFLogs GraphQL 返回错误")
    return payload if isinstance(payload, dict) else {}


async def fetch_fflogs_metadata(host: str, token: str) -> dict:
    cached = FFLOGS_METADATA_CACHE.get(host)
    if cached:
        return cached
    payload = await fflogs_graphql(host, token, FFLOGS_METADATA_QUERY, {})
    metadata = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(metadata, dict):
        raise RuntimeError("FFLogs metadata 响应为空")
    FFLOGS_METADATA_CACHE[host] = metadata
    return metadata


def fflogs_zone_by_id(metadata: dict) -> dict[int, dict]:
    zones = metadata.get("worldData", {}).get("zones", [])
    return {
        int(zone["id"]): zone
        for zone in zones
        if isinstance(zone, dict) and zone.get("id") is not None
    }


def fflogs_encounters_by_id(zone: dict | None) -> dict[int, dict]:
    if not isinstance(zone, dict):
        return {}
    encounters = zone.get("encounters", [])
    return {
        int(encounter["id"]): encounter
        for encounter in encounters
        if isinstance(encounter, dict) and encounter.get("id") is not None
    }


def fflogs_metadata_regions(zone: dict | None) -> list[str]:
    if not isinstance(zone, dict):
        return []
    partitions = [
        part
        for part in zone.get("partitions", [])
        if isinstance(part, dict) and part.get("id") is not None
    ]
    selected = [
        part
        for part in partitions
        if isinstance(part.get("name"), str)
        and (
            ("Standard" in part["name"] and "Non-Standard" not in part["name"])
            or ("标准" in part["name"] and "非标准" not in part["name"])
        )
    ]
    if not selected:
        selected = [part for part in partitions if part.get("default")]
    if not selected:
        selected = partitions
    if not selected:
        return ["-1###1"]
    return [
        f"{part.get('name') or part['id']}###{int(part['id'])}" for part in selected
    ]


def fflogs_metadata_difficulty(zone: dict | None) -> int:
    if not isinstance(zone, dict):
        return 100
    ids = [
        int(item["id"])
        for item in zone.get("difficulties", [])
        if isinstance(item, dict) and item.get("id") is not None
    ]
    return max(ids) if ids else 100


def build_fflogs_metadata_boss(
    en_zone: dict, cn_zone: dict | None, encounter_id: int
) -> dict | None:
    en_encounter = fflogs_encounters_by_id(en_zone).get(encounter_id)
    if not en_encounter:
        return None
    cn_encounter = (
        fflogs_encounters_by_id(cn_zone).get(encounter_id) if cn_zone else None
    )
    en_name = en_encounter.get("name") or ""
    cn_name = cn_encounter.get("name") if isinstance(cn_encounter, dict) else None
    return {
        "pk": encounter_id,
        "quest": int(en_zone["id"]),
        "zone_name": en_zone.get("name") or "",
        "cn_zone_name": (
            cn_zone.get("name")
            if isinstance(cn_zone, dict)
            else en_zone.get("name") or ""
        ),
        "name": en_name,
        "cn_name": cn_name or en_name,
        "nickname": [],
        "patch": 0,
        "savage": fflogs_metadata_difficulty(en_zone),
        "region": fflogs_metadata_regions(en_zone),
        "cn_region": (
            fflogs_metadata_regions(cn_zone)
            if cn_zone
            else fflogs_metadata_regions(en_zone)
        ),
    }


def fflogs_metadata_boss_names(entry: dict, zone: dict | None = None) -> list[str]:
    names = [entry.get("name"), entry.get("cn_name"), *entry.get("nickname", [])]
    if isinstance(zone, dict) and len(zone.get("encounters", []) or []) == 1:
        names.append(zone.get("name"))
    return [
        normalize_logs_lookup(name)
        for name in names
        if isinstance(name, str) and name.strip()
    ]


async def find_logs_boss_metadata(
    boss_name: str, client_id: str, client_secret: str
) -> dict | None:
    if not client_id or not client_secret:
        return None
    query = normalize_logs_lookup(boss_name)
    if not query:
        return None

    metadata: dict[bool, dict] = {}
    for cn_source, host in FFLOGS_HOSTS.items():
        token = await get_fflogs_token(host, client_id, client_secret)
        metadata[cn_source] = await fetch_fflogs_metadata(host, token)

    en_zones = fflogs_zone_by_id(metadata[False])
    cn_zones = fflogs_zone_by_id(metadata[True])
    candidates: list[tuple[dict, list[str]]] = []
    for zone_id, en_zone in en_zones.items():
        cn_zone = cn_zones.get(zone_id)
        encounter_ids = set(fflogs_encounters_by_id(en_zone))
        encounter_ids.update(fflogs_encounters_by_id(cn_zone))
        for encounter_id in encounter_ids:
            entry = build_fflogs_metadata_boss(en_zone, cn_zone, encounter_id)
            if not entry:
                continue
            names = fflogs_metadata_boss_names(entry, en_zone)
            candidates.append((entry, names))
            if query in names:
                return entry
    for entry, names in candidates:
        if any(query in name or name in query for name in names):
            return entry
    return None


def normalize_fflogs_result(
    stat: dict,
    query: LogsQuery,
    boss: dict,
    job: dict,
    region_info: str,
    source_label: str,
) -> str:
    period_label = "日期范围" if stat.get("date_range") else "天数"
    period_value = stat.get("date_range", stat.get("day", "最新"))
    lines = [
        f"服务器: {'国服' if query.cn_source else '国际服'}  dps类型: {query.dps_type}",
        f"数据源: {source_label}",
        f"版本: {region_info}",
        f"副本: {boss['cn_zone_name']}",
        f"boss: {boss['cn_name']}",
        f"职业: {job['cn_name']}  {period_label}: {period_value}",
    ]
    if stat.get("parses"):
        lines.append(f"记录数: {stat['parses']}")
    lines.extend(
        f"{percentile}%: {stat[str(percentile)]:.2f}"
        for percentile in FFLOGS_PERCENTILES
    )
    return "\n".join(lines)


def parse_fflogs_number(value: str) -> float:
    return float(value.replace(",", ""))


def decode_fflogs_page_text(value: str) -> str:
    text = html.unescape(value)
    text = re.sub(
        r"\\u([0-9a-fA-F]{4})", lambda match: chr(int(match.group(1), 16)), text
    )
    return text.replace("\\/", "/")


def fflogs_text_from_html(value: str) -> str:
    text = decode_fflogs_page_text(value)
    text = re.sub(
        r"<script\b[^>]*>.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL
    )
    text = re.sub(
        r"<style\b[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL
    )
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def fflogs_number_candidates(value: str) -> list[float]:
    numbers = re.findall(r"\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?", value)
    return [
        parse_fflogs_number(number)
        for number in numbers
        if parse_fflogs_number(number) >= 1000
    ]


def fflogs_decimal_candidates(value: str) -> list[float]:
    numbers = re.findall(r"\d{1,3}(?:,\d{3})*\.\d+|\d+\.\d+", value)
    return [
        parse_fflogs_number(number)
        for number in numbers
        if parse_fflogs_number(number) >= 1000
    ]


def choose_fflogs_percentile_values(values: list[float]) -> list[float] | None:
    blocks = find_fflogs_percentile_value_blocks(values, limit=1)
    return blocks[0] if blocks else None


def find_fflogs_percentile_value_blocks(
    values: list[float], limit: int = 3
) -> list[list[float]]:
    expected_count = len(FFLOGS_PERCENTILES)
    blocks = []
    for index in range(0, len(values) - expected_count + 1):
        block = values[index : index + expected_count]
        if (
            block[0] > block[1]
            and all(
                block[item] >= block[item + 1] for item in range(expected_count - 1)
            )
            and block[-1] >= block[0] * 0.35
        ):
            blocks.append(block)
            if len(blocks) >= limit:
                break
    return blocks


def stat_from_fflogs_percentile_values(values: list[float], text: str) -> dict:
    stat = {}
    for percentile, value in zip(sorted(FFLOGS_PERCENTILES, reverse=True), values):
        stat[str(percentile)] = value
    date_match = re.search(
        r"[A-Z][a-z]{2}\s+\d{1,2}\s*-\s*[A-Z][a-z]{2}\s+\d{1,2}", text
    )
    if date_match:
        stat["date_range"] = date_match.group(0)
    return stat


def parse_logs_statistics_global_value_block(text: str) -> dict | None:
    values = choose_fflogs_percentile_values(fflogs_decimal_candidates(text))
    if not values:
        return None
    return stat_from_fflogs_percentile_values(values, text)


def parse_logs_statistics_chart_value_block(text: str, job: dict) -> dict | None:
    label_groups = [
        [f"{job.get('cn_name', '')} 最高", f"{job.get('name', '')} Max"],
        ["第99百分位数", "99th percentile", "99th Percentile"],
        ["第95百分位数", "95th percentile", "95th Percentile"],
        ["第75百分位数", "75th percentile", "75th Percentile"],
        ["第50百分位数", "50th percentile", "50th Percentile", "Median"],
        ["第25百分位数", "25th percentile", "25th Percentile"],
        ["第10百分位数", "10th percentile", "10th Percentile"],
    ]
    positions = []
    for labels in label_groups:
        match = None
        for label in labels:
            if not label.strip():
                continue
            match = re.search(re.escape(label), text, flags=re.IGNORECASE)
            if match:
                break
        if not match:
            return None
        positions.append(match.start())
    if not positions:
        return None
    start = max(0, min(positions) - 8000)
    end = min(len(text), max(positions) + 8000)
    values = choose_fflogs_percentile_values(fflogs_decimal_candidates(text[start:end]))
    if not values:
        return None
    return stat_from_fflogs_percentile_values(values, text)


def parse_logs_statistics_chart_values(page: str, job: dict) -> dict | None:
    text = decode_fflogs_page_text(page)
    labels = {
        100: [
            f"{job.get('cn_name', '')} 最高",
            f"{job.get('name', '')} Max",
            "最高",
            "Max",
        ],
        99: ["第99百分位数", "99th percentile", "99th Percentile"],
        95: ["第95百分位数", "95th percentile", "95th Percentile"],
        75: ["第75百分位数", "75th percentile", "75th Percentile"],
        50: ["第50百分位数", "50th percentile", "50th Percentile", "Median"],
        25: ["第25百分位数", "25th percentile", "25th Percentile"],
        10: ["第10百分位数", "10th percentile", "10th Percentile"],
    }
    stat = {}
    for percentile, names in labels.items():
        for label in names:
            if not label.strip():
                continue
            for match in re.finditer(re.escape(label), text, flags=re.IGNORECASE):
                section = text[match.end() : match.end() + 400]
                candidates = fflogs_number_candidates(section)
                if candidates:
                    stat[str(percentile)] = candidates[0]
                    break
            if str(percentile) in stat:
                break
        if str(percentile) not in stat:
            return None
    date_match = re.search(
        r"[A-Z][a-z]{2}\s+\d{1,2}\s*-\s*[A-Z][a-z]{2}\s+\d{1,2}", text
    )
    if date_match:
        stat["date_range"] = date_match.group(0)
    return stat


def parse_logs_statistics_primary_cell(page: str) -> dict | None:
    cells = re.findall(
        r"<td\b(?=[^>]*\bmain-table-number\b)(?=[^>]*\bprimary\b)[^>]*>(.*?)</td>",
        page,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for cell in cells:
        text = fflogs_text_from_html(cell)
        candidates = fflogs_decimal_candidates(text)
        if candidates:
            return {"dps": candidates[0]}
    return None


def fflogs_debug_snippet(value: str, limit: int = 120) -> str:
    text = fflogs_text_from_html(value)
    return text[:limit]


def fflogs_debug_td_samples(page: str, pattern: str, limit: int = 3) -> list[str]:
    cells = re.findall(r"<td\b[^>]*>.*?</td>", page, flags=re.IGNORECASE | re.DOTALL)
    samples = []
    for cell in cells:
        if re.search(pattern, cell, flags=re.IGNORECASE):
            samples.append(fflogs_debug_snippet(cell))
            if len(samples) >= limit:
                break
    return samples


def fflogs_debug_script_sources(page: str, limit: int = 12) -> list[str]:
    sources = re.findall(
        r"<script\b[^>]*\bsrc=[\"']([^\"']+)[\"']", page, flags=re.IGNORECASE
    )
    return [html.unescape(source) for source in sources[:limit]]


def fflogs_debug_urls(page: str, limit: int = 12) -> list[str]:
    urls = re.findall(
        r"(?:https?:)?//[^\"'<>\\\s]+|(?<!<)/[^\"'<>\\\s]*(?:statistics|api|rankings|table)[^\"'<>\\\s]*",
        page,
    )
    deduped = []
    for url in urls:
        value = html.unescape(url)
        if value not in deduped:
            deduped.append(value)
            if len(deduped) >= limit:
                break
    return deduped


def fflogs_debug_table_tags(page: str, limit: int = 5) -> list[str]:
    tables = re.findall(r"<table\b[^>]*>", page, flags=re.IGNORECASE)
    return [html.unescape(table)[:180] for table in tables[:limit]]


def parse_logs_statistics_summary_row(page: str, job: dict) -> dict | None:
    job_names = [
        name
        for name in [job.get("cn_name"), job.get("name")]
        if isinstance(name, str) and name
    ]
    rows = re.findall(r"<tr\b[^>]*>.*?</tr>", page, flags=re.IGNORECASE | re.DOTALL)
    candidates = rows if rows else [page]
    for row in candidates:
        text = fflogs_text_from_html(row)
        if not any(name in text for name in job_names):
            continue
        date_match = re.search(
            r"[A-Z][a-z]{2}\s+\d{1,2}\s*-\s*[A-Z][a-z]{2}\s+\d{1,2}", text
        )
        number_text = text[date_match.end() :] if date_match else text
        numbers = re.findall(r"\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?", number_text)
        dps_candidates = [
            parse_fflogs_number(number)
            for number in numbers
            if "." in number and parse_fflogs_number(number) >= 1000
        ]
        if not dps_candidates:
            continue
        result = {
            "dps": dps_candidates[0],
            "date_range": date_match.group(0) if date_match else None,
        }
        if len(dps_candidates) > 1:
            result["max"] = dps_candidates[1]
        parse_candidates = [
            int(number.replace(",", "")) for number in numbers if "." not in number
        ]
        if parse_candidates:
            result["parses"] = parse_candidates[-1]
        return result

    text = fflogs_text_from_html(page)
    for name in job_names:
        index = text.rfind(name)
        if index < 0:
            continue
        section = text[index : index + 800]
        date_match = re.search(
            r"[A-Z][a-z]{2}\s+\d{1,2}\s*-\s*[A-Z][a-z]{2}\s+\d{1,2}", section
        )
        number_text = section[date_match.end() :] if date_match else section
        numbers = re.findall(r"\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?", number_text)
        dps_candidates = [
            parse_fflogs_number(number)
            for number in numbers
            if "." in number and parse_fflogs_number(number) >= 1000
        ]
        if dps_candidates:
            return {
                "dps": dps_candidates[0],
                "date_range": date_match.group(0) if date_match else None,
            }
    return None


def parse_logs_statistics_version_label(page: str) -> str | None:
    text = fflogs_text_from_html(page)
    matches = re.findall(r"(?:标准阵容构成|Standard Comps)\s*(?:\([^)]+\))?", text)
    return matches[-1] if matches else None


def log_fflogs_statistics_page_diagnostics(page: str, job: dict) -> None:
    decoded = decode_fflogs_page_text(page)
    text = fflogs_text_from_html(page)
    primary_cells = re.findall(
        r"<td\b(?=[^>]*\bmain-table-number\b)(?=[^>]*\bprimary\b)[^>]*>(.*?)</td>",
        decoded,
        flags=re.IGNORECASE | re.DOTALL,
    )
    primary_values = []
    for cell in primary_cells[:10]:
        primary_values.extend(fflogs_decimal_candidates(fflogs_text_from_html(cell)))
    job_decimal_candidates = []
    for name in [job.get("cn_name"), job.get("name")]:
        if not name:
            continue
        index = decoded.rfind(str(name))
        if index >= 0:
            job_decimal_candidates.extend(
                fflogs_decimal_candidates(decoded[index : index + 1200])
            )
    global_decimal_candidates = fflogs_decimal_candidates(decoded)
    global_blocks = find_fflogs_percentile_value_blocks(global_decimal_candidates)
    markers = {
        "cn_job": bool(job.get("cn_name") and job["cn_name"] in decoded),
        "en_job": bool(job.get("name") and job["name"] in decoded),
        "date": bool(
            re.search(r"[A-Z][a-z]{2}\s+\d{1,2}\s*-\s*[A-Z][a-z]{2}\s+\d{1,2}", text)
        ),
        "chart": "第99百分位数" in decoded
        or "99th percentile" in decoded
        or "99th Percentile" in decoded,
        "table_rows": bool(re.search(r"<tr\b", decoded, flags=re.IGNORECASE)),
        "data_push": "data.push" in decoded,
        "cloudflare": "Just a moment" in decoded
        or "Enable JavaScript and cookies" in decoded,
        "td_count": len(re.findall(r"<td\b", decoded, flags=re.IGNORECASE)),
        "primary_td_count": len(
            re.findall(r"<td\b[^>]*\bprimary\b", decoded, flags=re.IGNORECASE)
        ),
        "main_table_number_td_count": len(
            re.findall(r"<td\b[^>]*\bmain-table-number\b", decoded, flags=re.IGNORECASE)
        ),
        "primary_main_table_cell_count": len(primary_cells),
        "datatable_string_count": decoded.count("DataTables"),
        "summary_table_string_count": decoded.count("summary-table"),
        "main_table_number_string_count": decoded.count("main-table-number"),
        "statistics_table_string_count": decoded.count("statistics/table"),
    }
    logger.info(
        f"FFLogs statistics page diagnostics: len={len(page)} markers={markers}"
    )
    logger.info(f"FFLogs statistics primary values sample: {primary_values[:8]}")
    logger.info(
        f"FFLogs statistics job-window decimals sample: {job_decimal_candidates[:12]}"
    )
    logger.info(
        f"FFLogs statistics global decimals sample: {global_decimal_candidates[:30]}"
    )
    logger.info(f"FFLogs statistics descending blocks sample: {global_blocks}")
    logger.info(
        f"FFLogs statistics main-table-number td samples: {fflogs_debug_td_samples(decoded, r'main-table-number')}"
    )
    primary_pattern = r"\bprimary\b"
    logger.info(
        f"FFLogs statistics primary td samples: {fflogs_debug_td_samples(decoded, primary_pattern)}"
    )
    logger.info(
        f"FFLogs statistics script src sample: {fflogs_debug_script_sources(decoded)}"
    )
    logger.info(f"FFLogs statistics url sample: {fflogs_debug_urls(decoded)}")
    logger.info(
        f"FFLogs statistics table tag sample: {fflogs_debug_table_tags(decoded)}"
    )


async def fetch_logs_statistics_summary(
    query: LogsQuery, boss: dict, job: dict
) -> tuple[dict, str] | None:
    host = FFLOGS_HOSTS[query.cn_source]
    stat = {}
    date_range = None
    parses = None
    version_label = None
    sorted_percentiles = sorted(FFLOGS_PERCENTILES, reverse=True)
    for index, percentile in enumerate(sorted_percentiles):
        params = {
            "boss": str(boss["pk"]),
            "class": "Global",
            "spec": job["name"],
            "dataset": str(percentile),
        }
        if query.dps_type != "rdps":
            params["dpstype"] = query.dps_type
        url = f"{host}/zone/statistics/{boss['quest']}?{urlencode(params)}"
        logger.info(f"FFLogs statistics page URL: {url}")
        page = await safe_aiohttp_get(
            url,
            "FFLogs statistics page",
            res_type="text",
            headers={"Referer": host},
        )
        if not isinstance(page, str):
            return None
        if index == 0:
            decoded_page = decode_fflogs_page_text(page)
            chart_stat = parse_logs_statistics_chart_value_block(decoded_page, job)
            if chart_stat:
                logger.info("FFLogs statistics chart values matched")
                version_label = parse_logs_statistics_version_label(page)
                return chart_stat, version_label or "网页默认"
            chart_stat = parse_logs_statistics_global_value_block(decoded_page)
            if chart_stat:
                logger.info("FFLogs statistics global value block matched")
                version_label = parse_logs_statistics_version_label(page)
                return chart_stat, version_label or "网页默认"
            chart_stat = parse_logs_statistics_chart_values(page, job)
            if chart_stat:
                logger.info("FFLogs statistics chart label values matched")
                version_label = parse_logs_statistics_version_label(page)
                return chart_stat, version_label or "网页默认"
        parsed = parse_logs_statistics_primary_cell(page)
        if parsed:
            logger.info("FFLogs statistics primary table cell matched")
        else:
            parsed = parse_logs_statistics_summary_row(page, job)
        if not parsed:
            log_fflogs_statistics_page_diagnostics(page, job)
            return None
        stat[str(percentile)] = parsed["dps"]
        date_range = date_range or parsed.get("date_range")
        parses = parses or parsed.get("parses")
        version_label = version_label or parse_logs_statistics_version_label(page)
    if any(str(percentile) not in stat for percentile in FFLOGS_PERCENTILES):
        return None
    if date_range:
        stat["date_range"] = date_range
    if parses:
        stat["parses"] = parses
    return stat, version_label or "网页默认"


async def fetch_logs_statistics_browser_table(
    query: LogsQuery, boss: dict, job: dict
) -> tuple[dict, str] | None:
    regions = fflogs_region_entries(boss, query.cn_source)
    if not regions:
        return None
    host = FFLOGS_HOSTS[query.cn_source]
    for region_entry in [regions[-index - 1] for index in range(len(regions))]:
        region_info, region_id = region_entry.split("###", 1)
        params = {"keystone": "15", "dpstype": query.dps_type}
        url = (
            f"{host}/zone/statistics/table/"
            f"{boss['quest']}/dps/{boss['pk']}/{boss['savage']}/8/{int(region_id)}/100/1/14/0/"
            f"Global/{job['name']}/All/0/normalized/single/0/-1/?"
            f"{urlencode(params)}"
        )
        logger.info(f"FFLogs statistics browser table URL: {url}")
        page = await safe_aiohttp_get(
            url,
            "FFLogs statistics browser table",
            res_type="text",
            headers={"Referer": host},
        )
        if not isinstance(page, str) or "data.push" not in page:
            continue
        rows = parse_logs_statistics_page(page)
        if not rows:
            continue
        stat = rows[-1]
        summary = parse_logs_statistics_summary_row(page, job)
        if summary and summary.get("date_range"):
            stat["date_range"] = summary["date_range"]
        if summary and summary.get("parses"):
            stat["parses"] = summary["parses"]
        logger.info("FFLogs statistics browser table matched")
        return stat, f"{region_info} / normalized"
    return None


async def fetch_logs_statistics_page(
    query: LogsQuery, boss: dict, job: dict
) -> tuple[str, str] | None:
    regions = fflogs_region_entries(boss, query.cn_source)
    if not regions:
        return None
    host = FFLOGS_HOSTS[query.cn_source]
    search_range = [regions[-index - 1] for index in range(len(regions))]
    for region_entry in search_range:
        region_info, region_id = region_entry.split("###", 1)
        params = {}
        if query.dps_type != "rdps":
            params["dpstype"] = query.dps_type
        for aggregate in FFLOGS_STAT_AGGREGATES:
            query_string = f"?{urlencode(params)}" if params else ""
            url = (
                f"{host}/zone/statistics/table/"
                f"{boss['quest']}/dps/{boss['pk']}/{boss['savage']}/8/{int(region_id)}/100/1000/7/"
                f"{boss['patch']}/Global/{job['name']}/All/0/{aggregate}/single/0/-1/"
                f"{query_string}"
            )
            logger.info(f"FFLogs statistics table URL: {url}")
            page = await safe_aiohttp_get(
                url,
                "FFLogs statistics table",
                res_type="text",
                headers={"Referer": host},
            )
            if isinstance(page, str) and "data.push" in page:
                logger.info(f"FFLogs statistics aggregate matched: {aggregate}")
                return page, f"{region_info} / {aggregate}"
    return None


def parse_logs_statistics_page(page: str) -> list[dict]:
    statistics = {}
    for percentile in FFLOGS_PERCENTILES:
        series_name = "" if percentile == 100 else str(percentile)
        pattern = rf"series{series_name}\.data\.push\(([+-]?(?:0|[1-9]\d*)(?:\.\d+)?)\)"
        statistics[str(percentile)] = [
            float(value) for value in re.findall(pattern, page)
        ]
    total_length = len(statistics["100"])
    if not total_length or any(
        len(statistics[str(percentile)]) != total_length
        for percentile in FFLOGS_PERCENTILES
    ):
        return []

    def all_zero(index: int) -> bool:
        return (
            sum(statistics[str(percentile)][index] for percentile in FFLOGS_PERCENTILES)
            == 0
        )

    left = 0
    right = total_length - 1
    while left < total_length and all_zero(left):
        left += 1
    while right >= 0 and all_zero(right):
        right -= 1
    if left > right:
        return []

    rows = []
    for index in range(left, right + 1):
        item = {"day": len(rows) + 1}
        for percentile in FFLOGS_PERCENTILES:
            item[str(percentile)] = statistics[str(percentile)][index]
        rows.append(item)
    return rows


async def create_logs_text_crawl(query: LogsQuery, boss: dict, job: dict) -> str:
    if query.day == -1:
        browser_table_result = await fetch_logs_statistics_browser_table(
            query, boss, job
        )
        if browser_table_result:
            stat, region_info = browser_table_result
            return normalize_fflogs_result(
                stat, query, boss, job, region_info, "FFLogs statistics browser table"
            )
        summary_result = await fetch_logs_statistics_summary(query, boss, job)
        if summary_result:
            stat, region_info = summary_result
            return normalize_fflogs_result(
                stat, query, boss, job, region_info, "FFLogs statistics page"
            )
        return "FFLogs statistics 页面解析失败，已记录诊断日志，请检查插件日志中的 FFLogs statistics page diagnostics。"

    result = await fetch_logs_statistics_page(query, boss, job)
    if not result:
        return "查不到数据，怎么回事呢？"
    page, region_info = result
    rows = parse_logs_statistics_page(page)
    if not rows:
        return "No data found"
    row = rows[-1] if query.day == -1 or query.day >= len(rows) else rows[query.day]
    return normalize_fflogs_result(
        row, query, boss, job, region_info, "FFLogs statistics table"
    )


async def create_logs_text(query: LogsQuery, client_id: str, client_secret: str) -> str:
    if not query.boss_name or not query.job_name:
        return (
            "查logs格式：输出 boss名 职业名 (国服) (rdps) (day2)\n例：输出 海德林 武士"
        )
    if query.day == -2:
        return "day格式不对，例如：day2"
    job = find_logs_job(query.job_name)
    if not job:
        return "检查职业名称是否正确"
    bosses = find_logs_bosses(query.boss_name)
    if not bosses:
        try:
            boss = await find_logs_boss_metadata(
                query.boss_name, client_id, client_secret
            )
            bosses = [boss] if boss else []
        except Exception as exc:
            logger.info(f"FFLogs metadata 动态查找失败: {exc}")
    if not bosses:
        return "检查boss名称是否正确"

    results = []
    for boss in bosses:
        results.append(await create_logs_text_crawl(query, boss, job))
    return "\n\n".join(results)


def parse_character_logs_query(query: str) -> CharacterLogsQuery:
    parts = query.split()
    cn_source = None
    content_parts = []
    for part in parts:
        lower = part.lower()
        if lower in LOGS_SERVER_TOKENS:
            cn_source = LOGS_SERVER_TOKENS[lower]
            continue
        content_parts.append(part)
    if len(content_parts) < 2:
        return CharacterLogsQuery(
            " ".join(content_parts).strip() or None, None, cn_source
        )
    return CharacterLogsQuery(
        character_name=" ".join(content_parts[:-1]).strip() or None,
        server_name=content_parts[-1].strip() or None,
        cn_source=cn_source,
    )


def fflogs_character_partition_label(partition: dict) -> str:
    return str(
        partition.get("compactName")
        or partition.get("name")
        or partition.get("id")
        or ""
    ).strip()


def fflogs_character_partition_version(
    zone_id: int, partition_id: int, label: str, fallback: str | None = None
) -> str:
    override = FFLOGS_CHARACTER_PARTITION_VERSION_OVERRIDES.get((zone_id, partition_id))
    if override:
        return override
    match = re.search(r"(\d+(?:\.\d+)?)", label or "")
    if match:
        return match.group(1)
    label_key = normalize_logs_lookup(label or "")
    if label_key in {"standard", "标准"}:
        return fallback or label
    return label or fallback or str(partition_id)


def fflogs_character_zone_partitions(
    metadata: dict, zone_id: int
) -> list[tuple[int, str]]:
    zone = fflogs_zone_by_id(metadata).get(zone_id)
    if not isinstance(zone, dict):
        return []
    partitions = []
    for partition in zone.get("partitions", []):
        if not isinstance(partition, dict) or partition.get("id") is None:
            continue
        try:
            partition_id = int(partition["id"])
        except (TypeError, ValueError):
            continue
        if partition_id <= 0:
            continue
        label = fflogs_character_partition_label(partition)
        label_key = normalize_logs_lookup(label)
        if "nonstandard" in label_key or "非标准" in label:
            continue
        partitions.append((partition_id, label or str(partition_id)))
    return sorted(partitions, key=lambda item: item[0])


def build_fflogs_character_zone_requests(metadata: dict | None = None) -> list[dict]:
    if not metadata:
        return [dict(item) for item in FFLOGS_CHARACTER_BASE_ZONES]
    requests = []
    for base in FFLOGS_CHARACTER_BASE_ZONES:
        if base.get("type") == "savage":
            requests.append(dict(base))
            continue
        partitions = fflogs_character_zone_partitions(metadata, int(base["zone_id"]))
        if not partitions:
            requests.append(dict(base))
            continue
        for partition_id, label in partitions:
            item = dict(base)
            item["partition"] = partition_id
            item["version"] = fflogs_character_partition_version(
                int(base["zone_id"]),
                partition_id,
                label,
                str(base.get("version") or ""),
            )
            item["partition_order"] = partition_id
            item["key"] = f"{base['key']}_p{partition_id}"
            requests.append(item)
    return requests


def iter_fflogs_character_request_batches(requests: list[dict]) -> list[list[dict]]:
    return [
        requests[index : index + FFLOGS_CHARACTER_QUERY_BATCH_SIZE]
        for index in range(0, len(requests), FFLOGS_CHARACTER_QUERY_BATCH_SIZE)
    ]


def build_fflogs_character_logs_query(requests: list[dict] | None = None) -> str:
    requests = requests or FFLOGS_CHARACTER_ZONE_REQUESTS
    ranking_fields = []
    for request in requests:
        args = [f"zoneID: {request['zone_id']}"]
        if request.get("difficulty") is not None:
            args.append(f"difficulty: {request['difficulty']}")
        if request.get("partition") is not None:
            args.append(f"partition: {int(request['partition'])}")
        ranking_fields.append(f"{request['key']}: zoneRankings({', '.join(args)})")
    joined_rankings = "\n                  ".join(ranking_fields)
    return f"""
query ($name: String, $server: String, $region: String) {{
  characterData {{
    character(name: $name, serverSlug: $server, serverRegion: $region) {{
      id
      name
      server {{ name }}
      {joined_rankings}
    }}
  }}
}}
"""


def exception_detail(exc: BaseException) -> str:
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


async def is_cn_character_server(server_name: str) -> bool:
    if server_name in HOUSE_SERVER_IDS:
        return True
    try:
        worlds = await load_cn_world_names()
    except Exception as exc:
        logger.info(f"FFLogs 角色服务器名动态解析失败: {exc}")
        return False
    return server_name in worlds


async def fflogs_character_server_candidates(
    query: CharacterLogsQuery,
    default_cn_source: bool,
) -> list[tuple[str, str, str]]:
    server_name = query.server_name or ""
    if query.cn_source is True:
        return [(FFLOGS_HOSTS[True], "CN", "国服")]
    if query.cn_source is False:
        return [
            (FFLOGS_HOSTS[False], region, f"国际服 {region}")
            for region in FFLOGS_GLOBAL_CHARACTER_REGIONS
        ]

    known_cn = await is_cn_character_server(server_name)
    if known_cn:
        return [(FFLOGS_HOSTS[True], "CN", "国服")]

    cn_candidate = [(FFLOGS_HOSTS[True], "CN", "国服")]
    global_candidates = [
        (FFLOGS_HOSTS[False], region, f"国际服 {region}")
        for region in FFLOGS_GLOBAL_CHARACTER_REGIONS
    ]
    return (
        cn_candidate + global_candidates
        if default_cn_source
        else global_candidates + cn_candidate
    )


async def fetch_fflogs_character_logs(
    query: CharacterLogsQuery,
    host: str,
    region: str,
    client_id: str,
    client_secret: str,
) -> dict | None:
    token = await get_fflogs_token(host, client_id, client_secret)
    requests = FFLOGS_CHARACTER_ZONE_REQUESTS
    try:
        metadata = await fetch_fflogs_metadata(host, token)
        requests = build_fflogs_character_zone_requests(metadata)
    except Exception as exc:
        logger.info(
            f"FFLogs 角色查询分区 metadata 获取失败，使用默认分区: {exception_detail(exc)}"
        )
    logger.info(f"FFLogs 角色查询分区数量: {len(requests)}")
    batches = iter_fflogs_character_request_batches(requests)
    variables = {
        "name": query.character_name,
        "server": query.server_name,
        "region": region,
    }
    semaphore = asyncio.Semaphore(FFLOGS_CHARACTER_QUERY_CONCURRENCY)

    async def fetch_batch(batch: list[dict]) -> dict | None:
        async with semaphore:
            payload = await fflogs_graphql(
                host,
                token,
                build_fflogs_character_logs_query(batch),
                variables,
            )
        character_payload = (
            payload.get("data", {}).get("characterData", {}).get("character")
        )
        return character_payload if isinstance(character_payload, dict) else None

    results = await asyncio.gather(
        *(fetch_batch(batch) for batch in batches), return_exceptions=True
    )
    character: dict | None = None
    failed_batches = []
    for index, result in enumerate(results, start=1):
        if isinstance(result, BaseException):
            failed_batches.append(f"{index}/{len(batches)} {exception_detail(result)}")
            continue
        if result is None:
            continue
        if character is None:
            character = {
                "id": result.get("id"),
                "name": result.get("name"),
                "server": result.get("server"),
            }
        for batch_request in batches[index - 1]:
            key = batch_request["key"]
            if key in result:
                character[key] = result[key]

    if failed_batches:
        logger.warning(f"FFLogs 角色查询分批失败: {'; '.join(failed_batches)}")
    if character is None:
        if failed_batches:
            raise RuntimeError(
                f"FFLogs 角色查询全部分批失败: {'; '.join(failed_batches)}"
            )
        return None
    character["_tataru_requests"] = requests
    return character


def fflogs_character_value(item: dict, *keys: str):
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return None


def fflogs_character_float(item: dict, *keys: str) -> float | None:
    value = fflogs_character_value(item, *keys)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fflogs_character_int(item: dict, *keys: str) -> int | None:
    value = fflogs_character_value(item, *keys)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def find_logs_boss_by_pk(encounter_id: int) -> dict | None:
    for item in load_json_list(BOSS_JSON):
        try:
            if int(item.get("pk")) == encounter_id:
                return item
        except (TypeError, ValueError):
            continue
    return None


def fflogs_character_job_label(job_name: str | None) -> str:
    if not job_name:
        return "未知职业"
    job = find_logs_job(job_name)
    return str(job.get("cn_name") or job_name) if job else job_name


def fflogs_character_encounter_label(
    encounter_id: int, encounter_name: str | None = None
) -> str:
    short_label = FFLOGS_CHARACTER_SHORT_LABELS.get(encounter_id)
    boss = find_logs_boss_by_pk(encounter_id)
    boss_name = str((boss or {}).get("cn_name") or encounter_name or encounter_id)
    if short_label:
        if short_label.startswith("绝"):
            return short_label
        return f"{short_label} {boss_name}"
    return boss_name


def collect_fflogs_character_records(character: dict) -> dict[str, dict]:
    records: dict[str, dict] = {}
    requests = character.get("_tataru_requests", FFLOGS_CHARACTER_ZONE_REQUESTS)
    for request in requests:
        ranking_payload = character.get(request["key"])
        if not isinstance(ranking_payload, dict):
            continue
        rankings = (
            ranking_payload.get("rankings")
            or ranking_payload.get("encounterRanks")
            or ranking_payload.get("encounter_ranks")
            or []
        )
        if not isinstance(rankings, list):
            continue
        for ranking in rankings:
            if not isinstance(ranking, dict):
                continue
            encounter = (
                ranking.get("encounter")
                if isinstance(ranking.get("encounter"), dict)
                else {}
            )
            encounter_id = fflogs_character_int(encounter, "id")
            if encounter_id is None:
                encounter_id = fflogs_character_int(
                    ranking, "encounterID", "encounter_id"
                )
            if encounter_id is None:
                continue
            label = fflogs_character_encounter_label(
                encounter_id, encounter.get("name")
            )
            percent = fflogs_character_float(
                ranking, "rankPercent", "rank_percent", "historicalPercent"
            )
            amount = fflogs_character_float(
                ranking, "bestAmount", "best_amount", "amount"
            )
            rank = fflogs_character_int(ranking, "rank", "bestRank", "best_rank")
            total_parses = fflogs_character_int(
                ranking, "totalParses", "rankTotalParses", "total_parses"
            )
            job_name = fflogs_character_value(
                ranking, "spec", "bestSpec", "best_job", "job"
            )
            job_label = fflogs_character_job_label(str(job_name) if job_name else None)
            has_percent = percent is not None and percent > 0
            has_amount = amount is not None and amount > 0
            has_rank = rank is not None and rank > 0
            if not any([has_percent, has_amount, has_rank]):
                continue
            current = records.get(label)
            current_percent = current.get("percent") if current else None
            if current:
                if request.get("type") in {"ultimate", "savage"}:
                    request_order = int(request.get("order", 0)) * 100000 + int(
                        request.get("partition_order", 0)
                    )
                    current_order = int(current.get("version_order", 0))
                    if request_order < current_order:
                        continue
                    if request_order > current_order:
                        current_percent = None
                if percent is None and current_percent is not None:
                    continue
                if (
                    percent is not None
                    and current_percent is not None
                    and percent <= current_percent
                ):
                    continue
            records[label] = {
                "encounter_id": encounter_id,
                "label": label,
                "category": request.get("type"),
                "version": request.get("version"),
                "version_order": int(request.get("order", 0)) * 100000
                + int(request.get("partition_order", 0)),
                "percent": percent,
                "amount": amount,
                "rank": rank,
                "total_parses": total_parses,
                "job": job_label,
            }
    return records


def fflogs_character_url(
    host: str, region: str, server_name: str, character_name: str
) -> str:
    return (
        f"{host}/character/{quote(region)}/{quote(server_name)}/{quote(character_name)}"
    )


def format_fflogs_character_record(record: dict) -> str:
    percent = record.get("percent")
    percent_text = f"{percent:.1f}%" if isinstance(percent, (int, float)) else "--"
    details = [str(record.get("job") or "未知职业")]
    if record.get("category") == "ultimate" and record.get("version"):
        details.append(f"{record['version']}记录")
    amount = record.get("amount")
    if isinstance(amount, (int, float)) and amount > 0:
        details.append(f"{amount:,.0f} rDPS")
    rank = record.get("rank")
    total = record.get("total_parses")
    if isinstance(rank, int) and isinstance(total, int) and total > 0:
        details.append(f"#{rank}/{total}")
    elif isinstance(rank, int):
        details.append(f"#{rank}")
    return f"{record['label']}: {percent_text} ({' / '.join(details)})"


def format_fflogs_character_logs(
    query: CharacterLogsQuery,
    character: dict,
    source_label: str,
    host: str,
    region: str,
) -> str:
    character_name = str(character.get("name") or query.character_name)
    server = (
        character.get("server") if isinstance(character.get("server"), dict) else {}
    )
    server_name = str(server.get("name") or query.server_name)
    records = collect_fflogs_character_records(character)
    lines = [
        f"【FFLogs角色】{character_name} @ {server_name}",
        f"数据源: FFLogs API ({source_label})",
        fflogs_character_url(host, region, server_name, character_name),
    ]
    if not records:
        lines.append("暂无公开可用的零式/绝本排名记录。")
        return "\n".join(lines)

    for title, encounter_ids in FFLOGS_CHARACTER_GROUPS:
        section_lines = []
        seen_labels = set()
        for encounter_id in encounter_ids:
            label = fflogs_character_encounter_label(encounter_id)
            if label in seen_labels:
                continue
            seen_labels.add(label)
            record = records.get(label)
            if record:
                section_lines.append(format_fflogs_character_record(record))
        lines.append("")
        lines.append(f"【{title}】")
        lines.extend(section_lines if section_lines else ["暂无记录"])
    return "\n".join(lines)


async def create_character_logs_text(
    query: CharacterLogsQuery,
    client_id: str,
    client_secret: str,
    default_cn_source: bool = True,
) -> str:
    if not query.character_name or not query.server_name:
        return "查角色logs格式：logs 角色名 服务器名 (国服/国际服)\n例：logs 一色彩羽 银泪湖"
    if not client_id or not client_secret:
        return "请先在 AstrBot 插件配置中填写 FFLogs API Client ID 和 FFLogs API Client Secret。"

    candidates = await fflogs_character_server_candidates(query, default_cn_source)
    errors = []
    for host, region, source_label in candidates:
        try:
            character = await fetch_fflogs_character_logs(
                query, host, region, client_id, client_secret
            )
        except Exception as exc:
            detail = exception_detail(exc)
            logger.warning(f"FFLogs 角色查询失败 ({source_label}): {detail}")
            errors.append(f"{source_label}: {detail}")
            continue
        if character:
            return format_fflogs_character_logs(
                query, character, source_label, host, region
            )

    if errors:
        return "FFLogs 角色查询失败，请检查插件日志或稍后再试。"
    return f"未找到角色：{query.character_name} @ {query.server_name}。请检查角色名、服务器名或追加 国服/国际服。"


def party_optional_text(value) -> str:
    if value is None:
        return ""
    return html.unescape(str(value)).strip()


def format_party_time_left(seconds_value) -> str:
    try:
        seconds = int(float(seconds_value))
    except (TypeError, ValueError):
        return ""

    if seconds <= 0:
        return "已过期"
    minutes = max(1, seconds // 60)
    if minutes < 60:
        return f"剩余 {minutes} 分"
    hours = minutes // 60
    rest_minutes = minutes % 60
    if rest_minutes:
        return f"剩余 {hours} 小时 {rest_minutes} 分"
    return f"剩余 {hours} 小时"


def format_party_updated_at(updated_at: str) -> str:
    if not updated_at:
        return ""
    try:
        updated_time = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        now = datetime.now(updated_time.tzinfo)
        seconds = int((now - updated_time).total_seconds())
    except ValueError:
        return f"更新 {updated_at}"

    if seconds < 60:
        return "刚刚更新"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟前更新"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} 小时前更新"
    return f"{hours // 24} 天前更新"


def format_party_finder_api_entry(index: int, listing: dict) -> str:
    category_now = party_optional_text(listing.get("category"))
    if not category_now and listing.get("category_id") is not None:
        category_label = PARTY_CATEGORY_ID_LABELS.get(
            int(listing["category_id"]), f"分类ID {listing['category_id']}"
        )
    else:
        category_label = PARTY_CATEGORY_LABELS.get(
            category_now, category_now or "未知分类"
        )
    duty_now = party_optional_text(listing.get("duty")) or "无"
    description_now = party_optional_text(listing.get("description")) or "无描述"
    creator_now = (
        party_optional_text(listing.get("name") or listing.get("player_name")) or "未知"
    )
    world_now = party_optional_text(
        listing.get("created_world") or listing.get("home_world")
    )
    if not world_now:
        world_id = listing.get("created_world_id") or listing.get("home_world_id")
        world_now = f"世界ID {world_id}" if world_id else "未知世界"

    filled = listing.get("slots_filled")
    available = listing.get("slots_available")
    total_now = ""
    if filled is not None and available is not None:
        try:
            total_now = f"{filled}/{int(available)}"
        except (TypeError, ValueError):
            total_now = f"{filled}/{available}"

    item_level = listing.get("min_item_level")
    item_level_text = f"IL {item_level}" if item_level else ""
    time_left = format_party_time_left(
        listing.get("time_left") or listing.get("time_left_seconds")
    )
    updated_text = format_party_updated_at(
        party_optional_text(listing.get("updated_at"))
    )
    meta_parts = [
        f"{creator_now} @ {world_now}",
        total_now,
        item_level_text,
        time_left,
        updated_text,
    ]
    meta_text = " | ".join(part for part in meta_parts if part)

    text_now = f"{index:02d}. [{category_label}] {duty_now}\n"
    text_now += f"    {truncate_text(description_now, 86)}\n"
    text_now += f"    {meta_text}\n"
    return text_now


def xivapi_field_text(row: dict | None, *path: str) -> str:
    current = row.get("fields", {}) if isinstance(row, dict) else {}
    for part in path:
        if not isinstance(current, dict):
            return ""
        current = current.get(part)
        if isinstance(current, dict) and "fields" in current:
            current = current["fields"]
    return party_optional_text(current)


async def get_xivapi_sheet_rows(
    sheet: str, row_ids: set[int], fields: str
) -> dict[int, dict]:
    ids = sorted(row_id for row_id in row_ids if row_id)
    if not ids:
        return {}

    params = urlencode(
        {
            "rows": ",".join(str(row_id) for row_id in ids),
            "fields": fields,
            "language": "chs",
        }
    )
    payload = await aiohttp_get(
        f"{XIVAPI_BASE_URL}/sheet/{sheet}?{params}", use_api_user_agent=True
    )
    if not isinstance(payload, dict):
        return {}

    rows = payload.get("rows")
    if not isinstance(rows, list):
        return {}
    return {
        int(row["row_id"]): row
        for row in rows
        if isinstance(row, dict) and row.get("row_id") is not None
    }


async def load_cn_world_names() -> dict[str, dict]:
    global CN_WORLD_NAME_CACHE
    if CN_WORLD_NAME_CACHE is not None:
        return CN_WORLD_NAME_CACHE

    worlds = {}
    after = None
    seen_after = set()
    while True:
        query = {
            "fields": "Name,DataCenter.Name",
            "language": "chs",
            "limit": 500,
        }
        if after is not None:
            query["after"] = after
        payload = await aiohttp_get(
            f"{XIVAPI_BASE_URL}/sheet/World?{urlencode(query)}",
            use_api_user_agent=True,
        )
        rows = payload.get("rows") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows:
            break

        for row in rows:
            if not isinstance(row, dict):
                continue
            row_id = row.get("row_id")
            name = xivapi_field_text(row, "Name")
            data_centre = xivapi_field_text(row, "DataCenter", "Name")
            if (
                row_id
                and row_id >= 1000
                and name
                and data_centre in CN_WORLD_DATA_CENTRES
            ):
                worlds[name] = {
                    "id": int(row_id),
                    "data_centre": data_centre,
                    "name": name,
                }

        last_row_id = rows[-1].get("row_id")
        if not last_row_id or last_row_id in seen_after:
            break
        seen_after.add(last_row_id)
        if last_row_id >= 65535:
            break
        after = last_row_id

    CN_WORLD_NAME_CACHE = worlds
    return worlds


async def resolve_party_world(search_terms: list[str]) -> tuple[dict | None, list[str]]:
    if not search_terms:
        return None, search_terms
    worlds = await load_cn_world_names()
    for index, term in enumerate(search_terms):
        world = worlds.get(term)
        if world:
            remain_terms = search_terms[:index] + search_terms[index + 1 :]
            return world, remain_terms
    return None, search_terms


def parse_house_number(value: str) -> int | None:
    value = value.strip()
    if value.isdigit():
        return int(value)
    chinese_digits = {
        "零": 0,
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    if not value:
        return None
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        tens = chinese_digits.get(left, 1 if left == "" else None)
        ones = chinese_digits.get(right, 0 if right == "" else None)
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    return chinese_digits.get(value)


def normalize_house_area_name(value: str | None) -> str | None:
    if not value:
        return None
    return HOUSE_AREA_ALIASES.get(value, value if value in HOUSE_AREA_NAMES else None)


def parse_house_area_token(token: str) -> tuple[str | None, str]:
    area_labels = sorted(
        set(HOUSE_AREA_NAMES) | set(HOUSE_AREA_ALIASES), key=len, reverse=True
    )
    for label in area_labels:
        if token.startswith(label):
            return normalize_house_area_name(label), token[len(label) :]
    return None, token


def parse_house_query(query: str) -> HouseQuery:
    parts = query.split()
    server_name = parts[0] if parts else None
    area_name = None
    size_name = None
    ward = None
    plot_id = None
    limit = HOUSE_DEFAULT_LISTINGS

    for raw_part in parts[1:]:
        part = raw_part.strip().upper()
        if not part:
            continue
        if part in HOUSE_SIZE_NAMES:
            size_name = part
            continue
        if part.isdigit():
            limit = max(1, min(int(part), HOUSE_MAX_LISTINGS))
            continue

        area_from_token, rest = parse_house_area_token(raw_part.strip())
        if area_from_token:
            area_name = area_from_token
            if not rest:
                continue
        else:
            rest = raw_part.strip()

        ward_match = re.search(r"([0-9]+|[一二三四五六七八九十]+)区", rest)
        if ward_match:
            ward = parse_house_number(ward_match.group(1))

        plot_match = re.search(r"([0-9]+|[一二三四五六七八九十]+)号", rest)
        if plot_match:
            plot_id = parse_house_number(plot_match.group(1))

        normalized_area = normalize_house_area_name(raw_part.strip())
        if normalized_area:
            area_name = normalized_area

    return HouseQuery(
        server_name=server_name,
        area_name=area_name,
        size_name=size_name,
        ward=ward,
        plot_id=plot_id,
        limit=limit,
    )


async def resolve_house_server_id(server_name: str) -> int | None:
    if server_name in HOUSE_SERVER_IDS:
        return HOUSE_SERVER_IDS[server_name]

    try:
        worlds = await load_cn_world_names()
    except Exception as exc:
        logger.warning(f"房屋服务器名解析失败: {exc}")
        return None

    world = worlds.get(server_name)
    if world:
        return int(world["id"])
    return None


def format_house_time(timestamp_value) -> str:
    try:
        timestamp = int(timestamp_value)
    except (TypeError, ValueError):
        return "未知"
    if timestamp <= 0:
        return "未知"
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError):
        return "未知"


def house_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def house_index_label(labels: list[str], value, fallback: str) -> str:
    index = house_int(value, -1)
    if 0 <= index < len(labels):
        return labels[index]
    return f"{fallback}({value})" if value is not None else fallback


async def fetch_house_sales(server_id: int) -> list[dict] | None:
    query = urlencode({"server": server_id, "ts": int(datetime.now().timestamp())})
    payload = await aiohttp_get(f"{HOUSE_API_URL}?{query}", use_api_user_agent=True)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return None


async def create_house_text(query: HouseQuery) -> str:
    if not query.server_name:
        return (
            "看空房格式：房子 服务器名 (主城/房区/房号) (房子大小) (数量)\n"
            f"主城名：{'、'.join(HOUSE_AREA_NAMES)}\n"
            f"房子大小：{'、'.join(HOUSE_SIZE_NAMES)}\n"
            f"默认返回前 {HOUSE_DEFAULT_LISTINGS} 条，最多 {HOUSE_MAX_LISTINGS} 条\n"
            "例：房子 银泪湖 森都 S\n"
            "例：房子 银泪湖 森都一区\n"
            "例：房子 银泪湖 森都一区5号\n"
            "例：房子 银泪湖 森都 5号"
        )
    if query.plot_id is not None and not query.area_name:
        return "房号筛选需要带主城区域，例如：房子 银泪湖 森都5号 或 房子 银泪湖 森都一区5号"
    if query.ward is not None and not query.area_name:
        return "房区筛选需要带主城区域，例如：房子 银泪湖 森都一区"

    server_id = await resolve_house_server_id(query.server_name)
    if not server_id:
        return "检查一下服务器名称呀：\n" + "、".join(HOUSE_SERVER_IDS)

    sales = await fetch_house_sales(server_id)
    if sales is None:
        return "房屋数据获取失败，请稍后再试"

    matched = sales
    filters = []
    if query.area_name:
        area_index = HOUSE_AREA_NAMES.index(query.area_name)
        matched = [
            item for item in matched if house_int(item.get("Area"), -1) == area_index
        ]
        filters.append(query.area_name)
    if query.ward is not None:
        matched = [
            item for item in matched if house_int(item.get("Slot"), 0) + 1 == query.ward
        ]
        filters.append(f"{query.ward}区")
    if query.plot_id is not None:
        matched = [
            item for item in matched if house_int(item.get("ID"), 0) == query.plot_id
        ]
        filters.append(f"{query.plot_id}号")
    if query.size_name:
        size_index = HOUSE_SIZE_NAMES.index(query.size_name)
        matched = [
            item for item in matched if house_int(item.get("Size"), -1) == size_index
        ]
        filters.append(query.size_name)
    matched.sort(
        key=lambda item: (house_int(item.get("Slot")), house_int(item.get("ID")))
    )

    filter_text = " ".join(filters) if filters else "全部"
    total = len(matched)
    shown = matched[: query.limit]
    title = f"【{query.server_name}空房】筛选：{filter_text}  数量：{total}"
    if not matched:
        return title + "\n没空房子了"

    lines = [title, "────────────────────────"]
    if total > len(shown):
        lines.append(
            f"仅显示前 {len(shown)} 条，可在命令末尾添加数量，最多 {HOUSE_MAX_LISTINGS} 条。"
        )
    for index, item in enumerate(shown, start=1):
        area_name = house_index_label(HOUSE_AREA_NAMES, item.get("Area"), "未知区域")
        size_name = house_index_label(HOUSE_SIZE_NAMES, item.get("Size"), "未知大小")
        slot = house_int(item.get("Slot")) + 1
        plot_id = house_int(item.get("ID"))
        price = house_int(item.get("Price"))
        purchase_type = HOUSE_PURCHASE_TYPES.get(
            house_int(item.get("PurchaseType"), -1), "未知"
        )
        region_type = HOUSE_REGION_TYPES.get(
            house_int(item.get("RegionType"), -1), "未知"
        )
        last_seen = format_house_time(item.get("LastSeen"))
        lines.append(
            f"{index:02d}. {area_name}{slot}区 {plot_id}号 {size_name} "
            f"| {price // 10000}万 | {purchase_type} | {region_type} | 更新 {last_seen}"
        )
    return "\n".join(lines)


async def enrich_party_finder_v2_listings(listings: list[dict]) -> list[dict]:
    world_ids = set()
    duty_ids = set()
    for listing in listings:
        for key in ("created_world_id", "home_world_id"):
            try:
                world_ids.add(int(listing.get(key) or 0))
            except (TypeError, ValueError):
                pass
        try:
            duty_ids.add(int(listing.get("duty_id") or 0))
        except (TypeError, ValueError):
            pass

    world_rows, duty_rows = await asyncio.gather(
        get_xivapi_sheet_rows("World", world_ids, "Name,DataCenter.Name"),
        get_xivapi_sheet_rows(
            "ContentFinderCondition", duty_ids, "Name,ContentType.Name"
        ),
    )

    enriched = []
    for listing in listings:
        item = dict(listing)
        created_world = world_rows.get(int(item.get("created_world_id") or 0))
        home_world = world_rows.get(int(item.get("home_world_id") or 0))
        duty = duty_rows.get(int(item.get("duty_id") or 0))

        item["created_world"] = xivapi_field_text(created_world, "Name")
        item["home_world"] = xivapi_field_text(home_world, "Name")
        item["datacenter"] = xivapi_field_text(created_world, "DataCenter", "Name")
        duty_id = int(item.get("duty_id") or 0)
        item["duty"] = (
            xivapi_field_text(duty, "Name")
            or PARTY_DUTY_ID_NAME_OVERRIDES.get(duty_id)
            or "无"
        )
        enriched.append(item)
    return enriched


def party_finder_matches_search(listing: dict, search_text: str | None) -> bool:
    if not search_text:
        return True
    search_area = " ".join(
        party_optional_text(listing.get(key))
        for key in (
            "duty",
            "description",
            "name",
            "player_name",
            "created_world",
            "home_world",
            "datacenter",
        )
    ).lower()
    return search_text.lower() in search_area


def extract_party_finder_listings(payload) -> list[dict] | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("listings"), list):
        return data["listings"]
    if isinstance(payload.get("listings"), list):
        return payload["listings"]
    return None


async def get_party_finder_entries_api_v1(
    data_centre: str | None = None,
    world_name: str | None = None,
    category: str | None = None,
    search_text: str | None = None,
    job_ids: list[int] | None = None,
    limit: int = 10,
) -> list[dict] | None:
    params = {
        "page": 1,
        "per_page": max(1, min(limit, 100)),
    }
    if data_centre:
        params["datacenter"] = data_centre
    if world_name:
        params["world"] = world_name
    if category:
        params["category"] = category
    if search_text:
        params["search"] = search_text
    if job_ids:
        params["jobs"] = ",".join(str(job_id) for job_id in job_ids)
    payload = await aiohttp_get(
        f"{PARTY_FINDER_API_V1_URL}?{urlencode(params)}", use_api_user_agent=True
    )
    listings = extract_party_finder_listings(payload)
    if listings is None:
        return None

    entries = []
    for index, listing in enumerate(listings[:limit], start=1):
        if not isinstance(listing, dict):
            continue
        entries.append(normalize_party_finder_entry(index, listing))
    return entries


async def get_party_finder_entries_api_v2(
    data_centre: str | None = None,
    world_id: int | None = None,
    category: str | None = None,
    search_text: str | None = None,
    job_ids: list[int] | None = None,
    duty_ids: list[int] | None = None,
    limit: int = 10,
) -> list[dict] | None:
    fetch_limit = 100 if search_text and not duty_ids else max(1, min(limit, 100))
    params = {
        "page": 1,
        "per_page": fetch_limit,
    }
    if data_centre:
        params["datacenter"] = data_centre
    if category:
        category_id = PARTY_CATEGORY_IDS.get(category)
        if category_id is None:
            return None
        params["category_id"] = category_id
    if job_ids:
        params["job_ids"] = ",".join(str(job_id) for job_id in job_ids)

    scope_param_sets = []
    if world_id:
        created_world_params = dict(params)
        created_world_params["created_world_id"] = world_id
        home_world_params = dict(params)
        home_world_params["home_world_id"] = world_id
        scope_param_sets.extend([created_world_params, home_world_params])
    else:
        scope_param_sets.append(params)

    param_sets = []
    if duty_ids:
        for scope_params in scope_param_sets:
            for duty_id in duty_ids:
                duty_params = dict(scope_params)
                duty_params["duty_id"] = duty_id
                param_sets.append(duty_params)
    else:
        param_sets = scope_param_sets

    listings = []
    seen_ids = set()
    for param_set in param_sets:
        payload = await aiohttp_get(
            f"{PARTY_FINDER_API_V2_URL}?{urlencode(param_set)}",
            use_api_user_agent=True,
        )
        payload_listings = extract_party_finder_listings(payload)
        if payload_listings is None:
            return None
        for listing in payload_listings:
            if not isinstance(listing, dict):
                continue
            listing_id = listing.get("id")
            if listing_id in seen_ids:
                continue
            seen_ids.add(listing_id)
            listings.append(listing)

    if not listings:
        return []

    valid_listings = [
        listing for listing in listings[:fetch_limit] if isinstance(listing, dict)
    ]
    enriched_listings = await enrich_party_finder_v2_listings(valid_listings)
    filtered_listings = [
        listing
        for listing in enriched_listings
        if duty_ids or party_finder_matches_search(listing, search_text)
    ][:limit]
    return [
        normalize_party_finder_entry(index, listing)
        for index, listing in enumerate(filtered_listings, start=1)
    ]


async def get_party_finder_entries_html(
    data_centre: str | None = None,
    world_name: str | None = None,
    category: str | None = None,
    search_text: str | None = None,
    job_ids: list[int] | None = None,
    limit: int = 10,
) -> list[dict]:
    if job_ids:
        return []
    all_info = await aiohttp_get(PARTY_FINDER_URL, res_type="text")
    if not all_info:
        raise ValueError("获取招募板失败")

    category_list = re.findall(r'data-pf-category="(.*?)"', all_info)
    data_centre_list = re.findall(r'data-centre=".*?"', all_info)
    duty_list = re.findall(r'<div class="duty .*?</div>', all_info)
    description_list = re.findall(r'<div class="description">.*?</div>', all_info)
    meta_list = re.findall(r'class="text">.*?</span>', all_info)
    total_list = re.findall(r'<div class="total">.*?</div>', all_info)

    entries = []
    index_now = 1
    entry_count = min(
        len(category_list),
        len(data_centre_list),
        len(duty_list),
        len(description_list),
        len(meta_list) // 4,
        len(total_list),
    )
    for index in range(entry_count):
        if data_centre and data_centre not in data_centre_list[index]:
            continue
        category_now = html.unescape(category_list[index])
        if category and category_now != category:
            continue

        data_centre_now = data_centre_list[index].split('"')[1].replace('"', "")
        duty_now = strip_html(duty_list[index])
        description_now = description_list[index].split(">", 1)[1].replace("</div>", "")
        if "</span>" in description_now:
            description_now = description_now.split("</span>", 1)[1]
        description_now = strip_html(description_now)
        creator_now = strip_html(meta_list[index * 4])
        world_now = strip_html(meta_list[index * 4 + 1])
        if world_name and world_name not in world_now:
            continue
        expires_now = strip_html(meta_list[index * 4 + 2])
        updated_now = strip_html(meta_list[index * 4 + 3])
        total_now = strip_html(total_list[index])
        if search_text:
            search_area = (
                f"{duty_now} {description_now} {creator_now} {world_now}".lower()
            )
            if search_text.lower() not in search_area:
                continue
        entries.append(
            normalize_party_finder_entry(
                index_now,
                {
                    "category": category_now,
                    "duty": duty_now,
                    "description": description_now,
                    "name": creator_now,
                    "created_world": world_now,
                    "home_world": world_now,
                    "datacenter": data_centre_now,
                    "total_text": total_now,
                    "time_left": expires_now,
                    "updated_at": updated_now,
                },
            )
        )
        index_now += 1
        if len(entries) >= limit:
            break
    return entries


async def get_party_finder_entries(
    data_centre: str | None = None,
    world_name: str | None = None,
    world_id: int | None = None,
    category: str | None = None,
    search_text: str | None = None,
    job_ids: list[int] | None = None,
    duty_ids: list[int] | None = None,
    limit: int = 10,
) -> list[dict]:
    try:
        v2_entries = await get_party_finder_entries_api_v2(
            data_centre,
            world_id=world_id,
            category=category,
            search_text=search_text,
            job_ids=job_ids,
            duty_ids=duty_ids,
            limit=limit,
        )
        if v2_entries is not None:
            return v2_entries
    except Exception as exc:
        logger.warning(f"招募板 API v2 获取失败，尝试 API v1: {exc}")
    try:
        api_entries = await get_party_finder_entries_api_v1(
            data_centre,
            world_name=world_name,
            category=category,
            search_text=search_text,
            job_ids=job_ids,
            limit=limit,
        )
        if api_entries is not None:
            return api_entries
    except Exception as exc:
        logger.warning(f"招募板 API v1 获取失败，尝试 HTML 兜底: {exc}")

    return await get_party_finder_entries_html(
        data_centre,
        world_name=world_name,
        category=category,
        search_text=search_text,
        job_ids=job_ids,
        limit=limit,
    )


@register(
    "astrbot_plugin_tataru",
    "aaron-li / Codex",
    "FF14 塔塔露 AstrBot 插件",
    PLUGIN_VERSION,
    "https://github.com/jawwe/astrbot_plugin_tataru",
)
class TataruPlugin(Star):
    def __init__(self, context: Context, config=None):
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
        logger.info("Tataru AstrBot plugin initialized.")

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

    def risingstones_owner_credentials(self) -> RisingstonesCredentials | None:
        return configured_risingstones_credentials(self.config)

    def risingstones_checkin_hour(self) -> int:
        try:
            hour = int(self.config.get("risingstones_checkin_hour", 8))
        except (TypeError, ValueError):
            hour = 8
        return hour if 0 <= hour <= 23 else 8

    def _register_admin_web_apis(self, context: Context) -> None:
        """Register the authenticated endpoints used by the admin Page."""
        endpoints = [
            ("overview", self.admin_overview, ["GET"], "Get Tataru admin overview"),
            ("settings", self.admin_settings_get, ["GET"], "Get Tataru settings"),
            ("settings", self.admin_settings_save, ["POST"], "Save Tataru settings"),
            ("features", self.admin_features_get, ["GET"], "Get Tataru feature flags"),
            (
                "features",
                self.admin_features_save,
                ["POST"],
                "Save Tataru feature flags",
            ),
            ("tests/proxy", self.admin_test_proxy, ["POST"], "Test Tataru proxy"),
            ("tests/fflogs", self.admin_test_fflogs, ["POST"], "Test FFLogs access"),
            (
                "tests/risingstones",
                self.admin_test_risingstones,
                ["POST"],
                "Test Rising Stones access",
            ),
            (
                "tests/sources",
                self.admin_test_sources,
                ["POST"],
                "Test public data sources",
            ),
            (
                "risingstones/owner-curl",
                self.admin_owner_curl_get,
                ["GET"],
                "Get owner Rising Stones cURL status",
            ),
            (
                "risingstones/owner-curl",
                self.admin_owner_curl_save,
                ["POST"],
                "Save owner Rising Stones cURL",
            ),
            (
                "risingstones/accounts",
                self.admin_risingstones_accounts,
                ["GET"],
                "List masked Rising Stones accounts",
            ),
            (
                "database/summary",
                self.admin_database_summary,
                ["GET"],
                "Get Tataru database summary",
            ),
            (
                "database/backup",
                self.admin_database_backup,
                ["POST"],
                "Back up Tataru admin database",
            ),
            (
                "database/clear-cache",
                self.admin_clear_cache,
                ["POST"],
                "Clear Tataru cache files",
            ),
            ("activity", self.admin_activity, ["GET"], "Get Tataru admin activity"),
        ]
        for suffix, handler, methods, description in endpoints:
            context.register_web_api(
                f"/{PLUGIN_NAME}/admin/{suffix}", handler, methods, description
            )

    def _admin_risingstones_summary(self) -> dict[str, int]:
        return self.risingstones_accounts.summary()

    def _persist_plugin_config(self) -> None:
        """Persist Page-managed settings when AstrBot passes an AstrBotConfig."""
        save_config = getattr(self.config, "save_config", None)
        if callable(save_config):
            save_config()

    async def admin_overview(self):
        """Return live plugin state without exposing configuration secrets."""
        summary = self._admin_risingstones_summary()
        overview = build_admin_overview(
            self.admin_store,
            version=PLUGIN_VERSION,
            started_at=self.started_at,
            risingstones_accounts=summary["accounts"],
            auto_checkin_accounts=summary["auto_checkin_accounts"],
        )
        overview.update(
            {
                "cache_size": plugin_cache_size(self.cache_dir),
                "tasks": {
                    "calendar": bool(
                        self.calendar_task and not self.calendar_task.done()
                    ),
                    "risingstones_checkin": bool(
                        self.risingstones_checkin_task
                        and not self.risingstones_checkin_task.done()
                    ),
                },
                "network": {
                    "proxy_enabled": NETWORK_SETTINGS.proxy_enabled,
                    "proxy_error": NETWORK_SETTINGS.proxy_error or "",
                },
                "sources": {
                    "fflogs_configured": bool(
                        self.fflogs_client_id() and self.fflogs_client_secret()
                    ),
                    "risingstones_owner_configured": bool(
                        self.risingstones_owner_credentials()
                    ),
                    "weibo_cookie_configured": bool(self.weibo_cookie()),
                },
            }
        )
        return page_json_response(overview)

    async def admin_features_get(self):
        return page_json_response({"features": self.admin_store.get_feature_flags()})

    async def admin_settings_get(self):
        return page_json_response({"settings": admin_settings_public_view(self.config)})

    async def admin_settings_save(self):
        payload = await get_page_request_json()
        try:
            updates = validate_admin_settings_update(payload)
        except ValueError as exc:
            return page_error_response(str(exc))
        for key, value in updates.items():
            self.config[key] = value
        configure_network_settings(self.config)
        self._persist_plugin_config()
        self.admin_store.record_activity("settings", "success", sorted(updates))
        return await self.admin_settings_get()

    async def admin_features_save(self):
        payload = await get_page_request_json()
        if not isinstance(payload, dict) or not isinstance(
            payload.get("features"), dict
        ):
            return page_error_response("功能开关必须是 JSON 对象。")
        raw_features = payload["features"]
        if any(not isinstance(value, bool) for value in raw_features.values()):
            return page_error_response("功能开关值必须是布尔值。")
        flags = self.admin_store.set_feature_flags(raw_features)
        self.admin_store.record_activity("features", "success", "updated feature flags")
        return page_json_response({"features": flags})

    async def _admin_http_test(
        self,
        *,
        source: str,
        url: str,
        use_api_user_agent: bool = False,
    ):
        started = time.monotonic()
        try:
            response = await aiohttp_request(
                "GET",
                url,
                res_type="text",
                timeout_seconds=12,
                use_api_user_agent=use_api_user_agent,
            )
            result = {
                "source": source,
                "success": response.status == 200,
                "status": response.status,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "message": "请求完成" if response.status == 200 else "服务返回异常状态",
            }
        except Exception as exc:
            result = {
                "source": source,
                "success": False,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "message": f"请求失败：{type(exc).__name__}",
            }
        safe_result = sanitize_admin_test_result(result)
        self.admin_store.record_activity(
            f"test.{source}",
            "success" if safe_result["success"] else "failed",
            safe_result,
        )
        return page_json_response(safe_result)

    async def admin_test_proxy(self):
        if not NETWORK_SETTINGS.proxy_enabled:
            return page_error_response("代理未启用，无法执行代理连接测试。")
        if NETWORK_SETTINGS.proxy_error:
            return page_error_response(NETWORK_SETTINGS.proxy_error)
        return await self._admin_http_test(
            source="proxy", url="https://www.example.com/", use_api_user_agent=False
        )

    async def admin_test_fflogs(self):
        return await self._admin_http_test(
            source="fflogs", url="https://cn.fflogs.com/", use_api_user_agent=False
        )

    async def admin_test_sources(self):
        return await self._admin_http_test(
            source="calendar",
            url=CALENDAR_SOURCES["国服"]["primary"],
            use_api_user_agent=False,
        )

    async def admin_test_risingstones(self):
        credentials = self.risingstones_owner_credentials()
        if not credentials:
            return page_error_response(
                "请先保存有效的石之家 getUserInfo cURL（bash）。"
            )
        started = time.monotonic()
        try:
            profile = await risingstones_verify_credential(credentials)
            result = {
                "source": "risingstones",
                "success": True,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "message": "主人登录态有效",
                "character_name": str(profile.get("character_name", "") or ""),
                "server_name": str(profile.get("server_name", "") or ""),
            }
        except Exception as exc:
            result = {
                "source": "risingstones",
                "success": False,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "message": f"验证失败：{type(exc).__name__}",
            }
        safe_result = sanitize_admin_test_result(result)
        self.admin_store.record_activity(
            "test.risingstones",
            "success" if safe_result["success"] else "failed",
            safe_result,
        )
        return page_json_response(safe_result)

    async def admin_owner_curl_get(self):
        curl_value = self.admin_store.get_setting("risingstones_owner_curl")
        return page_json_response(
            {
                "configured": bool(curl_value),
                "summary": mask_admin_display_secret(curl_value) if curl_value else "",
            }
        )

    async def admin_owner_curl_save(self):
        payload = await get_page_request_json()
        curl_value = payload.get("curl", "") if isinstance(payload, dict) else ""
        if not isinstance(curl_value, str) or not validate_owner_curl_for_admin(
            curl_value
        ):
            return page_error_response(
                "仅接受 Chrome DevTools 复制的 getUserInfo cURL（bash）内容。"
            )
        curl_value = curl_value.strip()
        self.admin_store.set_setting("risingstones_owner_curl", curl_value)
        self.config["risingstones_owner_curl"] = curl_value
        self._persist_plugin_config()
        self.admin_store.record_activity(
            "risingstones.owner_curl", "success", "updated"
        )
        return await self.admin_owner_curl_get()

    async def admin_risingstones_accounts(self):
        return page_json_response(
            {"accounts": self.risingstones_accounts.admin_rows(limit=100)}
        )

    async def admin_database_summary(self):
        risingstones = self._admin_risingstones_summary()
        admin_database = self.admin_store.database_summary()
        return page_json_response(
            {
                "admin": admin_database,
                "risingstones": {
                    **risingstones,
                    "size": (
                        RISINGSTONES_DB_PATH.stat().st_size
                        if RISINGSTONES_DB_PATH.exists()
                        else 0
                    ),
                },
                "cache_size": plugin_cache_size(self.cache_dir),
                "fflogs_tracking": {
                    "reserved": True,
                    "count": int(
                        admin_database["row_counts"].get("fflogs_tracking_accounts", 0)
                    ),
                    "message": "仅预留存储结构，尚未启用角色跟踪。",
                },
            }
        )

    async def admin_database_backup(self):
        import shutil

        backup_dir = DATA_DIR / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(RISINGSTONES_TIMEZONE).strftime("%Y%m%d-%H%M%S")
        backup_path = backup_dir / f"admin-{timestamp}.sqlite3"
        try:
            shutil.copy2(self.admin_store.path, backup_path)
        except OSError as exc:
            self.admin_store.record_activity(
                "database.backup", "failed", type(exc).__name__
            )
            return page_error_response("数据库备份失败。", status_code=500)
        self.admin_store.record_activity("database.backup", "success", backup_path.name)
        return page_json_response({"created": backup_path.name})

    async def admin_clear_cache(self):
        payload = await get_page_request_json()
        if not isinstance(payload, dict) or payload.get("confirmed") is not True:
            return page_error_response("清理缓存需要明确确认。")
        removed = 0
        if self.cache_dir.exists():
            for path in self.cache_dir.rglob("*"):
                if path.is_file():
                    path.unlink()
                    removed += 1
        self.admin_store.record_activity("cache.clear", "success", f"removed={removed}")
        return page_json_response({"removed": removed, "cache_size": 0})

    async def admin_activity(self):
        return page_json_response(
            {"activity": self.admin_store.recent_activity(limit=100)}
        )

    def render_text_image(
        self, text: str, output_path: Path, width_now: int = 20
    ) -> None:
        text_to_image(
            text,
            output_path,
            width_now=width_now,
            font_path=self.configured_font_path(),
        )

    @filter.command("帮帮忙")
    @debug_command("帮帮忙")
    async def help(self, event: AstrMessageEvent):
        """显示塔塔露当前指令。"""
        yield event.plain_result(create_help_text())

    @filter.command("选门")
    @debug_command("选门")
    async def precious(self, event: AstrMessageEvent):
        """帮你选藏宝洞的门。"""
        yield event.plain_result("塔塔露在藏宝洞中横冲直撞！\n" + random_left_right())

    @filter.command("仙人彩")
    @debug_command("仙人彩")
    async def lottery(self, event: AstrMessageEvent):
        """帮你选每周仙人仙彩数字。"""
        yield event.plain_result("塔塔露觉得这个可以！\n" + random_lottery())

    @filter.command("日历")
    @debug_command("日历")
    async def calendar(self, event: AstrMessageEvent):
        """获取FF近期活动日历。"""
        requested_server = command_args(event.message_str, "日历") or None
        server = normalize_calendar_server(
            requested_server, self.default_calendar_server()
        )
        await self.ensure_calendar(server)
        yield event.plain_result(self.create_calendar_text(server))

    @filter.command("暖暖")
    @debug_command("暖暖")
    async def nuannuan(self, event: AstrMessageEvent):
        """本周时尚品鉴作业。"""
        result = await self.create_nuannuan_result(event)
        yield result

    @filter.command("攻略")
    @debug_command("攻略")
    async def dungeon_note(self, event: AstrMessageEvent):
        """查简单副本攻略。"""
        dungeon_info = command_args(event.message_str, "攻略")
        result_text, as_text = await get_dungeon_note(dungeon_info)
        if as_text:
            yield event.plain_result(result_text)
            return

        image_path = self.cache_dir / "dungeon_note.jpg"
        self.render_text_image(result_text, image_path, width_now=25)
        yield event.image_result(str(image_path))

    @filter.command("石之家")
    @debug_command("石之家")
    async def risingstones_posts(self, event: AstrMessageEvent):
        """查询石之家公开内容和招募。"""
        raw_query = command_args(event.message_str, "石之家")
        feature = risingstones_feature_for_query(raw_query)
        if not feature_enabled(self.admin_store, feature):
            yield event.plain_result("该石之家功能已在塔塔露管理台中停用。")
            return
        private_result = await self.risingstones_private_action(event, raw_query)
        if isinstance(private_result, RisingstonesGlamourResponse):
            for message in private_result.messages:
                components = []
                if message.image_url:
                    components.append(Comp.Image.fromURL(message.image_url))
                components.append(Comp.Plain(message.text))
                yield event.chain_result(components)
            return
        if private_result is not None:
            yield event.plain_result(private_result)
            return
        if raw_query.split(maxsplit=1)[:1] == ["招募"]:
            query = parse_risingstones_recruit_query(
                raw_query.removeprefix("招募").strip()
            )
            try:
                rows = await fetch_risingstones_recruits(query)
            except Exception as exc:
                logger.warning(f"石之家招募查询失败: {exc}")
                yield event.plain_result("石之家招募查询失败，请稍后再试")
                return
            if not rows:
                kind_labels = {
                    "party": "副本",
                    "beginner": "萌新",
                    "other": "其他",
                    "rp": "RP",
                }
                keyword_label = f"「{query.keyword}」" if query.keyword else "当前条件"
                yield event.plain_result(
                    f"石之家{kind_labels[query.kind]}招募中没有找到{keyword_label}的内容"
                )
                return
            yield event.plain_result(format_risingstones_recruits(query, rows))
            return

        query = parse_risingstones_posts_query(raw_query)
        try:
            rows = await fetch_risingstones_posts(query)
        except Exception as exc:
            logger.warning(f"石之家内容查询失败: {exc}")
            yield event.plain_result("石之家内容查询失败，请稍后再试")
            return
        if not rows:
            kind_label = "攻略" if query.kind == "strat" else "帖子"
            keyword_label = f"「{query.keyword}」" if query.keyword else "当前条件"
            yield event.plain_result(
                f"石之家{kind_label}中没有找到{keyword_label}的内容"
            )
            return
        yield event.plain_result(format_risingstones_posts(query, rows))

    async def risingstones_private_action(
        self, event: AstrMessageEvent, raw_query: str
    ) -> str | RisingstonesGlamourResponse | None:
        """Handle private credential and check-in operations without exposing secrets."""
        action, _, argument = raw_query.partition(" ")
        action = action.strip()
        argument = argument.strip()
        personal_actions = {"绑定", "解绑", "签到", "自动签到", "我的", "通知", "统计"}
        session_actions = {"幻化", "部队"}
        if action not in personal_actions | session_actions:
            return None
        account_key = None
        credentials = None
        if action in personal_actions:
            if not is_risingstones_private_event(event):
                return "石之家本人信息、签到和自动签到仅支持私聊，并且需要先私聊完成账号绑定。"
            account_key = risingstones_account_key(event)
            if not account_key:
                return "无法识别当前私聊账号，请更换平台后重试。"

            if action == "绑定":
                if not argument:
                    return risingstones_binding_guide()
                credentials = parse_risingstones_binding(argument)
                if not credentials:
                    return (
                        "绑定信息格式不正确。请发送 `石之家 绑定` 获取 Chrome 控制台脚本，"
                        "并在 Network 中选择“以 cURL (bash) 格式复制”后，将完整内容私聊发送给机器人。"
                    )
                try:
                    profile = await risingstones_verify_credential(credentials)
                except Exception as exc:
                    logger.warning(f"石之家凭据验证失败: {exc}")
                    return "石之家凭据验证失败，请重新登录石之家后执行 `石之家 绑定` 获取新的绑定信息。"
                self.risingstones_accounts.set_credential(account_key, credentials)
                character_name = str(profile.get("character_name") or "已绑定角色")
                server = "@".join(
                    part
                    for part in (
                        str(profile.get("area_name") or "").strip(),
                        str(profile.get("group_name") or "").strip(),
                    )
                    if part
                )
                return f"石之家账号已绑定：{character_name}{f' @ {server}' if server else ''}"

            if action == "解绑":
                self.risingstones_accounts.remove(account_key)
                return "石之家账号凭据和自动签到设置已移除。"

            credentials = self.risingstones_accounts.get_credentials(account_key)
            if not credentials:
                return "尚未完成石之家账号绑定，请先在私聊发送：石之家 绑定"
        else:
            if is_risingstones_private_event(event):
                account_key = risingstones_account_key(event)
                credentials = (
                    self.risingstones_accounts.get_credentials(account_key)
                    if account_key
                    else None
                )
            credentials = credentials or self.risingstones_owner_credentials()
            if not credentials:
                return (
                    "石之家幻化和部队招待需要主人在插件设置中填写完整的 getUserInfo cURL（bash）内容，"
                    "或在私聊中发送 `石之家 绑定` 完成绑定。"
                )

        if action == "我的":
            try:
                payload = await risingstones_account_request(
                    credentials, "GET", "/home/userInfo/getUserInfo"
                )
            except Exception as exc:
                logger.warning(f"石之家档案查询失败: {exc}")
                return "石之家档案查询失败，请检查凭据是否过期后重新绑定。"
            data = payload.get("data")
            return (
                format_risingstones_profile(data)
                if isinstance(data, dict)
                else "石之家档案为空，请稍后再试。"
            )

        if action == "通知":
            try:
                payload = await risingstones_account_request(
                    credentials, "GET", "/home/sysMsg/getTip"
                )
            except Exception as exc:
                logger.warning(f"石之家通知查询失败: {exc}")
                return "石之家通知查询失败，请检查凭据是否过期后重新绑定。"
            data = payload.get("data")
            return (
                format_risingstones_notifications(data)
                if isinstance(data, dict)
                else "石之家通知为空，请稍后再试。"
            )

        if action == "统计":
            kind = parse_risingstones_stat_kind(argument)
            try:
                statistics = await risingstones_statistics(credentials, kind)
            except Exception as exc:
                logger.warning(f"石之家统计查询失败: {exc}")
                return "石之家统计查询失败，请检查凭据是否过期后重新绑定。"
            if not statistics:
                return "当前绑定角色没有可用的石之家统计记录。"
            return format_risingstones_statistics(statistics)

        if action == "幻化":
            query = parse_risingstones_glamour_query(argument)
            try:
                rows = await risingstones_glamour_rows(credentials, query)
            except ValueError as exc:
                return str(exc)
            except Exception as exc:
                logger.warning(f"石之家幻化查询失败: {exc}")
                return "石之家幻化查询失败，请检查私聊绑定信息或插件设置页的石之家 getUserInfo cURL（bash）内容。"
            if not rows:
                return "没有找到符合条件的石之家幻化投稿。"
            return RisingstonesGlamourResponse(
                messages=[
                    format_risingstones_glamour_message(query, row, index, len(rows))
                    for index, row in enumerate(rows, start=1)
                ]
            )

        if action == "部队":
            query = parse_risingstones_guild_query(argument)
            try:
                rows = await risingstones_guild_rows(credentials, query)
            except ValueError as exc:
                return str(exc)
            except Exception as exc:
                logger.warning(f"石之家部队招待查询失败: {exc}")
                return "石之家部队招待查询失败，请检查私聊绑定信息或插件设置页的石之家 cURL (bash) 配置。"
            if not rows:
                return "没有找到符合条件的石之家部队招待。"
            return format_risingstones_guilds(query, rows)

        if action == "自动签到":
            enabled = argument in {"开启", "开", "on", "ON"}
            disabled = argument in {"关闭", "关", "off", "OFF"}
            if not enabled and not disabled:
                return "自动签到格式：石之家 自动签到 开启 或 石之家 自动签到 关闭"
            self.risingstones_accounts.set_auto_checkin(account_key, enabled)
            if enabled:
                return f"石之家自动签到已开启，将在每日 {self.risingstones_checkin_hour():02d}:00 后执行。"
            return "石之家自动签到已关闭。"

        try:
            _, message = await risingstones_checkin(credentials)
        except Exception as exc:
            logger.warning(f"石之家手动签到失败: {exc}")
            return "石之家签到失败，请检查凭据是否过期后重新绑定。"
        day = datetime.now(RISINGSTONES_TIMEZONE).date().isoformat()
        self.risingstones_accounts.mark_attempt(account_key, day)
        self.risingstones_accounts.mark_checkin(account_key, day)
        return f"石之家签到结果：{message}"

    @filter.command("招募")
    @debug_command("招募")
    async def party_finder(self, event: AstrMessageEvent):
        """获取指定大区招募板信息。"""
        query = parse_party_finder_query(command_args(event.message_str, "招募"))
        if (
            not query.data_centre
            and not query.search_terms
            and not query.category
            and not query.job_ids
        ):
            yield event.plain_result(
                "查招募版格式：招募 (大区或服务器) (分类或关键词或职业) (数量)\n例：招募 陆行鸟 随机任务"
            )
            return

        try:
            world, search_terms = await resolve_party_world(query.search_terms)
        except Exception as exc:
            logger.warning(f"招募服务器名解析失败: {exc}")
            world, search_terms = None, query.search_terms
        search_text = " ".join(search_terms).strip() or None
        data_centre = query.data_centre
        if world:
            data_centre = data_centre or world["data_centre"]
        scope_label = world["name"] if world else (data_centre or "全服")
        duty_ids = await resolve_party_duty_ids(search_text)
        if duty_ids:
            logger.info(f"招募副本名解析为 duty_id: {search_text} -> {duty_ids}")

        try:
            entries = await get_party_finder_entries(
                data_centre,
                world_name=world["name"] if world else None,
                world_id=world["id"] if world else None,
                category=query.category,
                search_text=search_text,
                job_ids=query.job_ids,
                duty_ids=duty_ids,
                limit=query.limit,
            )
        except Exception as exc:
            logger.warning(f"招募板获取失败: {exc}")
            yield event.plain_result("招募板获取失败，请稍后再试")
            return

        if not entries:
            category_hint = (
                f"「{PARTY_CATEGORY_LABELS.get(query.category, query.category)}」"
                if query.category
                else ""
            )
            search_hint = f"包含「{search_text}」的" if search_text else ""
            job_hint = "指定职业的" if query.job_ids else ""
            yield event.plain_result(
                f"当前{scope_label}{category_hint}{search_hint}{job_hint}无人上传招募信息"
            )
            return

        image_components = []
        for index in range(0, len(entries), PARTY_FINDER_CARDS_PER_IMAGE):
            image_path = (
                self.cache_dir
                / f"party_finder_{index // PARTY_FINDER_CARDS_PER_IMAGE}.jpg"
            )
            render_party_finder_cards(
                entries[index : index + PARTY_FINDER_CARDS_PER_IMAGE],
                image_path,
                font_path=self.configured_font_path(),
                icon_font_path=self.ffxiv_icon_font_path(),
            )
            image_components.append(Comp.Image.fromFileSystem(str(image_path)))

        yield event.chain_result(image_components)

    @filter.command("看看微博")
    @debug_command("看看微博")
    async def ff_weibo(self, event: AstrMessageEvent):
        """获取FF官方微博新闻。"""
        yield event.plain_result(await get_ff_weibo_text(self.weibo_cookie()))

    @filter.command("物品")
    @debug_command("物品")
    async def item(self, event: AstrMessageEvent):
        """查询物品信息。"""
        item_name = command_args(event.message_str, "物品")
        if not item_name:
            yield event.plain_result("查物品格式：物品 物品名\n例：物品 铁矿")
            return

        try:
            item_text, icon_path = await create_item_info(item_name, self.cache_dir)
        except Exception as exc:
            logger.warning(f"物品查询失败: {exc}")
            yield event.plain_result("物品查询失败，请稍后再试")
            return

        text_image_path = self.cache_dir / "item_text.jpg"
        self.render_text_image(item_text, text_image_path, width_now=34)
        components = []
        if icon_path:
            components.append(Comp.Image.fromFileSystem(str(icon_path)))
        components.append(Comp.Image.fromFileSystem(str(text_image_path)))
        yield event.chain_result(components)

    @filter.command("价格")
    @debug_command("价格")
    async def market(self, event: AstrMessageEvent):
        """查询市场物价。"""
        market_query = await parse_market_query(command_args(event.message_str, "价格"))
        if not market_query.item_name:
            yield event.plain_result(
                "查物价格式：价格 (大区/服务器) 物品名 (HQ) (数量)\n例：价格 陆行鸟 铁矿 HQ 10"
            )
            return

        try:
            market_text = await create_market_text(market_query)
        except Exception as exc:
            logger.warning(f"物价查询失败: {exc}")
            yield event.plain_result("物价查询失败，请稍后再试")
            return

        image_path = self.cache_dir / "market.jpg"
        self.render_text_image(market_text, image_path, width_now=42)
        yield event.image_result(str(image_path))

    @filter.command("房子")
    @debug_command("房子")
    async def house(self, event: AstrMessageEvent):
        """查询指定服务器空房。"""
        async for result in self.create_house_result(event, "房子"):
            yield result

    @filter.command("房屋")
    @debug_command("房屋")
    async def house_alias(self, event: AstrMessageEvent):
        """查询指定服务器空房。"""
        async for result in self.create_house_result(event, "房屋"):
            yield result

    async def create_house_result(self, event: AstrMessageEvent, command: str):
        """查询指定服务器空房。"""
        house_info = command_args(event.message_str, command)
        house_query = parse_house_query(house_info)
        try:
            house_text = await create_house_text(house_query)
        except Exception as exc:
            logger.warning(f"房屋查询失败: {exc}")
            yield event.plain_result("房屋查询失败，请稍后再试")
            return

        if "────────────────────────" not in house_text:
            yield event.plain_result(house_text)
            return

        parts = house_text.split("\n")
        header = "\n".join(parts[:2])
        rows = parts[2:]
        components = []
        for index in range(0, len(rows), 30):
            page_text = header + "\n" + "\n".join(rows[index : index + 30])
            image_path = self.cache_dir / f"house_{index // 30}.jpg"
            self.render_text_image(page_text, image_path, width_now=44)
            components.append(Comp.Image.fromFileSystem(str(image_path)))
        yield event.chain_result(components)

    @filter.command("输出")
    @debug_command("输出")
    async def logs_dps(self, event: AstrMessageEvent):
        """查询FFLogs输出分段。"""
        logs_query = parse_logs_query(
            command_args(event.message_str, "输出"), self.default_logs_cn_source()
        )
        yield event.plain_result(
            await create_logs_text(
                logs_query,
                self.fflogs_client_id(),
                self.fflogs_client_secret(),
            )
        )

    @filter.command("logs")
    @debug_command("logs")
    async def character_logs(self, event: AstrMessageEvent):
        """查询角色FFLogs战绩。"""
        logs_query = parse_character_logs_query(command_args(event.message_str, "logs"))
        yield event.plain_result(
            await create_character_logs_text(
                logs_query,
                self.fflogs_client_id(),
                self.fflogs_client_secret(),
                self.default_logs_cn_source(),
            )
        )

    @filter.command("抽卡")
    @debug_command("抽卡")
    async def tarot(self, event: AstrMessageEvent):
        """随机抽取一张FF14塔罗牌。"""
        result = self.create_tarot_result(event)
        async for item in result:
            yield item

    async def create_tarot_result(self, event: AstrMessageEvent):
        if self.tarot_dict is None:
            self.tarot_dict = load_tarot()

        text_now, tarot_image_path = choose_tarot(self.tarot_dict)
        if not tarot_image_path.exists():
            yield event.plain_result(f"塔罗牌图片不存在：{tarot_image_path.name}")
            return

        text_image_path = self.cache_dir / "tarot_text.jpg"
        self.render_text_image(text_now, text_image_path)

        yield event.chain_result(
            [
                Comp.Image.fromFileSystem(str(text_image_path)),
                Comp.Image.fromFileSystem(str(tarot_image_path)),
            ]
        )

    async def download_calendar_loop(self):
        while True:
            try:
                await self.download_calendar_once(self.default_calendar_server())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"日历更新连接错误: {exc}")
            await asyncio.sleep(60 * 60)

    async def risingstones_checkin_loop(self):
        """Run opted-in private account check-ins once per Shanghai calendar day."""
        while True:
            try:
                now = datetime.now(RISINGSTONES_TIMEZONE)
                if (
                    feature_enabled(self.admin_store, "risingstones_checkin")
                    and now.hour == self.risingstones_checkin_hour()
                ):
                    day = now.date().isoformat()
                    accounts = self.risingstones_accounts.due_auto_checkins(day)
                    for account_key, credentials in accounts:
                        self.risingstones_accounts.mark_attempt(account_key, day)
                        try:
                            _, message = await risingstones_checkin(credentials)
                            self.risingstones_accounts.mark_checkin(account_key, day)
                            debug_log(
                                "risingstones.auto_checkin.success",
                                account_key=account_key,
                                message=message,
                            )
                        except Exception as exc:
                            logger.warning(
                                "石之家自动签到失败: %s (%s)",
                                account_key,
                                type(exc).__name__,
                            )
                            debug_log(
                                "risingstones.auto_checkin.error",
                                account_key=account_key,
                                error_type=type(exc).__name__,
                            )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"石之家自动签到任务异常: {exc}")
            await asyncio.sleep(60)

    def calendar_cache_path(self, server: str) -> Path:
        return (
            self.cache_dir / f"calendar_{'global' if server == '国际服' else 'cn'}.ics"
        )

    async def download_calendar_once(self, server: str) -> bool:
        sources = CALENDAR_SOURCES[server]
        debug_log("calendar.refresh.start", server=server)

        async def fetch_calendar_source(source_name: str, url: str) -> bytes | None:
            try:
                result = await aiohttp_get(url, res_type="bytes")
            except Exception as exc:
                logger.warning(f"{server}日历{source_name}更新异常: {exc}")
                return None
            if result is None:
                return None
            try:
                Calendar.from_ical(result)
            except Exception as exc:
                logger.warning(f"{server}日历{source_name}内容解析失败: {exc}")
                return None
            return result

        result = await fetch_calendar_source("主链接", sources["primary"])
        if result is None:
            logger.info(f"{server}日历主链接更新失败，尝试备用链接")
            result = await fetch_calendar_source("备用链接", sources["fallback"])

        if result is None:
            logger.warning(f"{server}日历更新失败，将使用本地缓存")
            debug_log("calendar.refresh.finish", server=server, updated=False)
            return False

        self.calendar_cache_path(server).write_bytes(result)
        self.last_calendar_download_time[server] = datetime.now()
        logger.info(f"{server}日历更新成功")
        debug_log("calendar.refresh.finish", server=server, updated=True)
        return True

    async def ensure_calendar(self, server: str):
        cache_path = self.calendar_cache_path(server)
        if not cache_path.exists():
            await self.download_calendar_once(server)

    def calendar_read_path(self, server: str) -> Path | None:
        for calendar_path in self.calendar_read_paths(server):
            try:
                Calendar.from_ical(calendar_path.read_bytes())
            except Exception as exc:
                logger.warning(f"{server}日历文件解析失败: {calendar_path}, {exc}")
                continue
            return calendar_path
        return None

    def calendar_read_paths(self, server: str) -> list[Path]:
        paths = []
        cache_path = self.calendar_cache_path(server)
        if cache_path.exists():
            paths.append(cache_path)
        bundled_path = CALENDAR_SOURCES[server]["bundled"]
        if bundled_path and bundled_path.exists():
            paths.append(bundled_path)
        return paths

    def create_calendar_text(self, server: str) -> str:
        calendar_path = self.calendar_read_path(server)
        if calendar_path is None:
            return f"{server}日历文件不存在，请稍后再试"

        gcal = Calendar.from_ical(calendar_path.read_bytes())
        today = datetime.now().date()
        warn_ics = []
        week_ics = []
        future_ics = []

        for component in gcal.walk():
            if component.name != "VEVENT":
                continue

            start_component = component.get("dtstart")
            end_component = component.get("dtend")
            if start_component is None or end_component is None:
                logger.warning(f"{server}日历事件缺少开始或结束时间，已跳过")
                continue

            start_raw = start_component.dt
            end_raw = end_component.dt
            start_date, start_info = normalize_calendar_date(start_raw)
            end_date, end_info = normalize_calendar_date(end_raw)

            if end_date < today:
                continue

            info_item = [
                end_info,
                start_info,
                component.get("summary"),
                component.get("DESCRIPTION"),
            ]
            sortable_item = (
                end_date,
                start_date,
                str(component.get("summary")),
                info_item,
            )
            days_left = (end_date - today).days
            if days_left <= 2:
                warn_ics.append(sortable_item)
            elif days_left <= 7:
                week_ics.append(sortable_item)
            else:
                future_ics.append(sortable_item)

        warn_ics.sort()
        week_ics.sort()
        future_ics.sort()

        result = f"【{server}日历】\n今天是 " + str(today).replace("-", ".") + "\n"
        if warn_ics:
            result += "【近2天结束】\n"
            for item in warn_ics:
                result += format_calendar_item(item[3]) + "\n"
        if week_ics:
            result += "【近7天内】\n"
            for item in week_ics:
                result += format_calendar_item(item[3]) + "\n"
        if future_ics:
            result += "【未来活动】\n"
            for item in future_ics:
                result += format_calendar_item(item[3]) + "\n"

        if server in self.last_calendar_download_time:
            result += "\n日历更新时间: " + str(
                self.last_calendar_download_time[server]
            ).split(".")[0].replace("-", ".")
        else:
            result += "\n日历更新时间: 使用本地缓存"
        return result

    async def create_nuannuan_result(self, event: AstrMessageEvent):
        period = get_current_period()
        cache_path = self.cache_dir / "nuannuan.json"

        if cache_path.exists():
            try:
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
                cached_message = cache.get(str(period))
                if cached_message:
                    image_path = self.cache_dir / "nuannuan.jpg"
                    self.render_text_image(cached_message, image_path, width_now=25)
                    return event.image_result(str(image_path))
            except Exception as exc:
                logger.warning(f"读取暖暖缓存失败: {exc}")

        try:
            bili_url = await get_bili_url()
            message = await get_bili_detail(bili_url)
            cache_path.write_text(
                json.dumps({str(period): message}, ensure_ascii=False), encoding="utf-8"
            )
            image_path = self.cache_dir / "nuannuan.jpg"
            self.render_text_image(message, image_path, width_now=25)
            return event.image_result(str(image_path))
        except Exception as exc:
            logger.warning(f"暖暖获取失败: {exc}")
            return event.plain_result("暖暖获取失败，请看qq文档： " + QQ_DOC_URL)

    async def terminate(self):
        if self.calendar_task:
            self.calendar_task.cancel()
        if self.risingstones_checkin_task:
            self.risingstones_checkin_task.cancel()
        debug_log("plugin.terminate")
        logger.info("Tataru AstrBot plugin terminated.")
