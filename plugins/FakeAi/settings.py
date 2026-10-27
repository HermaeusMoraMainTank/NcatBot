"""FakeAi 配置外置：ConfigMixin ↔ 运行时模块。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set

from .cognition.config import DEFAULT_COGNITION, CognitionConfig
from .expression import catalog as expression_catalog_mod
from .interaction.config import DEFAULT_CONFIG, InteractionConfig
from .interaction.state_machine import state_store

_log = logging.getLogger(__name__)

# 运行时旁路名单（由 apply 刷新）
ADMIN_IDS: Set[str] = {"273421673"}
FROZEN_USERS: List[int] = [794383252]
BOT_QQ: str = "3555202423"

FAKEAI_CONFIG_DEFAULTS: Dict[str, Any] = {
    "allowed_groups": [853963912, 719518427, 585479130, 1064163905],
    "disabled_groups": [],
    "bot_qq": "3555202423",
    "aliases": ["蓝晴"],
    "admin_ids": ["273421673"],
    "frozen_users": [794383252],
    "enable_group_cd": True,
    "enable_user_cd": False,
    "enable_typing_delay": False,
    "typing_delay_per_char": 0.1,
    "agent_enabled": True,
    "skills_enabled": True,
    "agent_max_tokens": 25000,
    "agent_max_rounds": 8,
    "persona": "lanqing",
    "proactive_enabled": True,
    "proactive_quiet_minutes": 45,
    "proactive_interval_minutes": 180,
    "proactive_probability": 0.2,
    "interaction": asdict(InteractionConfig()),
    "cognition": asdict(CognitionConfig()),
    "expression": {
        "enabled": True,
        "catalog_path": "data/fakeai/stickers",
        "max_per_message": 1,
        "sticker_library": "both",
        "sticker_auto_rate": 0.25,
    },
}


def is_admin(user_id: object) -> bool:
    return str(user_id) in ADMIN_IDS


def _group_mode(plugin, allowed: List[int]) -> str:
    configured = str(plugin.get_config("group_mode", "") or "").strip().lower()
    if configured in {"all", "allowlist"}:
        return configured
    return "allowlist" if allowed else "all"


def _as_str_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, Iterable):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [str(raw)]


def _as_int_list(raw: Any) -> List[int]:
    out: List[int] = []
    for s in _as_str_list(raw):
        try:
            out.append(int(s))
        except ValueError:
            continue
    return out


def _merge_dict(base: Dict[str, Any], override: Any) -> Dict[str, Any]:
    merged = dict(base)
    if isinstance(override, dict):
        for k, v in override.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k] = _merge_dict(merged[k], v)
            else:
                merged[k] = v
    return merged


def apply_from_plugin(plugin) -> None:
    """从 FakeAi 插件 ConfigMixin 加载并应用到各模块。"""
    global ADMIN_IDS, FROZEN_USERS, BOT_QQ

    plugin.init_defaults(FAKEAI_CONFIG_DEFAULTS)

    allowed = _as_int_list(plugin.get_config("allowed_groups", []))
    disabled = _as_int_list(plugin.get_config("disabled_groups", []))
    group_mode = _group_mode(plugin, allowed)
    bot_qq = str(plugin.get_config("bot_qq", BOT_QQ) or BOT_QQ)
    aliases = _as_str_list(plugin.get_config("aliases", ["蓝晴"])) or ["蓝晴"]
    admin_ids = set(_as_str_list(plugin.get_config("admin_ids", ["273421673"])))
    frozen = _as_int_list(plugin.get_config("frozen_users", FROZEN_USERS))

    ADMIN_IDS = admin_ids or ADMIN_IDS
    FROZEN_USERS = frozen if frozen else FROZEN_USERS
    BOT_QQ = bot_qq

    # —— interaction ——
    inter_raw = plugin.get_config("interaction", {}) or {}
    if isinstance(inter_raw, dict):
        # 新增的子键只补内存，后台才看得见；保存时才写进 config.yaml
        for _k, _v in asdict(InteractionConfig()).items():
            inter_raw.setdefault(_k, _v)
    inter = _merge_dict(asdict(InteractionConfig()), inter_raw)
    inter["aliases"] = aliases
    inter["bot_qq"] = bot_qq
    inter["group_cd_sec"] = float(
        inter.get("group_cd_sec", plugin.get_config("group_cd_sec", 10))
    )
    new_inter = InteractionConfig(
        **{k: inter[k] for k in InteractionConfig.__dataclass_fields__ if k in inter}
    )
    for k, v in asdict(new_inter).items():
        setattr(DEFAULT_CONFIG, k, v)
    state_store.config = DEFAULT_CONFIG

    # —— cognition ——
    cog_raw = plugin.get_config("cognition", {}) or {}
    cog = _merge_dict(asdict(CognitionConfig()), cog_raw)
    new_cog = CognitionConfig(
        **{k: cog[k] for k in CognitionConfig.__dataclass_fields__ if k in cog}
    )
    for k, v in asdict(new_cog).items():
        setattr(DEFAULT_COGNITION, k, v)

    # —— expression ——
    expr_raw = plugin.get_config("expression", {}) or {}
    if isinstance(expr_raw, dict):
        # 默认值只补内存，保存时才会写进 config.yaml
        expr_raw.setdefault("sticker_library", "both")
        expr_raw.setdefault("sticker_auto_rate", 0.25)
    expr_enabled = bool(expr_raw.get("enabled", True))
    expr_max = int(expr_raw.get("max_per_message", 1))
    catalog_path = str(expr_raw.get("catalog_path", "data/fakeai/stickers"))
    sticker_lib = str(expr_raw.get("sticker_library", "both") or "both")
    try:
        sticker_rate = float(expr_raw.get("sticker_auto_rate", 0.25) or 0.0)
    except (TypeError, ValueError):
        sticker_rate = 0.25
    from . import expression as expr_pkg

    expr_pkg.EXPRESSION_ENABLED = expr_enabled
    expr_pkg.MAX_STICKERS_PER_REPLY = max(1, min(4, expr_max))
    expr_pkg.STICKER_LIBRARY = sticker_lib
    try:
        sticker_activity = int(expr_raw.get("activity", 2))
    except (TypeError, ValueError):
        sticker_activity = 2
    expr_pkg.STICKER_AUTO_RATE = min(1.0, max(0.0, sticker_rate))
    tone = str(expr_raw.get("tone_strength", "light") or "light").strip().lower()
    if tone not in ("off", "light", "normal", "max"):
        tone = "light"
    expr_pkg.TONE_STRENGTH = tone
    expr_pkg.EDITOR_PASS = bool(expr_raw.get("editor_pass", False))
    expression_catalog_mod.sticker_catalog.root = Path(catalog_path)
    # 先全量扫一遍，才能知道磁盘上有哪些图库
    expression_catalog_mod.sticker_catalog.active = None
    try:
        expression_catalog_mod.sticker_catalog.reload()
    except Exception as e:
        _log.warning("[FakeAi] 贴纸目录 reload 失败: %s", e)

    # 支持多图库：sticker_library 可以用逗号/空格分隔多个库名，
    # both / all / 空 = 全部启用；all_local = 只启用本地（不含梗鲸）
    import re as _re

    _libs = [x for x in _re.split(r"[,，\s]+", sticker_lib) if x]
    catalog = expression_catalog_mod.sticker_catalog
    if not _libs or any(x in ("both", "all") for x in _libs):
        catalog.active = None
    elif "all_local" in _libs:
        catalog.active = set(catalog.libraries().keys())
    else:
        known = set(catalog.libraries().keys())
        wanted = {x for x in _libs if x in known}
        if len(wanted) != len(_libs):
            missing = sorted(set(_libs) - known)
            _log.warning("[FakeAi] 未知图库 %s，可用：%s", missing, sorted(known))
        catalog.active = wanted or None
    try:
        whale_count = expr_pkg.whale_catalog.load_cache()
        if not whale_count:
            asyncio.create_task(expr_pkg.whale_catalog.ensure_index())
    except Exception as e:
        _log.debug("[FakeAi] 梗鲸图库初始化失败: %s", e)

    # —— FakeAi 模块级运行时（冷却 / 白名单）——
    # 注意：from . import FakeAi 会拿到 __init__ 导出的类，必须按子模块导入
    from importlib import import_module

    fakeai_mod = import_module(".FakeAi", package=__package__)

    fakeai_mod.USE_DS_AGENT = bool(plugin.get_config("agent_enabled", True))
    fakeai_mod.SKILLS_ENABLED = bool(plugin.get_config("skills_enabled", True))

    # —— 人设切换 ——
    persona = str(plugin.get_config("persona", "lanqing") or "lanqing").strip()
    path = fakeai_mod.persona_path(persona)
    if not path or not Path(path).is_file():
        _log.warning("[FakeAi] 人设 %s 不存在，回退蓝晴", persona)
        persona = "lanqing"
        path = fakeai_mod.persona_path(persona)
    fakeai_mod.PERSONA = persona
    # 后台「表情包图库」下拉跟着磁盘上的图库走
    try:
        fakeai_mod.refresh_sticker_choices()
    except Exception as e:
        _log.debug("[FakeAi] 刷新图库选项失败: %s", e)
    fakeai_mod.AGENT_YAML_PATH = path
    # 机器人自称跟随人设（yml 的 name 字段），避免换了人设还硬写「蓝晴」
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.startswith("name:"):
                display = line.split(":", 1)[1].strip()
                if display:
                    fakeai_mod.BOT_NAME = display
                break
    except Exception as e:
        _log.debug("[FakeAi] 读取人设名称失败: %s", e)
    _log.info("[FakeAi] 使用人设：%s（%s）", persona, path)

    fakeai_mod.AGENT_MAX_TOKENS = max(
        0, int(plugin.get_config("agent_max_tokens", 60000) or 0)
    )
    fakeai_mod.AGENT_MAX_ROUNDS = max(
        2, int(plugin.get_config("agent_max_rounds", 12) or 12)
    )
    fakeai_mod.STICKER_ACTIVITY = max(0, min(3, sticker_activity))
    # —— 触发档位（参考 QQ-Agent）——
    fakeai_mod.TRIGGER_TIER = max(1, min(4, int(DEFAULT_CONFIG.trigger_tier or 3)))
    fakeai_mod.TRIGGER_KEYWORDS = list(DEFAULT_CONFIG.keywords or [])
    try:
        percent = float(DEFAULT_CONFIG.random_percent or 0.0)
    except (TypeError, ValueError):
        percent = 0.0
    fakeai_mod.RANDOM_PERCENT = min(100.0, max(0.0, percent))
    fakeai_mod.HISTORY_COUNT = max(4, min(40, int(DEFAULT_CONFIG.history_count or 20)))
    fakeai_mod.PROACTIVE_ENABLED = bool(
        plugin.get_config("proactive_enabled", True)
    )
    fakeai_mod.PROACTIVE_QUIET_SEC = (
        max(5, int(plugin.get_config("proactive_quiet_minutes", 45) or 45)) * 60
    )
    fakeai_mod.PROACTIVE_INTERVAL_SEC = (
        max(30, int(plugin.get_config("proactive_interval_minutes", 180) or 180))
        * 60
    )
    try:
        prob = float(plugin.get_config("proactive_probability", 0.2) or 0.2)
    except (TypeError, ValueError):
        prob = 0.2
    # 兼容旧配置：写成 20/180 这种百分数时按百分比折算，而不是被 clamp 成 100%
    if prob > 1.0:
        prob = prob / 100.0
    fakeai_mod.PROACTIVE_PROBABILITY = min(1.0, max(0.0, prob))
    fakeai_mod.FAKEAI_GROUP_MODE = group_mode
    fakeai_mod.FAKEAI_ALLOWED_GROUPS = frozenset(allowed)
    fakeai_mod.FAKEAI_DISABLED_GROUPS = frozenset(disabled)
    fakeai_mod.trigger_interval = float(new_inter.group_cd_sec)
    fakeai_mod.enable_group_cd = bool(plugin.get_config("enable_group_cd", True))
    fakeai_mod.enable_user_cd = bool(plugin.get_config("enable_user_cd", False))
    fakeai_mod.enable_typing_delay = bool(
        plugin.get_config("enable_typing_delay", False)
    )
    fakeai_mod.typing_delay_per_char = float(
        plugin.get_config("typing_delay_per_char", 0.1)
    )
    # 运行时已经由事件校准过的话，以实际登录 QQ 为准
    fakeai_mod.BOT_QQ = getattr(fakeai_mod, "ACTUAL_BOT_QQ", "") or bot_qq
    fakeai_mod.FakeAi.frozen_users = list(FROZEN_USERS)

    _log.info(
        (
            "[FakeAi] 配置已加载 groups=%s aliases=%s tier=%s cd=%ss "
            "stickers=%s name=%s"
        ),
        list(allowed) if group_mode == "allowlist" else "ALL",
        aliases,
        new_inter.trigger_tier,
        new_inter.group_cd_sec,
        catalog_path,
        getattr(fakeai_mod, "BOT_NAME", ""),
    )


def set_group_enabled(plugin, group_id: object, enabled: bool) -> None:
    """更新单个群的 FakeAi 启停状态并刷新运行时配置。"""
    group = int(group_id)
    allowed = _as_int_list(plugin.get_config("allowed_groups", []))
    disabled = _as_int_list(plugin.get_config("disabled_groups", []))
    mode = _group_mode(plugin, allowed)

    if enabled:
        disabled = [item for item in disabled if item != group]
        if mode == "allowlist" and group not in allowed:
            allowed.append(group)
    elif mode == "allowlist":
        allowed = [item for item in allowed if item != group]
    elif group not in disabled:
        disabled.append(group)

    plugin.update_config(
        {
            "group_mode": mode,
            "allowed_groups": allowed,
            "disabled_groups": disabled,
        }
    )
    apply_from_plugin(plugin)
