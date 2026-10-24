"""Realtime environment tool and low-risk automation integration."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from src.agent.tools.environment import (
    EnvironmentSnapshot,
    EnvironmentUnavailableError,
    OpenMeteoEnvironmentTool,
)
from src.config import Settings, get_settings
from src.domain.enums import ActionStatus, SuggestionKind
from src.domain.models import ActionLog, Device, Sensor
from src.iot.simulator import ACTION_UNLOCK
from src.services.automation import AutomationEngine, Suggestion, registry


@pytest.fixture(autouse=True)
def clear_suggestion_registry():
    registry.clear()
    yield
    registry.clear()


def _snapshot(**overrides) -> EnvironmentSnapshot:
    values = {
        "observed_at": datetime(2026, 8, 20, 5, 0, tzinfo=UTC),
        "source": "test",
        "temperature_c": 31.0,
        "apparent_temperature_c": 35.0,
        "humidity_percent": 78.0,
        "precipitation_mm": 1.2,
        "rain_mm": 1.2,
        "weather_code": 61,
        "wind_speed_kmh": 18.0,
        "shortwave_radiation_wm2": 120.0,
        "pm25_ugm3": 82.0,
        "us_aqi": 155.0,
        "uv_index": 2.0,
    }
    values.update(overrides)
    return EnvironmentSnapshot(**values)


class _StaticEnvironmentTool:
    def __init__(self, snapshot: EnvironmentSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    async def current(self) -> EnvironmentSnapshot:
        self.calls += 1
        return self.snapshot


class _BrokenEnvironmentTool:
    async def current(self) -> EnvironmentSnapshot:
        raise EnvironmentUnavailableError("provider unavailable")


@pytest.mark.asyncio
async def test_open_meteo_tool_fetches_both_sources_and_caches() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host or "")
        if request.url.host == "api.open-meteo.com":
            return httpx.Response(
                200,
                json={
                    "current": {
                        "temperature_2m": 32.5,
                        "relative_humidity_2m": 70,
                        "apparent_temperature": 38,
                        "precipitation": 0.4,
                        "rain": 0.4,
                        "weather_code": 61,
                        "wind_speed_10m": 22,
                        "shortwave_radiation": 640,
                    }
                },
            )
        return httpx.Response(200, json={"current": {"pm2_5": 72, "us_aqi": 145, "uv_index": 7.2}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = OpenMeteoEnvironmentTool(
            Settings(environment_refresh_seconds=300, environment_realtime_enabled=True),
            client=client,
        )
        first = await tool.current()
        second = await tool.current()

    assert first is second
    assert first.is_raining
    assert first.temperature_c == 32.5
    assert first.pm25_ugm3 == 72
    assert first.sunlight_percent == 64
    assert sorted(calls) == ["air-quality-api.open-meteo.com", "api.open-meteo.com"]


@pytest.mark.asyncio
async def test_realtime_rain_and_bad_air_execute_only_safe_actions(seeded, monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT_REALTIME_ENABLED", "true")
    monkeypatch.setenv("ENVIRONMENT_AUTO_EXECUTE", "true")
    monkeypatch.setenv("ENVIRONMENT_REFRESH_SECONDS", "60")
    get_settings.cache_clear()

    window = seeded.scalar(select(Device).where(Device.slug == "cua_so_phong_khach"))
    purifier = seeded.scalar(select(Device).where(Device.slug == "may_loc_phong_khach"))
    window.state = {**(window.state or {}), "position": 100}
    purifier.state = {**(purifier.state or {}), "power": "off"}
    seeded.commit()

    tool = _StaticEnvironmentTool(_snapshot())
    pending = await AutomationEngine(environment_tool=tool).run_once()

    seeded.expire_all()
    window = seeded.scalar(select(Device).where(Device.slug == "cua_so_phong_khach"))
    purifier = seeded.scalar(select(Device).where(Device.slug == "may_loc_phong_khach"))
    assert window.state["position"] == 0
    assert purifier.state["power"] == "on"
    assert pending == []
    assert tool.calls == 1

    aqi = seeded.scalar(select(Sensor).where(Sensor.slug == "cam_bien_aqi"))
    temperature = seeded.scalar(select(Sensor).where(Sensor.slug == "cam_bien_nhiet_do"))
    assert aqi.value == 155
    assert temperature.value == 31
    assert temperature.room == ""

    logs = list(select_log for select_log in seeded.scalars(select(ActionLog)))
    assert logs
    assert all(log.source == "automation" for log in logs)
    assert all(log.status == ActionStatus.EXECUTED for log in logs)


@pytest.mark.asyncio
async def test_provider_failure_does_not_use_stale_weather_to_close_window(seeded, monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT_REALTIME_ENABLED", "true")
    monkeypatch.setenv("ENVIRONMENT_AUTO_EXECUTE", "true")
    get_settings.cache_clear()

    window = seeded.scalar(select(Device).where(Device.slug == "cua_so_phong_khach"))
    rain = seeded.scalar(select(Sensor).where(Sensor.slug == "cam_bien_mua"))
    window.state = {**(window.state or {}), "position": 100}
    rain.value = 1.0  # stale/local value must not drive realtime automation after a fetch error
    seeded.commit()

    await AutomationEngine(environment_tool=_BrokenEnvironmentTool()).run_once()

    seeded.expire_all()
    window = seeded.scalar(select(Device).where(Device.slug == "cua_so_phong_khach"))
    assert window.state["position"] == 100


@pytest.mark.asyncio
async def test_environment_guardrail_rejects_security_action(seeded) -> None:
    engine = AutomationEngine(environment_tool=_StaticEnvironmentTool(_snapshot()))
    suggestion = Suggestion(
        id="unsafe",
        household_id=1,
        kind=SuggestionKind.WEATHER,
        title_vi="Unsafe injected action",
        message_vi="",
        steps=[{"device_slug": "khoa_cua_chinh", "action": ACTION_UNLOCK, "params": {}}],
    )

    await engine._execute_environment(suggestion)

    seeded.expire_all()
    lock = seeded.scalar(select(Device).where(Device.slug == "khoa_cua_chinh"))
    log = seeded.scalar(select(ActionLog).order_by(ActionLog.id.desc()).limit(1))
    assert lock.state["locked"] is True
    assert log.status == ActionStatus.DENIED
    assert "rủi ro cao" in log.detail
