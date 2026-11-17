"""Model Context Protocol (MCP) client manager for Assistant."""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("assistant.mcp")

try:
    import asyncio
    import concurrent.futures
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    from mcp.client.streamable_http import streamable_http_client
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False


def run_sync(coro, timeout: float = 15.0) -> Any:
    """Run an async coroutine synchronously using a dedicated thread pool."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result(timeout=timeout)


class MCPManager:
    """Manages connections, tool discovery, and tool execution for configured MCP servers."""

    def __init__(self, servers_config: Optional[Dict[str, Any]] = None):
        self.config: Dict[str, Any] = servers_config or {}
        self.tools: Dict[str, Dict[str, Any]] = {}

    def discover_tools(self) -> List[Dict[str, Any]]:
        """Connect to configured MCP servers and discover available tools."""
        if not MCP_AVAILABLE or not self.config:
            return []
        discovered = []
        for server_name, server_cfg in self.config.items():
            url = server_cfg.get("url")
            transport = server_cfg.get("transport", "sse").lower()
            if not url:
                continue
            try:
                tools_list = run_sync(self._fetch_tools(url, transport), timeout=6.0)
                for t in tools_list:
                    name = t["function"]["name"]
                    self.tools[name] = {
                        "server": server_name,
                        "url": url,
                        "transport": transport,
                        "original_name": name,
                    }
                    discovered.append(t)
            except Exception as e:
                logger.warning(f"[MCP] Failed to connect to server '{server_name}' at {url}: {e}")
        return discovered

    async def _fetch_tools(self, url: str, transport: str) -> List[Dict[str, Any]]:
        client_cm = sse_client(url) if transport == "sse" else streamable_http_client(url)
        async with client_cm as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                tool_list = await session.list_tools()
                out = []
                for t in tool_list.tools:
                    schema = (
                        t.input_schema
                        if hasattr(t, "input_schema")
                        else {"type": "object", "properties": {}}
                    )
                    out.append(
                        {
                            "type": "function",
                            "function": {
                                "name": t.name,
                                "description": t.description or "",
                                "parameters": schema,
                            },
                        }
                    )
                return out

    def call_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """Call an MCP tool by registered name."""
        if tool_name not in self.tools:
            return f"Error: Tool '{tool_name}' not found in MCP registry."
        info = self.tools[tool_name]
        return run_sync(
            self._call_tool_async(info["url"], info["transport"], info["original_name"], tool_args),
            timeout=30.0,
        )

    async def _call_tool_async(self, url: str, transport: str, tool_name: str, tool_args: Dict[str, Any]) -> str:
        client_cm = sse_client(url) if transport == "sse" else streamable_http_client(url)
        async with client_cm as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool(tool_name, tool_args)
                texts = []
                for c in res.content:
                    if hasattr(c, "text"):
                        texts.append(c.text)
                    else:
                        texts.append(str(c))
                return "\n".join(texts)
