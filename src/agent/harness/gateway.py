"""Device gateway (spec §18, §45) — cổng vật lý cho harness.

LLM KHÔNG gọi thẳng device API (spec §18, invariant 5): CHỈ executor/refresh trong
harness đi qua cổng này. Protocol tối giản để test/vertical-slice chạy offline với
``InMemoryGateway``; adapter production bọc `src.iot.bus.DeviceBus` thật.
"""

from __future__ import annotations

from typing import Any, Protocol

from src.iot.registry import DEVICE_SPECS


class DeviceGateway(Protocol):
    def get_state(self, slug: str) -> dict[str, Any]: ...

    def send_command(self, slug: str, desired_state: dict[str, Any]) -> dict[str, Any]:
        """Áp desired_state lên thiết bị, trả trạng thái SAU khi thực hiện."""
        ...


class InMemoryGateway:
    """Gateway mô phỏng trong tiến trình — seed từ registry, ghi đè bằng live snapshot."""

    def __init__(self, live_states: dict[str, dict[str, Any]] | None = None, *, offline: frozenset[str] = frozenset()) -> None:
        self._state: dict[str, dict[str, Any]] = {spec.slug: dict(spec.initial_state) for spec in DEVICE_SPECS}
        for slug, st in (live_states or {}).items():
            self._state.setdefault(slug, {}).update(st or {})
        self._offline = set(offline)

    def get_state(self, slug: str) -> dict[str, Any]:
        return dict(self._state.get(slug, {}))

    def send_command(self, slug: str, desired_state: dict[str, Any]) -> dict[str, Any]:
        if slug in self._offline:
            raise DeviceOfflineError(slug)
        current = self._state.setdefault(slug, {})
        current.update(desired_state)
        return dict(current)


class DeviceOfflineError(RuntimeError):
    """Thiết bị offline khi gửi lệnh (spec §51 DEVICE_OFFLINE, §69 test offline)."""

    def __init__(self, slug: str) -> None:
        super().__init__(f"DEVICE_OFFLINE: {slug}")
        self.slug = slug


class DeviceBusGateway:
    """Adapter bọc `src.iot.bus.DeviceBus` async cho harness đồng bộ (spec §45).

    Cho phép pipeline mới thực thi trên hạ tầng THẬT (MQTT/in-process bus) mà không đổi
    executor. Chạy coroutine của bus bằng `asyncio.run` (harness executor là sync). Chỉ
    dùng ở chế độ execution-in-pipeline; bridge Phase 3 mặc định để execution ở agent_runner.
    """

    def __init__(self, bus: Any, household_id: int) -> None:
        self._bus = bus
        self._household_id = household_id

    def _run(self, coro: Any) -> Any:
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        # Đang trong event loop (vd FastAPI) → chạy trên loop riêng để không lồng loop.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()

    def get_state(self, slug: str) -> dict[str, Any]:
        return self._run(self._bus.get_state(self._household_id, slug))

    def send_command(self, slug: str, desired_state: dict[str, Any]) -> dict[str, Any]:
        result = self._run(self._bus.send_command(self._household_id, slug, dict(desired_state)))
        if not getattr(result, "ok", True):
            raise DeviceOfflineError(slug)
        return dict(getattr(result, "state", {}) or {})
