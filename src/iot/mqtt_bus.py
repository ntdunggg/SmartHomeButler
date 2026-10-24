"""IoT bus qua MQTT (Mosquitto) — adapter dùng khi ``MQTT_ENABLED=true``.

Kế thừa ``InMemoryBus`` để giữ nguyên logic máy trạng thái và lưu trữ, chỉ bổ sung
việc phát/nhận qua broker. Nhờ đó khi bật MQTT lên, hành vi nghiệp vụ không đổi —
chỉ khác là Home Assistant hay thiết bị thật cũng nghe được cùng những topic đó.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from src.config import get_settings
from src.iot.bus import CommandResult, command_topic, state_topic
from src.iot.memory_bus import InMemoryBus

if TYPE_CHECKING:
    from paho.mqtt.client import Client

logger = logging.getLogger(__name__)


class MqttBus(InMemoryBus):
    def __init__(self) -> None:
        super().__init__()
        self._client: Client | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        settings = get_settings()
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            logger.warning("Chưa cài paho-mqtt — quay về bus in-process")
            return

        self._loop = asyncio.get_running_loop()
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if settings.mqtt_username:
            client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        client.on_message = self._on_message

        try:
            client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=60)
        except OSError as exc:
            # Không tìm thấy broker thì vẫn chạy tiếp bằng bus in-process,
            # tránh việc thiếu Mosquitto làm sập cả ứng dụng.
            logger.warning(
                "Không kết nối được MQTT %s:%s (%s) — dùng bus in-process", settings.mqtt_host, settings.mqtt_port, exc
            )
            return

        client.subscribe("home/+/+/set")
        client.loop_start()
        self._client = client
        logger.info("IoT bus: đã nối MQTT %s:%s", settings.mqtt_host, settings.mqtt_port)

    async def stop(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
        await super().stop()

    async def send_command(self, household_id: int, slug: str, payload: dict) -> CommandResult:
        result = await super().send_command(household_id, slug, payload)
        self._publish(state_topic(household_id, slug), result.state)
        return result

    def _publish(self, topic: str, payload: dict) -> None:
        if self._client is None:
            return
        self._client.publish(topic, json.dumps(payload, ensure_ascii=False), retain=True)

    def _on_message(self, _client, _userdata, message) -> None:
        """Nhận lệnh do thiết bị/HA phát lên topic .../set và áp vào simulator.

        Chạy trên thread của paho, nên phải đẩy ngược về event loop của asyncio.
        """
        if self._loop is None:
            return
        try:
            parts = message.topic.split("/")
            household_id, slug = int(parts[1]), parts[2]
            payload = json.loads(message.payload.decode())
        except (IndexError, ValueError, json.JSONDecodeError):
            logger.warning("Bỏ qua message MQTT không hợp lệ trên topic %s", message.topic)
            return

        # Gọi thẳng bản của lớp cha để không phát ngược lại lên MQTT (tránh vòng lặp)
        asyncio.run_coroutine_threadsafe(
            InMemoryBus.send_command(self, household_id, slug, payload),
            self._loop,
        )


__all__ = ["MqttBus", "command_topic", "state_topic"]
