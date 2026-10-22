"""Agent 的模型接入层。

框架不依赖任何插件：默认走配置项 ``ai.*`` / 环境变量，
插件也可以在运行时用 ``AgentRuntime.set_llm`` 覆盖成自己的实现。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

import httpx

from ncatbot.utils import get_log

LOG = get_log("AgentLLM")


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""
    reasoning_content: str = ""


class LLMClient(Protocol):
    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: Optional[list[dict[str, Any]]] = None,
        capability: str = "chat",
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: float = 90.0,
    ) -> Optional[LLMResponse]: ...


class OpenAICompatibleLLM:
    """最小 OpenAI 兼容客户端（只用 httpx，无额外依赖）。"""

    def __init__(
        self,
        *,
        base_url: str = "https://api.deepseek.com",
        api_key: str = "",
        model: str = "deepseek-flash",
        extra_headers: Optional[dict[str, str]] = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.extra_headers = dict(extra_headers or {})

    @classmethod
    def from_config(cls, config: Any = None) -> "OpenAICompatibleLLM":
        ai = getattr(config, "ai", None)
        base_url = (
            getattr(ai, "base_url", "")
            or os.environ.get("DEEPSEEK_BASE_URL", "")
            or "https://api.deepseek.com"
        )
        api_key = (
            getattr(ai, "api_key", "")
            or os.environ.get("DEEPSEEK_API_KEY", "")
            or os.environ.get("OPENAI_API_KEY", "")
        )
        model = getattr(ai, "model", "") or "deepseek-flash"
        return cls(base_url=base_url, api_key=api_key, model=model)

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url)

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: Optional[list[dict[str, Any]]] = None,
        capability: str = "chat",
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: float = 90.0,
    ) -> Optional[LLMResponse]:
        if not self.configured:
            LOG.warning("未配置 LLM api_key（ai.api_key 或 DEEPSEEK_API_KEY）")
            return None
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        url = f"{self.base_url}/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=payload, headers=headers)
        except Exception as exc:
            LOG.error("LLM 请求失败：%s: %s", type(exc).__name__, exc)
            return None
        if response.status_code != 200:
            LOG.error("LLM HTTP %s: %s", response.status_code, response.text[:300])
            return None
        try:
            data = response.json()
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, ValueError) as exc:
            LOG.error("LLM 响应解析失败：%s", exc)
            return None
        calls = []
        for call in message.get("tool_calls") or []:
            calls.append(
                {
                    "id": call.get("id"),
                    "type": "function",
                    "function": {
                        "name": (call.get("function") or {}).get("name", ""),
                        "arguments": (call.get("function") or {}).get("arguments", "{}"),
                    },
                }
            )
        usage = data.get("usage") or {}
        return LLMResponse(
            content=message.get("content") or "",
            tool_calls=calls,
            usage={
                "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                "total_tokens": int(usage.get("total_tokens", 0) or 0),
                "prompt_cache_hit_tokens": int(
                    usage.get("prompt_cache_hit_tokens", 0) or 0
                ),
                "prompt_cache_miss_tokens": int(
                    usage.get("prompt_cache_miss_tokens", 0) or 0
                ),
            },
            model=data.get("model") or payload["model"],
            reasoning_content=message.get("reasoning_content") or "",
        )
