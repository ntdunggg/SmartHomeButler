"""Interface của IoT bus (port trong kiến trúc ports & adapters).

Hai adapter cùng thoả interface này:
- ``memory_bus.InMemoryBus``  — pub/sub trong tiến trình, mặc định, chạy ngay
- ``mqtt_bus.MqttBus``        — nối Mosquitto thật khi ``MQTT_ENABLED=true``

Nhờ đó phần agent và API không cần biết đang chạy trên hạ tầng nào.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

# Handler nhận (device_slug, state) mỗi khi trạng thái thiết bị đổi
StateHandler = Callable[[str, dict], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class CommandResult:
    ok: bool
    state: dict
    detail: str = ""


def state_topic(household_id: int, slug: str) -> str:
    return f"home/{household_id}/{slug}/state"


def command_topic(household_id: int, slug: str) -> str:
    return f"home/{household_id}/{slug}/set"


class DeviceBus(ABC):
    """Cổng giao tiếp với lớp thiết bị."""

    @abstractmethod
    async def start(self) -> None:
        """Khởi tạo kết nối / nạp trạng thái ban đầu."""

    @abstractmethod
    async def stop(self) -> None:
        """Đóng kết nối."""

    @abstractmethod
    async def send_command(self, household_id: int, slug: str, payload: dict) -> CommandResult:
        """Gửi lệnh tới thiết bị và trả về trạng thái sau khi thực hiện."""

    @abstractmethod
    async def get_state(self, household_id: int, slug: str) -> dict:
        """Đọc trạng thái hiện tại — dùng ở bước verify của agent."""

    @abstractmethod
    def subscribe(self, handler: StateHandler) -> None:
        """Đăng ký nhận thay đổi trạng thái (WebSocket dùng để đẩy realtime)."""
