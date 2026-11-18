"""Time-related tools for Assistant."""

import datetime
import json
import zoneinfo

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Get the real-time date, time, weekday, and timezone from the system host. Crucial for anchoring time-sensitive queries, relative dates ('today', 'yesterday', 'this week'), and internet searches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "Optional IANA timezone name (e.g. 'Europe/Madrid', 'UTC', 'America/New_York'). If omitted, uses the system local timezone.",
                    }
                },
            },
        },
    }
]


def get_current_time(timezone_name=""):
    """
    Returns current local date, time, weekday, and timezone information.
    Can optionally convert to any requested IANA timezone (e.g. Europe/Madrid, America/New_York).
    """
    try:
        if timezone_name and timezone_name.strip():
            tz = zoneinfo.ZoneInfo(timezone_name.strip())
            now = datetime.datetime.now(tz)
        else:
            now = datetime.datetime.now().astimezone()

        days_es = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
        months_es = [
            "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
            "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
        ]

        day_name = days_es[now.weekday()]
        month_name = months_es[now.month - 1]

        info = {
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "day_of_week": day_name,
            "day": now.day,
            "month": month_name,
            "year": now.year,
            "timezone": str(now.tzname() or now.tzinfo),
            "iso": now.isoformat(),
            "formatted": f"{day_name}, {now.day} de {month_name} de {now.year}, {now.strftime('%H:%M:%S')} ({now.tzname()})",
        }
        return json.dumps(info, ensure_ascii=False)
    except Exception as e:
        return f"Error retrieving current time: {str(e)}"
