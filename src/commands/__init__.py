"""Slash commands package for Assistant."""

from .base import BaseCommand, CommandContext, CommandRegistry
from .session import SessionCommand
from .system import HelpCommand, AboutCommand
from .voice import VoiceCommand
from .model import ModelCommand
from .role import RoleCommand
from .mcp import MCPCommand
from .reasoning import ReasoningCommand


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

    # Reasoning / Thinking management (/reasoning, /thinking)
    registry.register(ReasoningCommand())

    # Role management (/role)
    registry.register(RoleCommand())

    # MCP server status (/mcp)
    registry.register(MCPCommand())

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
    "ReasoningCommand",
    "RoleCommand",
    "MCPCommand",
    "create_default_registry",
]
