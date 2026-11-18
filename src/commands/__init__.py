"""Slash commands package for Assistant."""

from .base import BaseCommand, CommandContext, CommandRegistry
from .session import SessionCommand
from .system import HelpCommand, AboutCommand
from .voice import VoiceCommand
from .model import ModelCommand
from .role import RoleCommand


def create_default_registry() -> CommandRegistry:
    """Instantiate and register all built-in slash commands."""
    registry = CommandRegistry()

    # Session commands (/new, /reset, /clear)
    registry.register(SessionCommand())

    # System commands (/about, /help)
    registry.register(AboutCommand())
    registry.register(HelpCommand(registry))

    # Voice command (/voice, /tts)
    registry.register(VoiceCommand())

    # Model management (/model)
    registry.register(ModelCommand())

    # Role management (/role)
    registry.register(RoleCommand())

    return registry


__all__ = [
    "BaseCommand",
    "CommandContext",
    "CommandRegistry",
    "SessionCommand",
    "HelpCommand",
    "AboutCommand",
    "VoiceCommand",
    "ModelCommand",
    "RoleCommand",
    "create_default_registry",
]
