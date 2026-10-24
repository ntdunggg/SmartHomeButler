"""Contracts for disabling demo controls without disabling the real system."""

from __future__ import annotations

from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy import select

from src.config import Settings, get_settings
from src.core import clock
from src.domain.models import Sensor


def test_demo_controls_are_fail_closed_by_default_and_in_production():
    defaults = Settings(_env_file=None, app_env="test")
    assert defaults.clock_endpoint_enabled is False
    assert defaults.sensor_demo_endpoint_enabled is False

    production = Settings(
        _env_file=None,
        app_env="production",
        jwt_secret="production-jwt-secret-with-at-least-32-bytes",
        encryption_master_key="production-encryption-key-at-least-32-bytes",
        demo_clock_enabled=True,
        demo_sensor_controls_enabled=True,
    )
    assert production.clock_endpoint_enabled is False
    assert production.sensor_demo_endpoint_enabled is False


async def test_disabled_demo_controls_do_not_block_dashboard_or_agent(
    client: AsyncClient,
    login,
    seeded,
    monkeypatch,
):
    monkeypatch.setenv("DEMO_CLOCK_ENABLED", "false")
    monkeypatch.setenv("DEMO_SENSOR_CONTROLS_ENABLED", "false")
    get_settings.cache_clear()
    clock.reset()

    headers = await login("bo")
    sensor = seeded.scalar(select(Sensor).where(Sensor.slug == "cam_bien_bui"))
    original_sensor_value = sensor.value
    original_clock_state = clock.get_state()

    clock_get = await client.get("/api/v1/clock", headers=headers)
    clock_put = await client.put(
        "/api/v1/clock",
        headers=headers,
        json={"offset_ms": 0, "mode": "frozen", "frozen_ms": 1788188400000},
    )
    sensor_put = await client.put("/api/v1/sensors/demo", headers=headers, json={"pm25": 99.9})

    assert clock_get.status_code == 404
    assert clock_put.status_code == 404
    assert sensor_put.status_code == 404
    assert clock.get_state() == original_clock_state
    assert abs((clock.now() - datetime.now(UTC)).total_seconds()) < 1

    seeded.expire_all()
    sensor = seeded.scalar(select(Sensor).where(Sensor.slug == "cam_bien_bui"))
    assert sensor.value == original_sensor_value

    dashboard = await client.get("/api/v1/dashboard", headers=headers)
    assert dashboard.status_code == 200
    assert dashboard.json()["sensors"]

    command = await client.post(
        "/api/v1/agent/command",
        headers=headers,
        json={"message": "bật đèn phòng khách", "conversation_id": "demo-controls-disabled"},
    )
    assert command.status_code == 200, command.text
    body = command.json()
    assert body["plan"]
    assert not body["pending_approval"]

    dashboard_after_command = await client.get("/api/v1/dashboard", headers=headers)
    living_room_lights = [
        device
        for device in dashboard_after_command.json()["devices"]
        if device["room"] == "Phòng khách" and device["device_type"] == "light"
    ]
    assert living_room_lights
    assert all(device["state"]["power"] == "on" for device in living_room_lights)
