"""Web search tools for Assistant."""

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for live facts.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }
]


def deep_web_search(query):
    """Fallback search using DuckDuckGo and HTML scraping."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3, backend="lite"))
        out = ""
        for r in results:
            try:
                soup = BeautifulSoup(
                    requests.get(r["href"], timeout=4).text, "html.parser"
                )
                for s in soup(["script", "style", "nav", "footer"]):
                    s.extract()
                out += f"--- {r['title']} ---\n{soup.get_text(separator=' ', strip=True)[:1500]}\n"
            except (requests.RequestException, Exception):
                out += f"--- {r['title']} ---\n{r.get('body', '')}\n"
        return out[:5000]
    except Exception as e:
        return f"Error: {e}"


def perform_web_search(query, mcp_manager=None):
    """Execute web search prioritizing configured MCP web_search providers (by priority), falling back to DuckDuckGo."""
    # Priority 1: Check any MCP tools explicitly typed as 'web_search' or known search tools
    if mcp_manager is not None and hasattr(mcp_manager, "tools"):
        # 1.1 First check tools typed as 'web_search' in mcp_servers configuration
        configured_searchers = []
        if hasattr(mcp_manager, "get_web_search_tools"):
            configured_searchers = mcp_manager.get_web_search_tools()

        search_tool_names = [t["name"] for t in configured_searchers]

        # 1.2 Backwards compatibility / convention: if searxng_web_search exists and wasn't typed, include it
        if "searxng_web_search" in mcp_manager.tools and "searxng_web_search" not in search_tool_names:
            search_tool_names.append("searxng_web_search")

        for tool_name in search_tool_names:
            try:
                mcp_res = mcp_manager.call_tool(tool_name, {"query": query})
                if mcp_res and not mcp_res.startswith("Error:"):
                    return mcp_res
                print(f"[Search] MCP search tool '{tool_name}' failed/empty, trying next: {mcp_res}")
            except Exception as e:
                print(f"[Search] MCP search tool '{tool_name}' exception: {e}")

    # Priority 2: Fallback to DuckDuckGo
    return deep_web_search(query)
