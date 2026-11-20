"""Base definitions and registry for Assistant slash commands."""

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional


class CommandContext:
    """Context and execution bridge passed to commands.

    Provides a clean, decoupled interface for interacting with the application
    session, UI signals, models catalog, and speech settings.
    """

    def __init__(
        self,
        emit_message: Callable[[str, str], None],
        reset_session: Callable[[], None],
        toggle_tts: Callable[[], None],
        get_app_version: Callable[[], str],
        get_models_catalog: Callable[[], Dict[str, Any]],
        get_active_model_key: Callable[[], str],
        set_active_model_key: Callable[[str], None],
        get_models_list: Callable[[], List[str]],
        active_model_changed: Callable[[str], None],
        available_models_changed: Callable[[List[str]], None],
        get_available_roles: Optional[Callable[[], Dict[str, Any]]] = None,
        get_active_role: Optional[Callable[[], str]] = None,
        set_active_role: Optional[Callable[[str], bool]] = None,
        get_mcp_status: Optional[Callable[[], Dict[str, Any]]] = None,
    ):
        self.emit_message = emit_message
        self.reset_session = reset_session
        self.toggle_tts = toggle_tts
        self.get_app_version = get_app_version
        self.get_models_catalog = get_models_catalog
        self.get_active_model_key = get_active_model_key
        self.set_active_model_key = set_active_model_key
        self.get_models_list = get_models_list
        self.active_model_changed = active_model_changed
        self.available_models_changed = available_models_changed
        self.get_available_roles = get_available_roles or (lambda: {})
        self.get_active_role = get_active_role or (lambda: "default")
        self.set_active_role = set_active_role or (lambda r: False)
        self.get_mcp_status = get_mcp_status or (lambda: {})


class BaseCommand(ABC):
    """Abstract base class representing an executable slash command."""

    name: str = ""
    aliases: List[str] = []
    description: str = ""
    usage: str = ""

    @abstractmethod
    def execute(self, arg: str, context: CommandContext) -> None:
        """Execute the command with the given argument and context."""
        pass


class CommandRegistry:
    """Registry coordinating all slash command dispatching and discovery."""

    def __init__(self):
        self._commands: Dict[str, BaseCommand] = {}
        self._unique_commands: List[BaseCommand] = []

    def register(self, command: BaseCommand) -> None:
        """Register a command under its primary name and any aliases."""
        if command not in self._unique_commands:
            self._unique_commands.append(command)
        names = [command.name] + command.aliases
        for name in names:
            clean_name = name.lower()
            if not clean_name.startswith("/"):
                clean_name = f"/{clean_name}"
            self._commands[clean_name] = command

    def is_command(self, text: str) -> bool:
        """Check if input text begins with a registered slash command."""
        clean = text.strip()
        if not clean.startswith("/"):
            return False
        cmd_name = clean.split()[0].lower()
        return cmd_name in self._commands

    def dispatch(self, text: str, context: CommandContext) -> bool:
        """Parse and execute a slash command if registered.

        Returns True if handled as a slash command, False otherwise.
        """
        clean = text.strip()
        if not clean.startswith("/"):
            return False

        parts = clean.split(maxsplit=1)
        cmd_name = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        command = self._commands.get(cmd_name)
        if command:
            command.execute(arg, context)
            return True
        return False

    def get_registered_commands(self) -> List[BaseCommand]:
        """Return unique list of registered commands for documentation."""
        return list(self._unique_commands)
