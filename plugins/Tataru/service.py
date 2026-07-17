"""Tataru service — command logic ported from astrbot_plugin_tataru."""
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

    # 沿用原 FF14LogsInfo 内置凭据（配置为空时回退）
    _FFLOGS_DEFAULT_CLIENT_ID = "9ae4db97-1f1b-4123-82d4-32aff00d3283"
    _FFLOGS_DEFAULT_CLIENT_SECRET = "rAeNnp3Z1VFxYI92TcpBLggKPM3wtkmK9FxZX2C1"

    def fflogs_client_id(self) -> str:
        value = str(self.config.get("fflogs_client_id", "") or "").strip()
        return value or self._FFLOGS_DEFAULT_CLIENT_ID

    def fflogs_client_secret(self) -> str:
        value = str(self.config.get("fflogs_client_secret", "") or "").strip()
        return value or self._FFLOGS_DEFAULT_CLIENT_SECRET

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

    def render_text_image(
        self, text: str, output_path: Path, width_now: int = 20
    ) -> None:
        text_to_image(
            text,
            output_path,
            width_now=width_now,
            font_path=self.configured_font_path(),
        )

    async def help(self, event: SimpleEvent):
        """显示塔塔露当前指令。"""
        parts: list[ReplyPart] = []
        parts.append(ReplyPart.text(create_help_text() + "[石之家 玩家 角色名 (服务器)] 查询角色信息卡（现有绘图）\n"))
        return parts

    async def precious(self, event: SimpleEvent):
        """帮你选藏宝洞的门。"""
        parts: list[ReplyPart] = []
        parts.append(ReplyPart.text("塔塔露在藏宝洞中横冲直撞！\n" + random_left_right()))
        return parts

    async def lottery(self, event: SimpleEvent):
        """帮你选每周仙人仙彩数字。"""
        parts: list[ReplyPart] = []
        parts.append(ReplyPart.text("塔塔露觉得这个可以！\n" + random_lottery()))
        return parts

    async def calendar(self, event: SimpleEvent):
        """获取FF近期活动日历。"""
        parts: list[ReplyPart] = []
        requested_server = command_args(event.message_str, "日历") or None
        server = normalize_calendar_server(
            requested_server, self.default_calendar_server()
        )
        await self.ensure_calendar(server)
        parts.append(ReplyPart.text(self.create_calendar_text(server)))
        return parts

    async def nuannuan(self, event: SimpleEvent):
        """本周时尚品鉴作业。"""
        return await self.create_nuannuan_result(event)


    async def dungeon_note(self, event: SimpleEvent):
        """查简单副本攻略。"""
        parts: list[ReplyPart] = []
        dungeon_info = command_args(event.message_str, "攻略")
        result_text, as_text = await get_dungeon_note(dungeon_info)
        if as_text:
            parts.append(ReplyPart.text(result_text))
            return parts

        image_path = self.cache_dir / "dungeon_note.jpg"
        self.render_text_image(result_text, image_path, width_now=25)
        parts.append(ReplyPart.image(str(image_path)))
        return parts

    async def risingstones_posts(self, event: SimpleEvent):
        """查询石之家公开内容和招募。"""
        parts: list[ReplyPart] = []
        raw_query = command_args(event.message_str, "石之家")
        feature = risingstones_feature_for_query(raw_query)
        if not feature_enabled(self.admin_store, feature):
            parts.append(ReplyPart.text("该石之家功能已在塔塔露管理台中停用。"))
            return parts
        private_result = await self.risingstones_private_action(event, raw_query)
        if isinstance(private_result, RisingstonesGlamourResponse):
            for message in private_result.messages:
                components = []
                if message.image_url:
                    components.append(ReplyPart.image_url(message.image_url))
                components.append(ReplyPart.text(message.text))
                parts.extend(components)
            return parts
        if private_result is not None:
            parts.append(ReplyPart.text(private_result))
            return parts
        if raw_query.split(maxsplit=1)[:1] == ["招募"]:
            query = parse_risingstones_recruit_query(
                raw_query.removeprefix("招募").strip()
            )
            try:
                rows = await fetch_risingstones_recruits(query)
            except Exception as exc:
                logger.warning(f"石之家招募查询失败: {exc}")
                parts.append(ReplyPart.text("石之家招募查询失败，请稍后再试"))
                return parts
            if not rows:
                kind_labels = {
                    "party": "副本",
                    "beginner": "萌新",
                    "other": "其他",
                    "rp": "RP",
                }
                keyword_label = f"「{query.keyword}」" if query.keyword else "当前条件"
                parts.append(ReplyPart.text(f"石之家{kind_labels[query.kind]}招募中没有找到{keyword_label}的内容"))
                return parts
            parts.append(ReplyPart.text(format_risingstones_recruits(query, rows)))
            return parts

        query = parse_risingstones_posts_query(raw_query)
        try:
            rows = await fetch_risingstones_posts(query)
        except Exception as exc:
            logger.warning(f"石之家内容查询失败: {exc}")
            parts.append(ReplyPart.text("石之家内容查询失败，请稍后再试"))
            return parts
        if not rows:
            kind_label = "攻略" if query.kind == "strat" else "帖子"
            keyword_label = f"「{query.keyword}」" if query.keyword else "当前条件"
            parts.append(ReplyPart.text(f"石之家{kind_label}中没有找到{keyword_label}的内容"))
            return parts
        parts.append(ReplyPart.text(format_risingstones_posts(query, rows)))
        return parts

    async def risingstones_private_action(
        self, event: SimpleEvent, raw_query: str
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

    async def party_finder(self, event: SimpleEvent):
        """获取指定大区招募板信息。"""
        parts: list[ReplyPart] = []
        query = parse_party_finder_query(command_args(event.message_str, "招募"))
        if (
            not query.data_centre
            and not query.search_terms
            and not query.category
            and not query.job_ids
        ):
            parts.append(
                ReplyPart.text(
                    "查招募版格式：招募 (大区或服务器) (分类或关键词或职业) (数量)\n例：招募 陆行鸟 随机任务"
                )
            )
            return parts

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
            parts.append(ReplyPart.text("招募板获取失败，请稍后再试"))
            return parts

        if not entries:
            category_hint = (
                f"「{PARTY_CATEGORY_LABELS.get(query.category, query.category)}」"
                if query.category
                else ""
            )
            search_hint = f"包含「{search_text}」的" if search_text else ""
            job_hint = "指定职业的" if query.job_ids else ""
            parts.append(ReplyPart.text(f"当前{scope_label}{category_hint}{search_hint}{job_hint}无人上传招募信息"))
            return parts

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
            image_components.append(ReplyPart.image(str(image_path)))

        parts.extend(image_components)
        return parts

    async def ff_weibo(self, event: SimpleEvent):
        """获取FF官方微博新闻。"""
        parts: list[ReplyPart] = []
        parts.append(ReplyPart.text(await get_ff_weibo_text(self.weibo_cookie())))
        return parts

    async def item(self, event: SimpleEvent):
        """查询物品信息。"""
        parts: list[ReplyPart] = []
        item_name = command_args(event.message_str, "物品")
        if not item_name:
            parts.append(ReplyPart.text("查物品格式：物品 物品名\n例：物品 铁矿"))
            return parts

        try:
            item_text, icon_path = await create_item_info(item_name, self.cache_dir)
        except Exception as exc:
            logger.warning(f"物品查询失败: {exc}")
            parts.append(ReplyPart.text("物品查询失败，请稍后再试"))
            return parts

        text_image_path = self.cache_dir / "item_text.jpg"
        self.render_text_image(item_text, text_image_path, width_now=34)
        components = []
        if icon_path:
            components.append(ReplyPart.image(str(icon_path)))
        components.append(ReplyPart.image(str(text_image_path)))
        parts.extend(components)
        return parts

    async def market(self, event: SimpleEvent):
        """查询市场物价。"""
        parts: list[ReplyPart] = []
        market_query = await parse_market_query(command_args(event.message_str, "价格"))
        if not market_query.item_name:
            parts.append(
                ReplyPart.text(
                    "查物价格式：价格 (大区/服务器) 物品名 (HQ) (数量)\n例：价格 陆行鸟 铁矿 HQ 10"
                )
            )
            return parts

        try:
            market_text = await create_market_text(market_query)
        except Exception as exc:
            logger.warning(f"物价查询失败: {exc}")
            parts.append(ReplyPart.text("物价查询失败，请稍后再试"))
            return parts

        image_path = self.cache_dir / "market.jpg"
        self.render_text_image(market_text, image_path, width_now=42)
        parts.append(ReplyPart.image(str(image_path)))
        return parts

    async def house(self, event: SimpleEvent):
        """查询指定服务器空房。"""
        return await self.create_house_result(event, "房子")


    async def house_alias(self, event: SimpleEvent):
        """查询指定服务器空房。"""
        return await self.create_house_result(event, "房屋")


    async def create_house_result(self, event: SimpleEvent, command: str):
        """查询指定服务器空房。"""
        parts: list[ReplyPart] = []
        house_info = command_args(event.message_str, command)
        house_query = parse_house_query(house_info)
        try:
            house_text = await create_house_text(house_query)
        except Exception as exc:
            logger.warning(f"房屋查询失败: {exc}")
            parts.append(ReplyPart.text("房屋查询失败，请稍后再试"))
            return parts

        if "────────────────────────" not in house_text:
            parts.append(ReplyPart.text(house_text))
            return parts

        lines = house_text.split("\n")
        header = "\n".join(lines[:2])
        rows = lines[2:]
        components = []
        for index in range(0, len(rows), 30):
            page_text = header + "\n" + "\n".join(rows[index : index + 30])
            image_path = self.cache_dir / f"house_{index // 30}.jpg"
            self.render_text_image(page_text, image_path, width_now=44)
            components.append(ReplyPart.image(str(image_path)))
        parts.extend(components)
        return parts

    async def logs_dps(self, event: SimpleEvent):
        """查询FFLogs输出分段。"""
        logs_query = parse_logs_query(
            command_args(event.message_str, "输出"), self.default_logs_cn_source()
        )
        text = await create_logs_text(
            logs_query,
            self.fflogs_client_id(),
            self.fflogs_client_secret(),
        )
        return [ReplyPart.text(text)]

    async def character_logs(self, event: SimpleEvent):
        """查询角色 FFLogs 战绩：塔塔露 zone 数据 + 原风格卡片图。"""
        import asyncio

        from .engine import (
            collect_fflogs_character_records,
            exception_detail,
            fetch_fflogs_character_logs,
            fflogs_character_server_candidates,
            logger as eng_logger,
        )
        from .logs_card import render_character_logs_card

        logs_query = parse_character_logs_query(command_args(event.message_str, "logs"))
        if not logs_query.character_name or not logs_query.server_name:
            return [
                ReplyPart.text(
                    "查 logs 格式：logs 角色名 服务器名 (国服/国际服)\n例：logs 蓝晴 延夏"
                )
            ]

        if (
            logs_query.character_name == "武术有栖"
            and logs_query.server_name == "延夏"
            and event.sender_id != "273421673"
        ):
            return [ReplyPart.text("?")]

        client_id = self.fflogs_client_id()
        client_secret = self.fflogs_client_secret()
        if not client_id or not client_secret:
            return [
                ReplyPart.text(
                    "请先在 Tataru 插件配置中填写 FFLogs API Client ID 和 Secret。"
                )
            ]

        candidates = await fflogs_character_server_candidates(
            logs_query, self.default_logs_cn_source()
        )
        errors: list[str] = []
        for host, region, source_label in candidates:
            try:
                character = await fetch_fflogs_character_logs(
                    logs_query, host, region, client_id, client_secret
                )
            except Exception as exc:
                detail = exception_detail(exc)
                eng_logger.warning(f"FFLogs 角色查询失败 ({source_label}): {detail}")
                errors.append(f"{source_label}: {detail}")
                continue
            if not character:
                continue

            records = collect_fflogs_character_records(character)
            if not records:
                return [ReplyPart.text("暂无公开可用的零式/绝本排名记录。")]

            char_name = str(character.get("name") or logs_query.character_name)
            server_obj = (
                character.get("server")
                if isinstance(character.get("server"), dict)
                else {}
            )
            server_name = str(server_obj.get("name") or logs_query.server_name)

            image_path = await asyncio.to_thread(
                render_character_logs_card, char_name, server_name, records
            )
            if not image_path:
                from .engine import format_fflogs_character_logs

                return [
                    ReplyPart.text(
                        format_fflogs_character_logs(
                            logs_query, character, source_label, host, region
                        )
                    )
                ]

            return [
                ReplyPart.text(
                    "提醒:请谨慎使用该功能，用户使用引起其他玩家不快将会被禁止使用该功能。"
                ),
                ReplyPart.image(image_path),
            ]

        if errors:
            return [ReplyPart.text("FFLogs 角色查询失败，请检查插件日志或稍后再试。")]
        return [
            ReplyPart.text(
                f"未找到角色：{logs_query.character_name} @ {logs_query.server_name}。"
                "请检查角色名、服务器名或追加 国服/国际服。"
            )
        ]

    async def tarot(self, event: SimpleEvent):
        """随机抽取一张FF14塔罗牌。"""
        return await self.create_tarot_result(event)


    async def create_tarot_result(self, event: SimpleEvent):
        parts: list[ReplyPart] = []
        if self.tarot_dict is None:
            self.tarot_dict = load_tarot()

        text_now, tarot_image_path = choose_tarot(self.tarot_dict)
        if not tarot_image_path.exists():
            parts.append(ReplyPart.text(f"塔罗牌图片不存在：{tarot_image_path.name}"))
            return parts

        text_image_path = self.cache_dir / "tarot_text.jpg"
        self.render_text_image(text_now, text_image_path)

        parts.extend([
                ReplyPart.image(str(text_image_path)),
                ReplyPart.image(str(tarot_image_path)),
            ]
        )

        return parts

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

    async def create_nuannuan_result(self, event: SimpleEvent):
        period = get_current_period()
        cache_path = self.cache_dir / "nuannuan.json"

        if cache_path.exists():
            try:
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
                cached_message = cache.get(str(period))
                if cached_message:
                    image_path = self.cache_dir / "nuannuan.jpg"
                    self.render_text_image(cached_message, image_path, width_now=25)
                    return [ReplyPart.image(str(image_path))]
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
            return [ReplyPart.image(str(image_path))]
        except Exception as exc:
            logger.warning(f"暖暖获取失败: {exc}")
            return [ReplyPart.text("暖暖获取失败，请看qq文档： " + QQ_DOC_URL)]