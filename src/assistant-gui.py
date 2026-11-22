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

import glob
import mimetypes

# Fix GLib/GTK schema availability on NixOS/Hyprland so native dialogs never crash
def _ensure_glib_schemas():
    current = os.environ.get("XDG_DATA_DIRS", "")
    schema_dirs = []
    for pattern in [
        "/nix/store/*gtk+3*/share/gsettings-schemas/*",
        "/nix/store/*gsettings-desktop-schemas*/share/gsettings-schemas/*",
    ]:
        for d in glob.glob(pattern):
            if os.path.isdir(d):
                schema_dirs.append(d)
    if schema_dirs:
        extra = ":".join(schema_dirs)
        os.environ["XDG_DATA_DIRS"] = f"{extra}:{current}" if current else extra

_ensure_glib_schemas()

try:
    import pypdf
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False

from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QFileDialog
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont, QAction, QPainterPath
from PyQt6.QtQml import QQmlApplicationEngine
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot, QThread, QObject, QUrl, QMetaObject

import openai
from openai import OpenAI, Timeout
import requests
import urllib3

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
    get_cinema_showtimes,
    CINEMA_TOOLS,
    take_screenshot,
    SCREENSHOT_TOOLS,
    start_surveillance,
    stop_surveillance,
    get_surveillance_status,
    surveillance_manager,
    SURVEILLANCE_TOOLS,
)
from routing import DecisionRouter
from commands import create_default_registry, CommandContext
from mcp_client import MCPManager, MCP_AVAILABLE
from llm import ModelCatalog, AIGeneratorThread
from voice import TTSWorkerThread, stop_all_media_playback
from properties import PropertiesManager

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# ⚙️ PROPERTIES & CONFIGURATION INITIALIZATION
# ==========================================
props = PropertiesManager()
logger = logging.getLogger("assistant")

if not props.qml_file:
    print(
        "\033[1;31m[!] Error: ui.qml not found. Please ensure the frontend file is downloaded.\033[0m"
    )
    sys.exit(1)

if not os.path.exists(props.config_file):
    print(f"\033[1;31m[!] Configuration file not found at {props.config_file}.\033[0m")
    sys.exit(1)

# Configure knowledge and hyprland modules
configure_knowledge(user_dir=props.knowledge_dir, system_dir=props.system_knowledge_dir)
configure_hyprland(system_knowledge_file=props.system_knowledge_file, user_knowledge_file=props.user_knowledge_file)

# Configure Obsidian tool
configure_obsidian(api_key=props.obsidian_api_key, url=props.obsidian_url)

# ==========================================
# 🤖 MULTI-MODEL LLM MANAGER & CONFIGURATION
# ==========================================
model_catalog_manager = ModelCatalog(props.llm_config)
MODELS_CATALOG = model_catalog_manager.catalog
DEFAULT_MODEL_KEY = model_catalog_manager.default_model_key
props.set_active_model_key(DEFAULT_MODEL_KEY)

mcp_manager = MCPManager(props.mcp_servers_config)
mcp_tools = mcp_manager.discover_tools()

# Load once at startup: recipe actions drive the manage_desktop tool schema and
# _command_intents drives the silent/active classification of shell commands.
HYPRLAND_RECIPES = load_hyprland_recipes()
COMMAND_INTENTS = HYPRLAND_RECIPES.get("_command_intents", {})

# Exclude MCP tools that are categorized as 'web_search' from the raw tools list exposed to the LLM.
# They are accessed transparently via the canonical 'web_search' tool.
raw_web_mcp_names = {
    name for name, meta in mcp_manager.tools.items()
    if meta.get("server_type") == "web_search"
}
# Also exclude searxng_web_search specifically if it came from an untyped server
if "searxng_web_search" in mcp_manager.tools:
    raw_web_mcp_names.add("searxng_web_search")

exposed_mcp_tools = [
    t for t in mcp_tools
    if t.get("function", {}).get("name") not in raw_web_mcp_names
]

## TOOLS & LOGIC
tools = (
    TIME_TOOLS
    + WEATHER_TOOLS
    + CINEMA_TOOLS
    + SCREENSHOT_TOOLS
    + SURVEILLANCE_TOOLS
    + WEB_TOOLS
    + OBSIDIAN_TOOLS
    + OS_TOOLS
    + KNOWLEDGE_TOOLS
    + [build_manage_desktop_tool(HYPRLAND_RECIPES)]
    + exposed_mcp_tools
)

# ==========================================
# 🧭 DECISION ROUTER (d3 / System One)
# ==========================================
decision_router = DecisionRouter(props.decision_router_config)
decision_router.register_mcp_servers(mcp_manager)
DECISION_ENABLED = decision_router.enabled


def resolve_tools_for_query(user_query, recent_context="", base_url=""):
    return decision_router.resolve_tools_for_query(
        user_query=user_query,
        available_tools=tools,
        recent_context=recent_context,
        base_url=base_url,
    )


def _extract_pdf_text_and_forms(file_path):
    """
    Extract readable text and interactive form fields from a PDF file using pypdf.
    Returns (content_text, error_message).
    """
    if not PYPDF_AVAILABLE:
        return "", "pypdf library is not installed in the environment."

    try:
        reader = pypdf.PdfReader(file_path)
        sections = []

        # 1. Extract plain text page by page
        page_chunks = []
        for idx, page in enumerate(reader.pages):
            text = (page.extract_text() or "").strip()
            if text:
                page_chunks.append(f"--- Page {idx + 1} ---\n{text}")

        if page_chunks:
            sections.append("\n\n".join(page_chunks))

        # 2. Extract interactive form fields if present
        form_entries = []
        try:
            fields = reader.get_fields()
            if fields:
                for field_name, field_data in fields.items():
                    val = getattr(field_data, "value", None)
                    if val is not None:
                        val_str = str(val).strip()
                        # Clean up NameObject slashes (e.g. /Yes -> Yes)
                        if hasattr(val, "name") or (isinstance(val_str, str) and val_str.startswith("/")):
                            val_str = val_str.lstrip("/")
                        if val_str:
                            alt = getattr(field_data, "alternate_name", None)
                            label = f"{alt} ({field_name})" if alt and alt != field_name else field_name
                            form_entries.append(f"- {label}: {val_str}")
        except Exception as fe:
            logger.debug(f"Failed extracting form fields from {file_path}: {fe}")

        if form_entries:
            sections.append("--- PDF Form Fields & Inputs ---\n" + "\n".join(form_entries))

        extracted_body = "\n\n".join(sections).strip()
        return extracted_body, None
    except Exception as e:
        logger.warning(f"Failed to read PDF attachment {file_path}: {e}")
        return "", str(e)


def _extract_text_file(file_path):
    """
    Extract text from a plain text or markdown document with encoding fallbacks.
    Returns (content_text, error_message).
    """
    encodings = ["utf-8", "latin-1", "cp1252"]
    for enc in encodings:
        try:
            with open(file_path, "r", encoding=enc) as f:
                return f.read(), None
        except UnicodeDecodeError:
            continue
        except OSError as e:
            return "", str(e)
    return "", "Could not decode text file with supported encodings."


def process_prompt(user_query, attached_files=None, vision_supported=True):
    """
    Format prompt into standard text or multimodal OpenAI payload.
    Supports attached image files with base64 data URIs, as well as PDF,
    Markdown (.md), and plain text (.txt) document attachments.
    """
    attached_files = attached_files or []
    image_parts = []
    document_parts = []
    non_image_notes = []

    # 1. Process files explicitly attached
    for file_path in attached_files:
        if not os.path.exists(file_path):
            continue
        mime_type, _ = mimetypes.guess_type(file_path)
        mime_type = mime_type or "application/octet-stream"
        file_name = os.path.basename(file_path)
        _, ext = os.path.splitext(file_path.lower())

        if mime_type.startswith("image/"):
            if vision_supported:
                try:
                    with open(file_path, "rb") as f:
                        b64_data = base64.b64encode(f.read()).decode("utf-8")
                    image_parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{b64_data}"
                        }
                    })
                except OSError as e:
                    logger.warning(f"Failed to read image attachment {file_path}: {e}")
            else:
                non_image_notes.append(f"[Attached Image: {file_name} (Vision disabled for current model)]")

        elif ext == ".pdf" or mime_type == "application/pdf":
            content, err = _extract_pdf_text_and_forms(file_path)
            if content:
                document_parts.append(f"### Attached Document: {file_name} (PDF)\n```\n{content}\n```")
            else:
                reason = f": {err}" if err else " (Empty content)"
                non_image_notes.append(f"[Attached PDF: {file_name}{reason}]")

        elif ext in [".txt", ".md", ".markdown"] or mime_type.startswith("text/"):
            content, err = _extract_text_file(file_path)
            if content:
                doc_type = "Markdown" if ext in [".md", ".markdown"] else "Plain Text"
                document_parts.append(f"### Attached Document: {file_name} ({doc_type})\n```\n{content}\n```")
            else:
                reason = f": {err}" if err else " (Empty file)"
                non_image_notes.append(f"[Attached Document: {file_name}{reason}]")

        else:
            non_image_notes.append(f"[Attached File: {file_name} ({file_path})]")

    # 2. Legacy screenshot / clipboard syntax compatibility: [IMAGE PATH: ...]
    match = re.search(r"\[IMAGE PATH:\s*(.*?)\]", user_query)
    if match:
        path = match.group(1).strip()
        user_query = user_query.replace(match.group(0), "").strip()
        if os.path.exists(path) and vision_supported:
            try:
                mime_type, _ = mimetypes.guess_type(path)
                mime_type = mime_type or "image/png"
                with open(path, "rb") as f:
                    b64_data = base64.b64encode(f.read()).decode("utf-8")
                image_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{b64_data}"}
                })
            except OSError as e:
                logger.warning(f"Failed to read legacy image path {path}: {e}")
        elif not vision_supported:
            non_image_notes.append("[SYSTEM: Image shared but Vision disabled.]")

    clean_query = user_query.replace("Regarding this screenshot:", "").strip()

    # Append document bodies and metadata notes to prompt
    combined_notes = []
    if document_parts:
        combined_notes.extend(document_parts)
    if non_image_notes:
        combined_notes.extend(non_image_notes)

    if combined_notes:
        notes_text = "\n\n".join(combined_notes)
        if clean_query:
            clean_query = clean_query + "\n\n" + notes_text
        else:
            clean_query = notes_text

    if image_parts:
        final_text = clean_query if clean_query else "Please analyze the attached image(s) in detail."
        content_payload = [{"type": "text", "text": final_text}]
        content_payload.extend(image_parts)
        return content_payload

    return clean_query if clean_query else user_query


## QTHREADS
# Tool-execution round limits are managed per model by the LLM generator
# (src/llm/generator.py: DEFAULT_MAX_TOOL_ROUNDS). Override per model with the
# "max_tool_rounds" key in each model spec of config.json.


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
    pendingAttachmentsChanged = pyqtSignal(list)
    visionSupportedChanged = pyqtSignal(bool)
    activeReasoningChanged = pyqtSignal(str)
    reasoningSupportedChanged = pyqtSignal(bool)
    surveillanceActiveChanged = pyqtSignal(bool)
    surveillanceTriggeredSignal = pyqtSignal(str, str)
    surveillanceStateChangedSignal = pyqtSignal(bool)

    def __init__(self, initial_query, launch_mode):
        super().__init__()
        self.is_voice_mode = launch_mode == "--voice"
        self.tts_enabled = False  # Disabled by default on startup as requested
        self.is_recording = False
        self.rec_process = None
        self.messages = []
        self._pending_attachments = []
        props.set_voice_mode(self.is_voice_mode)
        props.set_tts_enabled(self.tts_enabled)

        self.timer = QTimer()
        self.timer.timeout.connect(self.check_signals)
        self.timer.start(200)

        # Initialize Slash Commands Registry and Execution Context
        self.command_registry = create_default_registry()
        self.command_context = CommandContext(
            emit_message=lambda role, msg: self.messageAdded.emit(role, msg),
            reset_session=self.reset_session,
            toggle_tts=self.toggleTTS,
            get_app_version=lambda: props.version,
            get_models_catalog=lambda: MODELS_CATALOG,
            get_active_model_key=lambda: props.active_model_key,
            set_active_model_key=self._set_active_model_key,
            get_models_list=self.get_models_list,
            active_model_changed=lambda text: self.activeModelChanged.emit(text),
            available_models_changed=lambda lst: self.availableModelsChanged.emit(lst),
            get_available_roles=lambda: props.get_available_roles(),
            get_active_role=lambda: props.active_role,
            set_active_role=self._set_active_role,
            get_mcp_status=lambda: props.get_mcp_status(),
            is_vision_supported=lambda: self.is_vision_supported,
            get_active_reasoning_effort=lambda: props.active_reasoning_effort,
            set_active_reasoning_effort=self.switch_reasoning,
            is_thinking_supported=lambda: self.is_thinking_supported,
        )

        self.load_history()

        # Connect internal Qt signals for thread-safe cross-thread surveillance notifications
        self.surveillanceTriggeredSignal.connect(self.on_surveillance_triggered)
        self.surveillanceStateChangedSignal.connect(self.on_surveillance_state_changed)

        # Configure Surveillance Manager with the active model client & notification callback
        self._update_surveillance_client()

    def _update_surveillance_client(self):
        selected_model_cfg = MODELS_CATALOG.get(self.active_model_key, next(iter(MODELS_CATALOG.values())))
        client = selected_model_cfg.get("client")
        model_name = selected_model_cfg.get("name")
        surveillance_manager.configure_vision_client(
            client=client,
            model_name=model_name,
            on_trigger_callback=lambda desc, path: self.surveillanceTriggeredSignal.emit(desc, path),
            on_state_changed_callback=lambda active: self.surveillanceStateChangedSignal.emit(active),
            default_interval=props.surveillance_interval,
            default_cooldown=props.surveillance_cooldown,
        )

    def _set_active_role(self, role_id: str) -> bool:
        success = props.set_active_role(role_id)
        if success:
            new_prompt = self._build_system_prompt()
            if self.messages and self.messages[0].get("role") == "system":
                self.messages[0]["content"] = new_prompt
            else:
                self.messages.insert(0, {"role": "system", "content": new_prompt})
        return success

    def _set_active_model_key(self, key: str) -> None:
        props.set_active_model_key(key)
        self.visionSupportedChanged.emit(self.is_vision_supported)
        self.reasoningSupportedChanged.emit(self.is_thinking_supported)
        self._update_surveillance_client()

    @property
    def is_vision_supported(self) -> bool:
        return model_catalog_manager.is_vision_supported(self.active_model_key)

    @property
    def is_thinking_supported(self) -> bool:
        return model_catalog_manager.is_thinking_supported(self.active_model_key)

    @pyqtSlot()
    def hide_window(self):
        """Temporarily hide window for screen operations if window is currently visible."""
        if hasattr(self, "tray_manager") and self.tray_manager and self.tray_manager.window:
            if self.tray_manager.window.isVisible():
                self.tray_manager.window.hide()

    @pyqtSlot()
    def show_window(self):
        """Restore window visibility after screen operations."""
        if hasattr(self, "tray_manager") and self.tray_manager:
            self.tray_manager.show_window()

    def request_hide_window(self):
        """Thread-safe request to hide window via Qt queued event loop."""
        QMetaObject.invokeMethod(self, "hide_window", Qt.ConnectionType.QueuedConnection)

    def request_show_window(self):
        """Thread-safe request to show window via Qt queued event loop."""
        QMetaObject.invokeMethod(self, "show_window", Qt.ConnectionType.QueuedConnection)

    @pyqtSlot(str, str)
    def on_surveillance_triggered(self, description: str, saved_image_path: str):
        """Handle background surveillance trigger event on the GUI main thread."""
        logger.info(f"GUI received surveillance trigger: {description}")
        msg = f"🚨 **¡Alerta de Vigilancia!**\n\n{description}"
        if saved_image_path and os.path.exists(saved_image_path):
            msg += f"\n\n📁 *Captura guardada en:* `{saved_image_path}`"
        self.messageAdded.emit("ai", msg)
        self.messages.append({"role": "assistant", "content": msg})
        self.save_history()
        self.statusChanged.emit("🚨 Surveillance Triggered!", "#ff79c6")
        if self.tts_enabled:
            self.play_audio(f"Alerta de vigilancia: {description}")

    @pyqtSlot(bool)
    def on_surveillance_state_changed(self, active: bool):
        """Handle surveillance active state change and notify QML UI."""
        logger.info(f"Surveillance active state changed to: {active}")
        self.surveillanceActiveChanged.emit(active)

    @property
    def active_model_key(self) -> str:
        return props.active_model_key

    @property
    def tts_enabled(self) -> bool:
        return props.tts_enabled

    @tts_enabled.setter
    def tts_enabled(self, val: bool) -> None:
        props.set_tts_enabled(val)

    @property
    def is_voice_mode(self) -> bool:
        return props.is_voice_mode

    @is_voice_mode.setter
    def is_voice_mode(self, val: bool) -> None:
        props.set_voice_mode(val)

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
        self.activeReasoningChanged.emit(props.active_reasoning_effort)
        self.reasoningSupportedChanged.emit(self.is_thinking_supported)
        self.voiceStateChanged.emit(self.is_recording or self.is_voice_mode)
        self.ttsStateChanged.emit(self.tts_enabled)
        self.visionSupportedChanged.emit(self.is_vision_supported)
        self.surveillanceActiveChanged.emit(surveillance_manager.is_active)
        self.pendingAttachmentsChanged.emit(list(self._pending_attachments))
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
            "- CRITICAL RULE FOR SCREEN & VISUAL ANALYSIS: When the user asks to look at, inspect, review, or analyze something on screen or an error currently displayed (e.g., 'mira la pantalla', 'haz una captura', 'mira este error que sale en pantalla', 'qué tengo abierto'), "
            "you MUST call `take_screenshot(target='fullscreen'|'active_window')`. The assistant window will automatically hide and restore during the capture.\n"
            "- CRITICAL RULE FOR WEBCAM SURVEILLANCE: When the user asks to activate surveillance mode or monitor the camera for something (e.g., 'ponte en modo vigilancia y avísame cuando veas a una persona', 'vigila la cámara si entra alguien', 'avísame si ves un gato'), "
            f"you MUST call `start_surveillance(condition='...', interval_seconds={props.surveillance_interval})`.\n"
            "- CRITICAL RULE FOR STOPPING SURVEILLANCE: When the user asks to stop, halt, disable, or cancel surveillance (e.g., 'para el modo vigilancia', 'para la vigilancia', 'detén la vigilancia', 'cancela la vigilancia', 'desactiva vigilancia', 'para la cámara', 'deja de vigilar', 'apaga la vigilancia'), "
            "you MUST immediately call `stop_surveillance()`. Do NOT call `start_surveillance()` or `get_surveillance_status()` when the user intends to stop or halt surveillance.\n"
            "- CRITICAL RULE FOR IMAGES & ATTACHMENTS: When an image is attached to the conversation or provided by a tool, inspect and analyze its visual content "
            "directly to answer the user's inquiry. When documents (such as PDF files, form fields, Markdown notes, or text files) are attached, "
            "their extracted text and form input data are included directly in the user turn; inspect and reference them thoroughly to answer the user's request.\n"
            "- Keep answers concise, natural, and conversational."
        )

        role_prompt = props.active_role_prompt
        if role_prompt and not role_prompt.startswith("<!--"):
            system_content += f"\n\n### ACTIVE ROLE ({props.active_role.upper()}):\n{role_prompt}"

        return system_content

    def load_history(self):
        system_content = self._build_system_prompt()
        self.messages = []
        if os.path.exists(props.history_file) and (
            time.time() - os.path.getmtime(props.history_file)
            < (props.history_duration_minutes * 60)
        ):
            try:
                with open(props.history_file, "r") as f:
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
        surveillance_manager.stop()
        self.interrupt_ai()
        self.clearPendingAttachments()
        self.messages = [{"role": "system", "content": self._build_system_prompt()}]
        if os.path.exists(props.history_file):
            try:
                os.remove(props.history_file)
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

    @pyqtSlot(str)
    def switch_reasoning(self, effort=""):
        """Switch or query LLM reasoning effort level."""
        cmd = f"/reasoning {effort}".strip() if effort else "/reasoning"
        self.command_registry.dispatch(cmd, self.command_context)
        self.activeReasoningChanged.emit(props.active_reasoning_effort)

    @pyqtSlot()
    def save_history(self):
        tail = [m for m in self.messages if m.get("role") != "system"]
        history_to_save = []
        for m in [self.messages[0]] + tail[-8:]:
            # If the user prompt is a multimodal list with base64 data, sanitize it for history storage
            if isinstance(m.get("content"), list):
                sanitized_parts = []
                for part in m["content"]:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        sanitized_parts.append({
                            "type": "text",
                            "text": "[Image attachment ingested]"
                        })
                    else:
                        sanitized_parts.append(part)
                # Combine into single text if appropriate or keep list
                history_to_save.append({
                    **m,
                    "content": " ".join([p.get("text", "") for p in sanitized_parts if p.get("type") == "text"]).strip()
                })
            else:
                history_to_save.append(m)

        with open(props.history_file, "w") as f:
            json.dump(history_to_save, f)

    def stop_media_playback(self):
        """Stop any active audio output or TTS generation without resetting voice mode."""
        if hasattr(self, "tts_thread") and self.tts_thread is not None:
            if self.tts_thread.isRunning():
                self.tts_thread.stop()

        stop_all_media_playback()

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
        sig_path = os.path.join(props.temp_dir, "gui_signal")
        if os.path.exists(sig_path):
            sig_content = "toggle"
            try:
                with open(sig_path, "r", encoding="utf-8") as f:
                    sig_content = f.read().strip()
                os.remove(sig_path)
            except OSError:
                pass

            if sig_content == "voice":
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
            else:
                # Window toggle signal: show if hidden, hide if visible
                if hasattr(self, "tray_manager") and self.tray_manager:
                    self.tray_manager.toggle_window()

    @pyqtSlot()
    def selectFilesDialog(self):
        """Open system file picker to attach one or multiple files."""
        try:
            filter_str = (
                "Archivos compatibles (*.png *.jpg *.jpeg *.webp *.gif *.bmp *.pdf *.txt *.md *.markdown);;"
                "Documentos (*.pdf *.txt *.md *.markdown);;"
                "Imágenes (*.png *.jpg *.jpeg *.webp *.gif *.bmp);;"
                "Todos los archivos (*)"
            )
            files, _ = QFileDialog.getOpenFileNames(
                None,
                "Seleccionar archivos para adjuntar",
                os.path.expanduser("~"),
                filter_str
            )
        except Exception as e:
            logger.error(f"Error opening file dialog: {e}")
            files = []

        if files:
            for f in files:
                if f not in self._pending_attachments:
                    self._pending_attachments.append(f)
            self.pendingAttachmentsChanged.emit(list(self._pending_attachments))

    @pyqtSlot(str)
    def removeAttachment(self, file_path):
        """Remove a single attached file by path."""
        if file_path in self._pending_attachments:
            self._pending_attachments.remove(file_path)
            self.pendingAttachmentsChanged.emit(list(self._pending_attachments))

    @pyqtSlot()
    def clearPendingAttachments(self):
        """Clear all pending attached files."""
        if self._pending_attachments:
            self._pending_attachments.clear()
            self.pendingAttachmentsChanged.emit([])

    @pyqtSlot(result=bool)
    def pasteFromClipboard(self):
        """
        Check if the system clipboard contains an image.
        If an image is found (via wl-paste or xclip), save it to temp_dir,
        add it to pending attachments, and return True.
        Otherwise return False so standard text paste can proceed.
        """
        os.makedirs(props.temp_dir, exist_ok=True)
        timestamp = int(time.time() * 1000)
        out_path = os.path.join(props.temp_dir, f"clipboard_{timestamp}.png")

        # 1. Wayland check via wl-paste
        try:
            proc = subprocess.run(
                ["wl-paste", "--list-types"],
                capture_output=True,
                text=True,
                timeout=0.6,
            )
            if proc.returncode == 0:
                types = proc.stdout.strip().splitlines()
                for t in types:
                    if t.startswith("image/"):
                        res = subprocess.run(
                            ["wl-paste", "-t", t],
                            capture_output=True,
                            timeout=1.5,
                        )
                        if res.returncode == 0 and len(res.stdout) > 0:
                            with open(out_path, "wb") as f:
                                f.write(res.stdout)
                            if out_path not in self._pending_attachments:
                                self._pending_attachments.append(out_path)
                                self.pendingAttachmentsChanged.emit(list(self._pending_attachments))
                            return True
        except (subprocess.SubprocessError, OSError):
            pass

        # 2. X11 fallback check via xclip
        try:
            res = subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
                capture_output=True,
                timeout=1.0,
            )
            if res.returncode == 0 and len(res.stdout) > 0:
                with open(out_path, "wb") as f:
                    f.write(res.stdout)
                if out_path not in self._pending_attachments:
                    self._pending_attachments.append(out_path)
                    self.pendingAttachmentsChanged.emit(list(self._pending_attachments))
                return True
        except (subprocess.SubprocessError, OSError):
            pass

        return False

    @pyqtSlot(str)
    def processInput(self, text):
        clean_cmd = text.strip()
        if not self._pending_attachments and self.command_registry.dispatch(clean_cmd, self.command_context):
            return

        self.interrupt_ai()
        self.is_voice_mode = False
        self.statusChanged.emit("Idle", "#828c99")
        attachments = list(self._pending_attachments)
        self.clearPendingAttachments()
        self.submit_query(text, attachments=attachments)

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
            ["pw-play", os.path.join(props.sound_dir, "start.wav")], stderr=subprocess.DEVNULL
        )
        threading.Thread(target=self.record_with_silence).start()

    def stop_recording(self):
        if self.is_recording and self.rec_process:
            self.is_recording = False
            self.rec_process.terminate()

    def record_with_silence(self):
        voice_conf = props.voice_config
        silence_duration = str(voice_conf.get("silence_duration", "0.8"))
        silence_threshold = str(voice_conf.get("silence_threshold", "4%"))
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
                os.path.join(props.temp_dir, "voice_reply.wav"),
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
                ["pw-play", os.path.join(props.sound_dir, "stop.wav")],
                stderr=subprocess.DEVNULL,
            )
            self.transcribe_audio()

    def transcribe_audio(self):
        self.statusChanged.emit("󰑮 Transcribing...", "#e5b454")
        wav_file = os.path.join(props.temp_dir, "voice_reply.wav")
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
        voice_conf = props.voice_config
        asr_conf = voice_conf.get("asr", {})
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
                voice_conf.get("whisper_model")
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

    def submit_query(self, query, attachments=None):
        self._input_enabled = False
        self.inputStateChanged.emit(False)
        self.stopButtonStateChanged.emit(True)
        if DECISION_ENABLED:
            self.statusChanged.emit("󰘳 Routing...", "#ffb454")
        else:
            self.statusChanged.emit("󰟷 Thinking...", "#59c2ff")

        # Visual user message with attachment badges if present
        display_query = query.strip()
        if attachments:
            att_badges = " ".join([f"`󰏫 {os.path.basename(a)}`" for a in attachments])
            display_query = f"{att_badges}\n\n{display_query}" if display_query else att_badges
        self.messageAdded.emit("user", display_query)

        content = process_prompt(
            query,
            attached_files=attachments,
            vision_supported=self.is_vision_supported
        )
        self.messages.append({"role": "user", "content": content})

        if hasattr(self, "ai_thread") and self.ai_thread is not None:
            if self.ai_thread.isRunning():
                self.ai_thread.stop()
            self.ai_thread.deleteLater()

        selected_model_cfg = MODELS_CATALOG.get(self.active_model_key, next(iter(MODELS_CATALOG.values())))
        self.ai_thread = AIGeneratorThread(
            self.messages,
            selected_model_cfg,
            mcp_manager=mcp_manager,
            command_intents=COMMAND_INTENTS,
            resolve_tools_fn=resolve_tools_for_query,
            hide_window_callback=self.request_hide_window,
            show_window_callback=self.request_show_window,
            reasoning_effort=props.active_reasoning_effort,
        )
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

        self.tts_thread = TTSWorkerThread(
            tts_text, voice_config=props.voice_config, temp_dir=props.temp_dir
        )
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
    def toggle_window(self):
        if self.window:
            if self.window.isVisible():
                self.window.hide()
            else:
                self.show_window()

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
    engine.load(QUrl.fromLocalFile(props.qml_file))

    if not engine.rootObjects():
        sys.exit(-1)

    window = engine.rootObjects()[0]
    tray_manager = TrayManager(app, window, backend)
    backend.tray_manager = tray_manager

    QTimer.singleShot(100, backend.emit_initial_state)

    sys.exit(app.exec())
