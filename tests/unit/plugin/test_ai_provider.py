"""对话供应商切换：DeepSeek 官方 / OpenCode Go 预设。"""

from __future__ import annotations

import pytest

import plugins.common.utils.AiUtil as ai_util
from plugins.common.utils.AiUtil import AiUtil


@pytest.fixture(autouse=True)
def _restore_provider():
    before = AiUtil.current_provider()
    yield
    AiUtil.set_provider(before)


def test_default_provider_is_deepseek_official():
    AiUtil.set_provider("ds_official")
    assert AiUtil.current_provider() == "ds_official"
    assert AiUtil.current_model("chat") == "deepseek-flash"
    assert AiUtil.current_model("pro") == "deepseek-v4-pro"


def test_ocgo_gemini_switches_model_and_headers():
    AiUtil.set_provider("ocgo_gemini")
    assert AiUtil.current_provider() == "ocgo_gemini"
    assert AiUtil.current_model("chat") == "gemini-4-argon"
    kwargs = ai_util._provider_client_kwargs(ai_util.active_provider(), 30)
    assert kwargs["base_url"] == "https://opencode.ai/inference/go/openai/v1"
    headers = kwargs["default_headers"]
    assert headers["x-opencode-session"]
    assert headers["User-Agent"].startswith("NcatBot")


def test_payg_gemini_lite_uses_console_base_url():
    AiUtil.set_provider("ocpay_gemini_lite")
    assert AiUtil.current_model("chat") == "gemini-3.5-flash-lite"
    kwargs = ai_util._provider_client_kwargs(ai_util.active_provider(), 30)
    assert kwargs["base_url"] == "https://opencode.ai/inference/openai/v1"


def test_ocgo_deepseek_presets_fall_back_to_official_vision():
    AiUtil.set_provider("ocgo_ds_flash")
    assert AiUtil.current_model("chat") == "deepseek-v4.1-flash"
    assert ai_util.active_provider()["vision_provider"] == "ds_official"
    assert ai_util.active_provider()["vision_model"] == "deepseek-flash"

    AiUtil.set_provider("ocgo_ds_pro")
    assert AiUtil.current_model("chat") == "deepseek-v4-pro"


def test_thinking_body_only_for_official():
    AiUtil.set_provider("ds_official")
    assert ai_util._provider_thinking_body(ai_util.active_provider(), True)

    AiUtil.set_provider("ocgo_gemini")
    assert ai_util._provider_thinking_body(ai_util.active_provider(), True) == {}


def test_unknown_provider_falls_back():
    AiUtil.set_provider("not-a-provider")
    assert AiUtil.current_provider() == "ds_official"


def test_provider_options_cover_all_presets():
    options = AiUtil.provider_options()
    values = {item["value"] for item in options}
    assert {
        "ds_official",
        "ocgo_gemini",
        "ocgo_ds_flash",
        "ocgo_ds_pro",
        "ocpay_gemini_lite",
        "ocpay_gemini_flash",
    } <= values


@pytest.mark.asyncio
async def test_auth_error_falls_back_to_deepseek(monkeypatch):
    from types import SimpleNamespace

    calls = {"n": 0}

    class AuthExpired(Exception):
        status_code = 401

    class FakeCompletions:
        async def create(self, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise AuthExpired("api_key_expired")
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="ok", tool_calls=[])
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=1, completion_tokens=1, total_tokens=2
                ),
            )

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(ai_util, "AsyncOpenAI", FakeClient)
    AiUtil.set_provider("ocgo_gemini")

    result = await AiUtil.chat_completion(
        [{"role": "user", "content": "hi"}], max_tokens=8
    )

    assert result is not None
    assert result["content"] == "ok"
    assert result["model"] == "deepseek-flash"
    assert calls["n"] == 2
