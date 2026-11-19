"""Cinema showtimes and listings tools using tucine.app."""

import datetime
import logging
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("assistant.tools.cinema")

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_cinema_showtimes",
            "description": "Obtiene la cartelera de películas, horarios y sesiones para un cine o ciudad y día determinado (ej: hoy, mañana, sábado) desde tucine.app.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cinema": {
                        "type": "string",
                        "description": "Nombre del cine o centro comercial (ej: 'Odeon León', 'Cines Van Gogh', 'Kinepolis', 'Yelmo').",
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
    "odeon leon": "1504-odeon-leon",
    "odeon multicines leon": "1504-odeon-leon",
    "espacio leon": "1504-odeon-leon",
    "cinebox espacio leon": "1504-odeon-leon",
    "van gogh": "1985-cines-van-gogh",
    "cines van gogh": "1985-cines-van-gogh",
    "van gogh leon": "1985-cines-van-gogh",
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

_ALL_CINEMAS_CACHE: Optional[List[Tuple[str, str]]] = None


def _normalize_text(text: str) -> str:
    """Normalize text by converting to lower case and removing diacritics/accents."""
    nfkd = unicodedata.normalize("NFD", text.lower().strip())
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


def _fetch_all_cinemas_index() -> List[Tuple[str, str]]:
    """Fetch and cache all Spanish cinemas list from tucine.app/cines/."""
    global _ALL_CINEMAS_CACHE
    if _ALL_CINEMAS_CACHE is not None:
        return _ALL_CINEMAS_CACHE

    cinemas = []
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
    try:
        r = requests.get("https://tucine.app/cines/", headers=headers, timeout=6)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith("/cine/") and href != "/cines/":
                    name = a.get_text(separator=" ", strip=True)
                    slug = href.replace("/cine/", "").strip("/")
                    cinemas.append((name, slug))
            _ALL_CINEMAS_CACHE = cinemas
    except Exception as e:
        logger.warning(f"[Cinema] Error fetching /cines/ index: {e}")

    return _ALL_CINEMAS_CACHE or []


def _resolve_cinema_slug(cinema_name: str) -> Optional[Tuple[str, str]]:
    """Resolve tucine.app cinema slug given a cinema name or location."""
    clean_name = _normalize_text(cinema_name)

    # 1. Fast cache check
    for alias, slug in KNOWN_CINEMAS.items():
        norm_alias = _normalize_text(alias)
        if norm_alias in clean_name or clean_name in norm_alias:
            return slug, cinema_name

    # 2. Search in tucine.app/cines/ catalog
    all_cinemas = _fetch_all_cinemas_index()
    cinema_tokens = clean_name.split()

    best_match = None
    best_score = 0
    for display_name, slug in all_cinemas:
        norm_display = _normalize_text(display_name)
        score = sum(1 for tok in cinema_tokens if tok in norm_display or tok in slug)
        if score > best_score:
            best_score = score
            best_match = (slug, display_name)

    if best_match and best_score >= max(1, len(cinema_tokens) - 1):
        KNOWN_CINEMAS[clean_name] = best_match[0]
        return best_match

    return None


def _calculate_day_offset(date_str: str) -> int:
    """Calculate day index offset (0=today, 1=tomorrow, ...) for the week."""
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
    """Retrieve cinema showtimes from tucine.app."""
    resolved = _resolve_cinema_slug(cinema)
    if not resolved:
        return f"No se pudo encontrar el cine '{cinema}' en el catálogo de salas de Tu Cine (tucine.app)."

    slug, cinema_title = resolved
    day_offset = _calculate_day_offset(date)
    url = f"https://tucine.app/cine/{slug}/"

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
            return f"Error al acceder a la cartelera de '{cinema}' en Tu Cine (HTTP {resp.status_code})."

        soup = BeautifulSoup(resp.text, "html.parser")

        # Resolve cinema name from H1 if present
        actual_cinema_name = cinema_title
        h1 = soup.find("h1")
        if h1:
            h1_text = h1.get_text(separator=" ", strip=True)
            if ":" in h1_text:
                h1_text = h1_text.split(":")[0].strip()
            actual_cinema_name = h1_text

        # The page contains 7 day panels: <div class="day-panel">
        day_panels = soup.find_all("div", class_="day-panel")
        if not day_panels:
            return f"No se encontraron paneles de horarios en la cartelera de '{actual_cinema_name}'."

        if day_offset >= len(day_panels):
            day_offset = 0

        target_panel = day_panels[day_offset]
        h3 = target_panel.find("h3")
        target_day_title = h3.get_text(strip=True) if h3 else f"Día +{day_offset}"

        # Extract movie blocks
        movies_found = []
        for show_block in target_panel.find_all("article", class_="show-block"):
            title_el = show_block.find(class_=re.compile(r"title")) or show_block.find(["h4", "h3", "h2"])
            if not title_el:
                continue
            title = title_el.get_text(strip=True)

            # Variants and hours
            variants_list = []
            variants = show_block.find_all(class_="show-variant")
            if variants:
                for var in variants:
                    v_label_el = var.find(class_="show-variant__label")
                    v_label = v_label_el.get_text(strip=True) if v_label_el else ""
                    v_times = [
                        btn.get_text(strip=True)
                        for btn in var.find_all(["button", "span", "a"])
                        if re.match(r"^\d{2}:\d{2}$", btn.get_text(strip=True))
                    ]
                    if v_times:
                        label_prefix = f"{v_label}: " if v_label else ""
                        variants_list.append(f"{label_prefix}`{'`, `'.join(v_times)}`")
            else:
                # Direct times chips without variant label
                raw_times = [
                    btn.get_text(strip=True)
                    for btn in show_block.find_all(["button", "span", "a"])
                    if re.match(r"^\d{2}:\d{2}$", btn.get_text(strip=True))
                ]
                if raw_times:
                    # deduplicate
                    seen = []
                    for t in raw_times:
                        if t not in seen:
                            seen.append(t)
                    variants_list.append(f"`{'`, `'.join(seen)}`")

            if variants_list:
                if movie:
                    if _normalize_text(movie) in _normalize_text(title):
                        movies_found.append((title, variants_list))
                else:
                    movies_found.append((title, variants_list))

        if not movies_found:
            filter_note = f" para la película '{movie}'" if movie else ""
            return f"No se han encontrado sesiones disponibles{filter_note} en {actual_cinema_name} para {target_day_title}."

        out = [
            f"🎬 **Cartelera {actual_cinema_name}** ({target_day_title}):\n",
        ]

        for m_title, m_variants in movies_found:
            times_str = " | ".join(m_variants)
            out.append(f"- **{m_title}**\n  ⏰ {times_str}")

        out.append(f"\n[Ver sesiones y comprar entradas en Tu Cine]({url})")
        return "\n".join(out)

    except Exception as e:
        logger.error(f"[Cinema] Exception fetching showtimes from tucine.app for {cinema}: {e}")
        return f"Error al consultar la cartelera de {cinema}: {e}"
