"""SkillHub：把框架与插件的可用能力整理成可被模型调用的 skill。

它本身不抢消息：日常聊天仍由 FakeAi 处理。
FakeAi 的 agent 会额外拿到 skill 目录；管理后台也可以直接手动跑任务。
"""

from __future__ import annotations

from typing import Any, Optional

from ncatbot.agent import LLMResponse
from ncatbot.plugin import NcatBotPlugin
from ncatbot.utils import get_log

from .skills.agents import AgentSkills
from .skills.files_web import FileWebSkills
from .skills.media import MediaSkills
from .skills.messages import MessageSkills
from .skills.plugin_catalog import PluginCatalogSkills
from .skills.sandbox import SandboxSkills
from .skills.tarot import TarotSkills

_log = get_log("SkillHub")


class AiUtilLLM:
    """把 plugins/common 的 AiUtil 适配成框架 Agent 的模型客户端。

    这样密钥仍只存在于 plugins/common/secrets.json，框架本身不需要知道。
    """

    model = "deepseek-flash"

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
        from common.utils.AiUtil import AiUtil

        result = await AiUtil.chat_completion(
            messages,
            tools=tools,
            capability=capability,
            model=model,
            temperature=0.3 if temperature is None else temperature,
            max_tokens=max_tokens or 1200,
            request_timeout=timeout,
        )
        if not result:
            return None
        return LLMResponse(
            content=result.get("content") or "",
            tool_calls=result.get("tool_calls") or [],
            usage=result.get("usage") or {},
            model=result.get("model") or (model or self.model),
            reasoning_content=result.get("reasoning_content") or "",
        )


class SkillHub(
    MessageSkills,
    MediaSkills,
    FileWebSkills,
    AgentSkills,
    SandboxSkills,
    PluginCatalogSkills,
    TarotSkills,
    NcatBotPlugin,
):
    name = "SkillHub"
    version = "1.0.0"
    author = "NcatBot"
    description = "插件能力 → skill 目录：渐进展示、受控调用、子智能体"

    async def on_load(self) -> None:
        agents = getattr(self, "agents", None)
        if agents is not None:
            agents.set_llm(AiUtilLLM())
        _log.info("SkillHub 已加载：skill 由插件方法自动注册")

    async def on_close(self) -> None:
        _log.info("SkillHub 已卸载")
