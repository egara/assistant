#!/usr/bin/env python3
import sys
import os
import re
import json
import base64
import urllib.parse
import subprocess
import threading
import time

from PyQt6.QtWidgets import QApplication
from PyQt6.QtQml import QQmlApplicationEngine
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot, QThread, QObject, QUrl

import openai
from openai import OpenAI
from ddgs import DDGS
import requests
import urllib3
from bs4 import BeautifulSoup
from langdetect import detect

try:
    from mcp.client.sse import sse_client
    from mcp.client.streamable_http import streamable_http_client
    from mcp import ClientSession
    import asyncio
    import concurrent.futures
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# ⚙️ CONFIGURATION & PATH RESOLUTION
# ==========================================
CONFIG_DIR = os.path.expanduser("~/.config/assistant")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
TEMP_DIR = "/tmp/assistant"
DATA_DIR = os.path.expanduser("~/.local/share/assistant")
SOUND_DIR = os.path.join(DATA_DIR, "sounds")
HISTORY_DIR = os.path.expanduser("~/.local/state/assistant")
HISTORY_FILE = os.path.join(HISTORY_DIR, "chat_history.json")
USER_KNOWLEDGE_FILE = os.path.join(DATA_DIR, "hyprland_knowledge.json")

os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(SOUND_DIR, exist_ok=True)
os.makedirs(HISTORY_DIR, exist_ok=True)

# Smart UI & Knowledge Path Resolver
_base_dir = os.path.dirname(os.path.abspath(__file__))
qml_paths_to_check = [
    os.path.join(CONFIG_DIR, "ui.qml"),
    os.path.join(_base_dir, "ui.qml"),
    os.path.abspath(os.path.join(_base_dir, "..", "share", "assistant", "ui.qml")),
    os.path.expanduser("~/.local/share/assistant/ui.qml"),
]

# Factory (system/immutable) knowledge paths
system_knowledge_paths = [
    os.path.join(_base_dir, "hyprland_knowledge.json"),
    os.path.abspath(os.path.join(_base_dir, "..", "share", "assistant", "hyprland_knowledge.json")),
]

SYSTEM_KNOWLEDGE_FILE = None
for path in system_knowledge_paths:
    if os.path.exists(path):
        SYSTEM_KNOWLEDGE_FILE = path
        break

QML_FILE = None
for path in qml_paths_to_check:
    if os.path.exists(path):
        QML_FILE = path
        break

if not QML_FILE:
    print(
        "\033[1;31m[!] Error: ui.qml not found. Please ensure the frontend file is downloaded.\033[0m"
    )
    sys.exit(1)

if not os.path.exists(CONFIG_FILE):
    print(f"\033[1;31m[!] Configuration file not found at {CONFIG_FILE}.\033[0m")
    sys.exit(1)

with open(CONFIG_FILE, "r") as f:
    config = json.load(f)

LLM_HISTORY_DURATION_MINS = config.get("llm", {}).get("history_duration_minutes", 15)
OBSIDIAN_API_KEY = config.get("obsidian", {}).get("api_key", "")
OBSIDIAN_URL = config.get("obsidian", {}).get("url", "https://127.0.0.1:27124")
OBSIDIAN_HEADERS = {
    "Authorization": f"Bearer {OBSIDIAN_API_KEY}",
    "Accept": "application/json",
}

LLM_API_KEY = config.get("llm", {}).get("api_key", "sk-none")
LLM_BASE_URL = config.get("llm", {}).get("base_url", "http://127.0.0.1:8080/v1")
LLM_MODEL = config.get("llm", {}).get("model", "Hermes-3-8B")
VISION_ENABLED = config.get("llm", {}).get("vision_enabled", True)

# Fallback LLM configuration
FALLBACK_CONF = config.get("llm", {}).get("fallback")
if FALLBACK_CONF:
    FALLBACK_CLIENT = OpenAI(
        base_url=FALLBACK_CONF.get("base_url", "http://127.0.0.1:8010/v1"),
        api_key=FALLBACK_CONF.get("api_key", "sk-none"),
        timeout=FALLBACK_CONF.get("timeout", 15.0),
    )
    FALLBACK_MODEL = FALLBACK_CONF.get("model", "GLM-5.3-Flash")
    FALLBACK_VISION_ENABLED = FALLBACK_CONF.get("vision_enabled", True)
else:
    FALLBACK_CLIENT = None
    FALLBACK_MODEL = None
    FALLBACK_VISION_ENABLED = True

VOICE_CONF = config.get("voice", {})
MCP_SERVERS_CONF = config.get("mcp_servers", {})
client = OpenAI(
    base_url=LLM_BASE_URL,
    api_key=LLM_API_KEY,
    timeout=config.get("llm", {}).get("timeout", 15.0),
)

CONTEXT_PATH = os.path.expanduser(
    config.get("llm", {}).get("system_context", "~/.config/assistant/context.md")
)
USER_CONTEXT = ""
if os.path.exists(CONTEXT_PATH):
    with open(CONTEXT_PATH, "r", encoding="utf-8") as f:
        USER_CONTEXT = f.read().strip()
else:
    try:
        os.makedirs(os.path.dirname(CONTEXT_PATH), exist_ok=True)
        with open(CONTEXT_PATH, "w", encoding="utf-8") as f:
            f.write(
                "<!-- Write your custom context here. Assistant will read this to personalize its answers. -->\n"
            )
    except:
        pass

# ==========================================
# 🔌 MCP (MODEL CONTEXT PROTOCOL) MANAGER
# ==========================================
def _run_sync(coro, timeout=15):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result(timeout=timeout)


class MCPManager:
    def __init__(self, servers_config):
        self.config = servers_config or {}
        self.tools = {}

    def discover_tools(self):
        if not MCP_AVAILABLE or not self.config:
            return []
        discovered = []
        for server_name, server_cfg in self.config.items():
            url = server_cfg.get("url")
            transport = server_cfg.get("transport", "sse").lower()
            if not url:
                continue
            try:
                tools_list = _run_sync(self._fetch_tools(url, transport), timeout=6)
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
                print(f"[MCP] Warning: Failed to connect to server '{server_name}' at {url}: {e}")
        return discovered

    async def _fetch_tools(self, url, transport):
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

    def call_tool(self, tool_name, tool_args):
        if tool_name not in self.tools:
            return f"Error: Tool '{tool_name}' not found in MCP registry."
        info = self.tools[tool_name]
        return _run_sync(
            self._call_tool_async(info["url"], info["transport"], info["original_name"], tool_args),
            timeout=30,
        )

    async def _call_tool_async(self, url, transport, tool_name, tool_args):
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


mcp_manager = MCPManager(MCP_SERVERS_CONF)
mcp_tools = mcp_manager.discover_tools()

## TOOLS & LOGIC
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather conditions and forecast for a specific city or region.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "City or location name (e.g. Madrid, Barcelona, Paris, Tokyo).",
                    }
                },
                "required": ["location"],
            },
        },
    },
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
    },
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
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the text contents of a file at the given absolute or relative path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to read (e.g. '~/prueba/ana.txt' or '/home/user/document.txt').",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file at the given path with new text content. Creates parent directories if needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Target file path (e.g. '~/prueba/resumen.txt').",
                    },
                    "content": {
                        "type": "string",
                        "description": "The exact full text content to write into the file.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_in_file",
            "description": "Replace occurrences of a target string or pattern in a file with replacement text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Target file path to modify (e.g. '~/prueba/ana.txt').",
                    },
                    "target": {
                        "type": "string",
                        "description": "The exact string or word to be replaced.",
                    },
                    "replacement": {
                        "type": "string",
                        "description": "The new replacement string.",
                    },
                },
                "required": ["path", "target", "replacement"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": "Execute a bash shell command. For GUI applications, open commands, or long-running processes, append '&' or invoke the command normally (they will run detached in background).",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_desktop",
            "description": "Manage workspaces and windows on Hyprland using version-aware recipes (switch workspaces, move windows, toggle floating, fullscreen, or close a window). IMPORTANT: If the user mentions a specific application or window (e.g. 'cierra chrome', 'minimiza firefox'), you MUST provide 'window_address' with the application name/class (e.g. 'chrome', 'firefox') so the action is applied to that specific window instead of the currently active/focused window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "focus_workspace",
                            "move_to_workspace",
                            "toggle_floating",
                            "toggle_fullscreen",
                            "close_window",
                            "pin_window",
                            "center_window",
                        ],
                        "description": "Desktop action to perform.",
                    },
                    "target": {
                        "type": "string",
                        "description": "Target workspace number (e.g. '1', '2') or name. Required for workspace actions.",
                    },
                    "window_address": {
                        "type": "string",
                        "description": "Target application name, class, title, or address (e.g. 'chrome', 'google-chrome', 'firefox', 'kitty', '0x...'). When specified, targets and focuses this window before performing the action. If omitted, applies to the currently active window.",
                    },
                },
                "required": ["action"],
            },
        },
    },
] + mcp_tools


WMO_WEATHER_CODES = {
    0: "Despejado",
    1: "Mayormente despejado",
    2: "Parcialmente nublado",
    3: "Nublado",
    45: "Niebla",
    48: "Niebla con escarcha",
    51: "Llovizna ligera",
    53: "Llovizna moderada",
    55: "Llovizna densa",
    56: "Llovizna helada ligera",
    57: "Llovizna helada densa",
    61: "Lluvia débil",
    63: "Lluvia moderada",
    65: "Lluvia fuerte",
    66: "Lluvia helada débil",
    67: "Lluvia helada fuerte",
    71: "Nevada débil",
    73: "Nevada moderada",
    75: "Nevada fuerte",
    77: "Granizo fino",
    80: "Chubascos débiles",
    81: "Chubascos moderados",
    82: "Chubascos violentos",
    85: "Chubascos de nieve débiles",
    86: "Chubascos de nieve fuertes",
    95: "Tormenta",
    96: "Tormenta con granizo leve",
    99: "Tormenta con granizo fuerte",
}


def get_weather(location):
    location = (location or "").strip()
    # 1. Primary engine: Open-Meteo (high-accuracy global weather models)
    try:
        lat, lon, full_name = None, None, location

        if location:
            geo_url = (
                f"https://geocoding-api.open-meteo.com/v1/search?"
                f"name={urllib.parse.quote(location)}&count=5&language=es&format=json"
            )
            geo_res = requests.get(geo_url, timeout=5).json()
            results = geo_res.get("results")
            if results:
                # Prioritize Spain (ES) or first result
                place = next((r for r in results if r.get("country_code") == "ES"), results[0])
                name = place.get("name", location)
                admin = place.get("admin1") or place.get("country") or ""
                country = place.get("country", "")
                full_name = f"{name}, {admin} ({country})" if admin and country != admin else (f"{name} ({country})" if country else name)
                lat, lon = place["latitude"], place["longitude"]
        else:
            # IP geolocation fallback to detect local city
            try:
                ip_data = requests.get("http://ip-api.com/json/", timeout=3).json()
                if ip_data.get("status") == "success":
                    lat, lon = ip_data["lat"], ip_data["lon"]
                    city = ip_data.get("city", "Tu ubicación")
                    region = ip_data.get("regionName", "")
                    full_name = f"{city} ({region})" if region else city
            except:
                pass

        if lat is not None and lon is not None:
            forecast_url = (
                f"https://api.open-meteo.com/v1/forecast?"
                f"latitude={lat}&longitude={lon}&"
                f"current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m&"
                f"daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max&"
                f"timezone=auto&forecast_days=3"
            )
            data = requests.get(forecast_url, timeout=5).json()
            curr = data.get("current", {})
            daily = data.get("daily", {})

            condition = WMO_WEATHER_CODES.get(curr.get("weather_code"), "Variable")
            temp = curr.get("temperature_2m")
            feels = curr.get("apparent_temperature")
            humidity = curr.get("relative_humidity_2m")
            wind = curr.get("wind_speed_10m")

            report = [
                f"Tiempo en {full_name}:",
                f"- Estado: {condition}",
                f"- Temperatura: {temp}°C (sensación térmica {feels}°C)",
                f"- Humedad: {humidity}%",
                f"- Viento: {wind} km/h",
            ]

            if daily and daily.get("time"):
                report.append("\nPrevisión próximos días:")
                for i, date in enumerate(daily["time"]):
                    d_cond = WMO_WEATHER_CODES.get(daily["weather_code"][i], "Variable")
                    t_max = daily["temperature_2m_max"][i]
                    t_min = daily["temperature_2m_min"][i]
                    pop = daily["precipitation_probability_max"][i]
                    report.append(
                        f"- {date}: {d_cond}, mín {t_min}°C / máx {t_max}°C, prob. precipitación: {pop}%"
                    )

            return "\n".join(report)

    except Exception:
        pass

    # 2. Fallback engine: wttr.in
    try:
        query_loc = urllib.parse.quote(location) if location else ""
        url = f"https://wttr.in/{query_loc}?format=%C+%t,+Feels+like+%f,+Wind:+%w,+Humidity:+%h"
        res = requests.get(url, timeout=5)
        if res.status_code == 200 and res.text.strip():
            loc_label = location if location else "tu ubicación"
            return f"Tiempo en {loc_label}: {res.text.strip()}"
    except Exception:
        pass

    return f"No se pudo obtener la información meteorológica para {location or 'tu ubicación'}."


def search_obsidian(query):
    try:
        url = f"{OBSIDIAN_URL.rstrip('/')}/search/"
        headers = OBSIDIAN_HEADERS.copy()
        headers["Content-Type"] = "application/json"

        # Attempt 1: Standard JSON body
        res = requests.post(
            url, headers=headers, json={"query": query}, verify=False, timeout=5
        )

        # Attempt 2: If 400/404, fallback to the plugin's custom Content-Type and raw text body
        if res.status_code >= 400:
            alt_headers = OBSIDIAN_HEADERS.copy()
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
            f"{OBSIDIAN_URL.rstrip('/')}/vault/{urllib.parse.quote(filename)}",
            headers=OBSIDIAN_HEADERS,
            verify=False,
            timeout=3,
        ).text[:3000]
    except Exception as e:
        return f"Error: {e}"


def write_obsidian(filename, content):
    try:
        headers = OBSIDIAN_HEADERS.copy()
        headers["Content-Type"] = "text/markdown"
        res = requests.put(
            f"{OBSIDIAN_URL.rstrip('/')}/vault/{urllib.parse.quote(filename)}",
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


def read_file(path):
    try:
        resolved = os.path.expanduser(path.strip())
        if not os.path.exists(resolved):
            return f"Error: File '{resolved}' does not exist."
        if not os.path.isfile(resolved):
            return f"Error: '{resolved}' is not a regular file."
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(50000)
        return f"--- File: {resolved} ---\n{content}"
    except Exception as e:
        return f"Error reading file '{path}': {e}"


def write_file(path, content):
    try:
        resolved = os.path.expanduser(path.strip())
        parent_dir = os.path.dirname(resolved)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Success: File '{resolved}' written successfully ({len(content)} characters)."
    except Exception as e:
        return f"Error writing file '{path}': {e}"


def replace_in_file(path, target, replacement):
    try:
        resolved = os.path.expanduser(path.strip())
        if not os.path.exists(resolved):
            return f"Error: File '{resolved}' does not exist."
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        if target not in text:
            return f"Warning: Target text '{target}' was not found in '{resolved}'."
        count = text.count(target)
        new_text = text.replace(target, replacement)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(new_text)
        return f"Success: Replaced {count} occurrence(s) of '{target}' in '{resolved}'."
    except Exception as e:
        return f"Error replacing text in '{path}': {e}"


def execute_command(command):
    try:
        cmd_str = command.strip()
        # Common GUI applications and commands intended to run as detached background processes
        gui_launchers = [
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "firefox",
            "nautilus",
            "kitty",
            "vlc",
            "mpv",
            "xdg-open",
            "gtk-launch",
            "code",
            "zed",
            "spotify",
            "steam",
            "discord",
            "telegram-desktop",
        ]

        is_background = (
            cmd_str.endswith("&")
            or cmd_str.startswith("nohup ")
            or cmd_str.startswith("uwsm app --")
            or any(
                cmd_str == app
                or cmd_str.startswith(f"{app} ")
                or f" {app} " in cmd_str
                for app in gui_launchers
            )
        )

        if is_background:
            # Clean trailing '&' if present to avoid syntax issues with shell launch
            exec_target = cmd_str.rstrip("&").strip()
            # Launch detached in a new session and redirect std streams so Python does not wait
            subprocess.Popen(
                exec_target,
                shell=True,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            return f"Application or command launched in background: {exec_target}"

        res = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=10
        )
        return (res.stdout + res.stderr)[:3000] or "Success with no output."
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after 10 seconds. If launching a GUI app, please specify running it in the background."
    except Exception as e:
        return f"Error: {e}"


def deep_web_search(query):
    # Fallback to DuckDuckGo search if MCP SearXNG is unavailable or failed
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


def perform_web_search(query):
    # Priority 1: MCP SearXNG tool if registered
    if "searxng_web_search" in mcp_manager.tools:
        try:
            mcp_res = mcp_manager.call_tool("searxng_web_search", {"query": query})
            if mcp_res and not mcp_res.startswith("Error:"):
                return mcp_res
            print(f"[Search] SearXNG MCP error, falling back to DuckDuckGo: {mcp_res}")
        except Exception as e:
            print(f"[Search] SearXNG MCP exception, falling back to DuckDuckGo: {e}")

    # Priority 2: Fallback to DuckDuckGo
    return deep_web_search(query)


def get_hyprland_version():
    """Detect current Hyprland version (e.g. '0.57.0' -> '0.57')."""
    try:
        res = subprocess.run(["hyprctl", "version"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0:
            m = re.search(r"Hyprland\s+([0-9]+\.[0-9]+)", res.stdout)
            if m:
                return m.group(1)
    except:
        pass
    return "default"


def load_hyprland_recipes():
    """Load and merge Hyprland recipes (Factory system recipes + User mutable recipes)."""
    merged = {}
    # 1. System/factory recipes (base)
    if SYSTEM_KNOWLEDGE_FILE and os.path.exists(SYSTEM_KNOWLEDGE_FILE):
        try:
            with open(SYSTEM_KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
                merged = json.load(f)
        except Exception as e:
            print(f"[Hyprland Knowledge] Error loading system recipes: {e}")

    # 2. User mutable recipes in ~/.local/share/assistant (highest priority)
    if os.path.exists(USER_KNOWLEDGE_FILE):
        try:
            with open(USER_KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
                user_recipes = json.load(f)
                for ver, acts in user_recipes.items():
                    if ver.startswith("_"):
                        continue
                    if ver not in merged:
                        merged[ver] = {}
                    merged[ver].update(acts)
        except Exception as e:
            print(f"[Hyprland Knowledge] Error loading user recipes: {e}")

    return merged


def save_user_hyprland_recipe(version, action, recipe_dict):
    """Persist newly learned recipe in mutable ~/.local/share/assistant/hyprland_knowledge.json."""
    try:
        user_data = {}
        if os.path.exists(USER_KNOWLEDGE_FILE):
            try:
                with open(USER_KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
                    user_data = json.load(f)
            except:
                user_data = {}

        if version not in user_data:
            user_data[version] = {}
        user_data[version][action] = recipe_dict

        with open(USER_KNOWLEDGE_FILE, "w", encoding="utf-8") as f:
            json.dump(user_data, f, indent=2)
        return True
    except Exception as e:
        print(f"[Hyprland Knowledge] Failed to save recipe: {e}")
        return False


def discover_and_learn_action(action, version):
    """Introspect Hyprland hl.dsp dispatchers in real time and learn unknown actions."""
    try:
        norm = action.lower().replace("-", "_").replace(" ", "_")
        script = 'local t = {}; for k, v in pairs(hl.dsp) do if type(v) == "function" then table.insert(t, k) elseif type(v) == "table" then for sk, sv in pairs(v) do if type(sv) == "function" then table.insert(t, k .. "." .. sk) end end end end; return table.concat(t, ",")'
        res = subprocess.run(["hyprctl", "repl", script], capture_output=True, text=True, timeout=2)
        if res.returncode != 0:
            return None

        candidates = [c.strip() for c in res.stdout.split(",") if c.strip()]
        matched_call = None
        for cand in candidates:
            cand_simple = cand.replace(".", "_")
            if norm == cand_simple or norm in cand_simple:
                matched_call = cand
                break

        if not matched_call:
            # Common synonyms mapping
            synonyms = {
                "center": "window.center",
                "center_window": "window.center",
                "pin": "window.pin",
                "float": "window.float",
                "floating": "window.float",
                "tile": "window.float",
                "fullscreen": "window.fullscreen",
                "kill": "window.close",
                "kill_window": "window.close",
                "close": "window.close",
                "bring_to_top": "window.bring_to_top",
                "toggle_special": "workspace.toggle_special",
                "special_workspace": "workspace.toggle_special",
                "group_toggle": "group.toggle",
                "toggle_group": "group.toggle",
            }
            if norm in synonyms and synonyms[norm] in candidates:
                matched_call = synonyms[norm]

        if matched_call:
            if matched_call.startswith("window."):
                fn_name = matched_call.split("window.", 1)[1]
                recipe = {
                    "type": "lua_repl",
                    "script": (
                        "local target_win = nil\n"
                        "local id = [[{window}]]\n"
                        "if id ~= '' then\n"
                        "  for _, w in ipairs(hl.get_windows()) do\n"
                        "    if w.address == id or w.class:lower():find(id:lower(), 1, true) or w.title:lower():find(id:lower(), 1, true) then\n"
                        "      target_win = w\n"
                        "      break\n"
                        "    end\n"
                        "  end\n"
                        "end\n"
                        "if target_win then\n"
                        "  hl.dispatch(hl.dsp.focus({ window = target_win }))\n"
                        f"  local res = hl.dispatch(hl.dsp.window.{fn_name}())\n"
                        "  return res and res.ok or false\n"
                        "else\n"
                        f"  local res = hl.dispatch(hl.dsp.window.{fn_name}())\n"
                        "  return res and res.ok or false\n"
                        "end"
                    ),
                    "description": f"Auto-discovered window dispatcher {matched_call}",
                }
            else:
                recipe = {
                    "type": "lua_eval",
                    "command": f"return hl.dispatch(hl.dsp.{matched_call}()).ok",
                    "description": f"Auto-discovered dispatcher {matched_call}",
                }
            save_user_hyprland_recipe(version, action, recipe)
            return recipe

    except Exception as e:
        print(f"[Hyprland Auto-Discovery] Error: {e}")

    return None


def manage_desktop(action, target="", window_address=""):
    try:
        ver = get_hyprland_version()
        if ver == "default":
            # Check basic hyprctl presence
            check = subprocess.run(["hyprctl", "version"], capture_output=True, text=True, timeout=2)
            if check.returncode != 0:
                return "Blocked: Only Hyprland supported."

        recipes = load_hyprland_recipes()
        ver_recipes = recipes.get(ver) or recipes.get("default", {})
        recipe = ver_recipes.get(action)

        # Auto-discover if not currently in knowledge base
        learned_notice = ""
        if not recipe:
            discovered = discover_and_learn_action(action, ver)
            if discovered:
                recipe = discovered
                learned_notice = f" [Learned & saved to ~/.local/share/assistant/hyprland_knowledge.json]"
            else:
                return f"Action '{action}' could not be executed or auto-discovered on Hyprland (v{ver})."

        target_str = str(target or "").strip()
        ws_param = target_str if target_str.isdigit() else f"[[{target_str}]]"
        win_param = str(window_address or "").strip()

        recipe_type = recipe.get("type")
        if recipe_type == "lua_eval":
            code = recipe.get("command", "").replace("{target}", ws_param).replace("{window}", win_param)
            res = subprocess.run(["hyprctl", "eval", code], capture_output=True, text=True, timeout=3)
            return f"Success on Hyprland (v{ver}).{learned_notice}" if res.returncode == 0 else f"Failed: {res.stderr or res.stdout}"

        elif recipe_type == "lua_repl":
            code = recipe.get("script", "").replace("{target}", ws_param).replace("{window}", win_param)
            res = subprocess.run(["hyprctl", "repl", code], capture_output=True, text=True, timeout=3)
            return f"Success on Hyprland (v{ver}).{learned_notice}" if "true" in res.stdout else f"Failed: {res.stdout.strip()}"

        elif recipe_type == "cli":
            cmd = recipe.get("command", "").replace("{target}", target_str).replace("{window}", win_param)
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=3)
            return f"Success on Hyprland (v{ver}).{learned_notice}" if res.returncode == 0 else f"Failed: {res.stderr or res.stdout}"

        return f"Unknown recipe type for action {action}."
    except Exception as e:
        return f"Error: {e}"


def process_prompt(user_query):
    image_base64, mime_type = None, "image/png"
    match = re.search(r"\[IMAGE PATH:\s*(.*?)\]", user_query)
    if match:
        path = match.group(1).strip()
        user_query = user_query.replace(match.group(0), "").strip()
        if os.path.exists(path) and VISION_ENABLED:
            with open(path, "rb") as f:
                image_base64 = base64.b64encode(f.read()).decode("utf-8")
        elif not VISION_ENABLED:
            user_query += "\n[SYSTEM: Image shared but Vision disabled.]"

    if image_base64:
        clean_query = user_query.replace("Regarding this screenshot:", "").strip()
        if not clean_query:
            final_text = "Please describe the attached image in detail."
        else:
            final_text = f"Regarding this attached image: {clean_query}"

        return [
            {"type": "text", "text": final_text},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{image_base64}"},
            },
        ]
    return user_query


## QTHREADS
class TTSWorkerThread(QThread):
    status_signal = pyqtSignal(str, str)
    finished_signal = pyqtSignal()

    def __init__(self, text):
        super().__init__()
        self.text = text
        self._is_stopped = False
        self.current_process = None

    def run(self):
        engine_type = VOICE_CONF.get("tts_engine", "online")
        if engine_type in ("none", "disabled", "off", None):
            self.finished_signal.emit()
            return

        audio_path = os.path.join(TEMP_DIR, "tts.wav")
        try:
            detected_lang = detect(self.text)
        except:
            detected_lang = "en"

        if self._is_stopped:
            return

        if engine_type == "online":
            self.status_signal.emit("🔊 Speaking...", "#89b4fa")
            voice = VOICE_CONF.get("tts_online_voices", {}).get(
                detected_lang, "en-US-GuyNeural"
            )
            self.current_process = subprocess.Popen(
                [
                    "edge-tts",
                    "--voice",
                    voice,
                    "--rate",
                    "+5%",
                    "--text",
                    self.text,
                    "--write-media",
                    audio_path,
                ],
                stderr=subprocess.DEVNULL,
            )
            if self.current_process:
                self.current_process.wait()
        else:
            local_models = VOICE_CONF.get("tts_local_models", {})
            model = os.path.expanduser(
                local_models.get(
                    detected_lang,
                    list(local_models.values())[0] if local_models else "",
                )
            )

            if os.path.exists(model):
                self.status_signal.emit("🔊 Speaking...", "#89b4fa")
                self.current_process = subprocess.Popen(
                    ["piper", "--model", model, "--output_file", audio_path],
                    stdin=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                self.current_process.communicate(input=self.text.encode("utf-8"))
            else:
                self.status_signal.emit("⚠️ Missing Voice Model", "#f38ba8")
                print(
                    f"[!] Error: Model {model} not found. Please verify your Nix Flake or install.sh downloaded it."
                )

        if self._is_stopped:
            return

        if os.path.exists(audio_path):
            self.current_process = subprocess.Popen(
                ["pw-play", audio_path], stderr=subprocess.DEVNULL
            )
            self.current_process.wait()

        if not self._is_stopped:
            self.finished_signal.emit()

    def stop(self):
        self._is_stopped = True
        if self.current_process:
            try:
                self.current_process.terminate()
            except:
                pass


class AIGeneratorThread(QThread):
    status_signal = pyqtSignal(str, str)
    new_bubble_signal = pyqtSignal(str, str)
    append_text_signal = pyqtSignal(str)
    sys_message_signal = pyqtSignal(str, str)
    require_approval_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(str)
    history_save_signal = pyqtSignal()

    SAFE_COMMANDS = [
        "ls",
        "cat",
        "echo",
        "date",
        "whoami",
        "uptime",
        "hyprctl",
        "wmctrl",
        "playerctl",
        "wpctl",
        "pactl",
        "xdotool",
        "sleep",
        "free",
        "df",
        "du",
        "top",
        "htop",
        "ps",
        "uname",
        "grep",
        "find",
        "neofetch",
        "fastfetch",
    ]

    def __init__(self, messages):
        super().__init__()
        self.messages = messages
        self.approval_event = threading.Event()
        self.approval_result = False
        self._is_stopped = False

    def stop(self):
        self._is_stopped = True
        self.approval_event.set()

    def execute_command_safe(self, command):
        base_cmd = command.strip().split()[0] if command.strip() else ""
        if base_cmd in self.SAFE_COMMANDS:
            return execute_command(command)

        self.approval_event.clear()
        self.require_approval_signal.emit(command)
        self.approval_event.wait()
        if self.approval_result and not self._is_stopped:
            return execute_command(command)
        else:
            return "Command execution denied by the user."

    def run(self):
        try:
            temp_messages = list(self.messages)

            # If an image exists, we MUST strip the 'tools' payload entirely to prevent the 8B model's attention from collapsing into hallucination.
            has_image = False
            for msg in temp_messages:
                if isinstance(msg.get("content"), list):
                    for part in msg["content"]:
                        if isinstance(part, dict) and part.get("type") == "image_url":
                            has_image = True
                            break

            # Select active client and model (primary with fallback support)
            active_client = client
            active_model = LLM_MODEL

            api_kwargs = {
                "model": active_model,
                "messages": temp_messages,
                "temperature": 0.0,
                "stream": True,
            }

            # Only inject the tools schema if the model is processing text-only context
            if not has_image:
                api_kwargs["tools"] = tools

            try:
                response = active_client.chat.completions.create(**api_kwargs)
            except (openai.APIConnectionError, openai.APIStatusError) as primary_err:
                if FALLBACK_CLIENT:
                    self.sys_message_signal.emit(
                        "sys",
                        f"⚠️ Primary model unavailable ({active_model}). Switching to fallback: {FALLBACK_MODEL}...",
                    )
                    active_client = FALLBACK_CLIENT
                    active_model = FALLBACK_MODEL
                    api_kwargs["model"] = active_model
                    response = active_client.chat.completions.create(**api_kwargs)
                else:
                    raise primary_err
            tool_calls, full_content = [], ""
            bubble_created = False

            buffer = ""
            last_emit = time.time()

            for chunk in response:
                if self._is_stopped:
                    break
                delta = chunk.choices[0].delta
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = (
                            tc.index
                            if tc.index is not None
                            else max(0, len(tool_calls) - 1)
                        )
                        while len(tool_calls) <= idx:
                            tool_calls.append(
                                {
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            )
                        if tc.id:
                            tool_calls[idx]["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                tool_calls[idx]["function"]["name"] = tc.function.name
                            if tc.function.arguments:
                                tool_calls[idx]["function"]["arguments"] += (
                                    tc.function.arguments
                                )

                if delta.content:
                    clean_text = (
                        delta.content.replace('<|"|>', '"')
                        .replace("<|/|>", "")
                        .replace("execute_command", "")
                    )
                    if clean_text:
                        if not bubble_created:
                            self.new_bubble_signal.emit("ai", "")
                            bubble_created = True
                            buffer = ""
                            last_emit = time.time()

                        full_content += clean_text
                        buffer += clean_text

                        if time.time() - last_emit > 0.05:
                            self.append_text_signal.emit(buffer)
                            buffer = ""
                            last_emit = time.time()

            if buffer and not self._is_stopped:
                self.append_text_signal.emit(buffer)

            if self._is_stopped:
                self.finished_signal.emit("")
                return

            if tool_calls:
                if full_content.strip():
                    temp_messages.append({"role": "assistant", "content": full_content})
                tool_results_text = ""
                for tool_call in tool_calls:
                    if self._is_stopped:
                        break

                    tool_name = tool_call["function"]["name"]
                    try:
                        args = json.loads(tool_call["function"]["arguments"])
                    except:
                        args = {}

                    self.sys_message_signal.emit("sys", f"⚙️ Running {tool_name}...")

                    if tool_name == "get_weather":
                        results = get_weather(args.get("location", ""))
                    elif tool_name == "web_search":
                        results = perform_web_search(args.get("query", ""))
                    elif tool_name == "read_file":
                        results = read_file(args.get("path", ""))
                    elif tool_name == "write_file":
                        results = write_file(args.get("path", ""), args.get("content", ""))
                    elif tool_name == "replace_in_file":
                        results = replace_in_file(
                            args.get("path", ""),
                            args.get("target", ""),
                            args.get("replacement", ""),
                        )
                    elif tool_name == "obsidian_search":
                        results = search_obsidian(args.get("query", ""))
                    elif tool_name == "obsidian_read":
                        results = read_obsidian(args.get("filename", ""))
                    elif tool_name == "obsidian_write":
                        results = write_obsidian(
                            args.get("filename", ""), args.get("content", "")
                        )
                    elif tool_name == "manage_desktop":
                        results = manage_desktop(
                            args.get("action", ""),
                            args.get("target", ""),
                            args.get("window_address", ""),
                        )
                    elif tool_name == "execute_command":
                        results = self.execute_command_safe(args.get("command", ""))
                    elif tool_name in mcp_manager.tools:
                        results = mcp_manager.call_tool(tool_name, args)
                    else:
                        results = "Done."

                    tool_results_text += f"\n--- {tool_name} ---\n{results}\n"

                if self._is_stopped:
                    self.finished_signal.emit("")
                    return

                temp_messages.append(
                    {
                        "role": "user",
                        "content": f"[SYSTEM RESULTS]:\n{tool_results_text}\nReply directly based on these results. If quoting written content, quote it exactly without paraphrasing.",
                    }
                )
                final_content = ""
                bubble_created = False

                # Turn 2 API Call (Processing Tool Results)
                api_kwargs_t2 = {
                    "model": active_model,
                    "messages": temp_messages,
                    "temperature": 0.0,
                    "stream": True,
                }

                try:
                    stream_response = active_client.chat.completions.create(**api_kwargs_t2)
                except (openai.APIConnectionError, openai.APIStatusError) as t2_err:
                    if active_client != FALLBACK_CLIENT and FALLBACK_CLIENT:
                        self.sys_message_signal.emit(
                            "sys",
                            f"⚠️ Primary model unavailable ({active_model}). Switching to fallback: {FALLBACK_MODEL}...",
                        )
                        active_client = FALLBACK_CLIENT
                        active_model = FALLBACK_MODEL
                        api_kwargs_t2["model"] = active_model
                        stream_response = active_client.chat.completions.create(**api_kwargs_t2)
                    else:
                        raise t2_err

                buffer = ""
                last_emit = time.time()

                for chunk in stream_response:
                    if self._is_stopped:
                        break
                    if chunk.choices[0].delta.content:
                        clean_text = (
                            chunk.choices[0]
                            .delta.content.replace('<|"|>', '"')
                            .replace("<|/|>", "")
                            .replace("execute_command", "")
                        )
                        if clean_text:
                            if not bubble_created:
                                self.new_bubble_signal.emit("ai", "")
                                bubble_created = True
                                buffer = ""
                                last_emit = time.time()

                            final_content += clean_text
                            buffer += clean_text

                            if time.time() - last_emit > 0.05:
                                self.append_text_signal.emit(buffer)
                                buffer = ""
                                last_emit = time.time()

                if buffer and not self._is_stopped:
                    self.append_text_signal.emit(buffer)

                if self._is_stopped:
                    self.finished_signal.emit("")
                    return

                if final_content.strip():
                    self.messages.append(
                        {"role": "assistant", "content": final_content}
                    )
                full_content = final_content
            else:
                if full_content.strip():
                    self.messages.append({"role": "assistant", "content": full_content})

            self.history_save_signal.emit()
            self.finished_signal.emit(full_content)

        except openai.APIConnectionError:
            self.sys_message_signal.emit(
                "sys", "⚠️ Error: The Local LLM Server is offline."
            )
            self.finished_signal.emit("The local model server is offline.")
        except Exception as e:
            self.sys_message_signal.emit("sys", f"[API ERROR]: {str(e)}")
            self.finished_signal.emit("")


## BACKEND CONTROLLER (Bridged to QML)
class Backend(QObject):
    statusChanged = pyqtSignal(str, str)
    messageAdded = pyqtSignal(str, str)
    messageAppended = pyqtSignal(str)
    chatCleared = pyqtSignal()
    inputStateChanged = pyqtSignal(bool)
    stopButtonStateChanged = pyqtSignal(bool)
    requireApproval = pyqtSignal(str)
    clearApproval = pyqtSignal()

    def __init__(self, initial_query, launch_mode):
        super().__init__()
        self.is_voice_mode = launch_mode == "--voice"
        self.is_recording = False
        self.rec_process = None
        self.messages = []

        self.timer = QTimer()
        self.timer.timeout.connect(self.check_signals)
        self.timer.start(200)

        self.load_history()

    @pyqtSlot(str, str)
    def handle_message_added(self, role, text):
        self.messageAdded.emit(role, text)

    @pyqtSlot(str)
    def handle_message_appended(self, text):
        self.messageAppended.emit(text)

    def emit_initial_state(self):
        if self.is_voice_mode:
            self.statusChanged.emit("🎙️ Voice Mode Active", "#a6e3a1")
        for msg in self.messages:
            if msg["role"] != "system":
                role_str = "ai" if msg["role"] == "assistant" else "user"
                self.messageAdded.emit(role_str, msg["content"])

        if len(sys.argv) > 1 and sys.argv[1].strip():
            self.submit_query(sys.argv[1])

    def _build_system_prompt(self):
        system_content = (
            "You are Assistant, an agentic AI desktop assistant with direct system access. "
            "You MUST use the provided JSON tool calls to interact with the system, files, and applications.\n"
            "- CRITICAL RULE FOR FILES: When the user asks to create, write, modify, read, or replace content in a file, "
            "you MUST call `write_file`, `read_file`, `replace_in_file`, or `execute_command`. "
            "NEVER claim or pretend you created or edited a file in your response text without invoking the corresponding tool first.\n"
            "- When opening GUI applications or web browsers, use `execute_command` (they will run in background).\n"
            "- Keep answers concise, natural, and conversational."
        )
        if USER_CONTEXT and not USER_CONTEXT.startswith("<!--"):
            system_content += f"\n\n### USER CONTEXT:\n{USER_CONTEXT}"
        return system_content

    def load_history(self):
        system_content = self._build_system_prompt()
        self.messages = []
        if os.path.exists(HISTORY_FILE) and (
            time.time() - os.path.getmtime(HISTORY_FILE)
            < (LLM_HISTORY_DURATION_MINS * 60)
        ):
            try:
                with open(HISTORY_FILE, "r") as f:
                    loaded_history = json.load(f)
                    for msg in loaded_history:
                        if msg.get("role") != "system":
                            self.messages.append(msg)
            except:
                pass

        self.messages.insert(0, {"role": "system", "content": system_content})

    @pyqtSlot()
    def reset_session(self):
        """Reset conversation context and clear screen."""
        self.interrupt_ai()
        self.messages = [{"role": "system", "content": self._build_system_prompt()}]
        if os.path.exists(HISTORY_FILE):
            try:
                os.remove(HISTORY_FILE)
            except:
                pass
        self.chatCleared.emit()
        self.messageAdded.emit(
            "sys",
            "✨ Nueva sesión iniciada. Contexto anterior eliminado."
        )

    @pyqtSlot()
    def show_help(self):
        """Display available slash commands and shortcuts."""
        help_text = (
            "💡 **Comandos disponibles:**\n\n"
            "- **/new** o **/reset**: Inicia una nueva sesión desde cero, eliminando el contexto y limpiando el chat.\n"
            "- **/clear**: Limpia los mensajes en pantalla y vacía la memoria de la conversación.\n"
            "- **/help**: Muestra esta información de ayuda.\n"
            "- **'v'**: Escribe 'v' y pulsa Enter para activar el modo de voz instantáneamente."
        )
        self.messageAdded.emit("sys", help_text)

    @pyqtSlot()
    def save_history(self):
        tail = [m for m in self.messages if m.get("role") != "system"]
        history_to_save = [self.messages[0]] + tail[-8:]
        with open(HISTORY_FILE, "w") as f:
            json.dump(history_to_save, f)

    @pyqtSlot()
    def interrupt_ai(self):
        if hasattr(self, "ai_thread") and self.ai_thread is not None:
            if self.ai_thread.isRunning():
                self.ai_thread.stop()

        if hasattr(self, "tts_thread") and self.tts_thread is not None:
            if self.tts_thread.isRunning():
                self.tts_thread.stop()

        self.is_recording = False
        if self.rec_process:
            try:
                self.rec_process.terminate()
            except:
                pass

        subprocess.run(["pkill", "-x", "pw-play"], stderr=subprocess.DEVNULL)
        subprocess.run(["pkill", "-x", "piper"], stderr=subprocess.DEVNULL)
        subprocess.run(["pkill", "-x", "edge-tts"], stderr=subprocess.DEVNULL)
        subprocess.run(["pkill", "-x", "rec"], stderr=subprocess.DEVNULL)
        subprocess.run(["pkill", "-x", "whisper-cli"], stderr=subprocess.DEVNULL)

        self.statusChanged.emit("Idle", "#6c7086")
        self.inputStateChanged.emit(True)
        self.stopButtonStateChanged.emit(False)
        self.is_voice_mode = False
        self._input_enabled = True

    def check_signals(self):
        if os.path.exists(os.path.join(TEMP_DIR, "gui_signal")):
            os.remove(os.path.join(TEMP_DIR, "gui_signal"))
            if (
                self.is_recording
                or not getattr(self, "_input_enabled", True)
                or (
                    hasattr(self, "ai_thread")
                    and self.ai_thread is not None
                    and self.ai_thread.isRunning()
                )
            ):
                self.interrupt_ai()
            else:
                self.startVoiceRecording()

    @pyqtSlot(str)
    def processInput(self, text):
        clean_cmd = text.strip().lower()
        if clean_cmd in ["/new", "/reset"]:
            self.reset_session()
            return
        elif clean_cmd == "/clear":
            self.reset_session()
            return
        elif clean_cmd == "/help":
            self.show_help()
            return

        self.interrupt_ai()
        self.is_voice_mode = False
        self.statusChanged.emit("Idle", "#6c7086")
        self.submit_query(text)

    @pyqtSlot()
    def stopAIOperation(self):
        self.interrupt_ai()

    @pyqtSlot(bool)
    def resolveCommandApproval(self, approved):
        self.clearApproval.emit()
        if hasattr(self, "ai_thread") and self.ai_thread:
            self.ai_thread.approval_result = approved
            self.ai_thread.approval_event.set()

    @pyqtSlot()
    def startVoiceRecording(self):
        self.interrupt_ai()
        if self.is_recording:
            return
        self.is_recording = True
        self.is_voice_mode = True
        self._input_enabled = False
        self.inputStateChanged.emit(False)
        self.stopButtonStateChanged.emit(True)
        self.statusChanged.emit("🎙️ Listening... (Say 'Stop' to end)", "#a6e3a1")
        subprocess.run(
            ["pw-play", os.path.join(SOUND_DIR, "start.wav")], stderr=subprocess.DEVNULL
        )
        threading.Thread(target=self.record_with_silence).start()

    def stop_recording(self):
        if self.is_recording and self.rec_process:
            self.is_recording = False
            self.rec_process.terminate()

    def record_with_silence(self):
        self.rec_process = subprocess.Popen(
            [
                "rec",
                "-q",
                "-r",
                "16000",
                "-c",
                "1",
                "-b",
                "16",
                "-e",
                "signed-integer",
                os.path.join(TEMP_DIR, "voice_reply.wav"),
                "silence",
                "1",
                "0.1",
                "1%",
                "1",
                "1.5",
                "1%",
            ]
        )
        self.rec_process.wait()
        if self.is_recording:
            self.is_recording = False
            subprocess.run(
                ["pw-play", os.path.join(SOUND_DIR, "stop.wav")],
                stderr=subprocess.DEVNULL,
            )
            self.transcribe_audio()

    def transcribe_audio(self):
        self.statusChanged.emit("⚙️ Transcribing...", "#f9e2af")
        whisper_model = os.path.expanduser(
            VOICE_CONF.get("whisper_model")
            or "~/.local/share/assistant/models/ggml-small.bin"
        )
        res = subprocess.run(
            [
                "whisper-cli",
                "-m",
                whisper_model,
                "-f",
                os.path.join(TEMP_DIR, "voice_reply.wav"),
                "-nt",
                "-l",
                "auto",
            ],
            capture_output=True,
            text=True,
        )
        transcript = re.sub(r"\[.*?\]", "", res.stdout).strip()

        if transcript:
            clean_no_spaces = re.sub(r"[^\w]", "", transcript.lower())
            hallucinations = [
                "ご視聴ありがとうございました",
                "はい承知しました",
                "thankyou",
                "thanksforwatching",
                "soustitres",
                "subtitles",
                "amaraorg",
                "thankyouverymuch",
                "merci",
            ]
            if (
                any(h in clean_no_spaces for h in hallucinations)
                and len(transcript.split()) <= 5
            ):
                transcript = ""

        if transcript:
            clean_trans = re.sub(r"[^\w\s]", "", transcript.lower().strip())
            stop_phrases = [
                "stop",
                "exit",
                "quit",
                "thank you",
                "thanks",
                "ok stop",
                "goodbye",
                "bye",
            ]
            if (
                any(p in clean_trans for p in stop_phrases)
                and len(clean_trans.split()) <= 4
            ):
                self.statusChanged.emit("Idle", "#6c7086")
                self.messageAdded.emit("sys", "🛑 Conversation Ended.")
                self.is_voice_mode = False
                self._input_enabled = True
                self.inputStateChanged.emit(True)
                self.stopButtonStateChanged.emit(False)
                return
            self.submit_query(transcript)
        else:
            self.statusChanged.emit("Idle", "#6c7086")
            self.messageAdded.emit("sys", "No speech detected.")
            self.is_voice_mode = False
            self._input_enabled = True
            self.inputStateChanged.emit(True)
            self.stopButtonStateChanged.emit(False)

    def submit_query(self, query):
        self._input_enabled = False
        self.inputStateChanged.emit(False)
        self.stopButtonStateChanged.emit(True)
        self.statusChanged.emit("🧠 Thinking...", "#cba6f7")
        self.messageAdded.emit("user", query)

        content = process_prompt(query)
        self.messages.append({"role": "user", "content": content})

        if hasattr(self, "ai_thread") and self.ai_thread is not None:
            if self.ai_thread.isRunning():
                self.ai_thread.stop()
            self.ai_thread.deleteLater()

        self.ai_thread = AIGeneratorThread(self.messages)
        self.ai_thread.status_signal.connect(self.statusChanged.emit)
        self.ai_thread.new_bubble_signal.connect(
            self.handle_message_added, Qt.ConnectionType.QueuedConnection
        )
        self.ai_thread.append_text_signal.connect(
            self.handle_message_appended, Qt.ConnectionType.QueuedConnection
        )
        self.ai_thread.sys_message_signal.connect(
            self.handle_message_added, Qt.ConnectionType.QueuedConnection
        )
        self.ai_thread.require_approval_signal.connect(self.requireApproval.emit)
        self.ai_thread.finished_signal.connect(self.on_ai_finished)
        self.ai_thread.history_save_signal.connect(self.save_history)
        self.ai_thread.start()

    def on_ai_finished(self, full_response):
        if full_response.strip():
            self.play_audio(full_response)
        else:
            self.statusChanged.emit("Idle", "#6c7086")
            self._input_enabled = True
            self.inputStateChanged.emit(True)
            self.stopButtonStateChanged.emit(False)

    def clean_text_for_tts(self, text):
        text = re.sub(r"```.*?```", " code block omitted. ", text, flags=re.DOTALL)
        text = re.sub(r"`.*?`", " code ", text)
        text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
        text = re.sub(r"https?://[^\s]+", " link ", text)
        text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
        text = re.sub(r"\*(.*?)\*", r"\1", text)
        text = re.sub(r"__(.*?)__", r"\1", text)
        text = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE)
        return text.strip()

    def play_audio(self, text):
        tts_text = self.clean_text_for_tts(text)

        if hasattr(self, "tts_thread") and self.tts_thread is not None:
            if self.tts_thread.isRunning():
                self.tts_thread.stop()
            self.tts_thread.deleteLater()

        self.tts_thread = TTSWorkerThread(tts_text)
        self.tts_thread.status_signal.connect(self.statusChanged.emit)
        self.tts_thread.finished_signal.connect(self.on_tts_finished)
        self.tts_thread.start()

    def on_tts_finished(self):
        if self.is_voice_mode:
            self.startVoiceRecording()
        else:
            self.statusChanged.emit("Idle", "#6c7086")
            self._input_enabled = True
            self.inputStateChanged.emit(True)
            self.stopButtonStateChanged.emit(False)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    engine = QQmlApplicationEngine()

    initial_q = sys.argv[1] if len(sys.argv) > 1 else ""
    launch_m = sys.argv[2] if len(sys.argv) > 2 else ""
    backend = Backend(initial_q, launch_m)

    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(QML_FILE))

    if not engine.rootObjects():
        sys.exit(-1)

    QTimer.singleShot(100, backend.emit_initial_state)

    sys.exit(app.exec())
