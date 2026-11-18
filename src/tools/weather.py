"""Weather tools for Assistant."""

import urllib.parse
import requests

WMO_WEATHER_CODES = {
    0: "Despejado",
    1: "Mayormente despejado",
    2: "Parcialmente nublado",
    3: "Nublado",
    45: "Niebla",
    48: "Niebla con escarcha",
    51: "Llovizna ligera",
    53: "Llovizna moderada",
    55: "Llovizna densa",
    56: "Llovizna helada ligera",
    57: "Llovizna helada densa",
    61: "Lluvia débil",
    63: "Lluvia moderada",
    65: "Lluvia fuerte",
    66: "Lluvia helada débil",
    67: "Lluvia helada fuerte",
    71: "Nevada débil",
    73: "Nevada moderada",
    75: "Nevada fuerte",
    77: "Granizo fino",
    80: "Chubascos débiles",
    81: "Chubascos moderados",
    82: "Chubascos violentos",
    85: "Chubascos de nieve débiles",
    86: "Chubascos de nieve fuertes",
    95: "Tormenta",
    96: "Tormenta con granizo leve",
    99: "Tormenta con granizo fuerte",
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather conditions and forecast for a specific city or region.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "City or location name (e.g. Madrid, Barcelona, Paris, Tokyo).",
                    }
                },
                "required": ["location"],
            },
        },
    }
]


def get_weather(location):
    location = (location or "").strip()
    # 1. Primary engine: Open-Meteo (high-accuracy global weather models)
    try:
        lat, lon, full_name = None, None, location

        if location:
            geo_url = (
                f"https://geocoding-api.open-meteo.com/v1/search?"
                f"name={urllib.parse.quote(location)}&count=5&language=es&format=json"
            )
            geo_res = requests.get(geo_url, timeout=5).json()
            results = geo_res.get("results")
            if results:
                # Prioritize Spain (ES) or first result
                place = next((r for r in results if r.get("country_code") == "ES"), results[0])
                name = place.get("name", location)
                admin = place.get("admin1") or place.get("country") or ""
                country = place.get("country", "")
                full_name = f"{name}, {admin} ({country})" if admin and country != admin else (f"{name} ({country})" if country else name)
                lat, lon = place["latitude"], place["longitude"]
        else:
            # IP geolocation fallback to detect local city
            try:
                ip_data = requests.get("http://ip-api.com/json/", timeout=3).json()
                if ip_data.get("status") == "success":
                    lat, lon = ip_data["lat"], ip_data["lon"]
                    city = ip_data.get("city", "Tu ubicación")
                    region = ip_data.get("regionName", "")
                    full_name = f"{city} ({region})" if region else city
            except (requests.RequestException, ValueError, KeyError):
                pass

        if lat is not None and lon is not None:
            forecast_url = (
                f"https://api.open-meteo.com/v1/forecast?"
                f"latitude={lat}&longitude={lon}&"
                f"current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m&"
                f"daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max&"
                f"timezone=auto&forecast_days=3"
            )
            data = requests.get(forecast_url, timeout=5).json()
            curr = data.get("current", {})
            daily = data.get("daily", {})

            condition = WMO_WEATHER_CODES.get(curr.get("weather_code"), "Variable")
            temp = curr.get("temperature_2m")
            feels = curr.get("apparent_temperature")
            humidity = curr.get("relative_humidity_2m")
            wind = curr.get("wind_speed_10m")

            report = [
                f"Tiempo en {full_name}:",
                f"- Estado: {condition}",
                f"- Temperatura: {temp}°C (sensación térmica {feels}°C)",
                f"- Humedad: {humidity}%",
                f"- Viento: {wind} km/h",
            ]

            if daily and daily.get("time"):
                report.append("\nPrevisión próximos días:")
                for i, date in enumerate(daily["time"]):
                    d_cond = WMO_WEATHER_CODES.get(daily["weather_code"][i], "Variable")
                    t_max = daily["temperature_2m_max"][i]
                    t_min = daily["temperature_2m_min"][i]
                    pop = daily["precipitation_probability_max"][i]
                    report.append(
                        f"- {date}: {d_cond}, mín {t_min}°C / máx {t_max}°C, prob. precipitación: {pop}%"
                    )

            return "\n".join(report)

    except Exception:
        pass

    # 2. Fallback engine: wttr.in
    try:
        query_loc = urllib.parse.quote(location) if location else ""
        url = f"https://wttr.in/{query_loc}?format=%C+%t,+Feels+like+%f,+Wind:+%w,+Humidity:+%h"
        res = requests.get(url, timeout=5)
        if res.status_code == 200 and res.text.strip():
            loc_label = location if location else "tu ubicación"
            return f"Tiempo en {loc_label}: {res.text.strip()}"
    except Exception:
        pass

    return f"No se pudo obtener la información meteorológica para {location or 'tu ubicación'}."
