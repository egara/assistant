"""LLM reasoning / thinking effort slash command: /reasoning, /thinking."""

import logging
from typing import Dict, List
from .base import BaseCommand, CommandContext

logger = logging.getLogger("assistant.commands.reasoning")

REASONING_MODES: List[str] = ["none", "low", "medium", "high"]

REASONING_ALIASES: Dict[str, str] = {
    "off": "none",
    "desactivado": "none",
    "disable": "none",
    "disabled": "none",
    "0": "none",
    "false": "none",
    "bajo": "low",
    "1": "low",
    "medio": "medium",
    "med": "medium",
    "2": "medium",
    "alto": "high",
    "3": "high",
}

REASONING_DISPLAY_LABELS: Dict[str, str] = {
    "none": "Desactivado (none)",
    "low": "Bajo (low)",
    "medium": "Medio (medium)",
    "high": "Alto (high)",
}


class ReasoningCommand(BaseCommand):
    """Configures or displays LLM reasoning effort (thinking mode)."""

    name = "/reasoning"
    aliases = ["/thinking"]
    description = (
        "Muestra o cambia el nivel de razonamiento (thinking) del modelo LLM: "
        "`none` (desactivado), `low` (bajo), `medium` (medio), `high` (alto)."
    )
    usage = "/reasoning [none|low|medium|high]"

    def execute(self, arg: str, context: CommandContext) -> None:
        target = (arg or "").strip().lower()
        is_supported = (
            context.is_thinking_supported()
            if hasattr(context, "is_thinking_supported") and context.is_thinking_supported
            else False
        )
        current_effort = (
            context.get_active_reasoning_effort()
            if hasattr(context, "get_active_reasoning_effort") and context.get_active_reasoning_effort
            else "medium"
        )

        if not target:
            support_tag = "🟢 **Soportado**" if is_supported else "⚪ **No soportado por el modelo activo**"
            curr_label = REASONING_DISPLAY_LABELS.get(current_effort, current_effort)
            lines = [
                f"🧠 **Nivel de Razonamiento:** `{curr_label}` ({support_tag})",
                "\n**Niveles disponibles:**",
                "- • `none`: Inferencia directa ultra-rápida sin bloque de pensamiento.",
                "- • `low`: Razonamiento conciso.",
                "- • `medium`: Razonamiento equilibrado estándar.",
                "- • `high`: Razonamiento profundo paso a paso.",
                "\nUso: `/reasoning <none|low|medium|high>` o `/thinking <modo>`",
            ]
            context.emit_message("sys", "\n".join(lines))
            return

        normalized = REASONING_ALIASES.get(target, target)
        if normalized not in REASONING_MODES:
            context.emit_message(
                "sys",
                f"⚠️ Nivel de razonamiento no reconocido: `{target}`.\n"
                f"Opciones válidas: `none`, `low`, `medium`, `high`."
            )
            return

        if hasattr(context, "set_active_reasoning_effort") and context.set_active_reasoning_effort:
            context.set_active_reasoning_effort(normalized)
            label = REASONING_DISPLAY_LABELS.get(normalized, normalized)
            warning = ""
            if not is_supported:
                warning = "\n*(Nota: El modelo actualmente seleccionado no anuncia soporte para thinking, por lo que el parámetro se mantendrá en espera hasta seleccionar un modelo compatible).* "
            context.emit_message(
                "sys",
                f"🧠 Nivel de razonamiento configurado a: **{label}**{warning}"
            )
