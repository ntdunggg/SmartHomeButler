"""Automation theo ngữ cảnh từ cảm biến cục bộ và dữ liệu môi trường realtime.

Vòng lặp nền đồng bộ Open-Meteo, quét cảm biến và thói quen theo chu kỳ. Các luật
môi trường chỉ được tự chạy khi ``ENVIRONMENT_AUTO_EXECUTE=true`` và hành động nằm
trong allowlist rủi ro thấp; thói quen/away vẫn là đề xuất cần người dùng chấp nhận.

Bốn nhóm luật, đúng như đề bài liệt kê:
- Bụi PM2.5 vượt ngưỡng → đóng cửa sổ, bật máy lọc
- Trời mưa            → đóng cửa sổ
- Gió mạnh            → đóng cửa sổ
- Nắng nóng / UV cao  → kéo rèm
- Cả nhà vắng lâu     → tắt thiết bị công suất lớn
- Tới giờ thói quen   → xin phép chạy, cho hoãn
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.agent.tools.environment import EnvironmentSnapshot, EnvironmentUnavailableError, OpenMeteoEnvironmentTool
from src.config import get_settings
from src.db.session import session_scope
from src.domain.enums import ActionStatus, DeviceType, RiskLevel, SensorType, SuggestionKind
from src.domain.models import Device, Household, PendingSuggestion, Sensor
from src.iot.factory import get_bus
from src.iot.registry import SENSOR_BY_SLUG, spec_for, spec_from_device
from src.iot.simulator import ACTION_CLOSE, ACTION_TURN_OFF, ACTION_TURN_ON, drift_sensor
from src.memory.habits import due_habits, learn_habits
from src.services.audit import SOURCE_AUTOMATION, log_action
from src.services.context import sensor_values, someone_home

logger = logging.getLogger(__name__)

SuggestionHandler = Callable[["Suggestion"], Awaitable[None]]

_ENVIRONMENT_SENSOR_TYPES = frozenset(
    {
        SensorType.PM25,
        SensorType.AQI,
        SensorType.TEMPERATURE,
        SensorType.HUMIDITY,
        SensorType.RAIN,
        SensorType.SUNLIGHT,
        SensorType.WIND_SPEED,
        SensorType.UV_INDEX,
    }
)
_AUTO_ENVIRONMENT_KINDS = frozenset({SuggestionKind.AIR_QUALITY, SuggestionKind.WEATHER})
_SAFE_ENVIRONMENT_ACTIONS = frozenset(
    {
        (DeviceType.WINDOW, ACTION_CLOSE),
        (DeviceType.CURTAIN, ACTION_CLOSE),
        (DeviceType.AIR_PURIFIER, ACTION_TURN_ON),
    }
)


@dataclass(slots=True)
class Suggestion:
    """Một đề xuất chờ người dùng chấp nhận hoặc bỏ qua."""

    id: str
    household_id: int
    kind: str
    title_vi: str
    message_vi: str
    steps: list[dict] = field(default_factory=list)
    # Thói quen sinh ra đề xuất này (nếu có) — để xử lý hoãn giờ
    habit_id: int | None = None
    # Đề xuất nhắm tới riêng ai (chủ thói quen). None = đề xuất chung cho cả hộ
    # (chất lượng không khí, thời tiết, vắng nhà...).
    user_id: int | None = None
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


_SUGGESTION_TTL_HOURS = 4


def _to_pending_row(s: Suggestion) -> PendingSuggestion:
    created = datetime.fromisoformat(s.created_at) if s.created_at else datetime.now(UTC)
    return PendingSuggestion(
        id=s.id,
        household_id=s.household_id,
        user_id=s.user_id,
        habit_id=s.habit_id,
        kind=s.kind,
        title_vi=s.title_vi,
        message_vi=s.message_vi,
        steps=s.steps,
        created_at=created,
        expires_at=created + timedelta(hours=_SUGGESTION_TTL_HOURS),
    )


def _row_to_suggestion(row: PendingSuggestion) -> Suggestion:
    return Suggestion(
        id=row.id,
        household_id=row.household_id,
        kind=row.kind,
        title_vi=row.title_vi,
        message_vi=row.message_vi,
        steps=list(row.steps) if row.steps else [],
        habit_id=row.habit_id,
        user_id=row.user_id,
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


class SuggestionRegistry:
    """Giữ các đề xuất đang chờ — cache in-memory + persist DB.

    In-memory cache phục vụ dedup nhanh (``has_similar``, ``has_pending_habit``).
    DB đảm bảo không mất khi user offline hoặc server restart.
    """

    def __init__(self) -> None:
        self._items: dict[str, Suggestion] = {}

    def add(self, suggestion: Suggestion, *, session: Session | None = None) -> None:
        self._items[suggestion.id] = suggestion
        if session is not None:
            session.merge(_to_pending_row(suggestion))
            session.commit()
        else:
            try:
                with session_scope() as s:
                    s.merge(_to_pending_row(suggestion))
            except Exception:
                logger.debug("suggestion DB persist failed (non-critical)", exc_info=True)

    def pop(self, suggestion_id: str, *, session: Session | None = None) -> Suggestion | None:
        item = self._items.pop(suggestion_id, None)
        if session is not None:
            # Có session của caller → chỉ STAGE việc xoá, KHÔNG tự commit. Caller sở hữu
            # transaction và commit gói cùng phần việc của họ (áp dụng đề xuất, ghi log),
            # để apply lỗi thì đề xuất KHÔNG bị xoá nửa vời (rollback cả cụm).
            row = session.get(PendingSuggestion, suggestion_id)
            if row is not None:
                session.delete(row)
        else:
            try:
                with session_scope() as s:
                    row = s.get(PendingSuggestion, suggestion_id)
                    if row is not None:
                        s.delete(row)
            except Exception:
                logger.debug("suggestion DB delete failed (non-critical)", exc_info=True)
        return item

    def get(self, suggestion_id: str) -> Suggestion | None:
        """Xem một đề xuất mà KHÔNG lấy ra — để kiểm quyền trước khi quyết định."""
        return self._items.get(suggestion_id)

    def list_for(self, household_id: int) -> list[Suggestion]:
        return [s for s in self._items.values() if s.household_id == household_id]

    def load_from_db(self, session: Session) -> int:
        """Nạp lại từ DB khi server khởi động — khôi phục suggestions chưa hết hạn."""
        now = datetime.now(UTC)
        rows = session.scalars(
            select(PendingSuggestion).where(PendingSuggestion.expires_at > now)
        ).all()
        loaded = 0
        for row in rows:
            if row.id not in self._items:
                self._items[row.id] = _row_to_suggestion(row)
                loaded += 1
        expired = session.scalars(
            select(PendingSuggestion).where(PendingSuggestion.expires_at <= now)
        ).all()
        for row in expired:
            session.delete(row)
        if expired:
            session.commit()
        return loaded

    def clear(self) -> None:
        self._items.clear()

    def has_similar(self, household_id: int, kind: str) -> bool:
        """Tránh spam: cùng một loại đề xuất chỉ giữ một cái đang chờ (dùng cho luật cả hộ)."""
        return any(s.household_id == household_id and s.kind == kind for s in self._items.values())

    def has_pending_habit(self, household_id: int, habit_id: int | None) -> bool:
        """Đã có đề xuất đang chờ cho ĐÚNG thói quen này chưa.

        Dedup theo TỪNG thói quen, KHÔNG phải cả nhóm HABIT của hộ — nếu chặn theo hộ thì
        một thói quen đang chờ của người này sẽ chặn sinh thói quen của người khác (#6):
        ví dụ đề xuất 17h của mẹ còn treo làm thói quen 6h của bố không bao giờ nổi lên.
        """
        return any(
            s.household_id == household_id and s.kind == SuggestionKind.HABIT and s.habit_id == habit_id
            for s in self._items.values()
        )


registry = SuggestionRegistry()


def _new_suggestion(
    household_id: int,
    kind: str,
    title: str,
    message: str,
    steps: list[dict],
    habit_id: int | None = None,
    user_id: int | None = None,
) -> Suggestion:
    return Suggestion(
        id=uuid.uuid4().hex[:12],
        household_id=household_id,
        kind=kind,
        title_vi=title,
        message_vi=message,
        steps=steps,
        habit_id=habit_id,
        user_id=user_id,
        created_at=datetime.now(UTC).isoformat(),
    )


def _devices_of_type(session: Session, household_id: int, device_type: DeviceType) -> list[Device]:
    return [
        device
        for device in session.scalars(select(Device).where(Device.household_id == household_id))
        if (spec := spec_for(device.slug)) is not None and spec.device_type is device_type
    ]


# --------------------------------------------------------------------------
# Các luật đề xuất
# --------------------------------------------------------------------------
def evaluate_rules(session: Session, *, household_id: int, include_environment: bool = True) -> list[Suggestion]:
    """Chạy toàn bộ luật ngữ cảnh, trả về các đề xuất mới."""
    settings = get_settings()
    sensors = sensor_values(session, household_id=household_id)
    suggestions: list[Suggestion] = []

    if include_environment:
        suggestions.extend(
            _rule_air_quality(
                session,
                household_id,
                sensors,
                pm25_threshold=settings.pm25_threshold,
                aqi_threshold=settings.environment_aqi_threshold,
            )
        )
        suggestions.extend(
            _rule_weather(
                session,
                household_id,
                sensors,
                hot_threshold=settings.hot_temperature_threshold,
                wind_threshold=settings.strong_wind_threshold_kmh,
                uv_threshold=settings.high_uv_threshold,
            )
        )
    suggestions.extend(_rule_away(session, household_id, sensors))
    suggestions.extend(_rule_habits(session, household_id))
    return suggestions


def _rule_air_quality(
    session: Session,
    household_id: int,
    sensors: dict,
    *,
    pm25_threshold: float,
    aqi_threshold: float,
) -> list[Suggestion]:
    pm25 = sensors.get(SensorType.PM25, 0.0)
    aqi = sensors.get(SensorType.AQI, 0.0)
    if (pm25 < pm25_threshold and aqi < aqi_threshold) or registry.has_similar(
        household_id, SuggestionKind.AIR_QUALITY
    ):
        return []

    steps = [
        {"device_slug": d.slug, "action": ACTION_CLOSE, "params": {}}
        for d in _devices_of_type(session, household_id, DeviceType.WINDOW)
        if (d.state or {}).get("position", 0) > 0
    ]
    steps += [
        {"device_slug": d.slug, "action": ACTION_TURN_ON, "params": {}}
        for d in _devices_of_type(session, household_id, DeviceType.AIR_PURIFIER)
        if (d.state or {}).get("power") != "on"
    ]
    if not steps:
        return []

    return [
        _new_suggestion(
            household_id,
            SuggestionKind.AIR_QUALITY,
            "Chất lượng không khí xấu",
            f"Không khí ngoài trời đang xấu (PM2.5 {pm25:.0f} µg/m³, AQI {aqi:.0f}). "
            "Tôi đề xuất đóng cửa sổ và bật máy lọc không khí.",
            steps,
        )
    ]


def _rule_weather(
    session: Session,
    household_id: int,
    sensors: dict,
    *,
    hot_threshold: float,
    wind_threshold: float,
    uv_threshold: float,
) -> list[Suggestion]:
    if registry.has_similar(household_id, SuggestionKind.WEATHER):
        return []

    raining = sensors.get(SensorType.RAIN, 0.0) >= 1.0
    wind_speed = sensors.get(SensorType.WIND_SPEED, 0.0)
    strong_wind = wind_speed >= wind_threshold
    if raining or strong_wind:
        steps = [
            {"device_slug": d.slug, "action": ACTION_CLOSE, "params": {}}
            for d in _devices_of_type(session, household_id, DeviceType.WINDOW)
            if (d.state or {}).get("position", 0) > 0
        ]
        if steps:
            return [
                _new_suggestion(
                    household_id,
                    SuggestionKind.WEATHER,
                    "Thời tiết có thể ảnh hưởng ngôi nhà",
                    (
                        "Trời đang mưa và có gió mạnh."
                        if raining and strong_wind
                        else "Trời đang mưa."
                        if raining
                        else f"Gió ngoài trời đang mạnh ({wind_speed:.0f} km/h)."
                    )
                    + " Tôi đóng cửa sổ lại nhé?",
                    steps,
                )
            ]

    sunlight = sensors.get(SensorType.SUNLIGHT, 0.0)
    temperature = sensors.get(SensorType.TEMPERATURE, 0.0)
    uv_index = sensors.get(SensorType.UV_INDEX, 0.0)
    if (sunlight >= 75 and temperature >= hot_threshold) or uv_index >= uv_threshold:
        steps = [
            {"device_slug": d.slug, "action": ACTION_CLOSE, "params": {}}
            for d in _devices_of_type(session, household_id, DeviceType.CURTAIN)
            if (d.state or {}).get("position", 0) > 30
        ]
        if steps:
            return [
                _new_suggestion(
                    household_id,
                    SuggestionKind.WEATHER,
                    "Nắng nóng hoặc UV cao",
                    f"Ngoài trời {temperature:.0f}°C, nắng {sunlight:.0f}% và UV {uv_index:.1f}. "
                    "Tôi kéo rèm lại để giảm nhiệt và tia UV nhé?",
                    steps,
                )
            ]
    return []


def _rule_away(session: Session, household_id: int, sensors: dict) -> list[Suggestion]:
    # Presence giờ theo phòng — gộp mọi phòng để biết còn ai ở nhà không.
    if someone_home(session, household_id=household_id) or registry.has_similar(household_id, SuggestionKind.AWAY):
        return []

    steps: list[dict[str, Any]] = []
    for device_type in (DeviceType.AIR_CONDITIONER, DeviceType.WATER_HEATER):
        steps += [
            {"device_slug": d.slug, "action": ACTION_TURN_OFF, "params": {}}
            for d in _devices_of_type(session, household_id, device_type)
            if (d.state or {}).get("power") == "on"
        ]
    if not steps:
        return []

    names = ", ".join(spec.name for step in steps if (spec := spec_for(step["device_slug"])))
    return [
        _new_suggestion(
            household_id,
            SuggestionKind.AWAY,
            "Cả nhà đang vắng",
            f"Không có ai ở nhà mà {names} vẫn đang chạy. Tôi tắt đi cho đỡ tốn điện nhé?",
            steps,
        )
    ]


def _rule_habits(session: Session, household_id: int) -> list[Suggestion]:
    suggestions = []
    for habit in due_habits(session, household_id=household_id):
        # Dedup THEO TỪNG thói quen (không phải cả hộ): thói quen này đang chờ thì không
        # nhắc lại, nhưng KHÔNG chặn thói quen của người/thiết bị khác (#6).
        if registry.has_pending_habit(household_id, habit.id):
            continue
        device = session.scalar(
            select(Device).where(Device.household_id == household_id, Device.slug == habit.device_slug)
        )
        if device is None:
            continue
        # spec_for chỉ biết thiết bị trong registry tĩnh; thói quen tay có thể trỏ tới
        # thiết bị người dùng tự thêm -> rơi về dựng spec từ bản ghi DB cho chắc.
        spec = spec_for(habit.device_slug) or spec_from_device(device)
        if spec is None:
            continue
        suggestions.append(
            _new_suggestion(
                household_id,
                SuggestionKind.HABIT,
                "Tới giờ thói quen",
                f"{habit.description_vi}. Bây giờ đã tới giờ, bạn có muốn tôi làm luôn không? "
                "Nếu chưa tiện, bạn có thể bảo tôi hoãn lại ít phút.",
                [{"device_slug": habit.device_slug, "action": habit.action, "params": dict(habit.params or {})}],
                habit_id=habit.id,
                user_id=habit.user_id,  # đề xuất nhắm đúng chủ thói quen (#7/#8)
            )
        )
    return suggestions


# --------------------------------------------------------------------------
# Vòng lặp nền
# --------------------------------------------------------------------------
def sync_environment_sensors(session: Session, *, household_id: int, snapshot: EnvironmentSnapshot) -> None:
    """Persist a validated external snapshot into the sensor contract used by the agent."""
    values = {
        "cam_bien_bui": snapshot.pm25_ugm3,
        "cam_bien_aqi": snapshot.us_aqi,
        "cam_bien_nhiet_do": snapshot.temperature_c,
        "cam_bien_do_am": snapshot.humidity_percent,
        "cam_bien_mua": 1.0 if snapshot.is_raining else 0.0,
        "cam_bien_nang": snapshot.sunlight_percent,
        "cam_bien_gio": snapshot.wind_speed_kmh,
        "cam_bien_uv": snapshot.uv_index,
    }
    for slug, value in values.items():
        spec = SENSOR_BY_SLUG[slug]
        sensor = session.scalar(
            select(Sensor).where(Sensor.household_id == household_id, Sensor.slug == slug)
        )
        if sensor is None:
            sensor = Sensor(household_id=household_id, slug=slug)
            session.add(sensor)
        # Refresh metadata as well so existing databases adopt the outdoor scope.
        sensor.name = spec.name
        sensor.sensor_type = spec.sensor_type
        sensor.value = value
        sensor.unit = spec.unit
        sensor.room = spec.room
        sensor.updated_at = snapshot.observed_at


def tick_sensors(session: Session, *, household_id: int, preserve_environment: bool = False) -> None:
    """Cho cảm biến dao động nhẹ để hệ thống có dữ liệu sống khi demo."""
    for sensor in session.scalars(select(Sensor).where(Sensor.household_id == household_id)):
        if preserve_environment and sensor.sensor_type in _ENVIRONMENT_SENSOR_TYPES:
            continue
        sensor.value = drift_sensor(sensor.sensor_type, sensor.value)


class AutomationEngine:
    """Chạy vòng quét ngữ cảnh và phát đề xuất qua handler đã đăng ký."""

    def __init__(self, *, environment_tool: OpenMeteoEnvironmentTool | None = None) -> None:
        self._task: asyncio.Task | None = None
        self._handlers: list[SuggestionHandler] = []
        # Handler nhận (household_id, [sensor dict]) mỗi vòng để đẩy cảm biến realtime
        self._sensor_handlers: list = []
        self._environment_tool = environment_tool
        self._last_auto_attempt: dict[tuple[int, str], float] = {}

    def subscribe(self, handler: SuggestionHandler) -> None:
        self._handlers.append(handler)

    def subscribe_sensors(self, handler) -> None:
        """Đăng ký nhận giá trị cảm biến sau mỗi vòng quét (WebSocket dùng để đẩy)."""
        self._sensor_handlers.append(handler)

    async def start(self) -> None:
        settings = get_settings()
        if not settings.automation_enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(settings.automation_interval_seconds))
        logger.info("Automation: quét ngữ cảnh mỗi %ss", settings.automation_interval_seconds)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self, interval: int) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Một vòng lỗi không được phép giết cả engine
                logger.exception("Vòng quét automation gặp lỗi")
            await asyncio.sleep(interval)

    async def run_once(self) -> list[Suggestion]:
        """Một vòng quét: đồng bộ realtime, học thói quen, tự chạy hoặc sinh đề xuất."""
        settings = get_settings()
        snapshot: EnvironmentSnapshot | None = None
        environment_ready = not settings.environment_realtime_enabled
        if settings.environment_realtime_enabled:
            if self._environment_tool is None:
                self._environment_tool = OpenMeteoEnvironmentTool(settings)
            try:
                snapshot = await self._environment_tool.current()
                environment_ready = True
            except EnvironmentUnavailableError as exc:
                # Fail closed: dữ liệu ngoài không đáng tin thì không chạy rule môi trường.
                logger.warning("Bỏ qua automation môi trường vì dữ liệu realtime lỗi: %s", exc)

        produced: list[Suggestion] = []
        sensor_updates: dict[int, list[dict]] = {}
        with session_scope() as session:
            household_ids = list(session.scalars(select(Household.id)))
            for household_id in household_ids:
                tick_sensors(
                    session,
                    household_id=household_id,
                    preserve_environment=settings.environment_realtime_enabled,
                )
                if snapshot is not None:
                    sync_environment_sensors(session, household_id=household_id, snapshot=snapshot)
                # Gom giá trị cảm biến mới để đẩy realtime — mọi màn hình đồng bộ
                sensor_updates[household_id] = [
                    {
                        "slug": s.slug,
                        "value": round(s.value, 1),
                        "unit": s.unit,
                        "sensor_type": str(s.sensor_type),
                    }
                    for s in session.scalars(select(Sensor).where(Sensor.household_id == household_id))
                ]
                learn_habits(session, household_id=household_id)
                produced.extend(
                    evaluate_rules(
                        session,
                        household_id=household_id,
                        include_environment=environment_ready,
                    )
                )

        pending: list[Suggestion] = []
        automatic: list[Suggestion] = []
        for suggestion in produced:
            if settings.environment_auto_execute and suggestion.kind in _AUTO_ENVIRONMENT_KINDS:
                key = (suggestion.household_id, str(suggestion.kind))
                last_attempt = self._last_auto_attempt.get(key, 0.0)
                if time.monotonic() - last_attempt >= settings.environment_refresh_seconds:
                    self._last_auto_attempt[key] = time.monotonic()
                    automatic.append(suggestion)
            else:
                pending.append(suggestion)

        for suggestion in automatic:
            await self._execute_environment(suggestion)

        for household_id, sensors in sensor_updates.items():
            for handler in list(self._sensor_handlers):
                try:
                    await handler(household_id, sensors)
                except Exception:
                    logger.exception("Handler cảm biến của hộ %s gặp lỗi", household_id)

        for suggestion in pending:
            registry.add(suggestion)
            await self._emit(suggestion)
        return pending

    async def _execute_environment(self, suggestion: Suggestion) -> None:
        """Execute only allowlisted, normal-risk environment actions and audit every attempt."""
        bus = get_bus()
        for step in suggestion.steps:
            action = str(step.get("action", ""))
            slug = str(step.get("device_slug", ""))
            params = dict(step.get("params") or {})
            allowed = False
            reason = "Automation môi trường bị guardrail từ chối."
            with session_scope() as session:
                device = session.scalar(
                    select(Device).where(
                        Device.household_id == suggestion.household_id,
                        Device.slug == slug,
                    )
                )
                if device is None:
                    reason = "Thiết bị không tồn tại."
                elif not device.online:
                    reason = f"{device.name} đang offline."
                elif device.risk_level != RiskLevel.NORMAL:
                    reason = "Automation không được tự điều khiển thiết bị có rủi ro cao."
                elif (device.device_type, action) not in _SAFE_ENVIRONMENT_ACTIONS:
                    reason = "Hành động không nằm trong allowlist automation môi trường."
                else:
                    allowed = True

            status = ActionStatus.DENIED
            detail = reason
            if allowed:
                try:
                    result = await bus.send_command(
                        suggestion.household_id,
                        slug,
                        {"action": action, "params": params},
                    )
                    status = ActionStatus.EXECUTED if result.ok else ActionStatus.FAILED
                    detail = result.detail
                except Exception as exc:  # one device failure must not stop the remaining safe steps
                    status = ActionStatus.FAILED
                    detail = f"Không thực thi được automation: {exc}"
                    logger.exception("Automation môi trường lỗi với thiết bị %s", slug)

            with session_scope() as session:
                log_action(
                    session,
                    household_id=suggestion.household_id,
                    user_id=None,
                    device_slug=slug,
                    action=action,
                    params=params,
                    status=status,
                    command_text=suggestion.title_vi,
                    detail=detail,
                    source=SOURCE_AUTOMATION,
                )

    async def _emit(self, suggestion: Suggestion) -> None:
        for handler in list(self._handlers):
            try:
                await handler(suggestion)
            except Exception:
                logger.exception("Handler đề xuất gặp lỗi")


engine = AutomationEngine()
