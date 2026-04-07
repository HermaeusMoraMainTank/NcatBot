"""confirm / async_confirm 中断语义 & CLI REPL 非交互保护 回归测试"""

from unittest.mock import patch

import pytest

from ncatbot.utils.prompt import confirm, async_confirm


# ── confirm 同步 ───────────────────────────────────────────────


class TestConfirmInterrupt:
    """confirm() 在 KeyboardInterrupt / EOFError 时必须返回 False，而非 default。"""

    @patch("ncatbot.utils.prompt.is_interactive", return_value=True)
    @patch("builtins.input", side_effect=KeyboardInterrupt)
    def test_keyboard_interrupt_returns_false_even_if_default_true(self, _inp, _ia):
        assert confirm("install?", default=True) is False

    @patch("ncatbot.utils.prompt.is_interactive", return_value=True)
    @patch("builtins.input", side_effect=EOFError)
    def test_eof_error_returns_false_even_if_default_true(self, _inp, _ia):
        assert confirm("install?", default=True) is False

    @patch("ncatbot.utils.prompt.is_interactive", return_value=True)
    @patch("builtins.input", side_effect=KeyboardInterrupt)
    def test_keyboard_interrupt_returns_false_when_default_false(self, _inp, _ia):
        assert confirm("install?", default=False) is False

    @patch("ncatbot.utils.prompt.is_interactive", return_value=True)
    @patch("builtins.input", return_value="")
    def test_empty_input_returns_default(self, _inp, _ia):
        assert confirm("install?", default=True) is True
        assert confirm("install?", default=False) is False


# ── async_confirm 异步 ────────────────────────────────────────


class TestAsyncConfirmInterrupt:
    """async_confirm() 同理。"""

    @pytest.mark.asyncio
    @patch("ncatbot.utils.prompt.is_interactive", return_value=True)
    @patch("ncatbot.utils.prompt._async_input", side_effect=KeyboardInterrupt)
    async def test_keyboard_interrupt_returns_false_even_if_default_true(
        self, _inp, _ia
    ):
        assert await async_confirm("install?", default=True) is False

    @pytest.mark.asyncio
    @patch("ncatbot.utils.prompt.is_interactive", return_value=True)
    @patch("ncatbot.utils.prompt._async_input", side_effect=EOFError)
    async def test_eof_error_returns_false_even_if_default_true(self, _inp, _ia):
        assert await async_confirm("install?", default=True) is False

    @pytest.mark.asyncio
    @patch("ncatbot.utils.prompt.is_interactive", return_value=True)
    @patch("ncatbot.utils.prompt._async_input", side_effect=KeyboardInterrupt)
    async def test_keyboard_interrupt_returns_false_when_default_false(self, _inp, _ia):
        assert await async_confirm("install?", default=False) is False


# ── 非交互模式 ────────────────────────────────────────────────


class TestNonInteractive:
    """非交互模式下 confirm/async_confirm 应返回 default（不弹 input）。"""

    @patch("ncatbot.utils.prompt.is_interactive", return_value=False)
    def test_confirm_returns_default_in_non_interactive(self, _ia):
        assert confirm("install?", default=True) is True
        assert confirm("install?", default=False) is False

    @pytest.mark.asyncio
    @patch("ncatbot.utils.prompt.is_interactive", return_value=False)
    async def test_async_confirm_returns_default_in_non_interactive(self, _ia):
        assert await async_confirm("install?", default=True) is True
        assert await async_confirm("install?", default=False) is False


# ── CLI REPL 非交互保护 ──────────────────────────────────────


class TestCliReplProtection:
    """非交互环境下 `ncatbot`（无子命令）不应进入 REPL。"""

    @patch("ncatbot.utils.prompt.is_interactive", return_value=False)
    def test_cli_shows_help_in_non_interactive(self, _ia):
        from click.testing import CliRunner
        from ncatbot.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, [])
        # 应该打印帮助而不是挂起
        assert result.exit_code == 0
        assert "NcatBot" in result.output
