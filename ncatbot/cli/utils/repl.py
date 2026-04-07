"""交互式 REPL 引擎。"""

import shlex

import click

from .colors import header, error, info, dim


def start_repl(ctx: click.Context):
    """启动交互式 REPL 循环。"""
    cli_group = ctx.command

    # 尝试读取 bot_uin 作为提示符
    try:
        from ncatbot.utils import get_config_manager

        mgr = get_config_manager()
        bot_uin = mgr.bot_uin
    except Exception:
        bot_uin = "NcatBot"

    click.echo(header(f"NcatBot CLI — 交互模式 [{bot_uin}]"))
    click.echo(dim("输入命令执行操作，输入 help 查看帮助，输入 exit 退出。"))
    click.echo()

    while True:
        try:
            line = input(click.style(f"ncatbot [{bot_uin}]> ", fg="cyan")).strip()
        except (EOFError, KeyboardInterrupt):
            click.echo()
            break

        if not line:
            continue

        if line in ("exit", "quit", "q"):
            break

        if line in ("help", "h", "?"):
            click.echo(ctx.get_help())
            click.echo()
            continue

        try:
            args = shlex.split(line)
        except ValueError as e:
            click.echo(error(f"解析错误: {e}"))
            continue

        try:
            with cli_group.make_context("ncatbot", args, parent=ctx) as sub_ctx:
                cli_group.invoke(sub_ctx)
        except click.UsageError as e:
            click.echo(error(str(e)))
        except SystemExit:
            pass
        except Exception as e:
            click.echo(error(f"执行出错: {e}"))

    click.echo(info("再见！"))
