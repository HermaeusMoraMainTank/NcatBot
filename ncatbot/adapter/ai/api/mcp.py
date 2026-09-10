"""MCP (Model Context Protocol) 客户端 — 管理 MCP 服务器会话

支持 stdio / http (Streamable HTTP) / sse 三种传输，配置格式与 LiteLLM 兼容::

    mcp_servers:
      server_name:
        transport: "http" | "sse" | "stdio"   # 缺省自动判断
        url: "https://mcp.example.com/mcp"    # http / sse
        headers: {Authorization: "Bearer ..."}  # http / sse
        command: "npx"                        # stdio
        args: ["-y", "@mcp/server"]           # stdio
        env: {TOKEN: "..."}                   # stdio

多个服务器加载的工具按 ``{server_name}_{tool_name}`` 命名空间区分，
避免同名工具冲突，与 LiteLLM MCP Gateway 的行为一致。
"""

from __future__ import annotations

import contextlib
import json
from typing import Any, AsyncContextManager, Dict, List, Optional, Tuple

from ncatbot.utils import get_log

LOG = get_log("AIMCP")


class MCPSessionManager:
    """管理一组 MCP 服务器会话

    负责连接的创建与关闭；跨服务器加载工具、处理工具调用。
    支持作为异步上下文管理器使用::

        async with MCPSessionManager(servers) as mcp:
            tools = await mcp.load_tools()
            text = await mcp.call_tool("server_get_weather", {"city": "北京"})
    """

    def __init__(self, servers: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        self._servers = servers or {}
        self._stack = contextlib.AsyncExitStack()
        # server_name -> 已初始化的 ClientSession
        self._sessions: Dict[str, Any] = {}
        # 命名空间化工具名 -> (server_name, 原始工具名)
        self._tool_map: Dict[str, Tuple[str, str]] = {}

    async def __aenter__(self) -> "MCPSessionManager":
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    @property
    def connected(self) -> bool:
        return bool(self._sessions)

    async def connect(self) -> None:
        """连接所有配置的 MCP 服务器并初始化会话。

        单个服务器失败不会阻断其他服务器，记录错误后继续。
        """
        for name, cfg in self._servers.items():
            try:
                session = await self._stack.enter_async_context(
                    self._build_session_cm(name, cfg)
                )
                await session.initialize()
                self._sessions[name] = session
                LOG.info("MCP 服务器 %s 已连接", name)
            except Exception as exc:  # noqa: BLE001
                LOG.error("MCP 服务器 %s 连接失败: %s", name, exc)

    async def close(self) -> None:
        """关闭所有 MCP 会话与底层传输。"""
        self._sessions.clear()
        self._tool_map.clear()
        await self._stack.aclose()
        LOG.info("MCP 会话已全部关闭")

    # ---- 工具加载与调用 ----

    async def load_tools(self, format: str = "openai") -> List[Dict[str, Any]]:
        """加载所有已连接服务器提供的工具。

        Parameters
        ----------
        format:
            返回格式，仅支持 ``"openai"``（OpenAI function calling 格式）。

        Returns
        -------
        工具列表，工具名为 ``{server_name}_{tool_name}``。
        """
        from litellm.experimental_mcp_client.tools import (
            load_mcp_tools,
            transform_mcp_tool_to_openai_tool,
        )

        tools: List[Dict[str, Any]] = []
        for server_name, session in self._sessions.items():
            try:
                mcp_tools = await load_mcp_tools(session, format="mcp")
            except Exception as exc:  # noqa: BLE001
                LOG.warning("MCP 服务器 %s 加载工具失败: %s", server_name, exc)
                continue
            for tool in mcp_tools:
                ns_name = f"{server_name}_{tool.name}"
                self._tool_map[ns_name] = (server_name, tool.name)
                openai_tool = transform_mcp_tool_to_openai_tool(tool)
                openai_tool["function"]["name"] = ns_name
                tools.append(openai_tool)
        return tools

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        """调用一个已加载的 MCP 工具，返回文本结果。

        Parameters
        ----------
        name:
            命名空间化工具名（``{server_name}_{tool_name}``）。
        arguments:
            工具参数。
        """
        server_name, tool_name = self._resolve_tool(name)
        session = self._sessions[server_name]
        result = await session.call_tool(tool_name, arguments=arguments)
        return self._result_to_text(result)

    async def call_openai_tool(self, openai_tool: Any) -> str:
        """执行 OpenAI 工具调用对象（``ChatCompletionMessageToolCall``）。"""
        function = self._get_function(openai_tool)
        name = function["name"]
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        return await self.call_tool(name, arguments)

    # ---- 内部辅助 ----

    def _build_session_cm(self, name: str, cfg: Dict[str, Any]) -> AsyncContextManager:
        """根据服务器配置构造一个连接 + 会话的异步上下文管理器。"""
        transport = self._resolve_transport(cfg)

        @contextlib.asynccontextmanager
        async def _stdio():
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(
                command=cfg["command"],
                args=list(cfg.get("args") or []),
                env=cfg.get("env"),
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    yield session

        @contextlib.asynccontextmanager
        async def _http():
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            # 部分版本返回三元组 (read, write, get_session_id)
            async with streamablehttp_client(
                cfg["url"], headers=cfg.get("headers")
            ) as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    yield session

        @contextlib.asynccontextmanager
        async def _sse():
            from mcp import ClientSession
            from mcp.client.sse import sse_client

            async with sse_client(cfg["url"], headers=cfg.get("headers")) as (
                read,
                write,
            ):
                async with ClientSession(read, write) as session:
                    yield session

        if transport == "stdio":
            if not cfg.get("command"):
                raise ValueError(f"MCP 服务器 {name} 的 stdio 传输缺少 command")
            return _stdio()
        if transport == "http":
            if not cfg.get("url"):
                raise ValueError(f"MCP 服务器 {name} 的 http 传输缺少 url")
            return _http()
        if transport == "sse":
            if not cfg.get("url"):
                raise ValueError(f"MCP 服务器 {name} 的 sse 传输缺少 url")
            return _sse()
        raise ValueError(f"MCP 服务器 {name} 不支持的传输类型: {transport}")

    @staticmethod
    def _resolve_transport(cfg: Dict[str, Any]) -> str:
        """解析传输类型，缺省时按配置内容自动判断。

        优先取 ``transport`` 字段；否则 ``command`` 存在走 stdio，
        否则有 ``url`` 走 http（Streamable HTTP，MCP 现行标准）。
        """
        transport = cfg.get("transport")
        if transport:
            return transport.lower()
        if cfg.get("command"):
            return "stdio"
        if cfg.get("url"):
            return "http"
        return "stdio"

    def _resolve_tool(self, name: str) -> Tuple[str, str]:
        """根据命名空间化工具名定位服务器与原始工具名。"""
        if name in self._tool_map:
            return self._tool_map[name]
        # 兜底：按第一个 "_" 拆分出服务器名
        server_name, _, tool_name = name.partition("_")
        if server_name not in self._sessions:
            raise KeyError(f"MCP 工具 {name} 未加载或服务器未连接")
        return server_name, tool_name or name

    @staticmethod
    def _get_function(openai_tool: Any) -> Dict[str, Any]:
        """兼容从对象或 dict 中取出 function 字段。"""
        if hasattr(openai_tool, "function"):
            function = openai_tool.function
        else:
            function = openai_tool["function"]
        if hasattr(function, "model_dump"):
            return function.model_dump()
        return dict(function)

    @staticmethod
    def _result_to_text(result: Any) -> str:
        """将 MCP ``CallToolResult`` 的 content 块转为纯文本。"""
        parts: List[str] = []
        for block in result.content:
            if getattr(block, "type", "") == "text":
                parts.append(block.text)
            else:
                # 图片等其他内容块：以 repr 兜底保留信息
                parts.append(repr(block))
        text = "\n".join(parts)
        if getattr(result, "isError", False):
            text = f"[MCP 工具调用错误]\n{text}"
        return text
