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
from llm import ModelCatalog, AIGeneratorThread
from voice import TTSWorkerThread, stop_all_media_playback

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
# Maximum number of chained tool-execution rounds per user request. Strong
# agentic models (e.g. GLM-5.3-Flash) often chain several searches before
# answering; the final allowed round withholds the tools schema to force a
# plain text answer instead of letting the model call tools indefinitely.
MAX_TOOL_ROUNDS = 4


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
        self.ai_thread = AIGeneratorThread(
            self.messages,
            selected_model_cfg,
            mcp_manager=mcp_manager,
            command_intents=COMMAND_INTENTS,
            resolve_tools_fn=resolve_tools_for_query,
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

        self.tts_thread = TTSWorkerThread(tts_text, voice_config=VOICE_CONF, temp_dir=TEMP_DIR)
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
