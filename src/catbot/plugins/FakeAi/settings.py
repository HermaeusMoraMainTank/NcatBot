"""FakeAi 配置外置：ConfigMixin ↔ 运行时模块。"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any, Dict, Iterable, List, Set

from .cognition.config import DEFAULT_COGNITION, CognitionConfig
from .expression import catalog as expression_catalog_mod
from .interaction.config import DEFAULT_CONFIG, InteractionConfig
from .interaction.state_machine import state_store

_log = logging.getLogger(__name__)

# 运行时旁路名单（由 apply 刷新）
ADMIN_IDS: Set[str] = {"273421673"}
FAVOR_SKIP_IDS: Set[str] = {"273421673", "635773721"}
FROZEN_USERS: List[int] = [794383252]
BOT_QQ: str = "3555202423"
VISION_CACHE_ENABLED: bool = True
VISION_CACHE_RETENTION_DAYS: int = 3

FAKEAI_CONFIG_DEFAULTS: Dict[str, Any] = {
    "allowed_groups": [853963912, 719518427, 585479130, 1064163905],
    "bot_qq": "3555202423",
    "aliases": ["蓝晴"],
    "admin_ids": ["273421673"],
    "favor_skip_ids": ["273421673", "635773721"],
    "frozen_users": [794383252],
    "vision_cache_enabled": True,
    "vision_cache_days": 3,
    "enable_group_cd": True,
    "enable_user_cd": False,
    "enable_callback": False,
    "callback_timeout": 15,
    "enable_typing_delay": False,
    "typing_delay_per_char": 0.1,
    "interaction": asdict(InteractionConfig()),
    "cognition": asdict(CognitionConfig()),
    "expression": {
        "enabled": True,
        "catalog_path": "data/fakeai/stickers",
        "max_per_message": 1,
    },
}


def is_admin(user_id: object) -> bool:
    return str(user_id) in ADMIN_IDS


def should_skip_favor(user_id: object) -> bool:
    return str(user_id) in FAVOR_SKIP_IDS or str(user_id) in ADMIN_IDS


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
    global ADMIN_IDS, FAVOR_SKIP_IDS, FROZEN_USERS, BOT_QQ
    global VISION_CACHE_ENABLED, VISION_CACHE_RETENTION_DAYS

    plugin.init_defaults(FAKEAI_CONFIG_DEFAULTS)

    allowed = _as_int_list(plugin.get_config("allowed_groups", []))
    bot_qq = str(plugin.get_config("bot_qq", BOT_QQ) or BOT_QQ)
    aliases = _as_str_list(plugin.get_config("aliases", ["蓝晴"])) or ["蓝晴"]
    admin_ids = set(_as_str_list(plugin.get_config("admin_ids", ["273421673"])))
    favor_skip = set(
        _as_str_list(plugin.get_config("favor_skip_ids", list(FAVOR_SKIP_IDS)))
    )
    frozen = _as_int_list(plugin.get_config("frozen_users", FROZEN_USERS))
    vision_cache_enabled = bool(plugin.get_config("vision_cache_enabled", True))
    vision_cache_days = int(plugin.get_config("vision_cache_days", 3))

    ADMIN_IDS = admin_ids or ADMIN_IDS
    FAVOR_SKIP_IDS = favor_skip or FAVOR_SKIP_IDS
    FROZEN_USERS = frozen if frozen else FROZEN_USERS
    BOT_QQ = bot_qq
    VISION_CACHE_ENABLED = vision_cache_enabled
    VISION_CACHE_RETENTION_DAYS = max(1, vision_cache_days)

    # —— interaction ——
    inter_raw = plugin.get_config("interaction", {}) or {}
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
    expr_enabled = bool(expr_raw.get("enabled", True))
    expr_max = int(expr_raw.get("max_per_message", 1))
    catalog_path = str(expr_raw.get("catalog_path", "data/fakeai/stickers"))
    from . import expression as expr_pkg

    expr_pkg.EXPRESSION_ENABLED = expr_enabled
    expr_pkg.MAX_STICKERS_PER_REPLY = expr_max
    expression_catalog_mod.sticker_catalog.root = __import__(
        "pathlib", fromlist=["Path"]
    ).Path(catalog_path)
    try:
        expression_catalog_mod.sticker_catalog.reload()
    except Exception as e:
        _log.warning("[FakeAi] 贴纸目录 reload 失败: %s", e)

    # —— FakeAi 模块级运行时（冷却 / 白名单）——
    # 注意：from . import FakeAi 会拿到 __init__ 导出的类，必须按子模块导入
    from importlib import import_module

    fakeai_mod = import_module(".FakeAi", package=__package__)

    fakeai_mod.FAKEAI_ALLOWED_GROUPS = frozenset(allowed)
    fakeai_mod.trigger_interval = float(new_inter.group_cd_sec)
    fakeai_mod.enable_group_cd = bool(plugin.get_config("enable_group_cd", True))
    fakeai_mod.enable_user_cd = bool(plugin.get_config("enable_user_cd", False))
    fakeai_mod.enable_callback = bool(plugin.get_config("enable_callback", False))
    fakeai_mod.callback_timeout = int(plugin.get_config("callback_timeout", 15))
    fakeai_mod.enable_typing_delay = bool(
        plugin.get_config("enable_typing_delay", False)
    )
    fakeai_mod.typing_delay_per_char = float(
        plugin.get_config("typing_delay_per_char", 0.1)
    )
    fakeai_mod.BOT_QQ = bot_qq
    fakeai_mod.VISION_CACHE_ENABLED = VISION_CACHE_ENABLED
    fakeai_mod.VISION_CACHE_RETENTION_DAYS = VISION_CACHE_RETENTION_DAYS
    fakeai_mod.FakeAi.frozen_users = list(FROZEN_USERS)

    _log.info(
        (
            "[FakeAi] 配置已加载 groups=%s aliases=%s obs=%ss cd=%ss "
            "stickers=%s vision_cache=%s/%sd"
        ),
        list(allowed) if allowed else "ALL",
        aliases,
        new_inter.observation_sec,
        new_inter.group_cd_sec,
        catalog_path,
        "ON" if VISION_CACHE_ENABLED else "OFF",
        VISION_CACHE_RETENTION_DAYS,
    )
