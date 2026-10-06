"""Demo sensor API sync contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select

from src.api import ws
from src.domain.enums import ActionStatus, SensorType
from src.domain.models import ActionLog, Sensor, User


async def test_owner_updates_demo_sensors_and_dashboard_reflects_backend(client: AsyncClient, login):
    headers = await login("bo")

    response = await client.put(
        "/api/v1/sensors/demo",
        headers=headers,
        json={
            "pm25": 88.8,
            "temperature": 35.5,
            "humidity": 40.0,
            "sunlight": 10.0,
            "rain": True,
            "presence": False,
        },
    )

    assert response.status_code == 200, response.text
    sensors = response.json()["sensors"]
    assert any(s["slug"] == "cam_bien_bui" and s["value"] == 88.8 for s in sensors)
    assert any(s["slug"] == "cam_bien_mua" and s["value"] == 1.0 for s in sensors)
    assert all(s["value"] == 0.0 for s in sensors if s["sensor_type"] == "presence")

    dashboard = (await client.get("/api/v1/dashboard", headers=headers)).json()
    presence = [s for s in dashboard["sensors"] if s["sensor_type"] == "presence"]
    assert presence and all(s["value"] == 0.0 for s in presence)


async def test_member_cannot_update_demo_sensors(client: AsyncClient, login):
    headers = await login("con_lon")

    response = await client.put("/api/v1/sensors/demo", headers=headers, json={"presence": False})

    assert response.status_code == 403


async def test_presence_true_requires_and_uses_explicit_room(client: AsyncClient, login):
    headers = await login("bo")

    missing_room = await client.put("/api/v1/sensors/demo", headers=headers, json={"presence": True})
    assert missing_room.status_code == 422

    response = await client.put(
        "/api/v1/sensors/demo",
        headers=headers,
        json={"presence": True, "presence_room": "Phòng ngủ con"},
    )

    assert response.status_code == 200, response.text
    presence = [s for s in response.json()["sensors"] if s["sensor_type"] == "presence"]
    assert any(s["room"] == "Phòng ngủ con" and s["value"] == 1.0 for s in presence)
    assert all(s["value"] == 0.0 for s in presence if s["room"] != "Phòng ngủ con")


async def test_demo_sensor_update_broadcasts_sensor_state(client: AsyncClient, login, monkeypatch):
    headers = await login("bo")
    messages: list[dict] = []

    async def fake_broadcast(household_id: int, message: dict, **_kwargs) -> None:
        messages.append({"household_id": household_id, **message})

    monkeypatch.setattr(ws.manager, "broadcast", fake_broadcast)

    response = await client.put("/api/v1/sensors/demo", headers=headers, json={"presence": False, "pm25": 66.6})

    assert response.status_code == 200, response.text
    sensor_messages = [m for m in messages if m["type"] == "sensor_state"]
    assert sensor_messages
    slugs = {s["slug"] for s in sensor_messages[-1]["sensors"]}
    assert "cam_bien_bui" in slugs
    assert any(slug.startswith("hien_dien_") for slug in slugs)


async def test_demo_presence_sync_enables_waste_warning(client: AsyncClient, login, seeded):
    headers = await login("bo")

    response = await client.put("/api/v1/sensors/demo", headers=headers, json={"presence": False})
    assert response.status_code == 200, response.text

    user = seeded.scalar(select(User).where(User.username == "bo"))
    seeded.add(
        ActionLog(
            household_id=user.household_id,
            user_id=user.id,
            action="demo_old_action",
            status=ActionStatus.EXECUTED,
            source="user",
            created_at=datetime.now(UTC) - timedelta(hours=3),
        )
    )
    seeded.commit()

    control = await client.post(
        "/api/v1/devices/binh_nong_lanh/control",
        headers=headers,
        json={"action": "turn_on"},
    )

    assert control.status_code == 200, control.text
    conflicts = control.json()["conflicts"]
    assert any(c["type"] == "waste" and "rất tốn điện" in c["message_vi"] for c in conflicts)


def test_backend_someone_home_uses_synced_presence_rows(seeded):
    from src.services.context import someone_home

    rows = seeded.scalars(select(Sensor).where(Sensor.sensor_type == SensorType.PRESENCE)).all()
    assert rows
    for sensor in rows:
        sensor.value = 0.0
    seeded.commit()

    assert someone_home(seeded, household_id=1) is False
