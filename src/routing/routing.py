"""Decision Router (System One / d3:edge) for Assistant.

Categorizes user intent and dynamically filters available system and MCP tools
to optimize prompt context and latency.
"""

import json
import logging
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("assistant.routing")

DEFAULT_CATEGORIES: Dict[str, List[str]] = {
    "conversational": [],
    "datetime": ["get_current_time"],
    "desktop_and_apps": ["open_application", "manage_desktop", "get_system_knowledge"],
    "filesystem": ["read_file", "write_file", "replace_in_file", "execute_command"],
    "notes": ["obsidian_search", "obsidian_read", "obsidian_write"],
    "weather": ["get_weather"],
    "cinema": ["get_cinema_showtimes"],
    "web_search": ["web_search"],
}

TIME_KEYWORDS = [
    "hoy", "ayer", "antier", "mañana", "hace ", "última semana", "este mes",
    "qué hora", "que hora", "qué día", "que dia", "en qué fecha", "en que fecha",
    "today", "yesterday", "ago", "last week", "current time", "current date"
]


DEFAULT_CRITERIA: Dict[str, str] = {
    "conversational": "Conversación informal, charla, respuestas teóricas, saludos o explicaciones conceptuales sin datos de última hora ni reportes.",
    "datetime": "Preguntas exclusivas sobre qué hora es, qué día es hoy, la fecha actual o zona horaria.",
    "desktop_and_apps": "Escritorio Hyprland y aplicaciones: abrir o lanzar aplicaciones o archivos (e.g. abrir cartera.ods con libreoffice, abre chrome, lanza kitty, confirmación afirmativa del usuario como 'si' para abrir o ver un archivo), reproducir música o canciones (reproduce pura dinamita), cerrar ventanas, alternar ventanas flotantes (flota chrome) o cambiar de workspace.",
    "filesystem": "Operaciones con archivos y terminal: buscar archivos en el disco (/home, extensiones .ods, .pdf), listar carpetas (lista canciones en descargas/Downloads), leer o escribir archivos, o ejecutar comandos bash.",
    "notes": "Buscar o leer notas en la aplicación Obsidian.",
    "weather": "Previsión del tiempo, lluvia, viento, nieve, temperatura o clima meteorológico en ciudades o regiones (e.g. qué tiempo hará en León, lloverá mañana, si puedo ir en bici, moto o pasear según el clima, qué ropa ponerme). No elegir para eventos, cine, compras o lugares de ocio.",
    "cinema": "Cartelera de cine, películas en emisión, horarios de sesiones de cine o cines (e.g. qué echan en el cine hoy, horarios de Odeon León, cartelera Van Gogh, a qué hora es la película).",
    "web_search": "Buscar información actualizada en internet: reportes, noticias del día de hoy, sucesos recientes, última hora, tiendas, restaurantes o páginas web.",
}


class DecisionRouter:
    """Manages routing of user queries to appropriate tool categories."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self.enabled: bool = bool(cfg.get("enabled", False))
        self.endpoint: str = cfg.get("endpoint", "")
        self.model: str = cfg.get("model", "d3:edge")
        self.timeout: float = float(cfg.get("timeout", 1.5))
        self.confidence_threshold: float = float(cfg.get("confidence_threshold", 0.60))
        self.categories: Dict[str, List[str]] = dict(DEFAULT_CATEGORIES)
        self.criteria: Dict[str, str] = dict(DEFAULT_CRITERIA)
        self.global_tools: Set[str] = set()

    def register_mcp_servers(self, mcp_manager: Any) -> None:
        """Register MCP tools dynamically into categories based on server configuration.

        - If server type == 'web_search': tools are handled internally by web_search wrapper.
        - If server type is in categories or new (e.g. 'rag', 'github'): category is registered with custom criteria.
        - If server type is 'always' or unspecified: tools are marked as global.
        """
        if not mcp_manager or not hasattr(mcp_manager, "tools"):
            return

        for tool_name, meta in mcp_manager.tools.items():
            server_type = meta.get("server_type", "")
            s_meta = mcp_manager.servers_meta.get(meta.get("server", ""), {})
            description = s_meta.get("description", "") or f"Operaciones y consultas relacionadas con {server_type or meta.get('server')}."

            if not server_type or server_type == "always":
                self.global_tools.add(tool_name)
            elif server_type == "web_search":
                # web_search tools are proxied through the unified web_search function
                # so we don't expose them separately, but ensure web_search is in the category
                if "web_search" not in self.categories.setdefault("web_search", []):
                    self.categories["web_search"].append("web_search")
            else:
                # Custom category (e.g., 'rag', 'github', 'home_assistant')
                cat_tools = self.categories.setdefault(server_type, [])
                if tool_name not in cat_tools:
                    cat_tools.append(tool_name)
                if server_type not in self.criteria:
                    self.criteria[server_type] = description

    def register_mcp_tools(self, tool_names: List[str]) -> None:
        """Backwards compatibility method."""
        pass

    def resolve_endpoint(self, base_url: str = "") -> str:
        """Determine endpoint URL, falling back to base_url or default."""
        if self.endpoint:
            return self.endpoint
        if base_url:
            parsed = urllib.parse.urlparse(base_url)
            return f"{parsed.scheme}://{parsed.netloc}/v1/systemone"
        return "http://127.0.0.1:52625/v1/systemone"

    def resolve_tools_for_query(
        self,
        user_query: str,
        available_tools: List[Dict[str, Any]],
        recent_context: str = "",
        base_url: str = "",
    ) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Route incoming user query using the d3:edge decision model (/v1/systemone).

        Returns (filtered_tools, decision_info_dict). Falls back to (available_tools, None)
        if disabled, unreachable, or if decision confidence is below threshold.
        """
        if not self.enabled or not user_query:
            return available_tools, None

        endpoint = self.resolve_endpoint(base_url)

        payload = {
            "model": self.model,
            "state": {
                "user_query": user_query[:1000],
                "context": recent_context[:1000] if recent_context else "",
            },
            "questions": {
                "category": {
                    "type": "choice",
                    "instructions": (
                        "Determina qué categoría de herramientas del sistema es requerida para satisfacer la petición del usuario en español teniendo en cuenta el contexto previo. "
                        "Elige 'conversational' si es charla informal, preguntas teóricas, saludos o explicaciones que no necesitan herramientas."
                    ),
                    "criteria": self.criteria,
                },
                "needs_datetime": {
                    "type": "noul",
                    "instructions": (
                        "Does resolving this request require knowing the current date, time, weekday, or resolving relative time expressions "
                        "(e.g. 'hoy', 'ayer', 'antier', 'mañana', 'hace unos días', 'última semana', 'noticias de ayer', 'ficheros de hace 3 días', "
                        "'today', 'yesterday', 'days ago', 'last week')?"
                    ),
                    "criteria": {
                        "true": "The user query mentions time, dates, or relative temporal anchors (today, yesterday, ago, current time/date).",
                        "false": "The user query does not depend on knowing the current date or time.",
                    },
                },
            },
        }

        t_start = time.time()
        try:
            req = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                elapsed_ms = int((time.time() - t_start) * 1000)
                if response.status == 200:
                    res_data = json.loads(response.read().decode("utf-8"))
                    answers = res_data.get("answers", {})

                    # Check category question
                    cat_info = answers.get("category", {})
                    choice = cat_info.get("choice")
                    probabilities = cat_info.get("probabilities", {})
                    confidence = probabilities.get(choice, 1.0) if choice else 0.0

                    # Check needs_datetime question (noul: true / false)
                    dt_info = answers.get("needs_datetime", {})
                    dt_choice = str(dt_info.get("choice", "")).lower() in ["true", "yes", "1"]
                    dt_probs = dt_info.get("probabilities", {})
                    dt_confidence = dt_probs.get("true", 0.0) if isinstance(dt_probs, dict) else 0.0

                    # Heuristic keyword check as a robust safety net for relative time expressions
                    lower_query = user_query.lower()
                    has_time_keywords = any(kw in lower_query for kw in TIME_KEYWORDS)
                    include_datetime = dt_choice or (dt_confidence > 0.40) or has_time_keywords

                    info = {
                        "choice": choice,
                        "confidence": confidence,
                        "needs_datetime": include_datetime,
                    }

                    if choice in self.categories and confidence >= self.confidence_threshold:
                        target_names: Set[str] = set(self.categories[choice])
                        if include_datetime and choice != "conversational":
                            target_names.add("get_current_time")
                        elif include_datetime and choice == "conversational":
                            # If conversational but strongly refers to relative time, allow get_current_time
                            target_names.add("get_current_time")

                        # Include global tools if the choice requires tools (non-conversational)
                        if choice != "conversational":
                            target_names.update(self.global_tools)

                        filtered = [
                            t for t in available_tools
                            if t.get("function", {}).get("name") in target_names
                        ]
                        info["tools_count"] = len(filtered)
                        info["tools_names"] = [t["function"]["name"] for t in filtered]
                        logger.info(
                            f"[Router] Query: '{user_query[:60]}' -> Category: {choice} ({int(confidence*100)}%) | "
                            f"Tools ({len(filtered)}): {info['tools_names']} | Latency: {elapsed_ms}ms"
                        )
                        return filtered, info

                    logger.warning(
                        f"[Router] Confidence too low for '{choice}' ({int(confidence*100)}% < {int(self.confidence_threshold*100)}%). "
                        f"Passing all {len(available_tools)} tools as fallback | Latency: {elapsed_ms}ms"
                    )
                    return available_tools, {
                        "choice": choice,
                        "confidence": confidence,
                        "tools_count": len(available_tools),
                        "fallback": True,
                    }
        except Exception as e:
            logger.warning(f"[Router] Bypass/Fallback (Error connecting to {endpoint}): {e}")

        return available_tools, None
