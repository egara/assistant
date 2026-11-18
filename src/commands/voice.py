"""Voice and TTS slash commands: /voice, /tts."""

from .base import BaseCommand, CommandContext


class VoiceCommand(BaseCommand):
    """Toggles spoken response audio (TTS) on or off."""

    name = "/voice"
    aliases = ["/tts"]
    description = "Activa o desactiva la síntesis de voz en las respuestas (TTS)."
    usage = "/voice o /tts"

    def execute(self, arg: str, context: CommandContext) -> None:
        context.toggle_tts()
