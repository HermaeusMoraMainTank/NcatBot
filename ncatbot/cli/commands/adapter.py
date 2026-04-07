"""adapter 命令组 — 适配器管理。"""

import asyncio
from typing import Optional

import click

from ..utils.colors import success, error, info, warning, header, key, value, dim


def _get_manager():
    from ncatbot.utils import get_config_manager

    return get_config_manager()


def _get_registry():
    from ncatbot.adapter import adapter_registry

    return adapter_registry


def _find_adapter_entry(mgr, adapter_type: str):
    """在 config.adapters 列表中查找指定类型的条目。"""
    for entry in mgr.config.adapters:
        if entry.type == adapter_type:
            return entry
    return None


# ========================== 适配器专属引导流程 ==========================

_PLATFORM_MAP = {
    "napcat": "qq",
    "bilibili": "bilibili",
    "github": "github",
    "mock": "mock",
}


def _prompt_napcat() -> dict:
    """NapCat (QQ) 适配器交互式配置。"""
    click.echo(info("配置 NapCat (QQ) 适配器:"))
    ws_uri = click.prompt("  WebSocket 地址", default="ws://localhost:3001", type=str)
    ws_token = click.prompt("  WebSocket Token", default="napcat_ws", type=str)
    return {
        "ws_uri": ws_uri,
        "ws_token": ws_token,
        "webui_uri": "http://localhost:6099",
        "webui_token": "napcat_webui",
        "enable_webui": True,
    }


def _prompt_github() -> dict:
    """GitHub 适配器交互式配置。"""
    click.echo(info("配置 GitHub 适配器:"))
    click.echo(
        dim(
            "  需要 GitHub Personal Access Token (PAT)\n"
            "  创建方式: GitHub → Settings → Developer settings → Personal access tokens"
        )
    )
    token = click.prompt("  GitHub Token", type=str, hide_input=True)
    if not token.strip():
        click.echo(warning("  未输入 token，API 调用将受到严格速率限制"))
    return {"token": token.strip()}


def _prompt_bilibili() -> Optional[dict]:
    """Bilibili 适配器交互式配置 — 扫码登录。"""
    click.echo(info("配置 Bilibili 适配器:"))
    click.echo(dim("  将启动扫码登录流程，请准备好 Bilibili APP"))
    if not click.confirm("  是否继续?", default=True):
        return None

    try:
        from ncatbot.adapter.bilibili.auth import qrcode_login

        credential = asyncio.run(qrcode_login())
        return {
            "sessdata": credential.sessdata,
            "bili_jct": credential.bili_jct,
            "buvid3": credential.buvid3,
            "dedeuserid": credential.dedeuserid,
            "ac_time_value": credential.ac_time_value,
        }
    except Exception as e:
        click.echo(error(f"  Bilibili 登录失败: {e}"))
        return None


def _prompt_mock() -> dict:
    """Mock 适配器 — 无需额外配置。"""
    click.echo(info("Mock 适配器无需额外配置。"))
    return {}


_PROMPT_MAP = {
    "napcat": _prompt_napcat,
    "bilibili": _prompt_bilibili,
    "github": _prompt_github,
    "mock": _prompt_mock,
}


# ========================== 命令组 ==========================


@click.group()
def adapter():
    """适配器管理（启用 / 禁用 / 查看）"""


@adapter.command("list")
def adapter_list():
    """列出所有可用适配器及启用状态"""
    registry = _get_registry()
    available = registry.discover()
    mgr = _get_manager()
    enabled_types = {e.type: e for e in mgr.config.adapters}

    click.echo(header("可用适配器:"))
    click.echo()
    for name in sorted(available.keys()):
        cls = available[name]
        platform = getattr(cls, "platform", "?")
        entry = enabled_types.get(name)

        if entry is not None:
            if entry.enabled:
                status = click.style("● 已启用", fg="green")
            else:
                status = click.style("○ 已禁用", fg="yellow")
        else:
            status = dim("  未配置")

        desc = getattr(cls, "description", "")
        click.echo(f"  {key(name):20s}  platform={value(platform):12s}  {status}")
        if desc:
            click.echo(f"  {dim(desc)}")
        click.echo()


@adapter.command()
@click.argument("adapter_type")
def enable(adapter_type: str):
    """交互式启用适配器（自动引导配置）"""
    registry = _get_registry()
    available = registry.discover()

    if adapter_type not in available:
        names = ", ".join(sorted(available.keys()))
        click.echo(error(f"未知的适配器类型 '{adapter_type}'。可用: {names}"))
        raise SystemExit(1)

    mgr = _get_manager()
    existing = _find_adapter_entry(mgr, adapter_type)

    if existing is not None and existing.enabled:
        if not click.confirm(
            warning(f"适配器 '{adapter_type}' 已启用，是否重新配置?"),
            default=False,
        ):
            return

    # 运行适配器专属引导
    prompt_fn = _PROMPT_MAP.get(adapter_type)
    if prompt_fn is not None:
        adapter_config = prompt_fn()
        if adapter_config is None:
            click.echo(info("已取消。"))
            return
    else:
        # 未知适配器（第三方），无引导流程
        adapter_config = {}
        click.echo(info(f"第三方适配器 '{adapter_type}'，跳过交互配置。"))

    platform = _PLATFORM_MAP.get(adapter_type, adapter_type)

    if existing is not None:
        existing.enabled = True
        existing.config = adapter_config
        existing.platform = platform
    else:
        from ncatbot.utils.config.models import AdapterEntry

        entry = AdapterEntry(
            type=adapter_type,
            platform=platform,
            enabled=True,
            config=adapter_config,
        )
        mgr.config.adapters.append(entry)

    mgr.save()
    click.echo(success(f"适配器 '{adapter_type}' 已启用并写入 config.yaml"))


@adapter.command()
@click.argument("adapter_type")
def disable(adapter_type: str):
    """禁用适配器"""
    mgr = _get_manager()
    existing = _find_adapter_entry(mgr, adapter_type)

    if existing is None:
        click.echo(error(f"适配器 '{adapter_type}' 未在 config.yaml 中配置"))
        raise SystemExit(1)

    if not existing.enabled:
        click.echo(info(f"适配器 '{adapter_type}' 已经是禁用状态"))
        return

    existing.enabled = False
    mgr.save()
    click.echo(success(f"适配器 '{adapter_type}' 已禁用"))


@adapter.command()
def status():
    """显示已配置适配器的状态摘要"""
    mgr = _get_manager()
    entries = mgr.config.adapters

    if not entries:
        click.echo(
            info("未配置任何适配器。使用 'ncatbot adapter enable <type>' 启用。")
        )
        return

    click.echo(header("适配器状态:"))
    click.echo()
    for entry in entries:
        enabled_str = (
            click.style("已启用", fg="green")
            if entry.enabled
            else click.style("已禁用", fg="yellow")
        )
        click.echo(
            f"  {key(entry.type):16s}  platform={value(entry.platform or '(默认)'):12s}  {enabled_str}"
        )

        # 显示关键配置摘要（隐藏敏感信息）
        cfg = entry.config
        if entry.type == "napcat":
            click.echo(f"    ws_uri: {dim(cfg.get('ws_uri', '(未设置)'))}")
        elif entry.type == "github":
            token = cfg.get("token", "")
            masked = f"{token[:4]}****" if len(token) > 4 else "(未设置)"
            click.echo(f"    token: {dim(masked)}")
        elif entry.type == "bilibili":
            uid = cfg.get("dedeuserid", "")
            click.echo(f"    uid: {dim(uid or '(未设置)')}")

        click.echo()
