"""Steam / 小黑盒价格查询核心逻辑。

移植自：https://github.com/penguin-madagascar/astrbot_plugin_steam_price_heybox
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable, Literal

import httpx

from .api_clients import (
    XIAOHEIHE_GLOBAL_PRICE_URL,
    XIAOHEIHE_PRICE_HISTORY_URL,
    HeyboxClient,
    SteamStoreClient,
)
from .models import GameIdentity, PriceHistory, RegionPrice, SaleEvent, SteamGameDetails
from .name_correction import NameCorrection, NameCorrectionRequest
from .price_analysis import parse_price_history

CommandMode = Literal["summary", "history", "regions", "info", "detailed_info"]
# 中文优先；英文别名仍可用
COMMAND_MODE_ALIASES = {
    "history": "history",
    "历史": "history",
    "促销历史": "history",
    "促销": "history",
    "regions": "regions",
    "区价": "regions",
    "区服": "regions",
    "info": "info",
    "资料": "info",
    "信息": "info",
    "detailed_info": "detailed_info",
    "detailedinfo": "detailed_info",
    "详细资料": "detailed_info",
    "详情": "detailed_info",
}
COMMAND_MODES = set(COMMAND_MODE_ALIASES.keys())
NameCorrector = Callable[[NameCorrectionRequest], Awaitable[NameCorrection | None]]

COUNTRY_NAMES = {
    "CN": "中国",
    "US": "美国",
    "HK": "香港",
    "TW": "台湾",
    "JP": "日本",
    "KR": "韩国",
    "UA": "乌克兰",
    "TR": "土耳其",
    "AR": "阿根廷",
    "BR": "巴西",
    "RU": "俄罗斯",
    "GB": "英国",
    "DE": "德国",
}
FORMAL_COUNTRY_CODES = {name: code for code, name in COUNTRY_NAMES.items()}
XIAOHEIHE_REGION_ALIASES = {
    "GB": "uk",
    "DE": "eu",
}
STATIC_QUERY_ALIASES = {
    "给他爱5": ("Grand Theft Auto V", "GTA V"),
    "侠盗猎车手5": ("Grand Theft Auto V", "GTA V"),
    "大表哥2": ("Red Dead Redemption 2",),
    "荒野大镖客2": ("Red Dead Redemption 2",),
    "老头环": ("ELDEN RING",),
    "艾尔登法环": ("ELDEN RING",),
    "博德之门3": ("Baldur's Gate 3", "Baldurs Gate 3"),
    "赛博朋克2077": ("Cyberpunk 2077",),
    "双人成行": ("It Takes Two",),
    "潜水员戴夫": ("DAVE THE DIVER",),
    "星露谷": ("Stardew Valley",),
}


class PriceLookupError(RuntimeError):
    pass


@dataclass(frozen=True)
class ParsedCommand:
    mode: CommandMode
    target: str
    country: str
    country_token: str = ""


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


class SteamPriceService:
    def __init__(
        self,
        *,
        steam_client: SteamStoreClient,
        heybox_client: HeyboxClient,
        default_country: str = "CN",
        default_history_country: str = "CN",
        default_language: str = "schinese",
        history_days: int = 720,
        history_event_limit: int = 5,
        global_price_limit: int = 10,
        show_api_links: bool = False,
        name_corrector: NameCorrector | None = None,
        llm_name_retry_count: int = 3,
        today_provider: Callable[[], date] = utc_today,
    ) -> None:
        self.steam_client = steam_client
        self.heybox_client = heybox_client
        self.default_country = parse_country(default_country) or "CN"
        self.default_history_country = parse_country(default_history_country) or "CN"
        self.default_language = default_language or "schinese"
        self.history_days = max(history_days, 1)
        self.history_event_limit = max(history_event_limit, 1)
        self.global_price_limit = max(global_price_limit, 1)
        self.show_api_links = show_api_links
        self.name_corrector = name_corrector
        self.llm_name_retry_count = llm_name_retry_count
        self.today_provider = today_provider

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any],
        client: httpx.AsyncClient,
        name_corrector: NameCorrector | None = None,
    ) -> SteamPriceService:
        return cls(
            steam_client=SteamStoreClient(client),
            heybox_client=HeyboxClient(client),
            default_country=str(config.get("default_country", "CN")),
            default_history_country=str(config.get("default_history_country", "CN")),
            default_language=str(config.get("default_language", "schinese")),
            history_days=int(config.get("history_days", 720)),
            history_event_limit=int(config.get("history_event_limit", 5)),
            global_price_limit=int(config.get("global_price_limit", 10)),
            show_api_links=bool(config.get("show_api_links", False)),
            name_corrector=name_corrector,
            llm_name_retry_count=int(config.get("llm_name_retry_count", 3)),
        )

    async def execute(self, text: str) -> list[str]:
        text = text.strip()
        if not text:
            return [usage_text()]
        command = parse_command(
            text,
            default_country=self.default_country,
            default_history_country=self.default_history_country,
        )
        if not command.target:
            if command.mode == "summary":
                return [usage_text()]
            raise PriceLookupError(usage_text())
        identity, country = await self.resolve_game(
            command.target,
            command.country,
            command.country_token,
        )
        if command.mode == "history":
            return [await self.history_text(identity, country)]
        if command.mode == "regions":
            return [await self.regions_text(identity)]
        if command.mode == "info":
            details = await self.require_details(identity, country)
            return [format_basic_info(details)]
        if command.mode == "detailed_info":
            details = await self.require_details(identity, country)
            return [format_basic_info(details), format_detailed_info(details)]
        return [await self.summary_text(identity, country)]

    async def resolve_game(
        self,
        text: str,
        country: str,
        country_token: str = "",
    ) -> tuple[GameIdentity, str]:
        appid = extract_appid(text)
        if appid:
            country = await self.resolve_country_for_appid(text, country, country_token)
            return GameIdentity(appid, f"appid={appid}"), country
        query = text.strip()
        if not query:
            raise PriceLookupError("请输入游戏名、Steam appid 或 Steam 商店链接。")

        searched = set()
        failed_names: list[str] = []
        suggestions: list[str] = []
        if self.name_corrector:
            for _ in range(self.correction_attempt_limit):
                correction = await self.request_name_correction(
                    query,
                    country,
                    country_token,
                    tuple(failed_names),
                )
                if correction is None:
                    continue
                if not country:
                    country = parse_country(correction.country_code)
                if correction.name:
                    suggestions.extend(unique_texts((correction.name,)))
                if not country:
                    continue
                for suggestion in unique_texts(tuple(suggestions)):
                    identity = await self.search_game(suggestion, country, searched)
                    if identity:
                        return identity, country
                    if suggestion.casefold() not in {name.casefold() for name in failed_names}:
                        failed_names.append(suggestion)

        if not country:
            raise unknown_country_error(country_token)

        variants = unique_texts((query, *STATIC_QUERY_ALIASES.get(query, ())))
        for variant in variants:
            identity = await self.search_game(variant, country, searched)
            if identity:
                return identity, country
        raise PriceLookupError(f"Steam 商店没有搜索到游戏：{query}")

    @property
    def correction_attempt_limit(self) -> int:
        return max(self.llm_name_retry_count, 0) + 1

    async def resolve_country_for_appid(
        self,
        target: str,
        country: str,
        country_token: str,
    ) -> str:
        if country:
            return country
        if self.name_corrector:
            for _ in range(self.correction_attempt_limit):
                correction = await self.request_name_correction(
                    target,
                    country,
                    country_token,
                    (),
                )
                if correction and (country := parse_country(correction.country_code)):
                    return country
        raise unknown_country_error(country_token)

    async def request_name_correction(
        self,
        original_name: str,
        country: str,
        country_token: str,
        failed_names: tuple[str, ...],
    ) -> NameCorrection | None:
        if not self.name_corrector:
            return None
        try:
            return await self.name_corrector(
                NameCorrectionRequest(
                    original_name=original_name,
                    country_code=country,
                    unresolved_country=country_token if not country else "",
                    failed_names=failed_names,
                )
            )
        except Exception:
            return None

    async def search_game(
        self,
        query: str,
        country: str,
        searched: set[str],
    ) -> GameIdentity | None:
        key = query.casefold()
        if key in searched:
            return None
        searched.add(key)
        results = await self.steam_client.search(query, country, self.default_language)
        candidate = choose_steam_candidate(query, results)
        if not candidate:
            return None
        return GameIdentity(
            int(candidate["appid"]),
            f"{candidate['name']} / appid={candidate['appid']}",
        )

    async def require_details(self, identity: GameIdentity, country: str) -> SteamGameDetails:
        try:
            return await self.steam_client.details(
                identity.appid,
                country,
                self.default_language,
            )
        except Exception as exc:
            raise PriceLookupError(f"Steam 游戏资料读取失败：{exc}") from exc

    async def summary_text(self, identity: GameIdentity, country: str) -> str:
        details_result, history_result, regions_result = await asyncio.gather(
            self.steam_client.details(identity.appid, country, self.default_language),
            self.load_history(identity.appid, country),
            self.heybox_client.global_prices(identity.appid),
            return_exceptions=True,
        )
        details = details_result if isinstance(details_result, SteamGameDetails) else None
        history = history_result if isinstance(history_result, PriceHistory) else None
        regions = regions_result if isinstance(regions_result, list) else []
        if details is None and history is None:
            raise PriceLookupError("Steam 当前价格和小黑盒历史价格均暂时不可用。")

        lines = [game_title(identity, details), f"地区：{country_label(country)}"]
        lines.append(format_current_price(details, history))
        if history is not None:
            lines.append(format_lowest(history))
            lines.extend(format_sale_status(history, self.today_provider()))
            comparison = format_lowest_comparison(details, history)
            if comparison:
                lines.append(comparison)
        else:
            lines.append("小黑盒历史价格：暂时不可用")

        region_summary = format_region_summary(regions)
        if region_summary:
            lines.append(region_summary)
        lines.extend(game_links(identity.appid))
        if self.show_api_links:
            lines.extend(api_links(identity.appid, country, self.history_days))
        return "\n".join(lines)

    async def history_text(self, identity: GameIdentity, country: str) -> str:
        details_result, history_result = await asyncio.gather(
            self.steam_client.details(identity.appid, country, self.default_language),
            self.load_history(identity.appid, country),
            return_exceptions=True,
        )
        details = details_result if isinstance(details_result, SteamGameDetails) else None
        if not isinstance(history_result, PriceHistory):
            raise PriceLookupError(f"小黑盒历史价格读取失败：{history_result}")

        history = history_result
        lines = [game_title(identity, details), f"地区：{country_label(country)}"]
        if history.points:
            lines.append(
                "记录范围："
                f"{history.points[0].recorded_on.isoformat()} 至 "
                f"{history.points[-1].recorded_on.isoformat()}，"
                f"{len(history.points)} 个价格点，{len(history.events)} 次促销"
            )
        lines.append(format_lowest(history))
        if history.maximum_discount:
            lines.append(f"记录内最大折扣：-{history.maximum_discount}%")
        lines.extend(format_sale_status(history, self.today_provider()))

        events = list(reversed(history.events[-self.history_event_limit :]))
        if events:
            lines.append(f"最近 {len(events)} 次促销：")
            for index, event in enumerate(events, start=1):
                lines.append(f"{index}. {format_sale_event(event, self.today_provider())}")
        else:
            lines.append("记录内没有发现折扣事件。")
        if self.show_api_links:
            lines.extend(api_links(identity.appid, country, self.history_days)[:1])
        return "\n".join(lines)

    async def regions_text(self, identity: GameIdentity) -> str:
        details_result, regions_result = await asyncio.gather(
            self.steam_client.details(
                identity.appid,
                self.default_country,
                self.default_language,
            ),
            self.heybox_client.global_prices(identity.appid),
            return_exceptions=True,
        )
        details = details_result if isinstance(details_result, SteamGameDetails) else None
        if not isinstance(regions_result, list) or not regions_result:
            raise PriceLookupError("小黑盒全球区价暂时不可用。")

        regions = sorted(regions_result, key=lambda item: item.current_rmb)
        cn_price = next((item for item in regions if item.code == "CN"), None)
        lines = [game_title(identity, details), "小黑盒全球区价（人民币折算）："]
        if cn_price:
            lines.append(f"国区基准：约 ¥{decimal_text(cn_price.current_rmb)}")
        for index, region in enumerate(regions[: self.global_price_limit], start=1):
            lines.append(f"{index}. {format_region_line(region, cn_price)}")
        if self.show_api_links:
            lines.append(
                f"小黑盒全球价格接口：{XIAOHEIHE_GLOBAL_PRICE_URL}?steam_appid={identity.appid}"
            )
        return "\n".join(lines)

    async def load_history(self, appid: int, country: str) -> PriceHistory:
        result = await self.heybox_client.price_history(
            appid,
            xiaoheihe_country(country),
            self.history_days,
        )
        return parse_price_history(result)


def parse_command(
    text: str,
    default_country: str = "CN",
    default_history_country: str = "CN",
) -> ParsedCommand:
    mode: CommandMode = "summary"
    remainder = text.strip()
    first, remainder_after_first = split_first(remainder)
    alias_key = first if first in COMMAND_MODE_ALIASES else first.casefold()
    if alias_key in COMMAND_MODE_ALIASES:
        mode = COMMAND_MODE_ALIASES[alias_key]
        remainder = remainder_after_first

    country = default_history_country if mode == "history" else default_country
    country_token = ""
    first, remainder_after_first = split_first(remainder)
    if first == "--":
        remainder = remainder_after_first
    else:
        extracted = extract_country_token(first, require_remainder=bool(remainder_after_first))
        if extracted is not None:
            if mode not in {"summary", "history"}:
                labels = {
                    "history": "历史",
                    "regions": "区价",
                    "info": "资料",
                    "detailed_info": "详细资料",
                }
                raise PriceLookupError(
                    f"「{labels.get(mode, mode)}」模式不支持地区参数。"
                )
            country_token = extracted
            country = parse_country(country_token)
            remainder = remainder_after_first
            first, remainder_after_first = split_first(remainder)
            if first == "--":
                remainder = remainder_after_first

    return ParsedCommand(
        mode=mode,
        target=remainder.strip(),
        country=parse_country(country),
        country_token=country_token if not country else "",
    )


def split_first(value: str) -> tuple[str, str]:
    parts = value.split(maxsplit=1)
    if not parts:
        return "", ""
    return parts[0], parts[1] if len(parts) == 2 else ""


def extract_country_token(value: str, *, require_remainder: bool = False) -> str | None:
    """解析地区参数，支持 ``-US`` / ``-美国``，以及后跟目标时的裸 ``美国`` / ``US``。"""
    if not value or value == "--":
        return None
    if value.startswith("-"):
        token = value[1:]
        if parse_country(token) or re.fullmatch(r"[\u3400-\u9fff]+", token):
            return token
        return None
    # 裸地区名：仅当后面还有游戏名等目标时才当作地区，避免「steam价格 美国」被吞掉
    if require_remainder and parse_country(value):
        return value
    return None


def is_country_argument(value: str) -> bool:
    return extract_country_token(value, require_remainder=True) is not None


def usage_text() -> str:
    return (
        "【Steam 价格查询】\n"
        "() 表示可选参数；游戏名 / appid / Steam 商店链接均可。\n"
        "\n"
        "steam价格 [地区] [--] <目标>\n"
        "  当前价、史低、促销摘要、最低价区服\n"
        "steam价格 历史 [地区] [--] <目标>\n"
        "  最近促销记录与历史统计\n"
        "steam价格 区价 [--] <目标>\n"
        "  全球区价排行（相对国区差额）\n"
        "steam价格 资料 [--] <目标>\n"
        "  发行日期、开发商、发行商、平台等\n"
        "steam价格 详细资料 [--] <目标>\n"
        "  语言、分类、成就、DLC、评分等扩展资料\n"
        "\n"
        "地区可用：美国 / -US / -CN / -中国 等（仅默认查询与「历史」）\n"
        "别名入口：小黑盒查价、steam查价\n"
        "\n"
        "例：\n"
        "steam价格 艾尔登法环\n"
        "steam价格 美国 鸣潮\n"
        "steam价格 历史 -CN 1245620\n"
        "steam价格 区价 https://store.steampowered.com/app/1091500/\n"
        "steam价格 资料 Stardew Valley\n"
        "steam价格 详细资料 2277560"
    )


def extract_appid(text: str) -> int:
    patterns = (
        r"store\.steampowered\.com/app/(\d+)",
        r"steam_appid[=:](\d+)",
        r"\bappid[=: ]+(\d+)\b",
        r"\b(\d{3,10})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return int(match.group(1))
    return 0


def parse_country(value: str) -> str:
    text = value.strip()
    if re.fullmatch(r"[A-Za-z]{2}", text):
        return text.upper()
    return FORMAL_COUNTRY_CODES.get(text, "")


def unknown_country_error(country_token: str) -> PriceLookupError:
    return PriceLookupError(f"无法识别地区：-{country_token}。请使用两字母代码或内置正式中文国名。")


def xiaoheihe_country(country: str) -> str:
    return XIAOHEIHE_REGION_ALIASES.get(country, country.lower())


def choose_steam_candidate(query: str, results: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not results:
        return None
    normalized_query = normalize_game_title(query)
    exact = [
        item
        for item in results
        if normalize_game_title(str(item.get("name") or "")) == normalized_query
    ]
    if exact:
        return min(exact, key=lambda item: len(str(item.get("name") or "")))
    partial = [
        item
        for item in results
        if normalized_query
        and normalized_query in normalize_game_title(str(item.get("name") or ""))
    ]
    if partial:
        return min(partial, key=lambda item: len(str(item.get("name") or "")))
    return None


def normalize_game_title(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.casefold())


def game_title(identity: GameIdentity, details: SteamGameDetails | None) -> str:
    name = details.name if details else identity.label
    return f"游戏：{name} / appid={identity.appid}"


def country_label(country: str) -> str:
    return f"{COUNTRY_NAMES.get(country, country)} / {country}"


def format_current_price(details: SteamGameDetails | None, history: PriceHistory | None) -> str:
    if details and details.is_free:
        return "Steam 当前价格：免费"
    if details and details.coming_soon and details.price is None:
        return "Steam 当前价格：尚未发售"
    if details and details.price:
        price = details.price
        text = f"Steam 当前价格：{money_text(price.current, price.currency)}"
        if price.initial != price.current:
            text += f"（原价 {money_text(price.initial, price.currency)}，-{price.discount}%）"
        return text
    if history and history.current:
        current = history.current
        return f"小黑盒最新价格：{money_text(current.price, current.currency)}"
    return "当前价格：暂时不可用"


def format_lowest(history: PriceHistory) -> str:
    if history.lowest_price is None:
        return "历史最低：暂无记录"
    text = f"历史最低：{money_text(history.lowest_price, history.lowest_currency)}"
    if history.lowest_date:
        text += f"（{history.lowest_date.isoformat()}"
        if history.lowest_discount:
            text += f"，-{history.lowest_discount}%"
        if history.lowest_occurrences:
            text += f"，出现 {history.lowest_occurrences} 次"
        text += "）"
    return text


def format_sale_status(history: PriceHistory, today: date) -> list[str]:
    active = history.active_sale
    if active:
        return [
            "当前促销："
            f"{active.started_on.isoformat()} 开始，"
            f"已持续 {duration_text(active.duration_days(today))}，"
            f"最低 {money_text(active.lowest_price, active.currency)}，"
            f"最大折扣 -{active.maximum_discount}%"
        ]
    previous = history.last_completed_sale
    if previous and previous.ended_on:
        days_ago = max((today - previous.ended_on).days, 0)
        return [
            "上次促销："
            f"{previous.started_on.isoformat()} 至 {previous.ended_on.isoformat()}，"
            f"持续 {duration_text(previous.duration_days(today))}，"
            f"最低 {money_text(previous.lowest_price, previous.currency)}，"
            f"最大折扣 -{previous.maximum_discount}%（结束于 {days_ago} 天前）"
        ]
    return ["促销记录：暂无"]


def format_lowest_comparison(
    details: SteamGameDetails | None,
    history: PriceHistory,
) -> str:
    if not details or not details.price or history.lowest_price is None:
        return ""
    if details.price.currency != history.lowest_currency:
        return ""
    difference = details.price.current - history.lowest_price
    if difference == 0:
        return "当前价格已达到历史最低。"
    if difference < 0:
        difference_text = money_text(-difference, details.price.currency)
        return f"当前价格比小黑盒已记录史低低 {difference_text}。"
    if history.lowest_price == 0:
        return f"当前价格高于史低 {money_text(difference, details.price.currency)}。"
    percentage = difference / history.lowest_price * 100
    return (
        f"当前价格高于史低 {money_text(difference, details.price.currency)}"
        f"（{decimal_text(percentage)}%）。"
    )


def format_region_summary(regions: list[RegionPrice]) -> str:
    if not regions:
        return ""
    cheapest = min(regions, key=lambda item: item.current_rmb)
    china = next((item for item in regions if item.code == "CN"), None)
    text = f"最低价区服：{cheapest.label}，约 ¥{decimal_text(cheapest.current_rmb)}"
    if china and cheapest.code != "CN" and china.current_rmb > 0:
        difference = china.current_rmb - cheapest.current_rmb
        percentage = difference / china.current_rmb * 100
        text += f"，比国区节省约 ¥{decimal_text(difference)}（{decimal_text(percentage)}%）"
    elif cheapest.code == "CN":
        text += "，国区当前最低"
    return text


def format_region_line(region: RegionPrice, china: RegionPrice | None) -> str:
    text = f"{region.label}：约 ¥{decimal_text(region.current_rmb)}"
    if region.initial_rmb is not None and region.initial_rmb != region.current_rmb:
        text += f"，原价约 ¥{decimal_text(region.initial_rmb)}"
    if region.discount:
        text += f"，-{region.discount}%"
    if china and region.code != "CN" and region.current_rmb < china.current_rmb:
        difference = china.current_rmb - region.current_rmb
        percentage = difference / china.current_rmb * 100 if china.current_rmb else Decimal(0)
        text += f"，比国区省约 ¥{decimal_text(difference)}（{decimal_text(percentage)}%）"
    return text


def format_sale_event(event: SaleEvent, today: date) -> str:
    end = event.ended_on.isoformat() if event.ended_on else "进行中"
    return (
        f"{event.started_on.isoformat()} 至 {end}，"
        f"{duration_text(event.duration_days(today))}，"
        f"最低 {money_text(event.lowest_price, event.currency)}，"
        f"最大折扣 -{event.maximum_discount}%"
    )


def format_basic_info(details: SteamGameDetails) -> str:
    release = "未发售" if details.coming_soon else details.release_date or "未知"
    return "\n".join(
        (
            "Steam 基本资料",
            f"游戏：{details.name}",
            f"AppID：{details.appid}",
            f"发行：{release}",
            f"开发商：{join_or_unknown(details.developers)}",
            f"发行商：{join_or_unknown(details.publishers)}",
            f"平台：{join_or_unknown(details.platforms)}",
            f"商店：{details.store_url}",
        )
    )


def format_detailed_info(details: SteamGameDetails) -> str:
    metacritic = str(details.metacritic_score) if details.metacritic_score is not None else "暂无"
    recommendations = (
        str(details.recommendation_count) if details.recommendation_count is not None else "暂无"
    )
    age = details.required_age or "未标注"
    lines = [
        "Steam 扩展资料",
        f"类型：{details.game_type or '未知'}",
        f"免费游戏：{'是' if details.is_free else '否'}",
        f"题材：{limited_join(details.genres, 12)}",
        f"功能：{limited_join(details.categories, 12)}",
        f"控制器：{details.controller_support or '未标注'}",
        f"语言：{limited_join(details.languages, 15)}",
        f"成就：{details.achievement_count} 个",
        f"DLC：{details.dlc_count} 个",
        f"Metacritic：{metacritic}",
        f"Steam 推荐数：{recommendations}",
        f"年龄要求：{age}",
    ]
    if details.content_notes:
        lines.append(f"内容提示：{short_text(details.content_notes, 300)}")
    if details.website:
        lines.append(f"官网：{details.website}")
    return "\n".join(lines)


def money_text(value: Decimal, currency: str) -> str:
    symbols = {"CNY": "¥", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}
    amount = decimal_text(value)
    symbol = symbols.get(currency.upper())
    return f"{symbol}{amount}" if symbol else f"{amount} {currency}".strip()


def decimal_text(value: Decimal) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def duration_text(days: int) -> str:
    return "不足 1 天" if days == 0 else f"{days} 天"


def join_or_unknown(values: tuple[str, ...]) -> str:
    return "、".join(values) if values else "未知"


def limited_join(values: tuple[str, ...], limit: int) -> str:
    if not values:
        return "未知"
    selected = values[:limit]
    text = "、".join(selected)
    if len(values) > limit:
        text += f" 等 {len(values)} 项"
    return text


def game_links(appid: int) -> list[str]:
    return [
        f"Steam：https://store.steampowered.com/app/{appid}/",
        f"小黑盒：https://www.xiaoheihe.cn/app/topic/game/pc/{appid}",
    ]


def api_links(appid: int, country: str, days: int) -> list[str]:
    return [
        "小黑盒历史价格接口："
        f"{XIAOHEIHE_PRICE_HISTORY_URL}?appid={appid}&platf=steam"
        f"&cc={xiaoheihe_country(country)}&days={days}",
        f"小黑盒全球价格接口：{XIAOHEIHE_GLOBAL_PRICE_URL}?steam_appid={appid}",
    ]


def unique_texts(values: tuple[str, ...]) -> tuple[str, ...]:
    result = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return tuple(result)


def short_text(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    return text if len(text) <= limit else text[: limit - 1] + "..."
