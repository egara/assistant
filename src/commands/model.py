"""LLM model management slash commands: /model."""

from .base import BaseCommand, CommandContext


class ModelCommand(BaseCommand):
    """Lists configured LLM models or switches active inference model."""

    name = "/model"
    aliases = []
    description = "Muestra los modelos configurados o alterna al siguiente. Usa `/model <nombre>` para cambiar a uno específico."
    usage = "/model [nombre]"

    def execute(self, arg: str, context: CommandContext) -> None:
        target = (arg or "").strip().lower()
        catalog = context.get_models_catalog()
        keys = list(catalog.keys())
        active_key = context.get_active_model_key()

        if not target:
            # If there's more than one, cycle to the next
            if len(keys) > 1:
                curr_idx = keys.index(active_key) if active_key in keys else 0
                next_idx = (curr_idx + 1) % len(keys)
                new_key = keys[next_idx]
                context.set_active_model_key(new_key)
                curr_cfg = catalog[new_key]
                m_name = curr_cfg.get("name", new_key)
                context.active_model_changed(f"{new_key} ({m_name})")
                context.available_models_changed(context.get_models_list())
                context.emit_message("sys", f"🔄 Modelo cambiado a: **{new_key}** (`{m_name}`)")
                return

        # Direct match or listing
        matched_key = None
        for k in keys:
            if target == k.lower() or target == catalog[k].get("name", "").lower():
                matched_key = k
                break

        if matched_key:
            context.set_active_model_key(matched_key)
            curr_cfg = catalog[matched_key]
            m_name = curr_cfg.get("name", matched_key)
            context.active_model_changed(f"{matched_key} ({m_name})")
            context.available_models_changed(context.get_models_list())
            context.emit_message("sys", f"🔄 Modelo seleccionado: **{matched_key}** (`{m_name}`)")
        else:
            lines = ["🤖 **Modelos disponibles en catálogo:**"]
            for k in keys:
                m_info = catalog[k]
                m_name = m_info.get("name", k)
                status = " **[ACTIVO]**" if k == active_key else ""
                vision_tag = " `[vision]`" if m_info.get("vision_enabled") else ""
                lines.append(f"- • `{k}`: {m_name}{vision_tag} ({m_info.get('base_url')}){status}")
            lines.append("\nUso: `/model <nombre>` o solo `/model` para alternar al siguiente.")
            context.emit_message("sys", "\n".join(lines))
