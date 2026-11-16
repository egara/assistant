"""System and information slash commands: /help, /about."""

from .base import BaseCommand, CommandContext, CommandRegistry


class HelpCommand(BaseCommand):
    """Displays interactive help with available slash commands and shortcuts."""

    name = "/help"
    aliases = []
    description = "Muestra esta información de ayuda con los comandos y atajos disponibles."
    usage = "/help"

    def __init__(self, registry: CommandRegistry):
        self.registry = registry

    def execute(self, arg: str, context: CommandContext) -> None:
        version = context.get_app_version()
        lines = [f"󰋖 **Comandos disponibles (v{version}):**\n"]

        for cmd in self.registry.get_registered_commands():
            aliases_str = f" o **{', '.join(cmd.aliases)}**" if cmd.aliases else ""
            lines.append(f"- **{cmd.name}**{aliases_str}: {cmd.description}")

        lines.append("- **󰍬 Botón o tecla 'v'**: Pulsa el botón de micrófono o escribe 'v' y pulsa Enter para activar/desactivar el modo de voz.")
        lines.append("- **󰕾 / 󰝟 Botón de voz**: Pulsa el botón de altavoz junto a la ayuda para silenciar/activar la voz de respuesta.")

        context.emit_message("sys", "\n".join(lines))


class AboutCommand(BaseCommand):
    """Displays version and system architecture details."""

    name = "/about"
    aliases = []
    description = "Muestra los detalles de versión y arquitectura de Assistant."
    usage = "/about"

    def execute(self, arg: str, context: CommandContext) -> None:
        version = context.get_app_version()
        about_text = (
            f"󰘳 **Assistant v{version}**\n\n"
            "Asistente nativo para el escritorio Hyprland con soporte agéntico, MCP, voz y gestión de ventanas.\n\n"
            f"- **Versión**: `{version}`\n"
            "- **Framework**: PyQt6 + QML (Material Design)\n"
            "- **Pipeline**: FastFlowLM / llama.cpp + Edge-TTS/Piper + Whisper STT"
        )
        context.emit_message("sys", about_text)
