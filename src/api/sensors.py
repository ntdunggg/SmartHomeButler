"""Manual demo sensor controls.

These endpoints make the demo sensor panel authoritative for backend context.
The browser still renders chips locally, but automation, conflict detection and
other connected clients now observe the same sensor state.
"""

from __future__ import annotations

from datetime import UTC

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from src.api import ws
from src.api.deps import DbSession, OwnerUser
from src.core import clock
from src.domain.enums import SensorType
from src.domain.models import Sensor
from src.iot.registry import SENSOR_BY_SLUG
from src.models.schemas import DemoSensorsOut, DemoSensorsUpdate, SensorOut

router = APIRouter(prefix="/sensors", tags=["sensors"])

_DEMO_SENSOR_FIELDS: dict[str, tuple[str, float]] = {
    "pm25": ("cam_bien_bui", 28.0),
    "temperature": ("cam_bien_nhiet_do", 28.0),
    "humidity": ("cam_bien_do_am", 68.0),
    "sunlight": ("cam_bien_nang", 45.0),
}


def _sensor_out(sensor: Sensor) -> SensorOut:
    return SensorOut(
        slug=sensor.slug,
        name=sensor.name,
        sensor_type=str(sensor.sensor_type),
        value=round(sensor.value, 1),
        unit=sensor.unit,
        room=sensor.room or "",
    )


def _get_sensor(session: DbSession, *, household_id: int, slug: str) -> Sensor:
    sensor = session.scalar(select(Sensor).where(Sensor.household_id == household_id, Sensor.slug == slug))
    if sensor is None:
        spec = SENSOR_BY_SLUG.get(slug)
        if spec is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Không tìm thấy cảm biến '{slug}'.")
        sensor = Sensor(
            household_id=household_id,
            slug=spec.slug,
            name=spec.name,
            sensor_type=spec.sensor_type,
            value=spec.value,
            unit=spec.unit,
            room=spec.room,
        )
        session.add(sensor)
    return sensor


def _set_value(session: DbSession, *, household_id: int, slug: str, value: float) -> Sensor:
    sensor = _get_sensor(session, household_id=household_id, slug=slug)
    sensor.value = float(value)
    sensor.updated_at = clock.now().astimezone(UTC)
    return sensor


@router.put("/demo", response_model=DemoSensorsOut)
async def update_demo_sensors(payload: DemoSensorsUpdate, owner: OwnerUser, session: DbSession) -> DemoSensorsOut:
    """Persist demo sensor panel values and push realtime sensor updates.

    Owner-only because these values affect automation and conflict detection for
    the whole household.
    """
    updated: list[Sensor] = []
    data = payload.model_dump(exclude_unset=True)

    for field, (slug, _default) in _DEMO_SENSOR_FIELDS.items():
        if field in data and data[field] is not None:
            updated.append(_set_value(session, household_id=owner.household_id, slug=slug, value=float(data[field])))

    if "rain" in data and data["rain"] is not None:
        updated.append(_set_value(session, household_id=owner.household_id, slug="cam_bien_mua", value=1.0 if data["rain"] else 0.0))

    if "presence" in data and data["presence"] is not None:
        present = bool(data["presence"])
        presence_rows = list(
            session.scalars(
                select(Sensor).where(
                    Sensor.household_id == owner.household_id,
                    Sensor.sensor_type == SensorType.PRESENCE,
                ).order_by(Sensor.slug)
            )
        )
        if not presence_rows:
            presence_rows = [
                _get_sensor(session, household_id=owner.household_id, slug=slug)
                for slug, spec in SENSOR_BY_SLUG.items()
                if spec.sensor_type == SensorType.PRESENCE
            ]
        target_room = str(data.get("presence_room") or "").strip()
        if present and not target_room:
            raise HTTPException(
                status_code=422,
                detail="Cần chọn phòng đang có người.",
            )
        if present and not any(sensor.room == target_room for sensor in presence_rows):
            raise HTTPException(
                status_code=422,
                detail=f"Không tìm thấy cảm biến hiện diện cho phòng '{target_room}'.",
            )
        for sensor in presence_rows:
            sensor.value = 1.0 if present and sensor.room == target_room else 0.0
            sensor.updated_at = clock.now().astimezone(UTC)
            updated.append(sensor)

    session.commit()
    sensors = [_sensor_out(sensor) for sensor in updated]
    await ws.on_sensor_state(owner.household_id, [sensor.model_dump() for sensor in sensors])
    return DemoSensorsOut(sensors=sensors)
