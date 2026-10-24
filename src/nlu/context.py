"""NLU Context Builder — dựng RuntimeContext đã lọc từ Registry + snapshot live.

Nguyên tắc (Bước 5):
- Giữ nguyên `device_id` chính thức, không tự sinh thiết bị.
- Chỉ đưa vào thiết bị *liên quan* (theo phòng người nói, theo câu, theo hội thoại
  gần đây), không dump toàn bộ catalog.
- Giữ timestamp + timezone.
- Phân biệt rõ observation / inference / assumption; không biến suy đoán thành sự thật.
  Vị trí người nói (`speaker_location`) là OBSERVATION (đo được); phòng suy từ hội
  thoại (`focus_room`) là ASSUMPTION.
- Ưu tiên trạng thái thiết bị + cảm biến LIVE (từ snapshot) hơn giá trị tĩnh trong
  registry, để agent hiểu và lập kế hoạch theo hoàn cảnh thực tế.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.iot.registry import DEVICE_SPECS, ROOMS, SENSOR_SPECS, DeviceSpec
from src.nlu.normalizer import NormalizedUtterance
from src.nlu.schemas import ContextKind, ContextNote, Device, RuntimeContext, SensorReading

# Giới hạn số lượt hội thoại gần đây đưa vào context — đủ để giải tham chiếu, không hơn.
_MAX_RECENT_DIALOGUE = 6
# Trần số thiết bị đưa vào khi không có tín hiệu thu hẹp nào (tránh dump cả nhà).
_MAX_FALLBACK_DEVICES = 12


def _to_device(spec: DeviceSpec, live_states: dict[str, dict[str, Any]] | None) -> Device:
    # Trạng thái LIVE (nếu snapshot có) đè lên initial_state tĩnh: context phải mô tả
    # đúng hiện thực, không phải trạng thái mặc định khi seed.
    state = dict(spec.initial_state)
    has_live = bool(live_states and spec.slug in live_states)
    if has_live and live_states is not None:
        state.update(live_states[spec.slug] or {})
    # §7.4: nếu CHƯA có snapshot, state chỉ là default tĩnh (không phải quan sát) → đánh dấu để
    # consumer thận trọng; `online` đọc từ snapshot nếu có, mặc định True.
    online = bool(state.get("online", True)) if has_live else True
    state.pop("online", None)
    return Device(
        device_id=spec.slug,
        name=spec.name,
        room=spec.room,
        device_type=spec.device_type.value,
        risk_level=spec.risk_level,
        capabilities=list(spec.capabilities),
        state=state,
        online=online,
        state_source="live" if has_live else "default",
    )


def _relevant_specs(nu: NormalizedUtterance, *, speaker_location: str | None, focus_room: str | None) -> list[DeviceSpec]:
    """Chọn thiết bị liên quan theo thứ tự ưu tiên:
    khớp trực tiếp trong câu > phòng nêu trong câu > phòng người nói đang đứng >
    phòng đang tập trung (hội thoại) > fallback tập nhỏ."""
    matched = set(nu.matched_device_ids)
    rooms = set(nu.matched_rooms)
    # Không nêu phòng trong câu → dùng vị trí người nói (đo được) trước, rồi mới tới
    # focus_room (suy đoán từ hội thoại).
    if not rooms:
        loc = speaker_location or focus_room
        if loc:
            rooms = {loc}

    selected: list[DeviceSpec] = []
    for spec in DEVICE_SPECS:
        if spec.slug in matched or (rooms and spec.room in rooms):
            selected.append(spec)

    if selected:
        return selected
    # Không có tín hiệu thu hẹp: đưa một tập nhỏ có giới hạn để LLM còn ground được,
    # nhưng không phải toàn bộ catalog.
    return list(DEVICE_SPECS[:_MAX_FALLBACK_DEVICES])


def _build_sensor_readings(live_sensors: list[dict[str, Any]] | None, *, now: datetime) -> list[SensorReading]:
    """Dựng danh sách chỉ số cảm biến (spec §7.4). Live → observed/confident; fallback registry
    tĩnh → is_stale + source="default" + confidence=None (unknown, KHÔNG trình bày như live)."""
    if live_sensors:
        return [
            SensorReading(
                slug=str(s.get("slug", "")),
                name=str(s.get("name", "")),
                sensor_type=str(s.get("sensor_type", "")),
                value=float(s.get("value", 0.0)),
                unit=str(s.get("unit", "")),
                room=str(s.get("room", "") or ""),
                observed_at=now,
                is_stale=False,
                confidence=float(s["confidence"]) if s.get("confidence") is not None else 1.0,
                source="live",
            )
            for s in live_sensors
        ]
    return [
        SensorReading(
            slug=s.slug,
            name=s.name,
            sensor_type=s.sensor_type.value,
            value=s.value,
            unit=s.unit,
            room=s.room,
            observed_at=None,
            is_stale=True,
            confidence=None,
            source="default",
        )
        for s in SENSOR_SPECS
    ]


def build_runtime_context(
    nu: NormalizedUtterance,
    *,
    now: datetime,
    timezone: str = "Asia/Ho_Chi_Minh",
    focus_room: str | None = None,
    speaker_location: str | None = None,
    speaker_location_source: str = "",
    speaker_location_confidence: float = 0.0,
    speaker_home_room: str | None = None,
    speaker_private_room: str | None = None,
    recent_dialogue: list[str] | None = None,
    live_device_states: dict[str, dict[str, Any]] | None = None,
    live_sensors: list[dict[str, Any]] | None = None,
) -> RuntimeContext:
    # speaker_location chỉ có nghĩa khi là phòng thật trong nhà.
    speaker_location = speaker_location if speaker_location in ROOMS else None
    speaker_home_room = speaker_home_room if speaker_home_room in ROOMS else None
    speaker_private_room = speaker_private_room if speaker_private_room in ROOMS else None

    specs = _relevant_specs(nu, speaker_location=speaker_location, focus_room=focus_room)
    if live_device_states:
        # A caller-provided live snapshot is physical evidence. Keep every device
        # represented in that snapshot available for state-qualified grounding
        # ("close the window that is open"), even when no room was named.
        selected = {spec.slug for spec in specs}
        specs.extend(
            spec for spec in DEVICE_SPECS
            if spec.slug in live_device_states and spec.slug not in selected
        )
    devices = [_to_device(s, live_device_states) for s in specs]
    sensors = _build_sensor_readings(live_sensors, now=now)

    notes: list[ContextNote] = [
        ContextNote(kind=ContextKind.OBSERVATION, text=f"Thời điểm hiện tại: {now.isoformat()} ({timezone})"),
        ContextNote(
            kind=ContextKind.OBSERVATION,
            text=f"Số thiết bị liên quan đưa vào ngữ cảnh: {len(devices)}",
        ),
    ]
    # Vị trí người nói là ĐO ĐƯỢC → OBSERVATION (khác focus_room là ASSUMPTION).
    if speaker_location:
        notes.append(
            ContextNote(kind=ContextKind.OBSERVATION, text=f"Người nói đang ở: {speaker_location}")
        )
    if nu.matched_rooms:
        notes.append(
            ContextNote(kind=ContextKind.INFERENCE, text=f"Phòng nhắc tới trong câu: {', '.join(nu.matched_rooms)}")
        )
    elif not speaker_location and focus_room:
        notes.append(
            ContextNote(kind=ContextKind.ASSUMPTION, text=f"Không nêu phòng — giả định phòng đang tập trung: {focus_room}")
        )
    if nu.has_reference:
        notes.append(
            ContextNote(kind=ContextKind.INFERENCE, text="Câu có tham chiếu ngầm — cần lượt hội thoại trước để giải")
        )
    # §7.4: thiếu snapshot live → trạng thái/cảm biến chỉ là default tĩnh, đánh dấu ASSUMPTION.
    if not live_device_states:
        notes.append(ContextNote(kind=ContextKind.ASSUMPTION, text="Chưa có snapshot thiết bị live — dùng trạng thái mặc định (stale)"))
    if not live_sensors:
        notes.append(ContextNote(kind=ContextKind.ASSUMPTION, text="Chưa có cảm biến live — chỉ số môi trường là mặc định (unknown)"))

    recent = (recent_dialogue or [])[-_MAX_RECENT_DIALOGUE:]

    # focus_room cho giải tham chiếu: câu > vị trí người nói > hội thoại.
    effective_focus = (
        nu.matched_rooms[0] if nu.matched_rooms else (speaker_location or focus_room)
    )

    return RuntimeContext(
        now=now,
        timezone=timezone,
        devices=devices,
        rooms=list(ROOMS),
        speaker_location=speaker_location,
        speaker_location_source=speaker_location_source,
        speaker_location_confidence=speaker_location_confidence,
        speaker_home_room=speaker_home_room,
        speaker_private_room=speaker_private_room,
        focus_room=effective_focus,
        recent_dialogue=recent,
        sensors=sensors,
        notes=notes,
    )


def hydrate_focus_room(
    ctx: RuntimeContext,
    room: str,
    *,
    live_device_states: dict[str, dict[str, Any]] | None = None,
) -> RuntimeContext:
    """Ensure a deterministically resolved conversation room has its inventory.

    Layer 1 initially scopes devices from the current utterance.  Elliptical
    follow-ups (``thêm chút``, ``giảm nữa``) contain no room, so that first pass
    intentionally uses the bounded fallback catalog.  After Layer 2 resolves a
    room from the canonical ledger, planning still needs the devices in that
    room; merely changing ``focus_room`` leaves the fallback inventory stale.

    This helper keeps inventory ownership in the context builder: callers provide
    only a registry-valid room, and this layer adds canonical devices plus current
    live state without replacing any entities already selected for the turn.
    """
    if room not in ctx.rooms:
        return ctx
    present = {device.device_id for device in ctx.devices}
    additions = [
        _to_device(spec, live_device_states)
        for spec in DEVICE_SPECS
        if spec.room == room and spec.slug not in present
    ]
    if not additions and ctx.focus_room == room:
        return ctx
    return ctx.model_copy(
        update={
            "focus_room": room,
            "devices": [*ctx.devices, *additions],
        }
    )
