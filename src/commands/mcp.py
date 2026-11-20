"""MCP server management and status slash command: /mcp."""

from typing import Any, Dict
from .base import BaseCommand, CommandContext


class MCPCommand(BaseCommand):
    """Displays status and configuration of Model Context Protocol (MCP) servers."""

    name = "/mcp"
    aliases = ["/servers"]
    description = "Muestra el estado de conexión y prioridad de los servidores MCP configurados."
    usage = "/mcp"

    def execute(self, arg: str, context: CommandContext) -> None:
        mcp_status: Dict[str, Any] = context.get_mcp_status()

        if not mcp_status:
            context.emit_message(
                "sys",
                "🔌 **Servidores MCP:**\n\nNo hay servidores MCP configurados o activos en `config.json`."
            )
            return

        lines = ["🔌 **Servidores MCP configurados:**\n"]

        for s_name, info in mcp_status.items():
            status = info.get("status", "unknown")
            is_selected = info.get("selected", False)
            stype = info.get("type", "general") or "general"
            priority = info.get("priority", 0)
            url = info.get("url", "")
            tools_count = info.get("tools_count", 0)
            desc = info.get("description", "")

            if status == "active" and is_selected:
                badge = "🟢 **[ACTIVO]**"
                state_str = f"Conectado y en uso ({tools_count} herramientas)"
            elif status == "standby":
                badge = "🟡 **[STANDBY]**"
                state_str = f"Disponible (reemplazado por servidor de mayor prioridad)"
            else:
                badge = "🔴 **[OFFLINE]**"
                state_str = "Inalcanzable al iniciar la sesión"

            desc_line = f"\n  - *{desc}*" if desc else ""
            lines.append(
                f"- {badge} **{s_name}** (`{stype}` | prioridad: {priority})\n"
                f"  - **URL**: `{url}`\n"
                f"  - **Estado**: {state_str}{desc_line}"
            )

        context.emit_message("sys", "\n".join(lines))
