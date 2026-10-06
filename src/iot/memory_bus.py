"""IoT bus chạy trong tiến trình — adapter mặc định.

Không cần Mosquitto, Docker hay bất kỳ dịch vụ ngoài nào: app chạy được ngay sau
khi clone. Trạng thái thiết bị lưu trong bảng ``devices`` nên vẫn bền vững qua
các lần khởi động lại, giống hệt khi dùng broker thật.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from src.core.errors import NotFoundError
from src.db.session import session_scope
from src.domain.models import Device
from src.iot.bus import CommandResult, DeviceBus, StateHandler
from src.iot.registry import spec_from_device
from src.iot.simulator import apply_command

logger = logging.getLogger(__name__)

# Độ trễ giả lập của thiết bị vật lý — đủ nhỏ để tổng phản hồi luôn dưới 2s
_SIMULATED_DEVICE_LATENCY = 0.02


class InMemoryBus(DeviceBus):
    def __init__(self, *, latency: float = _SIMULATED_DEVICE_LATENCY) -> None:
        self._handlers: list[StateHandler] = []
        self._latency = latency
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        logger.info("IoT bus: chế độ in-process (không dùng MQTT)")

    async def stop(self) -> None:
        self._handlers.clear()

    def subscribe(self, handler: StateHandler) -> None:
        self._handlers.append(handler)

    def unsubscribe(self, handler: StateHandler) -> None:
        if handler in self._handlers:
            self._handlers.remove(handler)

    async def get_state(self, household_id: int, slug: str) -> dict:
        with session_scope() as session:
            device = session.scalar(select(Device).where(Device.household_id == household_id, Device.slug == slug))
            if device is None:
                raise NotFoundError(f"Không tìm thấy thiết bị '{slug}'.")
            return dict(device.state or {})

    async def send_command(self, household_id: int, slug: str, payload: dict) -> CommandResult:
        """Áp lệnh lên thiết bị, lưu trạng thái mới và phát tin cho subscriber."""
        action = payload.get("action", "")
        params = payload.get("params") or {}

        # Khoá để hai lệnh song song lên cùng thiết bị không ghi đè lẫn nhau
        async with self._lock:
            await asyncio.sleep(self._latency)
            with session_scope() as session:
                device = session.scalar(select(Device).where(Device.household_id == household_id, Device.slug == slug))
                if device is None:
                    raise NotFoundError(f"Không tìm thấy thiết bị '{slug}'.")
                # Spec dựng từ chính bản ghi DB → điều khiển được cả thiết bị tự thêm
                spec = spec_from_device(device)
                if not device.online:
                    return CommandResult(ok=False, state=dict(device.state or {}), detail=f"{spec.name} đang offline.")

                result = apply_command(spec, device.state or {}, action, params)
                device.state = result.state
                new_state = dict(result.state)
                detail = result.detail

        await self._notify(slug, new_state)
        return CommandResult(ok=True, state=new_state, detail=detail)

    async def publish_state(self, slug: str, state: dict) -> None:
        """Phát trạng thái ra ngoài mà không đi qua lệnh (dùng cho cảm biến)."""
        await self._notify(slug, state)

    async def _notify(self, slug: str, state: dict) -> None:
        """Gọi các handler đã đăng ký. Một handler lỗi không được chặn các handler khác."""
        for handler in list(self._handlers):
            try:
                await handler(slug, state)
            except Exception:
                logger.exception("Handler trạng thái thiết bị '%s' gặp lỗi", slug)
