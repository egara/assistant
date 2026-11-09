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
import shutil
import logging
from logging.handlers import RotatingFileHandler

from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont, QAction, QPainterPath
from PyQt6.QtQml import QQmlApplicationEngine
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot, QThread, QObject, QUrl

import openai
from openai import OpenAI, Timeout
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
APP_VERSION = "1.1.0"
CONFIG_DIR = os.path.expanduser("~/.config/assistant")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
TEMP_DIR = "/tmp/assistant"
DATA_DIR = os.path.expanduser("~/.local/share/assistant")
SOUND_DIR = os.path.join(DATA_DIR, "sounds")
HISTORY_DIR = os.path.expanduser("~/.local/state/assistant")
HISTORY_FILE = os.path.join(HISTORY_DIR, "chat_history.json")
LOG_FILE = os.path.join(HISTORY_DIR, "assistant.log")
USER_KNOWLEDGE_FILE = os.path.join(DATA_DIR, "hyprland_knowledge.json")

os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(SOUND_DIR, exist_ok=True)
os.makedirs(HISTORY_DIR, exist_ok=True)

# Configure rotating file logging and console handler
logger = logging.getLogger("assistant")
logger.setLevel(logging.INFO)
if not logger.handlers:
    rf_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    rf_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(rf_handler)
    c_handler = logging.StreamHandler(sys.stdout)
    c_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(c_handler)

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

# Modular knowledge directory paths (Config directory first, with factory fallback)
KNOWLEDGE_DIR = os.path.join(CONFIG_DIR, "knowledge")
SYSTEM_KNOWLEDGE_DIR = os.path.join(_base_dir, "knowledge")
os.makedirs(KNOWLEDGE_DIR, exist_ok=True)

# Seed user modular knowledge files from factory if they do not exist yet
if os.path.exists(SYSTEM_KNOWLEDGE_DIR):
    for f_name in os.listdir(SYSTEM_KNOWLEDGE_DIR):
        src_k_file = os.path.join(SYSTEM_KNOWLEDGE_DIR, f_name)
        dst_k_file = os.path.join(KNOWLEDGE_DIR, f_name)
        if os.path.isfile(src_k_file) and not os.path.exists(dst_k_file):
            try:
                shutil.copyfile(src_k_file, dst_k_file)
            except Exception as e:
                print(f"[Knowledge] Notice: could not seed {f_name}: {e}")

SYSTEM_KNOWLEDGE_FILE = None
for path in system_knowledge_paths:
    if os.path.exists(path):
        SYSTEM_KNOWLEDGE_FILE = path
        break

# Seed user knowledge file from factory if it does not exist yet
if SYSTEM_KNOWLEDGE_FILE and not os.path.exists(USER_KNOWLEDGE_FILE):
    try:
        shutil.copyfile(SYSTEM_KNOWLEDGE_FILE, USER_KNOWLEDGE_FILE)
    except Exception as e:
        print(f"[Hyprland Knowledge] Notice: could not seed user knowledge: {e}")

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

# ==========================================
# 🤖 MULTI-MODEL LLM MANAGER & CONFIGURATION
# ==========================================
LLM_CONF = config.get("llm", {})

MODELS_CATALOG = {}
# 1. New multi-model format: {"models": {"gemma": {...}, "glm": {...}}}
if isinstance(LLM_CONF.get("models"), dict) and LLM_CONF["models"]:
    for key, spec in LLM_CONF["models"].items():
        if isinstance(spec, dict):
            m_key = str(key).strip()
            m_name = spec.get("name") or spec.get("model") or m_key
            m_url = spec.get("base_url", "http://127.0.0.1:8080/v1")
            m_key_auth = spec.get("api_key", "sk-none")
            m_timeout = float(spec.get("timeout", 180.0))
            m_vision = spec.get("vision_enabled", True)
            m_provider = spec.get("provider_name", "local")

            client_inst = OpenAI(
                base_url=m_url,
                api_key=m_key_auth,
                timeout=Timeout(m_timeout, connect=10.0),
            )

            MODELS_CATALOG[m_key] = {
                "key": m_key,
                "name": m_name,
                "client": client_inst,
                "base_url": m_url,
                "api_key": m_key_auth,
                "vision_enabled": m_vision,
                "provider_name": m_provider,
            }

# 2. Backward compatibility: if "models" is not defined, read legacy primary and fallback
if not MODELS_CATALOG:
    primary_name = LLM_CONF.get("model", "Hermes-3-8B")
    primary_url = LLM_CONF.get("base_url", "http://127.0.0.1:8080/v1")
    primary_key = LLM_CONF.get("api_key", "sk-none")
    primary_timeout = float(LLM_CONF.get("timeout", 180.0))
    primary_vision = LLM_CONF.get("vision_enabled", True)
    primary_provider = LLM_CONF.get("provider_name", "local")

    primary_client = OpenAI(
        base_url=primary_url,
        api_key=primary_key,
        timeout=Timeout(primary_timeout, connect=10.0),
    )

    primary_id = primary_name.split(":")[0].lower() if ":" in primary_name else "primary"
    MODELS_CATALOG[primary_id] = {
        "key": primary_id,
        "name": primary_name,
        "client": primary_client,
        "base_url": primary_url,
        "api_key": primary_key,
        "vision_enabled": primary_vision,
        "provider_name": primary_provider,
    }

    fallback_conf = LLM_CONF.get("fallback")
    if isinstance(fallback_conf, dict):
        fb_name = fallback_conf.get("model", "GLM-5.3-Flash")
        fb_url = fallback_conf.get("base_url", "http://127.0.0.1:8010/v1")
        fb_key = fallback_conf.get("api_key", "sk-none")
        fb_timeout = float(fallback_conf.get("timeout", 180.0))
        fb_vision = fallback_conf.get("vision_enabled", True)
        fb_provider = fallback_conf.get("provider_name", "local")

        fb_client = OpenAI(
            base_url=fb_url,
            api_key=fb_key,
            timeout=Timeout(fb_timeout, connect=10.0),
        )

# Mark presets as favorites / config-defined
for spec in MODELS_CATALOG.values():
    spec["is_preset"] = True

def discover_endpoint_models():
    """
    Query each configured provider base_url/models endpoint to discover
    all available models and register them under MODELS_CATALOG.
    """
    seen_urls = set()
    endpoints = []
    for info in list(MODELS_CATALOG.values()):
        b_url = info.get("base_url")
        if not b_url or b_url in seen_urls:
            continue
        seen_urls.add(b_url)
        endpoints.append(info)

    for base_info in endpoints:
        b_url = base_info.get("base_url", "").rstrip("/")
        models_url = f"{b_url}/models"
        p_name = base_info.get("provider_name", "Local")
        auth_header = f"Bearer {base_info.get('api_key', 'sk-none')}"
        req = urllib.request.Request(models_url, headers={"Authorization": auth_header})
        try:
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for m in data.get("data", []):
                    m_id = m.get("id")
                    if not m_id:
                        continue
                    # Check if already registered
                    already_in_catalog = any(
                        info.get("base_url", "").rstrip("/") == b_url and (info.get("name") == m_id or info.get("key") == m_id)
                        for info in MODELS_CATALOG.values()
                    )
                    if not already_in_catalog:
                        # Build a unique key for the discovered model
                        MODELS_CATALOG[m_id] = {
                            "key": m_id,
                            "name": m_id,
                            "client": base_info.get("client"),
                            "base_url": base_info.get("base_url"),
                            "api_key": base_info.get("api_key"),
                            "vision_enabled": base_info.get("vision_enabled", True),
                            "provider_name": p_name,
                            "is_preset": False,
                        }
                        logger.info(f"[Discovery] Found model '{m_id}' on {p_name} ({b_url})")
        except Exception as e:
            logger.debug(f"[Discovery] Could not fetch models from {models_url}: {e}")

# Initial synchronous discovery with short timeout
discover_endpoint_models()

# Determine initial active model
DEFAULT_MODEL_KEY = LLM_CONF.get("default")
if not DEFAULT_MODEL_KEY or DEFAULT_MODEL_KEY not in MODELS_CATALOG:
    DEFAULT_MODEL_KEY = next(iter(MODELS_CATALOG))

ACTIVE_MODEL_KEY = DEFAULT_MODEL_KEY

VOICE_CONF = config.get("voice", {})
MCP_SERVERS_CONF = config.get("mcp_servers", {})

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
            "name": "get_current_time",
            "description": "Get the current date, time, day of the week, and timezone from the system host.",
            "parameters": {
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "Optional IANA timezone name (e.g. 'Europe/Madrid', 'UTC', 'America/New_York'). If omitted, uses the system local timezone.",
                    }
                },
            },
        },
    },
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
            "name": "open_application",
            "description": "Open or launch any desktop application, file, or URL. When opening a file or playing media (audio/video), pass the file path in 'target'; if 'app' is 'xdg-open' or an uninstalled tool (e.g. mpv), it automatically opens with your configured system default application (e.g. qmmp for audio, vlc for video). Terminal-based apps automatically run inside kitty, and all apps launch under uwsm.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": "Application name (e.g. 'xdg-open', 'yazi', 'google-chrome', 'firefox', 'nautilus', 'zed', 'qmmp', 'vlc'). Use 'xdg-open' or leave default when opening files with their default association.",
                    },
                    "target": {
                        "type": "string",
                        "description": "Optional file path, directory path, or URL to open with the application (e.g. '~/Downloads', '~/Documents/report.pdf', 'https://github.com').",
                    },
                },
                "required": ["app"],
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
    {
        "type": "function",
        "function": {
            "name": "get_system_knowledge",
            "description": "Consult SicOS modular knowledge or live Hyprland shortcuts. Available topics: 'index' (overview), 'hardware' (audio/volume with wpctl, brightness with brightnessctl, monitors), 'apps' (installed tools and whether GUI/TUI), 'hyprland' (desktop architecture & layouts), or 'shortcuts' (active keyboard shortcuts from compositor).",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Topic to consult: 'index', 'hardware', 'apps', 'hyprland', or 'shortcuts'.",
                    }
                },
                "required": ["topic"],
            },
        },
    },
] + mcp_tools

# ==========================================
# 🧭 DECISION ROUTER (d3 / System One)
# ==========================================
DECISION_CONF = config.get("decision_router", {})
DECISION_ENABLED = DECISION_CONF.get("enabled", False)
DECISION_ENDPOINT = DECISION_CONF.get("endpoint", "")
DECISION_MODEL = DECISION_CONF.get("model", "d3:edge")
DECISION_TIMEOUT = float(DECISION_CONF.get("timeout", 1.5))
DECISION_CONFIDENCE_THRESHOLD = float(DECISION_CONF.get("confidence_threshold", 0.60))

# Tool categories mapping
TOOL_CATEGORIES = {
    "conversational": [],
    "datetime": ["get_current_time"],
    "desktop_and_apps": ["open_application", "manage_desktop", "get_system_knowledge"],
    "filesystem": ["read_file", "write_file", "replace_in_file", "execute_command"],
    "notes": ["obsidian_search", "obsidian_read", "obsidian_write"],
    "weather": ["get_weather"],
    "web_search": ["web_search"] + [t["function"]["name"] for t in mcp_tools],
}

def resolve_tools_for_query(user_query, recent_context="", base_url=""):
    """
    Route incoming user query using the d3:edge decision model (/v1/systemone).
    Returns (filtered_tools, decision_info_dict). Falls back to (tools, None)
    if disabled, unreachable, or if decision confidence is below threshold.
    """
    if not DECISION_ENABLED or not user_query:
        return tools, None

    # Derive endpoint from base_url if not explicitly provided or if default
    endpoint = DECISION_ENDPOINT
    if not endpoint:
        if base_url:
            parsed = urllib.parse.urlparse(base_url)
            endpoint = f"{parsed.scheme}://{parsed.netloc}/v1/systemone"
        else:
            endpoint = "http://127.0.0.1:52625/v1/systemone"

    payload = {
        "model": DECISION_MODEL,
        "state": {
            "user_query": user_query[:1000],
            "context": recent_context[:1000] if recent_context else "",
        },
        "questions": {
            "category": {
                "type": "choice",
                "instructions": (
                    "Determina qué categoría de herramientas del sistema es requerida para satisfacer la petición del usuario en español teniendo en cuenta el contexto previo. "
                    "Elige 'conversational' si es charla informal, preguntas teóricas, saludos o explicaciones que no necesitan herramientas."
                ),
                "criteria": {
                    "conversational": "Conversación informal, charla, respuestas teóricas, saludos o explicaciones.",
                    "datetime": "Preguntas exclusivas sobre qué hora es, qué día es hoy, la fecha actual o zona horaria.",
                    "desktop_and_apps": "Escritorio Hyprland y aplicaciones: abrir o lanzar aplicaciones o archivos (e.g. abrir cartera.ods con libreoffice, abre chrome, lanza kitty, confirmación afirmativa del usuario como 'si' para abrir o ver un archivo), reproducir música o canciones (reproduce pura dinamita), cerrar ventanas, alternar ventanas flotantes (flota chrome) o cambiar de workspace.",
                    "filesystem": "Operaciones con archivos y terminal: buscar archivos en el disco (/home, extensiones .ods, .pdf), listar carpetas (lista canciones en descargas/Downloads), leer o escribir archivos, o ejecutar comandos bash.",
                    "notes": "Buscar o leer notas en la aplicación Obsidian.",
                    "weather": "Previsión del tiempo, lluvia, temperatura o clima meteorológico en ciudades o regiones (e.g. que tiempo hara en leon).",
                    "web_search": "Buscar noticias de última hora o información en internet a través de la web.",
                },
            },
            "needs_datetime": {
                "type": "noul",
                "instructions": (
                    "Does resolving this request require knowing the current date, time, weekday, or resolving relative time expressions "
                    "(e.g. 'hoy', 'ayer', 'antier', 'mañana', 'hace unos días', 'última semana', 'noticias de ayer', 'ficheros de hace 3 días', "
                    "'today', 'yesterday', 'days ago', 'last week')?"
                ),
                "criteria": {
                    "true": "The user query mentions time, dates, or relative temporal anchors (today, yesterday, ago, current time/date).",
                    "false": "The user query does not depend on knowing the current date or time.",
                },
            },
        },
    }

    t_start = time.time()
    try:
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=DECISION_TIMEOUT) as response:
            elapsed_ms = int((time.time() - t_start) * 1000)
            if response.status == 200:
                res_data = json.loads(response.read().decode("utf-8"))
                answers = res_data.get("answers", {})

                # Check category question
                cat_info = answers.get("category", {})
                choice = cat_info.get("choice")
                probabilities = cat_info.get("probabilities", {})
                confidence = probabilities.get(choice, 1.0) if choice else 0.0

                # Check needs_datetime question (noul: true / false)
                dt_info = answers.get("needs_datetime", {})
                dt_choice = str(dt_info.get("choice", "")).lower() in ["true", "yes", "1"]
                dt_probs = dt_info.get("probabilities", {})
                dt_confidence = dt_probs.get("true", 0.0) if isinstance(dt_probs, dict) else 0.0

                # Heuristic keyword check as a robust safety net for relative time expressions
                lower_query = user_query.lower()
                has_time_keywords = any(
                    kw in lower_query
                    for kw in [
                        "hoy", "ayer", "antier", "mañana", "hace ", "última semana", "este mes",
                        "qué hora", "que hora", "qué día", "que dia", "en qué fecha", "en que fecha",
                        "today", "yesterday", "ago", "last week", "current time", "current date"
                    ]
                )
                include_datetime = dt_choice or (dt_confidence > 0.40) or has_time_keywords

                info = {
                    "choice": choice,
                    "confidence": confidence,
                    "needs_datetime": include_datetime,
                }

                if choice in TOOL_CATEGORIES and confidence >= DECISION_CONFIDENCE_THRESHOLD:
                    target_names = set(TOOL_CATEGORIES[choice])
                    if include_datetime and choice != "conversational":
                        target_names.add("get_current_time")

                    filtered = [t for t in tools if t.get("function", {}).get("name") in target_names]
                    info["tools_count"] = len(filtered)
                    info["tools_names"] = [t["function"]["name"] for t in filtered]
                    logger.info(
                        f"[Router] Query: '{user_query[:60]}' -> Category: {choice} ({int(confidence*100)}%) | "
                        f"Tools ({len(filtered)}): {info['tools_names']} | Latency: {elapsed_ms}ms"
                    )
                    return filtered, info

                logger.warning(
                    f"[Router] Confidence too low for '{choice}' ({int(confidence*100)}% < {int(DECISION_CONFIDENCE_THRESHOLD*100)}%). "
                    f"Passing all {len(tools)} tools as fallback | Latency: {elapsed_ms}ms"
                )
                return tools, {"choice": choice, "confidence": confidence, "tools_count": len(tools), "fallback": True}
    except Exception as e:
        logger.warning(f"[Router] Bypass/Fallback (Error connecting to {endpoint}): {e}")

    return tools, None



def get_current_time(timezone_name=""):
    """
    Returns current local date, time, weekday, and timezone information.
    Can optionally convert to any requested IANA timezone (e.g. Europe/Madrid, America/New_York).
    """
    try:
        if timezone_name and timezone_name.strip():
            tz = zoneinfo.ZoneInfo(timezone_name.strip())
            now = datetime.datetime.now(tz)
        else:
            now = datetime.datetime.now().astimezone()

        days_es = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
        months_es = [
            "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
            "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
        ]

        day_name = days_es[now.weekday()]
        month_name = months_es[now.month - 1]

        info = {
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "day_of_week": day_name,
            "day": now.day,
            "month": month_name,
            "year": now.year,
            "timezone": str(now.tzname() or now.tzinfo),
            "iso": now.isoformat(),
            "formatted": f"{day_name}, {now.day} de {month_name} de {now.year}, {now.strftime('%H:%M:%S')} ({now.tzname()})",
        }
        return json.dumps(info, ensure_ascii=False)
    except Exception as e:
        return f"Error retrieving current time: {str(e)}"


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


# Fast in-memory cache of desktop applications discovered from XDG_DATA_DIRS
_DESKTOP_CACHE = None

def _get_desktop_cache():
    global _DESKTOP_CACHE
    if _DESKTOP_CACHE is not None:
        return _DESKTOP_CACHE

    cache = {}
    data_dirs = os.environ.get("XDG_DATA_DIRS", "").split(":")
    # Add standard NixOS profile paths if not in XDG_DATA_DIRS
    extra_dirs = [
        os.path.expanduser("~/.nix-profile/share"),
        f"/etc/profiles/per-user/{os.environ.get('USER', 'egarcia')}/share",
        "/run/current-system/sw/share",
    ]
    for ed in extra_dirs:
        if ed not in data_dirs:
            data_dirs.append(ed)

    for d in data_dirs:
        apps_dir = os.path.join(d, "applications")
        if not os.path.isdir(apps_dir):
            continue
        try:
            for entry in os.listdir(apps_dir):
                if entry.endswith(".desktop"):
                    full_p = os.path.join(apps_dir, entry)
                    base_name = entry[:-8].lower()
                    try:
                        with open(full_p, "r", encoding="utf-8", errors="ignore") as fp:
                            content = fp.read()
                            is_terminal = "Terminal=true" in content
                            cache[base_name] = {"is_terminal": is_terminal, "path": full_p}
                    except:
                        pass
        except:
            pass

    _DESKTOP_CACHE = cache
    return _DESKTOP_CACHE


def _resolve_fuzzy_target_path(raw_target):
    """Clean and resolve file paths even if the LLM hallucinated tildes or abbreviated intermediate folders."""
    if not raw_target:
        return ""
    target_clean = raw_target.strip().strip("'\"`")
    if target_clean.startswith(("http://", "https://")):
        return target_clean

    home = os.path.expanduser("~")
    # Fix broken concatenation like /home/egarcia~/ or /home/username/~/
    if target_clean.startswith(f"{home}~"):
        target_clean = home + target_clean[len(home) + 1:]
    elif target_clean.startswith(f"{home}/~"):
        target_clean = home + target_clean[len(home) + 2:]

    expanded = os.path.expanduser(target_clean)
    if os.path.exists(expanded):
        return expanded

    # If Gemma or another LLM abbreviated the path or simplified the filename (e.g. Mardom.mp3 instead of 'Mardom ft... .mp3')
    filename = os.path.basename(expanded).strip("_*` \t")
    name_stem, ext = os.path.splitext(filename)

    # Determine smartest search root: if path provided a valid folder hint (like ~/Downloads), search there first!
    parent_hint = os.path.dirname(expanded)
    search_root = parent_hint if (parent_hint and os.path.exists(parent_hint) and parent_hint != home) else home

    candidates = []
    # 1. First pass: exact filename match
    for root, dirs, files in os.walk(search_root):
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".") and d not in (
                "node_modules", "target", "build", "dist", ".cache", ".git"
            )
        ]
        if filename in files:
            candidates.append(os.path.join(root, filename))
            if len(candidates) >= 5:
                break

    # If not found in parent hint, scan home for exact filename
    if not candidates and search_root != home:
        for root, dirs, files in os.walk(home):
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".") and d not in (
                    "node_modules", "target", "build", "dist", ".cache", ".git"
                )
            ]
            if filename in files:
                candidates.append(os.path.join(root, filename))
                if len(candidates) >= 5:
                    break

    # 2. Second pass: Substring / stem matching (e.g. user/LLM says 'Mardom.mp3' for 'Mardom ft Miss Blanche... .mp3')
    if not candidates and name_stem and len(name_stem) >= 3:
        for scan_dir in ([search_root] if search_root == home else [search_root, home]):
            for root, dirs, files in os.walk(scan_dir):
                dirs[:] = [
                    d for d in dirs
                    if not d.startswith(".") and d not in (
                        "node_modules", "target", "build", "dist", ".cache", ".git"
                    )
                ]
                for f in files:
                    f_stem, f_ext = os.path.splitext(f)
                    if ext and f_ext.lower() != ext.lower():
                        continue
                    if name_stem.lower() in f_stem.lower():
                        candidates.append(os.path.join(root, f))
                        if len(candidates) >= 10:
                            break
                if candidates:
                    break
            if candidates:
                break

    if len(candidates) == 1:
        return candidates[0]
    elif len(candidates) > 1:
        # Score candidates based on folder/name tokens mentioned in the prompt path
        tokens = [t.strip("_*` \t").lower() for t in target_clean.replace("...", "/").split("/") if len(t) > 2]
        best_cand = candidates[0]
        max_score = -1
        for cand in candidates:
            cand_lower = cand.lower()
            score = sum(1 for tok in tokens if tok in cand_lower)
            if score > max_score:
                max_score = score
                best_cand = cand
        return best_cand

    return expanded


def open_application(app, target=""):
    """Universally launch any application with optional target (file, directory, URL).
    Prioritizes xdg-open for files when the specified app is not installed or when opening files/URLs.
    """
    try:
        app_clean = (app or "").strip().lower()
        target_str = (target or "").strip()

        # Resolve target argument (cleaning tildes, markdown ellipses, or truncated paths)
        target_arg = ""
        resolved_target = ""
        if target_str:
            resolved_target = _resolve_fuzzy_target_path(target_str)
            target_arg = f"'{resolved_target}'"

        # Map common aliases to real binary names
        aliases = {
            "chrome": "google-chrome-stable",
            "google-chrome": "google-chrome-stable",
            "zed": "zeditor",
            "code": "code",
            "terminal": "kitty",
            "files": "nautilus",
            "browser": "firefox",
        }
        binary = aliases.get(app_clean, app_clean)

        # Generic requests or missing specific player (e.g. mpv not installed)
        generic_app_names = {
            "", "default", "xdg-open", "open", "media", "player", "media_player",
            "reproductor", "reproduce", "audio", "video", "music", "musica"
        }

        # Check if the requested binary is actually available on the system PATH or in desktop cache
        desktop_cache = _get_desktop_cache()
        is_binary_available = bool(shutil.which(binary)) or (binary in desktop_cache)

        # 1. PRIMARY ROUTE: If opening a file/URL and either the requested app is generic,
        # or the requested binary does NOT exist on the system (e.g. mpv), route directly via xdg-open.
        # This respects NixOS xdg.mimeApps.defaultApplications (e.g. qmmp for audio, vlc for video, zed, papers).
        if target_str and (binary in generic_app_names or not is_binary_available):
            if shutil.which("xdg-open"):
                cmd = f"uwsm app -- xdg-open {target_arg}".strip()
                subprocess.Popen(
                    cmd,
                    shell=True,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                )
                return f"Application launched in background (via xdg-open): {cmd}"

        # 2. If no target was given and the app is not installed, report clearly
        if not is_binary_available:
            return f"Error: Application '{app}' is not installed on this system."

        # Check if the app is a TUI app (via desktop entry Terminal=true or common list)
        known_tui = {"yazi", "btop", "htop", "lazygit", "lazyssh", "herdr", "nvtop", "ranger", "neovim", "nvim"}
        is_terminal_app = binary in known_tui
        if not is_terminal_app:
            for name, meta in desktop_cache.items():
                if binary == name or binary in name:
                    if meta.get("is_terminal"):
                        is_terminal_app = True
                    break

        # Build final execution command
        if is_terminal_app:
            cmd = f"uwsm app -- kitty {binary} {target_arg}".strip()
        else:
            cmd = f"uwsm app -- {binary} {target_arg}".strip()

        subprocess.Popen(
            cmd,
            shell=True,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        return f"Application launched in background: {cmd}"
    except Exception as e:
        return f"Error launching application '{app}': {e}"


def execute_command(command):
    try:
        cmd_str = command.strip().rstrip("&").strip()

        # Terminal UI (TUI) applications that require a terminal emulator (Kitty) to display
        tui_apps = [
            "yazi",
            "btop",
            "htop",
            "lazygit",
            "lazyssh",
            "herdr",
            "nvtop",
            "ranger",
            "neovim",
            "nvim",
        ]

        # Common GUI applications intended to run as detached desktop processes
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
            "zeditor",
            "spotify",
            "steam",
            "discord",
            "telegram-desktop",
            "obsidian",
        ]

        # 1. Check if it's a TUI app that needs to be launched inside Kitty
        # Also clean invalid flags invented by LLMs (e.g. yazi does not have '-d', it takes paths positionally)
        if "yazi -d " in cmd_str:
            cmd_str = cmd_str.replace("yazi -d ", "yazi ")

        is_tui = any(
            cmd_str == app or cmd_str.startswith(f"{app} ")
            for app in tui_apps
        )
        if is_tui:
            cmd_str = f"kitty {cmd_str}"

        # 2. Check if it is a desktop app (GUI or wrapped TUI) that should launch under uwsm app
        is_desktop_app = (
            cmd_str.startswith("uwsm app --")
            or is_tui
            or any(
                cmd_str == app
                or cmd_str.startswith(f"{app} ")
                or f" {app} " in cmd_str
                for app in gui_launchers
            )
        )

        if is_desktop_app:
            # Wrap with 'uwsm app --' if on Hyprland/Wayland and not already prefixed
            if not cmd_str.startswith("uwsm app --"):
                cmd_str = f"uwsm app -- {cmd_str}"

            # Launch detached in a new session and redirect std streams
            subprocess.Popen(
                cmd_str,
                shell=True,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            return f"Application launched in background: {cmd_str}"

        # 3. Regular non-GUI command execution
        res = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=10
        )
        combined = (res.stdout + res.stderr).strip()
        if not combined:
            if res.returncode == 0:
                return "Command executed successfully with no errors (exit code 0)."
            else:
                return f"Command finished with exit code {res.returncode}."
        return combined[:3000]
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


def get_sicos_shortcuts():
    """Retrieve and format active Hyprland keybindings dynamically."""
    try:
        res = subprocess.run(["hyprctl", "binds", "-j"], capture_output=True, text=True, timeout=3)
        if res.returncode != 0:
            return "Could not retrieve keybindings from hyprctl."
        binds = json.loads(res.stdout)
        lines = []
        for b in binds:
            if not b.get("has_description") or "mouse" in b.get("dispatcher", "").lower():
                continue
            m = b.get("modmask", 0)
            mods = []
            if (m // 64) % 2 >= 1:
                mods.append("Super")
            if (m // 4) % 2 >= 1:
                mods.append("Ctrl")
            if (m // 8) % 2 >= 1:
                mods.append("Alt")
            if (m // 1) % 2 >= 1:
                mods.append("Shift")
            key = b.get("key", "")
            if key in ("SUPER_L", "SUPER_R"):
                key = "Super"
            elif not key:
                key = f"code:{b.get('keycode')}"
            
            if key not in mods:
                combo = "+".join(mods + [key]) if mods else key
            else:
                combo = "+".join(mods)
            desc = b.get("description", "Unknown action")
            lines.append(f"- **{combo}**: {desc}")
        
        unique_lines = sorted(list(set(lines)))
        return "# Hyprland Keybindings (Live from Compositor)\n\n" + "\n".join(unique_lines)
    except Exception as e:
        return f"Error reading shortcuts: {e}"


def get_system_knowledge(topic="index"):
    """Query modular Markdown knowledge base or live shortcuts."""
    topic_clean = topic.strip().lower()
    if topic_clean in ("shortcuts", "binds", "keybindings"):
        return get_sicos_shortcuts()

    # Topic file mapping
    valid_topics = {
        "index": "index.md",
        "hardware": "hardware.md",
        "audio": "hardware.md",
        "sound": "hardware.md",
        "volume": "hardware.md",
        "brightness": "hardware.md",
        "monitors": "hardware.md",
        "apps": "apps.md",
        "applications": "apps.md",
        "tools": "apps.md",
        "hyprland": "hyprland.md",
        "desktop": "hyprland.md",
        "windows": "hyprland.md",
    }

    target_filename = valid_topics.get(topic_clean, f"{topic_clean}.md")
    
    # 1. Check user knowledge (~/.config/assistant/knowledge/)
    user_file = os.path.join(KNOWLEDGE_DIR, target_filename)
    if os.path.exists(user_file):
        try:
            with open(user_file, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            return f"Error reading {user_file}: {e}"

    # 2. Check factory knowledge (src/knowledge/)
    factory_file = os.path.join(SYSTEM_KNOWLEDGE_DIR, target_filename)
    if os.path.exists(factory_file):
        try:
            with open(factory_file, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            return f"Error reading {factory_file}: {e}"

    return (
        f"Topic '{topic}' not found. Available topics: 'index', 'hardware' (audio/brightness), "
        "'apps' (installed tools), 'hyprland' (desktop & layouts), 'shortcuts' (live keybindings)."
    )


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
            self.status_signal.emit("󰓃 Speaking...", "#59c2ff")
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
                self.status_signal.emit("󰓃 Speaking...", "#59c2ff")
                self.current_process = subprocess.Popen(
                    ["piper", "--model", model, "--output_file", audio_path],
                    stdin=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                self.current_process.communicate(input=self.text.encode("utf-8"))
            else:
                self.status_signal.emit("󰀦 Missing Voice Model", "#f07178")
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


# Maximum number of chained tool-execution rounds per user request. Strong
# agentic models (e.g. GLM-5.3-Flash) often chain several searches before
# answering; the final allowed round withholds the tools schema to force a
# plain text answer instead of letting the model call tools indefinitely.
MAX_TOOL_ROUNDS = 4


def extract_reasoning(delta):
    """Extract reasoning_content from a streaming delta, when present.

    Reasoning models (GLM / DeepSeek style) stream their chain of thought in a
    dedicated field before the visible content. The OpenAI SDK surfaces it as
    an extra attribute on the delta object, not as a declared field.
    """
    extra = getattr(delta, "model_extra", None)
    if not extra:
        return None
    reasoning = extra.get("reasoning_content")
    if reasoning is None:
        reasoning = extra.get("reasoning")  # OpenRouter-style alias
    return reasoning if isinstance(reasoning, str) else None


def dispatch_tool(tool_name, args, execute_command_safe=None):
    """Execute a single tool call by name.

    Returns a tuple ``(results_text, is_silent)``. ``is_silent`` marks desktop
    operations that succeeded without user-facing output (app launches, volume
    or brightness changes, window management), which let the generator finish
    immediately without extra conversational chatter.
    """
    if execute_command_safe is None:
        execute_command_safe = execute_command

    if tool_name == "get_current_time":
        return get_current_time(args.get("timezone", "")), False
    if tool_name == "open_application":
        results = open_application(args.get("app", ""), args.get("target", ""))
        return results, results.startswith("Application launched in background:")
    if tool_name == "get_weather":
        return get_weather(args.get("location", "")), False
    if tool_name == "web_search":
        return perform_web_search(args.get("query", "")), False
    if tool_name == "read_file":
        return read_file(args.get("path", "")), False
    if tool_name == "write_file":
        return write_file(args.get("path", ""), args.get("content", "")), True
    if tool_name == "replace_in_file":
        return (
            replace_in_file(
                args.get("path", ""),
                args.get("target", ""),
                args.get("replacement", ""),
            ),
            True,
        )
    if tool_name == "obsidian_search":
        return search_obsidian(args.get("query", "")), False
    if tool_name == "obsidian_read":
        return read_obsidian(args.get("filename", "")), False
    if tool_name == "obsidian_write":
        return write_obsidian(args.get("filename", ""), args.get("content", "")), True
    if tool_name == "manage_desktop":
        results = manage_desktop(
            args.get("action", ""),
            args.get("target", ""),
            args.get("window_address", ""),
        )
        # Successful window/workspace dispatch stays silent; failures are surfaced.
        return results, results.startswith("Success")
    if tool_name == "get_system_knowledge":
        return get_system_knowledge(args.get("topic", "index")), False
    if tool_name == "execute_command":
        cmd = args.get("command", "")
        results = execute_command_safe(cmd)
        # Only state-changing commands may end the request silently. Read-only
        # inspection commands (hyprctl clients/binds/monitors, wpctl status,
        # brightnessctl info, ...) must keep the agentic loop alive so the model
        # can use their results in the next round; marking them silent would
        # terminate the request mid-investigation with an invisible "Hecho.".
        cmd_clean = cmd.strip()
        parts = cmd_clean.split()
        is_app_launch = results.startswith("Application launched in background:")
        is_action = False
        if len(parts) > 1:
            if parts[0] == "hyprctl":
                # dispatch/keyword/reload/... change compositor state; everything
                # else (clients, binds, monitors, getoption, ...) is a read query.
                is_action = parts[1] in (
                    "dispatch",
                    "keyword",
                    "reload",
                    "setcursor",
                    "movecursortocorner",
                    "exit",
                )
            elif parts[0] == "wpctl":
                # set-volume / set-mute / set-default change state; status/get-volume are reads.
                is_action = parts[1].startswith("set-")
            elif parts[0] == "brightnessctl":
                is_action = " set" in cmd_clean
        no_error = "error" not in results.lower()
        silent = is_app_launch or (is_action and no_error)
        return results, silent
    if tool_name in mcp_manager.tools:
        return mcp_manager.call_tool(tool_name, args), False

    # Unknown tool: report back instead of dropping the call silently.
    return "Done.", False


class AIGeneratorThread(QThread):
    status_signal = pyqtSignal(str, str)
    active_model_signal = pyqtSignal(str)
    new_bubble_signal = pyqtSignal(str, str)
    append_text_signal = pyqtSignal(str)
    sys_message_signal = pyqtSignal(str, str)
    require_approval_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(str)
    history_save_signal = pyqtSignal()

    SAFE_COMMANDS = [
        "uwsm",
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
        "yazi",
        "kitty",
        "firefox",
        "google-chrome",
        "google-chrome-stable",
        "nautilus",
        "zed",
        "zeditor",
    ]

    def __init__(self, messages, model_config):
        super().__init__()
        self.messages = messages
        self.model_config = model_config
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

            # Select active client and model from configured model spec
            active_client = self.model_config.get("client")
            active_model = self.model_config.get("name")
            model_key = self.model_config.get("key", active_model)

            api_kwargs = {
                "model": active_model,
                "messages": temp_messages,
                "temperature": 0.0,
                "stream": True,
            }

            # Only inject the tools schema if the model is processing text-only context
            if not has_image:
                # Extract the last user message text and previous assistant context for semantic decision routing
                last_user_query = ""
                recent_context = ""
                for i in range(len(temp_messages) - 1, -1, -1):
                    msg = temp_messages[i]
                    if msg.get("role") == "user" and not last_user_query:
                        c = msg.get("content")
                        if isinstance(c, str):
                            last_user_query = c
                    elif msg.get("role") == "assistant" and not recent_context:
                        c = msg.get("content")
                        if isinstance(c, str):
                            recent_context = c
                    if last_user_query and recent_context:
                        break

                routed_tools, d_info = resolve_tools_for_query(
                    last_user_query,
                    recent_context=recent_context,
                    base_url=self.model_config.get("base_url", ""),
                )
                if routed_tools:
                    api_kwargs["tools"] = routed_tools

            self.status_signal.emit("󰟷 Thinking...", "#59c2ff")
            self.active_model_signal.emit(f"{model_key} ({active_model})")
            logger.info(
                f"[LLM] Request to '{model_key}' ({active_model}) | "
                f"tools={len(api_kwargs.get('tools', []))}"
            )
            # Agentic loop: stream a response, execute any requested tools, and feed
            # the results back for another round until the model answers with plain
            # text or the safety cap is reached. Strong agentic models (e.g.
            # GLM-5.3-Flash) chain several tool rounds; the previous fixed 2-turn
            # flow silently dropped any chained tool call it did not know about.
            last_status_emit = 0.0
            for round_num in range(MAX_TOOL_ROUNDS + 1):
                request_kwargs = {
                    "model": active_model,
                    "messages": temp_messages,
                    "temperature": 0.0,
                    "stream": True,
                }
                # On the final forced round the tools schema is withheld so the
                # model must answer with text instead of chaining another call.
                if api_kwargs.get("tools") and round_num < MAX_TOOL_ROUNDS:
                    request_kwargs["tools"] = api_kwargs["tools"]
                elif api_kwargs.get("tools"):
                    logger.info(
                        f"[LLM] Round {round_num + 1}: tool round cap reached "
                        f"-> forcing text-only answer"
                    )

                try:
                    response = active_client.chat.completions.create(**request_kwargs)
                except openai.APIConnectionError:
                    self.sys_message_signal.emit(
                        "sys",
                        f"󰀦 Error: No se pudo conectar al endpoint de '{model_key}' ({active_model}) en {self.model_config.get('base_url')}.",
                    )
                    self.finished_signal.emit("")
                    return
                except Exception as req_err:
                    self.sys_message_signal.emit(
                        "sys", f"󰀦 Error en '{model_key}': {str(req_err)}"
                    )
                    self.finished_signal.emit("")
                    return

                tool_calls, full_content, full_reasoning = [], "", ""
                bubble_created = False

                buffer = ""
                last_emit = time.time()

                for chunk in response:
                    if self._is_stopped:
                        break
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta

                    # Reasoning models stream their chain of thought in a separate
                    # field: surface it as live status feedback instead of leaving
                    # the UI frozen on "Thinking..." with no visible progress.
                    reasoning_delta = extract_reasoning(delta)
                    if reasoning_delta:
                        full_reasoning += reasoning_delta
                        if time.time() - last_status_emit > 0.5:
                            snippet = full_reasoning[-48:].replace("\n", " ").strip()
                            self.status_signal.emit(
                                f"󰟷 Thinking… {snippet}", "#59c2ff"
                            )
                            last_status_emit = time.time()

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

                if full_reasoning:
                    logger.info(
                        f"[LLM] Round {round_num + 1}: model reasoning "
                        f"({len(full_reasoning)} chars): {full_reasoning[:300]!r}"
                    )

                # Fallback parser: if the model streamed raw XML-like tool call syntax instead of OpenAI tool_calls structure.
                # Only applies on rounds where tools were actually offered; on the
                # forced final round any tool-call-like output is a hallucination
                # and must be shown as plain text instead of being executed.
                tools_offered = bool(request_kwargs.get("tools"))
                if tools_offered and not tool_calls and "<|tool_call>" in full_content:
                    raw_call_match = re.search(r"<\|tool_call>.*?call:([a-zA-Z0-9_]+)\((.*?)\).*?<tool_call\|>", full_content)
                    if raw_call_match:
                        parsed_name = raw_call_match.group(1).strip()
                        parsed_arg_str = raw_call_match.group(2).strip()
                        parsed_args = {}
                        try:
                            import ast
                            expr = ast.parse(f"func({parsed_arg_str})")
                            for kw in expr.body[0].value.keywords:
                                parsed_args[kw.arg] = ast.literal_eval(kw.value)
                        except Exception as pe:
                            print(f"[Tool Parser] Error parsing raw args '{parsed_arg_str}': {pe}")

                        tool_calls.append({
                            "id": f"call_raw_{int(time.time())}",
                            "type": "function",
                            "function": {
                                "name": parsed_name,
                                "arguments": json.dumps(parsed_args),
                            },
                        })
                        # Strip the raw tool call markup from user-facing text
                        clean_display = re.sub(r"<\|tool_call>.*?<tool_call\|>", "", full_content).strip()
                        full_content = clean_display

                if not tool_calls:
                    # Plain text answer: the agentic request is complete.
                    if full_content.strip():
                        self.messages.append(
                            {"role": "assistant", "content": full_content}
                        )
                        logger.info(
                            f"[LLM] Round {round_num + 1}: answer ({len(full_content)} chars)"
                        )
                    else:
                        logger.warning(
                            f"[LLM] Round {round_num + 1}: model returned no content and no tool calls"
                        )
                    break

                # --- Tool execution round (any tool, chained until an answer) ---
                if full_content.strip():
                    temp_messages.append({"role": "assistant", "content": full_content})

                tool_results_text = ""
                all_tools_silent = True

                for tool_call in tool_calls:
                    if self._is_stopped:
                        break

                    tool_name = tool_call["function"]["name"]
                    try:
                        args = json.loads(tool_call["function"]["arguments"])
                    except Exception:
                        args = {}

                    logger.info(
                        f"[LLM] Round {round_num + 1}: executing '{tool_name}' "
                        f"with {json.dumps(args, ensure_ascii=False)[:200]}"
                    )
                    self.sys_message_signal.emit("sys", f"󰑮 Running {tool_name}...")

                    results, tool_silent = dispatch_tool(
                        tool_name, args, execute_command_safe=self.execute_command_safe
                    )
                    if not tool_silent:
                        all_tools_silent = False
                    tool_results_text += f"\n--- {tool_name} ---\n{results}\n"

                if self._is_stopped:
                    self.finished_signal.emit("")
                    return

                # If all executed actions were silent desktop operations that succeeded, finish immediately!
                if all_tools_silent:
                    logger.info(
                        f"[LLM] Round {round_num + 1}: all tools silent -> "
                        f"finishing with silent 'Hecho.' (no visible answer)"
                    )
                    # Append a concise, natural confirmation to conversation memory so the LLM knows this request
                    # was resolved without mimicking pseudo-code or leaving consecutive user turns.
                    self.messages.append(
                        {
                            "role": "assistant",
                            "content": "Hecho.",
                            "is_silent": True,
                        }
                    )
                    self.history_save_signal.emit()
                    self.finished_signal.emit("")
                    return

                temp_messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"[SYSTEM RESULTS]:\n{tool_results_text}\n"
                            "Reply directly based on these results. "
                            "CRITICAL: If the user asked you to play, open, or run something and you just located the file, "
                            "you MUST invoke `open_application(app='...', target='...')` in this turn to actually open it! "
                            "Do NOT just reply saying you found it or that you are going to play it without executing the tool."
                        ),
                    }
                )

            self.history_save_signal.emit()
            self.finished_signal.emit(full_content)

        except openai.APIConnectionError:
            logger.error("[LLM] Connection to the LLM server failed")
            self.sys_message_signal.emit(
                "sys", "󰀦 Error: The Local LLM Server is offline."
            )
            self.finished_signal.emit("The local model server is offline.")
        except Exception as e:
            logger.error(f"[LLM] Unhandled generator error: {e}", exc_info=True)
            self.sys_message_signal.emit("sys", f"[API ERROR]: {str(e)}")
            self.finished_signal.emit("")


## BACKEND CONTROLLER (Bridged to QML)
class Backend(QObject):
    statusChanged = pyqtSignal(str, str)
    activeModelChanged = pyqtSignal(str)
    availableModelsChanged = pyqtSignal(list)
    messageAdded = pyqtSignal(str, str)
    messageAppended = pyqtSignal(str)
    chatCleared = pyqtSignal()
    inputStateChanged = pyqtSignal(bool)
    stopButtonStateChanged = pyqtSignal(bool)
    voiceStateChanged = pyqtSignal(bool)
    ttsStateChanged = pyqtSignal(bool)
    requireApproval = pyqtSignal(str)
    clearApproval = pyqtSignal()

    def __init__(self, initial_query, launch_mode):
        super().__init__()
        self.is_voice_mode = launch_mode == "--voice"
        self.tts_enabled = False  # Disabled by default on startup as requested
        self.is_recording = False
        self.rec_process = None
        self.messages = []
        self.active_model_key = ACTIVE_MODEL_KEY

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

    def get_models_list(self):
        """
        Return structured list of items (headers and model entries) for the QML popup.
        Ordered by:
          1. Config Presets (Favorites / Defaults)
          2. Discovered Models grouped by provider / endpoint
        """
        res = []

        # 1. Configured Presets Header & Items
        presets = [info for info in MODELS_CATALOG.values() if info.get("is_preset")]
        if presets:
            res.append({
                "is_header": True,
                "header_title": "󰚩 MODELOS CONFIGURADOS",
                "key": "",
                "name": "",
                "url": "",
                "active": False,
            })
            for info in presets:
                k = info.get("key")
                res.append({
                    "is_header": False,
                    "header_title": "",
                    "key": k,
                    "name": info.get("name", k),
                    "url": info.get("base_url", ""),
                    "active": (k == self.active_model_key),
                })

        # 2. Group Discovered Models by Provider
        discovered_by_provider = {}
        for info in MODELS_CATALOG.values():
            if not info.get("is_preset"):
                p_name = info.get("provider_name", "Endpoint Local")
                b_url = info.get("base_url", "")
                group_key = f"{p_name} ({b_url})"
                discovered_by_provider.setdefault(group_key, []).append(info)

        for group_header, items in discovered_by_provider.items():
            res.append({
                "is_header": True,
                "header_title": f"󰒋 {group_header.upper()}",
                "key": "",
                "name": "",
                "url": "",
                "active": False,
            })
            for info in items:
                k = info.get("key")
                res.append({
                    "is_header": False,
                    "header_title": "",
                    "key": k,
                    "name": info.get("name", k),
                    "url": info.get("base_url", ""),
                    "active": (k == self.active_model_key),
                })

        return res

    def emit_initial_state(self):
        curr_cfg = MODELS_CATALOG.get(self.active_model_key, {})
        curr_name = curr_cfg.get("name", self.active_model_key)
        self.activeModelChanged.emit(f"{self.active_model_key} ({curr_name})")
        self.availableModelsChanged.emit(self.get_models_list())
        self.voiceStateChanged.emit(self.is_recording or self.is_voice_mode)
        self.ttsStateChanged.emit(self.tts_enabled)
        if self.is_voice_mode:
            self.statusChanged.emit("󰍬 Voice Mode Active", "#7fd962")
        for msg in self.messages:
            if msg["role"] != "system" and not msg.get("is_silent"):
                role_str = "ai" if msg["role"] == "assistant" else "user"
                self.messageAdded.emit(role_str, msg["content"])

        if len(sys.argv) > 1 and sys.argv[1].strip():
            self.submit_query(sys.argv[1])

    def _build_system_prompt(self):
        system_content = (
            "You are Assistant, an agentic AI desktop assistant with direct system access running on SicOS (NixOS + Hyprland).\n"
            "- CRITICAL RULE FOR FILES: When the user asks to create, write, modify, read, or replace content in a file, "
            "you MUST call `write_file`, `read_file`, `replace_in_file`, or `execute_command`. "
            "NEVER claim or pretend you created or edited a file in your response text without invoking the corresponding tool first.\n"
            "- CRITICAL RULE FOR APPLICATIONS: When the user asks to open or launch an application or file (e.g. 'abre yazi en Downloads', 'abre chrome', 'abre zed en mi proyecto'), "
            "you MUST call `open_application(app='...', target='...')`. Do NOT use execute_command for launching applications.\n"
            "- CRITICAL RULE FOR HARDWARE & SYSTEM: Audio volume uses `wpctl set-volume @DEFAULT_AUDIO_SINK@ <level>` (never amixer/pactl). "
            "Screen brightness uses `brightnessctl set <level>`. When in doubt or to discover installed apps, layouts, or keybindings, "
            "call `get_system_knowledge(topic='index'|'hardware'|'apps'|'hyprland'|'shortcuts')`.\n"
            "- For Hyprland workspace and window actions, use `manage_desktop`.\n"
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
            f"󰋖 **Comandos disponibles (v{APP_VERSION}):**\n\n"
            "- **/new** o **/reset**: Inicia una nueva sesión desde cero, eliminando el contexto y limpiando el chat.\n"
            "- **/clear**: Limpia los mensajes en pantalla y vacía la memoria de la conversación.\n"
            "- **/model**: Muestra los modelos configurados o alterna al siguiente. Usa `/model <nombre>` para cambiar a uno específico.\n"
            "- **/voice** o **/tts**: Activa o desactiva la síntesis de voz en las respuestas.\n"
            "- **/about**: Muestra los detalles de versión y arquitectura de Assistant.\n"
            "- **/help**: Muestra esta información de ayuda.\n"
            "- **󰍬 Botón o tecla 'v'**: Pulsa el botón de micrófono o escribe 'v' y pulsa Enter para activar/desactivar el modo de voz.\n"
            "- **󰕾 / 󰝟 Botón de voz**: Pulsa el botón de altavoz junto a la ayuda para silenciar/activar la voz de respuesta."
        )
        self.messageAdded.emit("sys", help_text)

    @pyqtSlot()
    def show_about(self):
        """Display version and application info."""
        about_text = (
            f"󰘳 **Assistant v{APP_VERSION}**\n\n"
            "Asistente nativo para el escritorio Hyprland con soporte agéntico, MCP, voz y gestión de ventanas.\n\n"
            f"- **Versión**: `{APP_VERSION}`\n"
            "- **Framework**: PyQt6 + QML (Material Design)\n"
            "- **Pipeline**: FastFlowLM / llama.cpp + Edge-TTS/Piper + Whisper STT"
        )
        self.messageAdded.emit("sys", about_text)

    @pyqtSlot(str)
    def switch_model(self, target_key=""):
        """List or switch active LLM from the models catalog."""
        target = (target_key or "").strip().lower()
        keys = list(MODELS_CATALOG.keys())

        if not target:
            # If there's more than one, cycle to the next
            if len(keys) > 1:
                curr_idx = keys.index(self.active_model_key) if self.active_model_key in keys else 0
                next_idx = (curr_idx + 1) % len(keys)
                self.active_model_key = keys[next_idx]
                curr_cfg = MODELS_CATALOG[self.active_model_key]
                m_name = curr_cfg.get("name", self.active_model_key)
                self.activeModelChanged.emit(f"{self.active_model_key} ({m_name})")
                self.availableModelsChanged.emit(self.get_models_list())
                self.messageAdded.emit("sys", f"🔄 Modelo cambiado a: **{self.active_model_key}** (`{m_name}`)")
                return

        # Direct match or listing
        matched_key = None
        for k in keys:
            if target == k.lower() or target == MODELS_CATALOG[k].get("name", "").lower():
                matched_key = k
                break

        if matched_key:
            self.active_model_key = matched_key
            curr_cfg = MODELS_CATALOG[matched_key]
            m_name = curr_cfg.get("name", matched_key)
            self.activeModelChanged.emit(f"{matched_key} ({m_name})")
            self.availableModelsChanged.emit(self.get_models_list())
            self.messageAdded.emit("sys", f"🔄 Modelo seleccionado: **{matched_key}** (`{m_name}`)")
        else:
            lines = ["🤖 **Modelos disponibles en catálogo:**"]
            for k in keys:
                m_info = MODELS_CATALOG[k]
                m_name = m_info.get("name", k)
                status = " **[ACTIVO]**" if k == self.active_model_key else ""
                lines.append(f"- • `{k}`: {m_name} ({m_info.get('base_url')}){status}")
            lines.append("\nUso: `/model <nombre>` o solo `/model` para alternar al siguiente.")
            self.messageAdded.emit("sys", "\n".join(lines))

    @pyqtSlot()
    def save_history(self):
        tail = [m for m in self.messages if m.get("role") != "system"]
        history_to_save = [self.messages[0]] + tail[-8:]
        with open(HISTORY_FILE, "w") as f:
            json.dump(history_to_save, f)

    def stop_media_playback(self):
        """Stop any active audio output or TTS generation without resetting voice mode."""
        if hasattr(self, "tts_thread") and self.tts_thread is not None:
            if self.tts_thread.isRunning():
                self.tts_thread.stop()

        subprocess.run(["pkill", "-x", "pw-play"], stderr=subprocess.DEVNULL)
        subprocess.run(["pkill", "-x", "piper"], stderr=subprocess.DEVNULL)
        subprocess.run(["pkill", "-x", "edge-tts"], stderr=subprocess.DEVNULL)

    @pyqtSlot()
    def interrupt_ai(self):
        if hasattr(self, "ai_thread") and self.ai_thread is not None:
            if self.ai_thread.isRunning():
                self.ai_thread.stop()

        self.stop_media_playback()

        self.is_recording = False
        if self.rec_process:
            try:
                self.rec_process.terminate()
            except:
                pass

        subprocess.run(["pkill", "-x", "rec"], stderr=subprocess.DEVNULL)
        subprocess.run(["pkill", "-x", "whisper-cli"], stderr=subprocess.DEVNULL)

        self.statusChanged.emit("Idle", "#828c99")
        self.inputStateChanged.emit(True)
        self.stopButtonStateChanged.emit(False)
        self.is_voice_mode = False
        self.voiceStateChanged.emit(False)
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
        clean_cmd = text.strip()
        lower_cmd = clean_cmd.lower()
        if lower_cmd in ["/new", "/reset"]:
            self.reset_session()
            return
        elif lower_cmd == "/clear":
            self.reset_session()
            return
        elif lower_cmd == "/help":
            self.show_help()
            return
        elif lower_cmd == "/about":
            self.show_about()
            return
        elif lower_cmd in ["/voice", "/tts"]:
            self.toggleTTS()
            return
        elif lower_cmd == "/model" or lower_cmd.startswith("/model "):
            parts = clean_cmd.split(maxsplit=1)
            arg = parts[1].strip() if len(parts) > 1 else ""
            self.switch_model(arg)
            return

        self.interrupt_ai()
        self.is_voice_mode = False
        self.statusChanged.emit("Idle", "#828c99")
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
    def toggleTTS(self):
        """Toggle text-to-speech spoken output on/off."""
        self.tts_enabled = not self.tts_enabled
        if not self.tts_enabled:
            self.stop_media_playback()
        self.ttsStateChanged.emit(self.tts_enabled)
        state_str = "activada" if self.tts_enabled else "desactivada"
        icon_str = "󰕾" if self.tts_enabled else "󰝟"
        self.messageAdded.emit("sys", f"{icon_str} Voz de respuesta (TTS) **{state_str}**.")

    @pyqtSlot()
    def toggleVoiceRecording(self):
        if self.is_recording or self.is_voice_mode:
            self.interrupt_ai()
        else:
            self.startVoiceRecording()

    @pyqtSlot()
    def startVoiceRecording(self):
        self.stop_media_playback()
        if self.is_recording:
            return
        self.is_recording = True
        self.is_voice_mode = True
        self._input_enabled = False
        self.inputStateChanged.emit(False)
        self.stopButtonStateChanged.emit(True)
        self.voiceStateChanged.emit(True)
        self.statusChanged.emit("󰍬 Listening... (Say 'Stop' to end)", "#7fd962")
        subprocess.run(
            ["pw-play", os.path.join(SOUND_DIR, "start.wav")], stderr=subprocess.DEVNULL
        )
        threading.Thread(target=self.record_with_silence).start()

    def stop_recording(self):
        if self.is_recording and self.rec_process:
            self.is_recording = False
            self.rec_process.terminate()

    def record_with_silence(self):
        silence_duration = str(VOICE_CONF.get("silence_duration", "0.8"))
        silence_threshold = str(VOICE_CONF.get("silence_threshold", "1%"))
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
                silence_threshold,
                "1",
                silence_duration,
                silence_threshold,
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
        self.statusChanged.emit("󰑮 Transcribing...", "#e5b454")
        wav_file = os.path.join(TEMP_DIR, "voice_reply.wav")
        transcript = ""

        # Validate that the recorded audio contains actual sound and is long enough
        if not os.path.exists(wav_file):
            self._handle_no_speech()
            return

        try:
            import wave, struct, math
            with wave.open(wav_file, "rb") as wf:
                n_frames = wf.getnframes()
                rate = wf.getframerate()
                duration = n_frames / float(rate) if rate > 0 else 0
                raw_data = wf.readframes(n_frames)
                
            # If the recording is shorter than 0.4s or empty, treat as no speech
            if duration < 0.4 or len(raw_data) < 4:
                logger.info(f"[ASR] Audio too short ({duration:.2f}s), skipping ASR.")
                self._handle_no_speech()
                return

            shorts = struct.unpack("<" + "h" * (len(raw_data) // 2), raw_data)
            rms = math.sqrt(sum(s * s for s in shorts) / len(shorts))
            logger.info(f"[ASR] Recorded audio: {duration:.2f}s, RMS volume: {rms:.1f}")

            # If the audio is essentially pure silence/background noise (RMS < 120), skip
            if rms < 120.0:
                logger.info(f"[ASR] Audio energy too low ({rms:.1f} < 120), ignoring silence.")
                self._handle_no_speech()
                return
        except Exception as e:
            logger.warning(f"[ASR] Error checking audio energy: {e}")

        # Check ASR configuration (e.g. FastFlowLM / OpenAI-compatible endpoint)
        asr_conf = VOICE_CONF.get("asr", {})
        asr_enabled = asr_conf.get("enabled", True) if isinstance(asr_conf, dict) else False
        asr_endpoint = asr_conf.get("endpoint") if isinstance(asr_conf, dict) else None

        if asr_enabled and asr_endpoint and os.path.exists(wav_file):
            try:
                import uuid
                boundary = uuid.uuid4().hex
                model_name = asr_conf.get("model", "whisper")
                timeout_val = float(asr_conf.get("timeout", 10.0))
                lang = asr_conf.get("language")

                with open(wav_file, "rb") as f:
                    wav_bytes = f.read()

                parts = []
                parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n{model_name}\r\n".encode("utf-8"))
                if lang:
                    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"language\"\r\n\r\n{lang}\r\n".encode("utf-8"))
                parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio.wav\"\r\nContent-Type: audio/wav\r\n\r\n".encode("utf-8"))
                parts.append(wav_bytes)
                parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
                body = b"".join(parts)

                headers = {
                    "Content-Type": f"multipart/form-data; boundary={boundary}"
                }
                api_key = asr_conf.get("api_key")
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"

                req = urllib.request.Request(asr_endpoint, data=body, headers=headers)
                logger.info(f"[ASR] Sending audio ({len(wav_bytes)} bytes) to ASR endpoint: {asr_endpoint}")
                with urllib.request.urlopen(req, timeout=timeout_val) as resp:
                    resp_json = json.loads(resp.read().decode("utf-8"))
                    transcript = resp_json.get("text", "").strip()
                    logger.info(f"[ASR] Remote transcription successful: '{transcript}'")
            except Exception as e:
                logger.warning(f"[ASR] Remote transcription failed, falling back to local whisper-cli: {e}")
                transcript = ""

        # Fallback to local whisper-cli if remote ASR was not used or failed
        if not transcript and os.path.exists(wav_file):
            whisper_model = os.path.expanduser(
                VOICE_CONF.get("whisper_model")
                or "~/.local/share/assistant/models/ggml-small.bin"
            )
            if os.path.exists(whisper_model):
                logger.info(f"[ASR] Running local whisper-cli with model: {whisper_model}")
                res = subprocess.run(
                    [
                        "whisper-cli",
                        "-m",
                        whisper_model,
                        "-f",
                        wav_file,
                        "-nt",
                        "-l",
                        "auto",
                    ],
                    capture_output=True,
                    text=True,
                )
                transcript = re.sub(r"\[.*?\]", "", res.stdout).strip()
            else:
                logger.warning(f"[ASR] Local whisper model not found: {whisper_model}")

        transcript = re.sub(r"\[.*?\]", "", transcript).strip()

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
                "gracias",
                "muchasgracias",
                "graciasporverelvideo",
                "graciasporver",
            ]
            if (
                any(clean_no_spaces == h or clean_no_spaces == h + "video" for h in hallucinations)
                or (any(h in clean_no_spaces for h in hallucinations) and len(transcript.split()) <= 4)
            ):
                logger.info(f"[ASR] Filtered out Whisper silence hallucination: '{transcript}'")
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
                self.statusChanged.emit("Idle", "#828c99")
                self.messageAdded.emit("sys", "󰓛 Conversation Ended.")
                self.is_voice_mode = False
                self.voiceStateChanged.emit(False)
                self._input_enabled = True
                self.inputStateChanged.emit(True)
                self.stopButtonStateChanged.emit(False)
                return
            self.submit_query(transcript)
        else:
            self._handle_no_speech()

    def _handle_no_speech(self):
        self.statusChanged.emit("Idle", "#828c99")
        self.messageAdded.emit("sys", "No speech detected.")
        self.is_voice_mode = False
        self.voiceStateChanged.emit(False)
        self._input_enabled = True
        self.inputStateChanged.emit(True)
        self.stopButtonStateChanged.emit(False)

    def submit_query(self, query):
        self._input_enabled = False
        self.inputStateChanged.emit(False)
        self.stopButtonStateChanged.emit(True)
        if DECISION_ENABLED:
            self.statusChanged.emit("󰘳 Routing...", "#ffb454")
        else:
            self.statusChanged.emit("󰟷 Thinking...", "#59c2ff")
        self.messageAdded.emit("user", query)

        content = process_prompt(query)
        self.messages.append({"role": "user", "content": content})

        if hasattr(self, "ai_thread") and self.ai_thread is not None:
            if self.ai_thread.isRunning():
                self.ai_thread.stop()
            self.ai_thread.deleteLater()

        selected_model_cfg = MODELS_CATALOG.get(self.active_model_key, next(iter(MODELS_CATALOG.values())))
        self.ai_thread = AIGeneratorThread(self.messages, selected_model_cfg)
        self.ai_thread.status_signal.connect(self.statusChanged.emit)
        self.ai_thread.active_model_signal.connect(self.activeModelChanged.emit)
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
        if full_response.strip() and self.tts_enabled:
            self.play_audio(full_response)
        elif self.is_voice_mode:
            # If in voice mode and the tool executed silently without spoken output, resume listening
            self.startVoiceRecording()
        else:
            self.statusChanged.emit("Idle", "#828c99")
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
            self.statusChanged.emit("Idle", "#828c99")
            self._input_enabled = True
            self.inputStateChanged.emit(True)
            self.stopButtonStateChanged.emit(False)


def create_tray_icon(color_hex):
    """Render a fully scaled and geometrically centered Nerd Font robot glyph for the System Tray."""
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    font = QFont("JetBrainsMono Nerd Font", 48)
    path = QPainterPath()
    path.addText(0, 0, font, "\U000f06a9")
    br = path.boundingRect()

    # Scale the glyph to fill the 64x64 icon box (with 2px edge safety padding) matching other tray icons
    target_dim = 60.0
    scale = target_dim / max(br.width(), br.height())

    # Center mathematically to avoid font baseline advance and glyph overhang clipping
    dx = (64.0 - br.width() * scale) / 2.0 - br.x() * scale
    dy = (64.0 - br.height() * scale) / 2.0 - br.y() * scale

    painter.translate(dx, dy)
    painter.scale(scale, scale)
    painter.fillPath(path, QColor(color_hex))
    painter.end()
    return QIcon(pix)


class TrayManager(QObject):
    """Manages the System Tray icon, states, and context menu."""

    def __init__(self, app, window, backend):
        super().__init__()
        self.app = app
        self.window = window
        self.backend = backend

        self.icon_idle = create_tray_icon("#828c99")
        self.icon_listening = create_tray_icon("#7fd962")
        self.icon_routing = create_tray_icon("#ffb454")
        self.icon_thinking = create_tray_icon("#59c2ff")

        self.tray = QSystemTrayIcon(self.icon_idle, self.app)
        self.tray.setToolTip("Assistant AI")

        self.menu = QMenu()
        self.action_maximize = QAction("Maximize", self.menu)
        self.action_maximize.triggered.connect(self.show_window)
        self.menu.addAction(self.action_maximize)

        self.action_talk = QAction("Talk", self.menu)
        self.action_talk.triggered.connect(self.toggle_talk)
        self.menu.addAction(self.action_talk)

        self.menu.addSeparator()

        self.action_exit = QAction("Exit", self.menu)
        self.action_exit.triggered.connect(self.exit_app)
        self.menu.addAction(self.action_exit)

        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.on_tray_activated)

        # Hook voice and status state changes to update icon and menu context
        self.backend.voiceStateChanged.connect(self.on_voice_state_changed)
        self.backend.statusChanged.connect(self.on_status_changed)

        self.tray.show()

    @pyqtSlot()
    def show_window(self):
        if self.window:
            self.window.show()
            self.window.raise_()
            self.window.requestActivate()

    @pyqtSlot()
    def toggle_talk(self):
        if self.backend.is_recording or self.backend.is_voice_mode:
            self.backend.interrupt_ai()
        else:
            self.backend.startVoiceRecording()

    @pyqtSlot(bool)
    def on_voice_state_changed(self, active):
        if active:
            self.tray.setIcon(self.icon_listening)
            self.action_talk.setText("Stop talking")
            self.tray.setToolTip("Assistant AI (Listening...)")
        else:
            self.action_talk.setText("Talk")
            if not (hasattr(self.backend, "ai_thread") and self.backend.ai_thread and self.backend.ai_thread.isRunning()):
                self.tray.setIcon(self.icon_idle)
                self.tray.setToolTip("Assistant AI")

    @pyqtSlot(str, str)
    def on_status_changed(self, text, color_hex):
        clean_text = text.lower()
        if "routing" in clean_text:
            self.tray.setIcon(self.icon_routing)
            self.tray.setToolTip(f"Assistant AI ({text})")
        elif "thinking" in clean_text or "running" in clean_text or "transcribing" in clean_text:
            self.tray.setIcon(self.icon_thinking)
            self.tray.setToolTip(f"Assistant AI ({text})")
        elif "listening" in clean_text or self.backend.is_recording:
            self.tray.setIcon(self.icon_listening)
            self.tray.setToolTip("Assistant AI (Listening...)")
        elif "idle" in clean_text or text == "Idle":
            self.tray.setIcon(self.icon_idle)
            self.tray.setToolTip("Assistant AI")

    @pyqtSlot(QSystemTrayIcon.ActivationReason)
    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            # Left mouse click
            self.show_window()

    @pyqtSlot()
    def exit_app(self):
        self.backend.interrupt_ai()
        self.tray.hide()
        self.app.quit()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    engine = QQmlApplicationEngine()

    initial_q = sys.argv[1] if len(sys.argv) > 1 else ""
    launch_m = sys.argv[2] if len(sys.argv) > 2 else ""
    backend = Backend(initial_q, launch_m)

    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(QML_FILE))

    if not engine.rootObjects():
        sys.exit(-1)

    window = engine.rootObjects()[0]
    tray_manager = TrayManager(app, window, backend)

    QTimer.singleShot(100, backend.emit_initial_state)

    sys.exit(app.exec())
