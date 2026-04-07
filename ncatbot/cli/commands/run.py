"""run / dev 命令 — 启动 Bot。"""

import click

from ncatbot.utils import MISSING
from .init import init


@click.command()
@click.option("--debug", is_flag=True, help="启用调试模式")
@click.option("--no-hot-reload", is_flag=True, help="禁用插件热重载")
@click.option(
    "--plugins-dir",
    default=None,
    type=str,
    help="插件目录路径（默认使用 config 中的 plugin.plugins_dir）",
)
@click.option(
    "--non-interactive",
    is_flag=True,
    help="非交互模式运行（所有确认自动使用默认值）",
)
@click.option(
    "--bot-uin",
    default=None,
    type=str,
    help="机器人 QQ 号（覆盖 config / 环境变量）",
)
@click.option(
    "--root",
    default=None,
    type=str,
    help="管理员 QQ 号（覆盖 config / 环境变量）",
)
def run(
    debug: bool,
    no_hot_reload: bool,
    plugins_dir: str | None,
    non_interactive: bool,
    bot_uin: str | None,
    root: str | None,
):
    """启动 NcatBot（连接 NapCat + 加载插件 + 监听事件）"""
    if non_interactive:
        from ncatbot.utils import set_non_interactive

        set_non_interactive()

    from ncatbot.utils import get_config_manager, DEFAULT_BOT_UIN

    mgr = get_config_manager()
    mgr.apply_runtime_overrides(
        bot_uin=bot_uin if bot_uin is not None else MISSING,
        root=root if root is not None else MISSING,
    )

    # bot_uin 无有效值时自动运行 init
    if mgr.bot_uin == DEFAULT_BOT_UIN:
        click.echo("尚未配置 bot_uin，进入初始化流程...")
        ctx = click.get_current_context()
        ctx.invoke(init)
        mgr.reload()
        mgr.apply_runtime_overrides(
            bot_uin=bot_uin if bot_uin is not None else MISSING,
            root=root if root is not None else MISSING,
        )

    from ncatbot.app import BotClient

    bot = BotClient(
        debug=True if debug else MISSING,
        plugins_dir=plugins_dir if plugins_dir is not None else MISSING,
        hot_reload=False if no_hot_reload else MISSING,
    )
    bot.run()


@click.command()
@click.option(
    "--plugins-dir",
    default=None,
    type=str,
    help="插件目录路径（默认使用 config 中的 plugin.plugins_dir）",
)
def dev(plugins_dir: str | None):
    """以开发模式启动（debug=True + 热重载）"""
    from ncatbot.app import BotClient

    bot = BotClient(
        debug=True,
        plugins_dir=plugins_dir if plugins_dir is not None else MISSING,
        hot_reload=True,
    )
    bot.run()
