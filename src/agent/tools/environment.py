"""Realtime weather and air-quality observations for the agent runtime.

The tool is intentionally read-only: it fetches observations but cannot control a
device. Automation decisions still pass through the deterministic allowlist in
``src.services.automation`` before reaching the IoT bus.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from src.config import Settings, get_settings

_WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
_AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"

_WEATHER_CURRENT = (
    "temperature_2m,relative_humidity_2m,apparent_temperature,"
    "precipitation,rain,weather_code,wind_speed_10m,shortwave_radiation"
)
_AIR_CURRENT = "pm2_5,us_aqi,uv_index"


class EnvironmentUnavailableError(RuntimeError):
    """Raised when realtime observations are incomplete or cannot be fetched."""


def _weather_params(latitude: float, longitude: float) -> dict[str, str | float]:
    return {"latitude": latitude, "longitude": longitude, "timezone": "auto", "current": _WEATHER_CURRENT}


def _air_params(latitude: float, longitude: float) -> dict[str, str | float]:
    return {"latitude": latitude, "longitude": longitude, "timezone": "auto", "current": _AIR_CURRENT}


@dataclass(frozen=True, slots=True)
class EnvironmentSnapshot:
    observed_at: datetime
    source: str
    temperature_c: float
    apparent_temperature_c: float
    humidity_percent: float
    precipitation_mm: float
    rain_mm: float
    weather_code: int
    wind_speed_kmh: float
    shortwave_radiation_wm2: float
    pm25_ugm3: float
    us_aqi: float
    uv_index: float

    @property
    def is_raining(self) -> bool:
        # WMO weather codes 51-67/80-82/95-99 cover drizzle, rain and storms.
        code = self.weather_code
        rainy_code = 51 <= code <= 67 or 80 <= code <= 82 or 95 <= code <= 99
        return self.precipitation_mm > 0 or self.rain_mm > 0 or rainy_code

    @property
    def sunlight_percent(self) -> float:
        # 1000 W/m² is a useful full-sun reference for the existing 0..100 sensor.
        return max(0.0, min(100.0, self.shortwave_radiation_wm2 / 10.0))


def _finite_number(payload: dict[str, Any], key: str, *, source: str) -> float:
    value = payload.get(key)
    if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise EnvironmentUnavailableError(f"{source} thiếu giá trị hợp lệ cho '{key}'.")
    return float(value)


def parse_open_meteo(weather: dict[str, Any], air_quality: dict[str, Any]) -> EnvironmentSnapshot:
    """Validate and combine the two Open-Meteo current-condition responses."""
    current_weather = weather.get("current")
    current_air = air_quality.get("current")
    if not isinstance(current_weather, dict) or not isinstance(current_air, dict):
        raise EnvironmentUnavailableError("Open-Meteo không trả về khối current hợp lệ.")

    return EnvironmentSnapshot(
        observed_at=datetime.now(UTC),
        source="open-meteo",
        temperature_c=_finite_number(current_weather, "temperature_2m", source="weather"),
        apparent_temperature_c=_finite_number(current_weather, "apparent_temperature", source="weather"),
        humidity_percent=_finite_number(current_weather, "relative_humidity_2m", source="weather"),
        precipitation_mm=_finite_number(current_weather, "precipitation", source="weather"),
        rain_mm=_finite_number(current_weather, "rain", source="weather"),
        weather_code=int(_finite_number(current_weather, "weather_code", source="weather")),
        wind_speed_kmh=_finite_number(current_weather, "wind_speed_10m", source="weather"),
        shortwave_radiation_wm2=_finite_number(current_weather, "shortwave_radiation", source="weather"),
        pm25_ugm3=_finite_number(current_air, "pm2_5", source="air-quality"),
        us_aqi=_finite_number(current_air, "us_aqi", source="air-quality"),
        uv_index=_finite_number(current_air, "uv_index", source="air-quality"),
    )


class OpenMeteoEnvironmentTool:
    """Fetch and cache current weather/AQI for the configured home coordinates."""

    def __init__(self, settings: Settings | None = None, *, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._cached: EnvironmentSnapshot | None = None
        self._cached_at = 0.0
        self._lock = asyncio.Lock()

    async def current(self, *, force: bool = False) -> EnvironmentSnapshot:
        if not self._settings.environment_realtime_enabled:
            raise EnvironmentUnavailableError(
                "Environment realtime đã tắt (ENVIRONMENT_REALTIME_ENABLED=false trong .env)"
            )
        max_age = self._settings.environment_refresh_seconds
        if not force and self._cached is not None and time.monotonic() - self._cached_at < max_age:
            return self._cached

        async with self._lock:
            if not force and self._cached is not None and time.monotonic() - self._cached_at < max_age:
                return self._cached
            snapshot = await self._fetch()
            self._cached = snapshot
            self._cached_at = time.monotonic()
            return snapshot

    async def _fetch(self) -> EnvironmentSnapshot:
        weather_params = _weather_params(
            self._settings.environment_latitude, self._settings.environment_longitude
        )
        air_params = _air_params(
            self._settings.environment_latitude, self._settings.environment_longitude
        )

        try:
            if self._client is not None:
                weather_response, air_response = await asyncio.gather(
                    self._client.get(_WEATHER_URL, params=weather_params),
                    self._client.get(_AIR_QUALITY_URL, params=air_params),
                )
            else:
                timeout = httpx.Timeout(self._settings.environment_timeout_seconds)
                headers = {"User-Agent": f"{self._settings.app_name}/1.0 environment-tool"}
                async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
                    weather_response, air_response = await asyncio.gather(
                        client.get(_WEATHER_URL, params=weather_params),
                        client.get(_AIR_QUALITY_URL, params=air_params),
                    )
            weather_response.raise_for_status()
            air_response.raise_for_status()
            return parse_open_meteo(weather_response.json(), air_response.json())
        except EnvironmentUnavailableError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise EnvironmentUnavailableError(
                f"Không lấy được dữ liệu môi trường realtime: {exc}. "
                f"Để tắt: set ENVIRONMENT_REALTIME_ENABLED=false trong .env "
                f"(file: src/agent/tools/environment.py)"
            ) from exc


# --- Đường ĐỒNG BỘ cho reasoning pipeline (hỏi thời tiết trong hội thoại) -------------
#
# `OpenMeteoEnvironmentTool` ở trên là async, dành cho vòng automation nền. Pipeline suy
# luận chạy đồng bộ nên cần một lối gọi riêng — nhưng DÙNG CHUNG URL, tham số và parser
# (§4: một nguồn sự thật cho tích hợp Open-Meteo). Toạ độ nhà vẫn lấy từ Settings; câu hỏi
# nêu tên thành phố thì geocode trước qua chính Open-Meteo.

@dataclass(frozen=True, slots=True)
class GeoPlace:
    name: str
    country: str
    latitude: float
    longitude: float

    @property
    def label(self) -> str:
        return f"{self.name}, {self.country}" if self.country else self.name


_GEO_CACHE: dict[str, GeoPlace | None] = {}
_SNAPSHOT_CACHE: dict[tuple[float, float], tuple[float, EnvironmentSnapshot]] = {}


def clear_caches() -> None:
    """Xoá cache geocode + snapshot (dùng cho test; production để TTL tự hết hạn)."""
    _GEO_CACHE.clear()
    _SNAPSHOT_CACHE.clear()


def _sync_client(settings: Settings, client: httpx.Client | None) -> tuple[httpx.Client, bool]:
    if client is not None:
        return client, False
    timeout = httpx.Timeout(settings.environment_timeout_seconds)
    headers = {"User-Agent": f"{settings.app_name}/1.0 environment-tool"}
    return httpx.Client(timeout=timeout, headers=headers), True


def geocode_city(
    name: str,
    *,
    client: httpx.Client | None = None,
    settings: Settings | None = None,
) -> GeoPlace | None:
    """Tên thành phố → toạ độ, qua Open-Meteo Geocoding. None nếu không tìm thấy."""
    settings = settings or get_settings()
    key = " ".join(name.strip().lower().split())
    if not key:
        return None
    if key in _GEO_CACHE:
        return _GEO_CACHE[key]

    http, owned = _sync_client(settings, client)
    try:
        response = http.get(
            _GEOCODING_URL,
            params={"name": name.strip(), "count": 1, "language": "vi", "format": "json"},
        )
        response.raise_for_status()
        results = response.json().get("results") or []
        if not results:
            _GEO_CACHE[key] = None
            return None
        top = results[0]
        place = GeoPlace(
            name=str(top.get("name") or name.strip()),
            country=str(top.get("country") or ""),
            latitude=float(top["latitude"]),
            longitude=float(top["longitude"]),
        )
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        return None
    finally:
        if owned:
            http.close()
    _GEO_CACHE[key] = place
    return place


def fetch_environment_sync(
    *,
    latitude: float,
    longitude: float,
    client: httpx.Client | None = None,
    settings: Settings | None = None,
    force: bool = False,
) -> EnvironmentSnapshot:
    """Lấy thời tiết + AQI hiện tại cho một toạ độ (đồng bộ), cache theo toạ độ + TTL."""
    settings = settings or get_settings()
    if not settings.environment_realtime_enabled:
        raise EnvironmentUnavailableError(
            "Environment realtime đã tắt (ENVIRONMENT_REALTIME_ENABLED=false trong .env)"
        )
    cache_key = (round(latitude, 2), round(longitude, 2))
    cached = _SNAPSHOT_CACHE.get(cache_key)
    if not force and cached is not None and time.monotonic() - cached[0] < settings.environment_refresh_seconds:
        return cached[1]

    http, owned = _sync_client(settings, client)
    try:
        weather_response = http.get(_WEATHER_URL, params=_weather_params(latitude, longitude))
        air_response = http.get(_AIR_QUALITY_URL, params=_air_params(latitude, longitude))
        weather_response.raise_for_status()
        air_response.raise_for_status()
        snapshot = parse_open_meteo(weather_response.json(), air_response.json())
    except EnvironmentUnavailableError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise EnvironmentUnavailableError(f"Không lấy được dữ liệu thời tiết: {exc}") from exc
    finally:
        if owned:
            http.close()

    _SNAPSHOT_CACHE[cache_key] = (time.monotonic(), snapshot)
    return snapshot
