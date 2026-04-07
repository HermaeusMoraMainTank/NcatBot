"""语义化颜色输出封装 — 基于 click.style()。"""

import click


def success(text: str) -> str:
    return click.style(text, fg="green")


def error(text: str) -> str:
    return click.style(text, fg="red")


def warning(text: str) -> str:
    return click.style(text, fg="yellow")


def info(text: str) -> str:
    return click.style(text, fg="cyan")


def header(text: str) -> str:
    return click.style(text, fg="cyan", bold=True)


def command(text: str) -> str:
    return click.style(text, fg="green", bold=True)


def key(text: str) -> str:
    return click.style(text, fg="blue", bold=True)


def value(text: str) -> str:
    return click.style(text, fg="white")


def dim(text: str) -> str:
    return click.style(text, dim=True)
