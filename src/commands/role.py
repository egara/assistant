"""Role management slash command: /role."""

from .base import BaseCommand, CommandContext


class RoleCommand(BaseCommand):
    """Lists available assistant roles or switches active system prompt role."""

    name = "/role"
    aliases = []
    description = "Muestra los roles disponibles o cambia al indicado. Usa `/role <nombre>` para alternar a uno específico."
    usage = "/role [nombre]"

    def execute(self, arg: str, context: CommandContext) -> None:
        target = (arg or "").strip().lower()
        roles = context.get_available_roles()
        active_role = context.get_active_role()
        role_keys = list(roles.keys())

        if not target:
            # If no target passed, show list of available roles and current active one
            lines = ["🎭 **Roles disponibles:**"]
            for r_id, r_info in roles.items():
                r_name = r_info.get("name", r_id)
                r_desc = r_info.get("description", "")
                r_src = r_info.get("source", "system")
                status = " **[ACTIVO]**" if r_id == active_role else ""
                lines.append(f"- • `{r_id}`: **{r_name}** ({r_src}){status}\n  _{r_desc}_")
            lines.append("\nUso: `/role <nombre>` para cambiar de rol (ej: `/role news`, `/role default`).")
            context.emit_message("sys", "\n".join(lines))
            return

        # Direct match check
        matched_id = None
        for r_id in role_keys:
            if target == r_id.lower() or target == roles[r_id].get("name", "").lower():
                matched_id = r_id
                break

        if matched_id:
            success = context.set_active_role(matched_id)
            if success:
                r_info = roles[matched_id]
                r_name = r_info.get("name", matched_id)
                context.emit_message(
                    "sys",
                    f"🎭 Rol cambiado con éxito a: **{r_name}** (`{matched_id}`)\n"
                    f"El nuevo comportamiento y directrices ya están activos para esta sesión."
                )
            else:
                context.emit_message("sys", f"❌ Error al activar el rol `{matched_id}`.")
        else:
            lines = [f"⚠️ No se encontró el rol **'{target}'**.", "\n🎭 **Roles disponibles:**"]
            for r_id, r_info in roles.items():
                r_name = r_info.get("name", r_id)
                status = " **[ACTIVO]**" if r_id == active_role else ""
                lines.append(f"- • `{r_id}`: **{r_name}**{status}")
            lines.append("\nUso: `/role <nombre>`")
            context.emit_message("sys", "\n".join(lines))
