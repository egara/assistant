"""Model Context Protocol (MCP) client manager for Assistant."""

import logging
from typing import Any, Dict, List, Optional, Tuple

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
        # Stores categorized server metadata, e.g. type, priority, description
        self.servers_meta: Dict[str, Dict[str, Any]] = {}
        for s_name, s_cfg in self.config.items():
            self.servers_meta[s_name] = {
                "type": s_cfg.get("type", "").strip().lower(),
                "priority": int(s_cfg.get("priority", 0)),
                "description": s_cfg.get("description", "").strip(),
                "transport": s_cfg.get("transport", "sse").lower(),
                "url": s_cfg.get("url", ""),
            }

    def _probe_server(self, server_name: str, server_cfg: Dict[str, Any], timeout: float = 2.0) -> Optional[List[Dict[str, Any]]]:
        """Attempt to connect and retrieve tools from a single server with a short timeout."""
        url = server_cfg.get("url")
        transport = server_cfg.get("transport", "sse").lower()
        if not url:
            return None
        try:
            return run_sync(self._fetch_tools(url, transport), timeout=timeout)
        except Exception as e:
            logger.warning(f"[MCP] Probe failed for server '{server_name}' at {url}: {e}")
            return None

    def discover_tools(self) -> List[Dict[str, Any]]:
        """
        Probe configured MCP servers, resolve redundancies by server type/priority,
        record the audit status in PropertiesManager, and register tools from active servers.
        """
        if not self.config:
            return []

        if not MCP_AVAILABLE:
            # Record servers as offline / unavailable due to missing client dependency
            unavailable_status = {}
            for s_name, s_cfg in self.config.items():
                unavailable_status[s_name] = {
                    "status": "offline",
                    "selected": False,
                    "type": s_cfg.get("type", "").strip().lower(),
                    "priority": int(s_cfg.get("priority", 0)),
                    "url": s_cfg.get("url", ""),
                    "description": s_cfg.get("description", "").strip(),
                    "tools_count": 0,
                    "error": "mcp python package not installed",
                }
            try:
                from properties import PropertiesManager
                PropertiesManager.get_instance().set_mcp_status(unavailable_status)
            except Exception:
                pass
            return []

        # 1. Group servers by type (or treat empty type as isolated)
        # Groups: type -> list of (server_name, server_cfg)
        groups: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
        for server_name, server_cfg in self.config.items():
            stype = server_cfg.get("type", "").strip().lower()
            if not stype:
                # Standalone server
                group_key = f"__standalone_{server_name}__"
            else:
                group_key = stype
            groups.setdefault(group_key, []).append((server_name, server_cfg))

        # 2. Probe servers concurrently to avoid sequential timeout delays
        probe_results: Dict[str, Optional[List[Dict[str, Any]]]] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(self.config))) as executor:
            future_to_name = {
                executor.submit(self._probe_server, s_name, s_cfg, 2.5): s_name
                for s_name, s_cfg in self.config.items()
            }
            for future in concurrent.futures.as_completed(future_to_name):
                s_name = future_to_name[future]
                try:
                    probe_results[s_name] = future.result()
                except Exception as e:
                    logger.warning(f"[MCP] Future exception for server '{s_name}': {e}")
                    probe_results[s_name] = None

        # 3. For each group, rank responsive servers by priority and elect the winner
        mcp_audit: Dict[str, Dict[str, Any]] = {}
        elected_servers: Dict[str, List[Dict[str, Any]]] = {}

        for group_key, server_entries in groups.items():
            # Sort candidate servers in group by priority descending
            server_entries.sort(key=lambda item: int(item[1].get("priority", 0)), reverse=True)

            elected_for_group = False
            for s_name, s_cfg in server_entries:
                tools_list = probe_results.get(s_name)
                is_alive = tools_list is not None
                priority = int(s_cfg.get("priority", 0))
                stype = s_cfg.get("type", "").strip().lower()
                url = s_cfg.get("url", "")
                desc = s_cfg.get("description", "").strip()

                if is_alive:
                    if not elected_for_group:
                        # Winner for this group/type
                        elected_for_group = True
                        elected_servers[s_name] = tools_list
                        mcp_audit[s_name] = {
                            "status": "active",
                            "selected": True,
                            "type": stype,
                            "priority": priority,
                            "url": url,
                            "description": desc,
                            "tools_count": len(tools_list),
                        }
                    else:
                        # Responded, but superseded by a higher priority server in the same group
                        mcp_audit[s_name] = {
                            "status": "standby",
                            "selected": False,
                            "type": stype,
                            "priority": priority,
                            "url": url,
                            "description": desc,
                            "tools_count": len(tools_list),
                        }
                else:
                    mcp_audit[s_name] = {
                        "status": "offline",
                        "selected": False,
                        "type": stype,
                        "priority": priority,
                        "url": url,
                        "description": desc,
                        "tools_count": 0,
                    }

        # Save status into PropertiesManager singleton if available
        try:
            from properties import PropertiesManager
            PropertiesManager.get_instance().set_mcp_status(mcp_audit)
        except Exception as e:
            logger.warning(f"[MCP] Could not update PropertiesManager with MCP status: {e}")

        # 4. Register tools from elected servers
        discovered = []
        for server_name, tools_list in elected_servers.items():
            server_cfg = self.config[server_name]
            url = server_cfg.get("url", "")
            transport = server_cfg.get("transport", "sse").lower()
            server_type = server_cfg.get("type", "").strip().lower()
            priority = int(server_cfg.get("priority", 0))

            for t in tools_list:
                name = t["function"]["name"]
                self.tools[name] = {
                    "server": server_name,
                    "url": url,
                    "transport": transport,
                    "original_name": name,
                    "server_type": server_type,
                    "priority": priority,
                }
                discovered.append(t)
            logger.info(f"[MCP] Active server '{server_name}' ({server_type}) registered with {len(tools_list)} tools.")

        return discovered

    def get_web_search_tools(self) -> List[Dict[str, Any]]:
        """Return registered MCP tools configured as type 'web_search', ordered by priority (highest first)."""
        search_tools = []
        for name, meta in self.tools.items():
            if meta.get("server_type") == "web_search":
                search_tools.append({
                    "name": name,
                    "server": meta["server"],
                    "priority": meta["priority"],
                })
        search_tools.sort(key=lambda x: x["priority"], reverse=True)
        return search_tools

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
        try:
            return run_sync(
                self._call_tool_async(info["url"], info["transport"], info["original_name"], tool_args),
                timeout=30.0,
            )
        except Exception as e:
            server_name = info.get("server", "unknown")
            url = info.get("url", "")
            logger.error(f"[MCP] Call to tool '{tool_name}' failed on server '{server_name}' ({url}): {e}")
            return (
                f"Error: El servidor MCP '{server_name}' ({url}) no responde o ha dejado de estar disponible. "
                "Reinicia Assistant para re-evaluar los servidores MCP configurados."
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
