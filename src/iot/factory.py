"""Chọn adapter IoT bus theo cấu hình.

Toàn app dùng chung một instance qua ``get_bus()`` — thiết bị chỉ có một trạng
thái duy nhất, và subscriber của WebSocket phải nghe đúng bus đang chạy.
"""

from __future__ import annotations

from src.config import get_settings
from src.iot.bus import DeviceBus
from src.iot.memory_bus import InMemoryBus

_bus: DeviceBus | None = None


def get_bus() -> DeviceBus:
    global _bus
    if _bus is None:
        settings = get_settings()
        if settings.mqtt_enabled:
            from src.iot.mqtt_bus import MqttBus

            _bus = MqttBus()
        else:
            _bus = InMemoryBus()
    return _bus


def set_bus(bus: DeviceBus | None) -> None:
    """Thay bus — dùng trong test để cô lập trạng thái giữa các case."""
    global _bus
    _bus = bus
