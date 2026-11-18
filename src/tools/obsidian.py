"""Obsidian integration tools for Assistant."""

import urllib.parse
import requests

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "obsidian_search",
            "description": "Search Obsidian notes.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "obsidian_read",
            "description": "Read an Obsidian note.",
            "parameters": {
                "type": "object",
                "properties": {"filename": {"type": "string"}},
                "required": ["filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "obsidian_write",
            "description": "Write/Overwrite an Obsidian note.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["filename", "content"],
            },
        },
    },
]

# Obsidian connection configuration
_obsidian_url = "https://127.0.0.1:27124"
_obsidian_headers = {"Authorization": "Bearer "}


def configure_obsidian(api_key: str = "", url: str = "https://127.0.0.1:27124"):
    """Configure credentials and base URL for Obsidian REST API."""
    global _obsidian_url, _obsidian_headers
    _obsidian_url = url.rstrip('/') if url else "https://127.0.0.1:27124"
    _obsidian_headers = {
        "Authorization": f"Bearer {api_key}",
    }


def search_obsidian(query):
    try:
        url = f"{_obsidian_url}/search/"
        headers = _obsidian_headers.copy()
        headers["Content-Type"] = "application/json"

        # Attempt 1: Standard JSON body
        res = requests.post(
            url, headers=headers, json={"query": query}, verify=False, timeout=5
        )

        # Attempt 2: If 400/404, fallback to the plugin's custom Content-Type and raw text body
        if res.status_code >= 400:
            alt_headers = _obsidian_headers.copy()
            alt_headers["Content-Type"] = "application/vnd.olrapi.search+json"
            res = requests.post(
                url,
                headers=alt_headers,
                data=query.encode("utf-8"),
                verify=False,
                timeout=5,
            )

        if res.status_code == 200:
            data = res.json()
            if not data:
                return "No results found for that query."
            if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
                return "\n\n".join(
                    [
                        f"File: {i.get('filename')}\nSnippets: {[m.get('context', '').strip() for m in i.get('matches', [])]}"
                        for i in data[:3]
                    ]
                )
            elif isinstance(data, list):
                return "Files found:\n" + "\n".join([str(i) for i in data[:10]])
            return f"Raw results: {str(data)[:2000]}"
        else:
            return f"Error: {res.status_code} - {res.text}"
    except Exception as e:
        return f"Error: {e}"


def read_obsidian(filename):
    try:
        return requests.get(
            f"{_obsidian_url}/vault/{urllib.parse.quote(filename)}",
            headers=_obsidian_headers,
            verify=False,
            timeout=3,
        ).text[:3000]
    except Exception as e:
        return f"Error: {e}"


def write_obsidian(filename, content):
    try:
        headers = _obsidian_headers.copy()
        headers["Content-Type"] = "text/markdown"
        res = requests.put(
            f"{_obsidian_url}/vault/{urllib.parse.quote(filename)}",
            headers=headers,
            data=content.encode("utf-8"),
            verify=False,
            timeout=3,
        )
        return (
            f"Success. Note saved. Exact content written:\n{content}"
            if res.status_code in [200, 201, 204]
            else f"Error: {res.status_code}"
        )
    except Exception as e:
        return f"Error: {e}"
