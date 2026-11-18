"""Session lifecycle slash commands: /new, /reset, /clear."""

from .base import BaseCommand, CommandContext


class SessionCommand(BaseCommand):
    """Resets the conversation session, clearing context and chat bubbles."""

    name = "/new"
    aliases = ["/reset", "/clear"]
    description = "Inicia una nueva sesión desde cero, eliminando el contexto y limpiando el chat."
    usage = "/new o /reset o /clear"

    def execute(self, arg: str, context: CommandContext) -> None:
        context.reset_session()
