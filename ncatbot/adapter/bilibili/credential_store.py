"""Bilibili 凭据持久化 — 扫码登录后将凭据写回 config.yaml"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ncatbot.utils import get_config_manager, get_log

if TYPE_CHECKING:
    from bilibili_api import Credential
    from .config import BilibiliConfig

LOG = get_log("BilibiliCredStore")


def has_valid_credential(config: "BilibiliConfig") -> bool:
    """检查配置中是否包含非空的 sessdata（最基本的凭据字段）。"""
    return bool(config.sessdata)


def save_credential_to_config(credential: "Credential") -> None:
    """将 Credential 中的凭据字段写回 config.yaml 中的 bilibili 适配器配置。

    定位 ``adapters`` 列表中 ``type == "bilibili"`` 的第一个条目，
    更新其 ``config`` 字典中的 sessdata / bili_jct / dedeuserid / ac_time_value，
    然后调用 ``ConfigManager.save()`` 原子写入磁盘。
    """
    mgr = get_config_manager()
    cfg = mgr.config

    # 找到 bilibili 适配器条目
    entry = None
    for adapter in cfg.adapters:
        if adapter.type == "bilibili":
            entry = adapter
            break

    if entry is None:
        LOG.error("config.yaml 中未找到 type=bilibili 的适配器条目，无法保存凭据")
        return

    entry.config["sessdata"] = credential.sessdata or ""
    entry.config["bili_jct"] = credential.bili_jct or ""
    entry.config["dedeuserid"] = credential.dedeuserid or ""
    entry.config["ac_time_value"] = credential.ac_time_value or ""

    mgr.save()
    LOG.info("Bilibili 凭据已保存到 config.yaml")
