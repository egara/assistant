"""Cinema showtimes and listings tools using eCartelera."""

import datetime
import logging
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple
import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

logger = logging.getLogger("assistant.tools.cinema")

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_cinema_showtimes",
            "description": "Obtiene la cartelera de películas, horarios y sesiones para un cine o ciudad y día determinado (ej: hoy, mañana, sábado).",
            "parameters": {
                "type": "object",
                "properties": {
                    "cinema": {
                        "type": "string",
                        "description": "Nombre del cine o centro comercial (ej: 'Odeon León', 'Cines Van Gogh', 'Kinepolis Madrid').",
                    },
                    "date": {
                        "type": "string",
                        "description": "Día o fecha de consulta: 'hoy', 'mañana', día de la semana (ej: 'viernes', 'sábado') o fecha 'YYYY-MM-DD'. Por defecto 'hoy'.",
                    },
                    "movie": {
                        "type": "string",
                        "description": "Filtro opcional si el usuario pregunta por una película en específico.",
                    },
                },
                "required": ["cinema"],
            },
        },
    }
]

# Fast in-memory cache for common cinemas
KNOWN_CINEMAS: Dict[str, str] = {
    "odeon leon": "211",
    "odeon multicines leon": "211",
    "espacio leon": "211",
    "cinebox espacio leon": "211",
    "van gogh": "212",
    "cines van gogh": "212",
    "van gogh leon": "212",
}

WEEKDAY_MAP = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "miércoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "sábado": 5,
    "domingo": 6,
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _normalize_text(text: str) -> str:
    """Normalize text by converting to lower case and removing diacritics/accents."""
    nfkd = unicodedata.normalize("NFD", text.lower().strip())
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


def _resolve_cinema_id(cinema_name: str) -> Optional[Tuple[str, str]]:
    """Resolve eCartelera cinema ID given a cinema name or location."""
    clean_name = _normalize_text(cinema_name)

    for alias, cid in KNOWN_CINEMAS.items():
        norm_alias = _normalize_text(alias)
        if norm_alias in clean_name or clean_name in norm_alias:
            return cid, cinema_name

    # Fallback to dynamic lookup with DuckDuckGo
    try:
        query = f"site:ecartelera.com/cines/ {cinema_name}"
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3, backend="lite"))
            for r in results:
                m = re.search(r"https://www\.ecartelera\.com/cines/(\d+),\d+,\d+\.html", r.get("href", ""))
                if m:
                    cinema_id = m.group(1)
                    title = r.get("title", cinema_name)
                    # Cache for next calls
                    KNOWN_CINEMAS[clean_name] = cinema_id
                    return cinema_id, title
    except Exception as e:
        logger.warning(f"[Cinema] Error discovering cinema ID for '{cinema_name}': {e}")

    return None


def _calculate_day_offset(date_str: str) -> int:
    """Calculate day index offset (0=today, 1=tomorrow, ...) for eCartelera URLs."""
    if not date_str:
        return 0

    clean_date = date_str.strip().lower()

    if clean_date in ["hoy", "today", "ahora", "current"]:
        return 0
    if clean_date in ["mañana", "manana", "tomorrow"]:
        return 1
    if clean_date in ["pasado mañana", "pasado manana", "overmorrow"]:
        return 2

    # Check day of week names
    today = datetime.date.today()
    current_weekday = today.weekday()  # Monday is 0 and Sunday is 6

    for day_name, target_weekday in WEEKDAY_MAP.items():
        if day_name in clean_date:
            offset = (target_weekday - current_weekday) % 7
            return offset if offset >= 0 else 0

    # Try explicit date parse (YYYY-MM-DD or DD/MM/YYYY)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            target_dt = datetime.datetime.strptime(clean_date, fmt).date()
            diff = (target_dt - today).days
            if 0 <= diff <= 10:
                return diff
        except ValueError:
            pass

    return 0


def get_cinema_showtimes(cinema: str, date: str = "hoy", movie: str = "") -> str:
    """Retrieve cinema showtimes from eCartelera."""
    resolved = _resolve_cinema_id(cinema)
    if not resolved:
        return f"No se pudo encontrar el cine '{cinema}' en la base de datos de carteleras de eCartelera."

    cinema_id, cinema_title = resolved
    day_offset = _calculate_day_offset(date)
    # eCartelera URL structure: /cines/<id>,<day_offset>,1.html
    url = f"https://www.ecartelera.com/cines/{cinema_id},{day_offset},1.html"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "es-ES,es;q=0.9",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=8)
        if resp.status_code != 200:
            return f"Error al acceder a la cartelera de '{cinema}' (HTTP {resp.status_code})."

        soup = BeautifulSoup(resp.text, "html.parser")

        # Resolve header or actual date displayed on the page
        actual_cinema_name = cinema_title
        header_el = soup.find(["h1", "h2"], class_=re.compile(r"tit|nombre"))
        if header_el:
            actual_cinema_name = header_el.get_text(strip=True)

        # Extract movies and their showtimes
        movies_found = []
        for tinfo in soup.find_all(class_="tinfo"):
            tit_p = tinfo.find("p", class_="tit")
            if not tit_p:
                continue

            title = tit_p.get_text(strip=True)
            parent = tinfo.parent

            # Details: duration, genre, age rating
            details_text = ""
            data_p = tinfo.find("p", class_="data")
            if data_p:
                details_text = " • ".join(
                    span.get_text(strip=True) for span in data_p.find_all("span") if span.get_text(strip=True)
                )

            # Hours can be in <a> tags (direct ticket buy) or in <span> tags (e.g. data-session-time)
            raw_hour_texts = [
                el.get_text(strip=True)
                for el in parent.find_all(["a", "span"])
                if re.match(r"^\d{2}:\d{2}$", el.get_text(strip=True))
            ]

            if raw_hour_texts:
                # Deduplicate hours while preserving order
                seen_hours = []
                for h in raw_hour_texts:
                    if h not in seen_hours:
                        seen_hours.append(h)

                if movie:
                    # Filter by movie keyword if requested
                    if movie.lower() in title.lower():
                        movies_found.append((title, details_text, seen_hours))
                else:
                    movies_found.append((title, details_text, seen_hours))

        if not movies_found:
            filter_note = f" para la película '{movie}'" if movie else ""
            return f"No se han encontrado sesiones disponibles{filter_note} en {actual_cinema_name} para el día consultado ({date})."

        target_date_label = (datetime.date.today() + datetime.timedelta(days=day_offset)).strftime("%d/%m/%Y")
        out = [
            f"🎬 **Cartelera {actual_cinema_name}** ({target_date_label} - {date}):\n",
        ]

        for m_title, m_details, m_hours in movies_found:
            details_suffix = f" *({m_details})*" if m_details else ""
            out.append(f"- **{m_title}**{details_suffix}\n  ⏰ Sesiones: `{'`, `'.join(m_hours)}`")

        out.append(f"\n[Ver en eCartelera]({url})")
        return "\n".join(out)

    except Exception as e:
        logger.error(f"[Cinema] Exception fetching showtimes for {cinema}: {e}")
        return f"Error al consultar la cartelera de {cinema}: {e}"
