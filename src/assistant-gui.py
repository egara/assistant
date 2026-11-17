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
import requests
import urllib3
from langdetect import detect
from langdetect.lang_detect_exception import LangDetectException

from tools import (
    get_current_time,
    TIME_TOOLS,
    get_weather,
    WEATHER_TOOLS,
    search_obsidian,
    read_obsidian,
    write_obsidian,
    configure_obsidian,
    OBSIDIAN_TOOLS,
    deep_web_search,
    perform_web_search,
    WEB_TOOLS,
    read_file,
    write_file,
    replace_in_file,
    open_application,
    execute_command,
    get_desktop_cache,
    resolve_fuzzy_target_path,
    OS_TOOLS,
    get_system_knowledge,
    get_sicos_shortcuts,
    configure_knowledge,
    KNOWLEDGE_TOOLS,
    get_hyprland_version,
    load_hyprland_recipes,
    save_user_hyprland_recipe,
    discover_and_learn_action,
    build_manage_desktop_tool,
    manage_desktop,
    configure_hyprland,
)
from routing import DecisionRouter
from commands import create_default_registry, CommandContext
from mcp import MCPManager, MCP_AVAILABLE
from llm import ModelCatalog

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

# Configure knowledge module paths
configure_knowledge(user_dir=KNOWLEDGE_DIR, system_dir=SYSTEM_KNOWLEDGE_DIR)

# Configure hyprland module recipe file locations
configure_hyprland(system_knowledge_file=SYSTEM_KNOWLEDGE_FILE, user_knowledge_file=USER_KNOWLEDGE_FILE)

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
configure_obsidian(api_key=OBSIDIAN_API_KEY, url=OBSIDIAN_URL)

# ==========================================
# 🤖 MULTI-MODEL LLM MANAGER & CONFIGURATION
# ==========================================
model_catalog_manager = ModelCatalog(config.get("llm", {}))
MODELS_CATALOG = model_catalog_manager.catalog
DEFAULT_MODEL_KEY = model_catalog_manager.default_model_key
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
    except OSError:
        pass

mcp_manager = MCPManager(MCP_SERVERS_CONF)
mcp_tools = mcp_manager.discover_tools()

# Load once at startup: recipe actions drive the manage_desktop tool schema and
# _command_intents drives the silent/active classification of shell commands.
HYPRLAND_RECIPES = load_hyprland_recipes()
COMMAND_INTENTS = HYPRLAND_RECIPES.get("_command_intents", {})

## TOOLS & LOGIC
tools = (
    TIME_TOOLS
    + WEATHER_TOOLS
    + WEB_TOOLS
    + OBSIDIAN_TOOLS
    + OS_TOOLS
    + KNOWLEDGE_TOOLS
    + [build_manage_desktop_tool(HYPRLAND_RECIPES)]
    + mcp_tools
)

# ==========================================
# 🧭 DECISION ROUTER (d3 / System One)
# ==========================================
DECISION_CONF = config.get("decision_router", {})
decision_router = DecisionRouter(DECISION_CONF)
decision_router.register_mcp_tools([t["function"]["name"] for t in mcp_tools])
DECISION_ENABLED = decision_router.enabled


def resolve_tools_for_query(user_query, recent_context="", base_url=""):
    return decision_router.resolve_tools_for_query(
        user_query=user_query,
        available_tools=tools,
        recent_context=recent_context,
        base_url=base_url,
    )


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
        except (LangDetectException, Exception):
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
            except OSError:
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
        return perform_web_search(args.get("query", ""), mcp_manager=mcp_manager), False
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
        # The silent/active policy is data-driven from the Hyprland knowledge
        # base (_command_intents): a command only ends the request silently when
        # its binary matches a state-changing intent (e.g. 'hyprctl dispatch',
        # 'wpctl set-volume'). Read-only inspection commands keep the agentic
        # loop alive so the model can use their results to answer.
        cmd_clean = cmd.strip()
        parts = cmd_clean.split()
        is_app_launch = results.startswith("Application launched in background:")
        intent_tokens = COMMAND_INTENTS.get(parts[0], []) if len(parts) > 1 else []
        is_action = any(parts[1].startswith(tok) for tok in intent_tokens)
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
            executed_signatures_history = set()
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

                # --- Loop prevention: detect identical consecutive tool call sets ---
                round_signatures = []
                for tc in tool_calls:
                    fn_name = tc.get("function", {}).get("name", "")
                    fn_args = tc.get("function", {}).get("arguments", "")
                    round_signatures.append((fn_name, fn_args))
                current_round_sig = tuple(round_signatures)

                if current_round_sig in executed_signatures_history:
                    logger.warning(
                        f"[LLM] Round {round_num + 1}: detected repetitive tool call loop "
                        f"{current_round_sig} -> stopping loop"
                    )
                    if full_content.strip():
                        self.messages.append({"role": "assistant", "content": full_content})
                    break
                executed_signatures_history.add(current_round_sig)

                # --- Tool execution round (standard OpenAI tool calling format) ---
                clean_tool_calls = []
                for i, tc in enumerate(tool_calls):
                    call_id = tc.get("id") or f"call_{round_num}_{i}_{int(time.time())}"
                    tc["id"] = call_id
                    clean_tool_calls.append({
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"],
                        },
                    })

                assistant_msg = {
                    "role": "assistant",
                    "content": full_content if full_content.strip() else None,
                    "tool_calls": clean_tool_calls,
                }
                temp_messages.append(assistant_msg)

                tool_results_list = []
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

                    tool_results_list.append({
                        "call_id": tool_call["id"],
                        "name": tool_name,
                        "content": str(results),
                    })

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

                # Canonical OpenAI tool response per tool call
                for res_item in tool_results_list:
                    temp_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": res_item["call_id"],
                            "name": res_item["name"],
                            "content": res_item["content"],
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

        # Initialize Slash Commands Registry and Execution Context
        self.command_registry = create_default_registry()
        self.command_context = CommandContext(
            emit_message=lambda role, msg: self.messageAdded.emit(role, msg),
            reset_session=self.reset_session,
            toggle_tts=self.toggleTTS,
            get_app_version=lambda: APP_VERSION,
            get_models_catalog=lambda: MODELS_CATALOG,
            get_active_model_key=lambda: self.active_model_key,
            set_active_model_key=self._set_active_model_key,
            get_models_list=self.get_models_list,
            active_model_changed=lambda text: self.activeModelChanged.emit(text),
            available_models_changed=lambda lst: self.availableModelsChanged.emit(lst),
        )

        self.load_history()

    def _set_active_model_key(self, key: str) -> None:
        self.active_model_key = key

    @pyqtSlot(str, str)
    def handle_message_added(self, role, text):
        self.messageAdded.emit(role, text)

    @pyqtSlot(str)
    def handle_message_appended(self, text):
        self.messageAppended.emit(text)

    def get_models_list(self):
        """Return structured list of items (headers and model entries) for the QML popup."""
        return model_catalog_manager.get_structured_models_list(self.active_model_key)

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
            "- CRITICAL RULE FOR TIME & WEB SEARCH: When answering questions about current events, recent news, "
            "or relative dates (e.g. 'hoy', 'ayer', 'esta semana'), you MUST use the exact date obtained from "
            "`get_current_time` to frame your search queries and final report. NEVER revert to pre-training cutoff dates or assume past years.\n"
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
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load chat history: {e}")

        self.messages.insert(0, {"role": "system", "content": system_content})

    @pyqtSlot()
    def reset_session(self):
        """Reset conversation context and clear screen."""
        self.interrupt_ai()
        self.messages = [{"role": "system", "content": self._build_system_prompt()}]
        if os.path.exists(HISTORY_FILE):
            try:
                os.remove(HISTORY_FILE)
            except OSError:
                pass
        self.chatCleared.emit()
        self.messageAdded.emit(
            "sys",
            "✨ Nueva sesión iniciada. Contexto anterior eliminado."
        )

    @pyqtSlot()
    def show_help(self):
        """Display available slash commands and shortcuts."""
        self.command_registry.dispatch("/help", self.command_context)

    @pyqtSlot()
    def show_about(self):
        """Display version and application info."""
        self.command_registry.dispatch("/about", self.command_context)

    @pyqtSlot(str)
    def switch_model(self, target_key=""):
        """List or switch active LLM from the models catalog."""
        cmd = f"/model {target_key}".strip() if target_key else "/model"
        self.command_registry.dispatch(cmd, self.command_context)

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
            except OSError:
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
        if self.command_registry.dispatch(clean_cmd, self.command_context):
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
