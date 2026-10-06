"""Gom toàn bộ router của API v1."""

from __future__ import annotations

from fastapi import APIRouter

from src.api import (
    agent,
    auth,
    childlock,
    clock,
    devices,
    energy,
    habits,
    history,
    members,
    notifications,
    rooms,
    sensors,
)
from src.config import get_settings

router = APIRouter()

router.include_router(auth.router)
router.include_router(devices.router)
router.include_router(agent.router)
router.include_router(history.router)
router.include_router(members.router)
router.include_router(habits.router)
router.include_router(clock.router)
router.include_router(rooms.router)
router.include_router(notifications.router)
router.include_router(childlock.router)
router.include_router(sensors.router)
router.include_router(energy.router)


@router.get("/status", tags=["system"])
async def system_status() -> dict:
    """Trạng thái các thành phần — cho biết đang chạy trên adapter nào."""
    settings = get_settings()
    return {
        "status": "ready",
        "agent": "LLM-first: understand → synthesize → validate → execute → verify",
        "llm": {
            "configured": settings.llm_available,
            "model": settings.model_name if settings.llm_available else None,
            "reachable": settings.llm_available,
            "fallback": "Deterministic direct-command synthesis + clarify",
        },
        "iot_bus": "mqtt" if settings.mqtt_enabled else "in-process",
        "session_memory": "redis" if settings.redis_url else "in-process",
        "database": "postgres" if settings.database_url.startswith("postgres") else "sqlite",
        "automation_enabled": settings.automation_enabled,
        "environment": {
            "provider": "open-meteo" if settings.environment_realtime_enabled else "local-sensors",
            "realtime_enabled": settings.environment_realtime_enabled,
            "auto_execute": settings.environment_auto_execute,
            "refresh_seconds": settings.environment_refresh_seconds,
        },
    }
