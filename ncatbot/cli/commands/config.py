"""config 命令组 — 配置管理。"""

import click
import yaml

from ..utils.colors import success, error, key, value, header, warning


def _get_manager():
    from ncatbot.utils import get_config_manager

    return get_config_manager()


def _resolve_value(raw: str):
    """将 CLI 传入的字符串转换为合适的 Python 类型。"""
    if raw.lower() in ("true", "yes"):
        return True
    if raw.lower() in ("false", "no"):
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    # 尝试解析为列表 (JSON 格式)
    if raw.startswith("["):
        import json

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return raw


@click.group()
def config():
    """配置管理"""


@config.command()
def show():
    """显示当前配置"""
    mgr = _get_manager()
    data = mgr.config.to_dict()
    click.echo(header("当前配置:"))
    click.echo(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
    )


@config.command("get")
@click.argument("config_key")
def config_get(config_key: str):
    """读取配置项（支持点分路径，如 napcat.ws_uri）"""
    mgr = _get_manager()
    cfg = mgr.config
    full_key = cfg.get_field_paths().get(config_key, config_key)
    parts = full_key.split(".")
    obj = cfg
    try:
        for part in parts:
            obj = getattr(obj, part)
    except AttributeError:
        click.echo(error(f"未知的配置项: {config_key}"))
        raise SystemExit(1)
    click.echo(f"{key(config_key)} = {value(str(obj))}")


@config.command("set")
@click.argument("config_key")
@click.argument("config_value")
def config_set(config_key: str, config_value: str):
    """设置配置项（支持点分路径）"""
    mgr = _get_manager()
    converted = _resolve_value(config_value)
    try:
        mgr.update_value(config_key, converted)
        mgr.save()
    except Exception as e:
        click.echo(error(f"设置失败: {e}"))
        raise SystemExit(1)
    click.echo(success(f"已设置 {config_key} = {converted}"))


@config.command()
def check():
    """检查配置问题（安全性 + 必填项）"""
    mgr = _get_manager()
    issues = mgr.get_issues()
    if not issues:
        click.echo(success("配置检查通过，无问题。"))
        return
    click.echo(warning(f"发现 {len(issues)} 个问题:"))
    for i, issue in enumerate(issues, 1):
        click.echo(f"  {i}. {issue}")
