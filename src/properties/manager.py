import json
import logging
from logging.handlers import RotatingFileHandler
import os
import shutil
import sys
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("assistant")


class PropertiesManager:
    """
    Singleton Properties & Configuration Manager for Assistant.

    Centralizes:
    - Path resolution (Config, Data, State, Knowledge, Sounds, QML)
    - Configuration loading (~/.config/assistant/config.json)
    - Dynamic runtime properties (active model, voice/TTS states, router status)
    - Roles management (system & user roles)
    - Change listeners / observer callbacks for dynamic settings
    """

    _instance: Optional["PropertiesManager"] = None

    def __new__(cls, *args, **kwargs) -> "PropertiesManager":
        if cls._instance is None:
            cls._instance = super(PropertiesManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    @classmethod
    def get_instance(cls) -> "PropertiesManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self, launch_mode: str = ""):
        if getattr(self, "_initialized", False):
            return

        self.version = "1.3.0"
        self._listeners: Dict[str, List[Callable[[Any], None]]] = {}

        # 1. Base Directories & XDG Paths
        self.config_dir = os.path.expanduser("~/.config/assistant")
        self.config_file = os.path.join(self.config_dir, "config.json")
        self.temp_dir = "/tmp/assistant"
        self.data_dir = os.path.expanduser("~/.local/share/assistant")
        self.sound_dir = os.path.join(self.data_dir, "sounds")
        self.state_dir = os.path.expanduser("~/.local/state/assistant")
        self.history_file = os.path.join(self.state_dir, "chat_history.json")
        self.log_file = os.path.join(self.state_dir, "assistant.log")
        self.user_knowledge_file = os.path.join(self.data_dir, "hyprland_knowledge.json")
        self.knowledge_dir = os.path.join(self.config_dir, "knowledge")
        self.roles_dir = os.path.join(self.config_dir, "roles")

        # Ensure base directories exist
        for d in (self.config_dir, self.data_dir, self.sound_dir, self.state_dir, self.knowledge_dir, self.roles_dir):
            try:
                os.makedirs(d, exist_ok=True)
            except OSError as e:
                logger.warning(f"Could not create directory {d}: {e}")

        # Package Base Dir & Path Resolution
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.system_knowledge_dir = self._resolve_system_knowledge_dir()
        self.system_knowledge_file = self._resolve_system_knowledge_file()
        self.system_roles_dir = self._resolve_system_roles_dir()
        self.qml_file = self._resolve_qml_file()

        # Seed files if needed
        self._seed_user_knowledge()

        # 3. Load config.json
        self._config: Dict[str, Any] = self._load_config()

        # 4. Role Resolution
        self.active_role = "default"
        self.active_role_prompt = self.get_role_prompt("default")

        # 5. Dynamic Runtime Properties
        self.is_voice_mode = (launch_mode == "--voice")
        self.tts_enabled = False
        self.active_model_key = ""
        self.mcp_status: Dict[str, Dict[str, Any]] = {}

        # Default model initialization from config if present
        default_model = self.get("llm.default_model") or self.get("llm.model", "")
        self.active_model_key = str(default_model)

        # Default reasoning effort initialization from config if present
        default_reasoning = self.get("llm.reasoning_effort") or self.get("llm.reasoning", "medium")
        self.active_reasoning_effort = str(default_reasoning).strip().lower()
        if self.active_reasoning_effort not in ("none", "low", "medium", "high"):
            self.active_reasoning_effort = "medium"

        # 6. Configure Rotating File Logger
        self._setup_logger()

        self._initialized = True

    def _setup_logger(self) -> None:
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            rf_handler = RotatingFileHandler(
                self.log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
            rf_handler.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
            )
            logger.addHandler(rf_handler)
            c_handler = logging.StreamHandler(sys.stdout)
            c_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
            logger.addHandler(c_handler)

    def _resolve_qml_file(self) -> Optional[str]:
        candidates = [
            os.path.join(self.base_dir, "ui.qml"),
            os.path.abspath(os.path.join(self.base_dir, "..", "share", "assistant", "ui.qml")),
            os.path.abspath(os.path.join(self.base_dir, "..", "..", "share", "assistant", "ui.qml")),
            os.path.expanduser("~/.local/share/assistant/ui.qml"),
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        return None

    def _resolve_system_knowledge_file(self) -> Optional[str]:
        candidates = [
            os.path.join(self.base_dir, "hyprland_knowledge.json"),
            os.path.abspath(os.path.join(self.base_dir, "..", "share", "assistant", "hyprland_knowledge.json")),
            os.path.abspath(os.path.join(self.base_dir, "..", "..", "share", "assistant", "hyprland_knowledge.json")),
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        return None

    def _resolve_system_knowledge_dir(self) -> str:
        candidates = [
            os.path.join(self.base_dir, "knowledge"),
            os.path.abspath(os.path.join(self.base_dir, "..", "share", "assistant", "knowledge")),
            os.path.abspath(os.path.join(self.base_dir, "..", "..", "share", "assistant", "knowledge")),
        ]
        for path in candidates:
            if os.path.isdir(path):
                return path
        return os.path.join(self.base_dir, "knowledge")

    def _resolve_system_roles_dir(self) -> str:
        candidates = [
            os.path.join(self.base_dir, "roles"),
            os.path.abspath(os.path.join(self.base_dir, "..", "share", "assistant", "roles")),
            os.path.abspath(os.path.join(self.base_dir, "..", "..", "share", "assistant", "roles")),
        ]
        for path in candidates:
            if os.path.isdir(path):
                return path
        return os.path.join(self.base_dir, "roles")

    def _seed_user_knowledge(self) -> None:
        # Seed modular markdown knowledge
        if os.path.exists(self.system_knowledge_dir):
            try:
                for f_name in os.listdir(self.system_knowledge_dir):
                    src_k = os.path.join(self.system_knowledge_dir, f_name)
                    dst_k = os.path.join(self.knowledge_dir, f_name)
                    if os.path.isfile(src_k) and not os.path.exists(dst_k):
                        shutil.copyfile(src_k, dst_k)
            except OSError as e:
                logger.warning(f"[Knowledge] Could not seed modular knowledge: {e}")

        # Seed hyprland recipes
        if self.system_knowledge_file and not os.path.exists(self.user_knowledge_file):
            try:
                shutil.copyfile(self.system_knowledge_file, self.user_knowledge_file)
            except OSError as e:
                logger.warning(f"[Hyprland Knowledge] Could not seed user knowledge: {e}")

    def _load_config(self) -> Dict[str, Any]:
        if not os.path.exists(self.config_file):
            logger.error(f"[PropertiesManager] Config file not found at {self.config_file}")
            return {}
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.error(f"[PropertiesManager] Failed to load {self.config_file}: {e}")
            return {}

    # -------------------------------------------------------------------------
    # Generic Property Accessors (Dot notation support)
    # -------------------------------------------------------------------------

    def get(self, key_path: str, default: Any = None) -> Any:
        """
        Retrieve a configuration property using dot notation (e.g. 'llm.history_duration_minutes').
        """
        parts = key_path.split(".")
        current = self._config
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    def set(self, key_path: str, value: Any, notify: bool = True) -> None:
        """
        Set or update a configuration property at runtime.
        """
        parts = key_path.split(".")
        current = self._config
        for part in parts[:-1]:
            if part not in current or not isinstance(current[part], dict):
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value

        if notify:
            self._notify_listeners(key_path, value)

    # -------------------------------------------------------------------------
    # Observers / Listeners
    # -------------------------------------------------------------------------

    def add_listener(self, key_path: str, callback: Callable[[Any], None]) -> None:
        """Register a callback for changes on a specific property key."""
        if key_path not in self._listeners:
            self._listeners[key_path] = []
        self._listeners[key_path].append(callback)

    def remove_listener(self, key_path: str, callback: Callable[[Any], None]) -> None:
        """Unregister a previously added callback."""
        if key_path in self._listeners and callback in self._listeners[key_path]:
            self._listeners[key_path].remove(callback)

    def _notify_listeners(self, key_path: str, value: Any) -> None:
        for cb in self._listeners.get(key_path, []):
            try:
                cb(value)
            except Exception as e:
                logger.error(f"[PropertiesManager] Error in listener for {key_path}: {e}")

    # -------------------------------------------------------------------------
    # Typed Helpers & Convenience Properties
    # -------------------------------------------------------------------------

    @property
    def config(self) -> Dict[str, Any]:
        """Direct access to full raw config dictionary."""
        return self._config

    @property
    def llm_config(self) -> Dict[str, Any]:
        return self.get("llm", {})

    @property
    def voice_config(self) -> Dict[str, Any]:
        return self.get("voice", {})

    @property
    def obsidian_config(self) -> Dict[str, Any]:
        return self.get("obsidian", {})

    @property
    def mcp_servers_config(self) -> Dict[str, Any]:
        return self.get("mcp_servers", {})

    @property
    def decision_router_config(self) -> Dict[str, Any]:
        return self.get("decision_router", {})

    @property
    def surveillance_interval(self) -> int:
        """Configured default surveillance interval in seconds (default: 5)."""
        return max(1, int(self.get("surveillance.interval_seconds", 5)))

    @property
    def surveillance_cooldown(self) -> float:
        """Configured cooldown between surveillance trigger notifications in seconds (default: 10.0)."""
        return max(1.0, float(self.get("surveillance.trigger_cooldown_seconds", 10.0)))

    @property
    def history_duration_minutes(self) -> int:
        return int(self.get("llm.history_duration_minutes", 15))

    @property
    def obsidian_api_key(self) -> str:
        return str(self.get("obsidian.api_key", ""))

    @property
    def obsidian_url(self) -> str:
        return str(self.get("obsidian.url", "https://127.0.0.1:27124"))

    @property
    def obsidian_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.obsidian_api_key}",
            "Accept": "application/json",
        }

    # -------------------------------------------------------------------------
    # Runtime State Setters & Getters
    # -------------------------------------------------------------------------

    def set_active_model_key(self, key: str) -> None:
        self.active_model_key = key
        self._notify_listeners("active_model_key", key)

    def set_active_reasoning_effort(self, effort: str) -> bool:
        clean = str(effort).strip().lower()
        if clean in ("none", "low", "medium", "high"):
            self.active_reasoning_effort = clean
            self._notify_listeners("active_reasoning_effort", clean)
            return True
        return False

    def set_tts_enabled(self, enabled: bool) -> None:
        self.tts_enabled = enabled
        self._notify_listeners("tts_enabled", enabled)

    def set_voice_mode(self, enabled: bool) -> None:
        self.is_voice_mode = enabled
        self._notify_listeners("is_voice_mode", enabled)

    def set_mcp_status(self, status: Dict[str, Dict[str, Any]]) -> None:
        self.mcp_status = status
        self._notify_listeners("mcp_status", status)

    def get_mcp_status(self) -> Dict[str, Dict[str, Any]]:
        return self.mcp_status

    # -------------------------------------------------------------------------
    # Roles Management
    # -------------------------------------------------------------------------

    def get_available_roles(self) -> Dict[str, Dict[str, Any]]:
        """
        Discover and return all available roles by aggregating system roles
        and user custom roles (~/.config/assistant/roles/).
        User roles take precedence over system roles with the same identifier.
        """
        roles: Dict[str, Dict[str, Any]] = {}

        # 1. System roles
        sys_index = os.path.join(self.system_roles_dir, "index.json")
        if os.path.exists(sys_index):
            try:
                with open(sys_index, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for r_id, r_info in data.get("roles", {}).items():
                        r_info_copy = dict(r_info)
                        r_info_copy["source"] = "system"
                        roles[r_id] = r_info_copy
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"[Roles] Failed to read system roles index {sys_index}: {e}")

        # 2. User roles (roles.json or index.json in ~/.config/assistant/roles/)
        user_index_candidates = [
            os.path.join(self.roles_dir, "roles.json"),
            os.path.join(self.roles_dir, "index.json"),
        ]
        for u_idx in user_index_candidates:
            if os.path.exists(u_idx):
                try:
                    with open(u_idx, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        for r_id, r_info in data.get("roles", {}).items():
                            r_info_copy = dict(r_info)
                            r_info_copy["source"] = "user"
                            roles[r_id] = r_info_copy
                except (OSError, json.JSONDecodeError) as e:
                    logger.warning(f"[Roles] Failed to read user roles index {u_idx}: {e}")

        # 3. Auto-discover standalone .md files in user roles directory
        if os.path.exists(self.roles_dir):
            try:
                for entry in os.listdir(self.roles_dir):
                    if entry.endswith(".md"):
                        r_id = os.path.splitext(entry)[0]
                        if r_id not in roles:
                            roles[r_id] = {
                                "name": r_id.capitalize(),
                                "description": f"Custom user role from {entry}",
                                "file": entry,
                                "source": "user",
                            }
            except OSError as e:
                logger.warning(f"[Roles] Failed to list user roles dir {self.roles_dir}: {e}")

        return roles

    def get_role_prompt(self, role_id: str) -> str:
        """
        Load and return the markdown content for a given role identifier.
        Checks user directory first (~/.config/assistant/roles/<file>),
        then system directory (<system_roles_dir>/<file>).
        """
        roles = self.get_available_roles()
        role_info = roles.get(role_id)
        filename = role_info.get("file", f"{role_id}.md") if role_info else f"{role_id}.md"

        # Check user directory first (user override)
        user_file = os.path.join(self.roles_dir, filename)
        if os.path.exists(user_file):
            try:
                with open(user_file, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except OSError as e:
                logger.warning(f"[Roles] Could not read user role file {user_file}: {e}")

        # Check system directory fallback
        sys_file = os.path.join(self.system_roles_dir, filename)
        if os.path.exists(sys_file):
            try:
                with open(sys_file, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except OSError as e:
                logger.warning(f"[Roles] Could not read system role file {sys_file}: {e}")

        return ""

    def set_active_role(self, role_id: str) -> bool:
        """
        Switch active role, update active_role_prompt, and notify observers.
        Returns True if successful, False if the role does not exist.
        """
        roles = self.get_available_roles()
        if role_id not in roles:
            return False

        self.active_role = role_id
        self.active_role_prompt = self.get_role_prompt(role_id)
        self._notify_listeners("active_role", role_id)
        return True
