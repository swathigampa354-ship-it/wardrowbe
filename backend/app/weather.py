"""Keyless weather via Open-Meteo (spec section 15).

Kept because it costs nothing to run: no API key, one HTTP GET, and the AI
prompt uses it for season/layering. Disabled entirely with WEATHER_ENABLED=false.

Removed from the original weather_service.py (533 lines): Redis result caching,
IP-geolocation fallback (sends the visitor's IP to a third party), the forecast
range plumbing, and the per-user weather preferences fan-out.
"""

import logging
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import User

logger = logging.getLogger(__name__)


def _settings():
    """Accessed, not snapshotted: a module-level copy would freeze the env at import."""
    return get_settings()


CONDITIONS = {
    0: "clear",
    1: "mainly-clear",
    2: "partly-cloudy",
    3: "overcast",
    45: "fog",
    48: "rime-fog",
    51: "light-drizzle",
    53: "drizzle",
    55: "heavy-drizzle",
    61: "light-rain",
    63: "rain",
    65: "heavy-rain",
    66: "freezing-rain",
    67: "heavy-freezing-rain",
    71: "light-snow",
    73: "snow",
    75: "heavy-snow",
    77: "snow-grains",
    80: "rain-showers",
    81: "showers",
    82: "violent-showers",
    85: "snow-showers",
    86: "heavy-snow-showers",
    95: "thunderstorm",
    96: "thunderstorm-hail",
    99: "severe-thunderstorm",
}


async def geocode(location: str) -> tuple[float, float] | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": location, "count": 1, "language": "en"},
                headers={"User-Agent": "Wardrowbe-Trial/1.0"},
            )
            response.raise_for_status()
            results = response.json().get("results") or []
        if not results:
            return None
        top = results[0]
        return float(top["latitude"]), float(top["longitude"])
    except Exception as exc:  # noqa: BLE001 - weather must never break a request
        logger.info("Geocoding failed for %r: %s", location, exc)
        return None


async def fetch_weather(latitude: float, longitude: float) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{_settings().openmeteo_url.rstrip('/')}/forecast",
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                    "precipitation_probability,precipitation,wind_speed_10m,weather_code,is_day,uv_index",
                    "timezone": "auto",
                    "forecast_days": 1,
                },
            )
            response.raise_for_status()
            current = response.json().get("current") or {}
    except Exception as exc:  # noqa: BLE001
        logger.info("Weather fetch failed: %s", exc)
        return None

    if not current:
        return None
    code = current.get("weather_code")
    return {
        "temperature": current.get("temperature_2m", 20),
        "feels_like": current.get("apparent_temperature", current.get("temperature_2m", 20)),
        "humidity": current.get("relative_humidity_2m", 50),
        "precipitation_chance": current.get("precipitation_probability", 0) or 0,
        "precipitation_mm": current.get("precipitation", 0) or 0,
        "wind_speed": current.get("wind_speed_10m", 0) or 0,
        "condition": CONDITIONS.get(int(code), "unknown") if code is not None else "unknown",
        "condition_code": code,
        "is_day": bool(current.get("is_day", True)),
        "uv_index": current.get("uv_index", 0) or 0,
        "timestamp": current.get("time"),
    }


async def resolve_coords(db: AsyncSession, user_id: str) -> tuple[float, float] | None:
    if _settings().default_latitude is not None and _settings().default_longitude is not None:
        return _settings().default_latitude, _settings().default_longitude

    user = await db.get(User, user_id)
    if user and user.location_lat is not None and user.location_lon is not None:
        return user.location_lat, user.location_lon
    if user and user.location_name:
        return await geocode(user.location_name)

    if _settings().default_location:
        coords = await geocode(_settings().default_location)
        if coords and user:
            user.location_lat, user.location_lon = coords
            user.location_name = _settings().default_location
            await db.flush()
        return coords
    return None


async def current_weather(db: AsyncSession, user_id: str) -> dict[str, Any] | None:
    if not _settings().weather_enabled:
        return None
    coords = await resolve_coords(db, user_id)
    if coords is None:
        return None
    return await fetch_weather(*coords)
