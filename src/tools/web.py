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
            except:
                out += f"--- {r['title']} ---\n{r['body']}\n"
        return out[:5000]
    except Exception as e:
        return f"Error: {e}"


def perform_web_search(query, mcp_manager=None):
    """Execute web search prioritizing MCP SearXNG when available, falling back to DuckDuckGo."""
    # Priority 1: MCP SearXNG tool if registered
    if mcp_manager is not None and hasattr(mcp_manager, "tools") and "searxng_web_search" in mcp_manager.tools:
        try:
            mcp_res = mcp_manager.call_tool("searxng_web_search", {"query": query})
            if mcp_res and not mcp_res.startswith("Error:"):
                return mcp_res
            print(f"[Search] SearXNG MCP error, falling back to DuckDuckGo: {mcp_res}")
        except Exception as e:
            print(f"[Search] SearXNG MCP exception, falling back to DuckDuckGo: {e}")

    # Priority 2: Fallback to DuckDuckGo
    return deep_web_search(query)
