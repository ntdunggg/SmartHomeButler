"""Layer 1 context builder (spec §7) — dựng Unified Runtime Context + Power Load.

Tái dùng `src.nlu.normalizer.analyze` (tách facts tất định) và
`src.nlu.context.build_runtime_context` (dựng RuntimeContext đã lọc theo phòng/câu)
— spec §71: ưu tiên tái dùng abstraction. Bổ sung Power Load model (§8) mà pipeline
cũ chưa có.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.agent.perception.power_monitor import build_power_load, estimate_baseline_watts
from src.agent.schemas import PowerLoad, RuntimeContext
from src.nlu.context import build_runtime_context as _build_nlu_context
from src.nlu.normalizer import NormalizedUtterance, analyze


def build_perception(
    utterance: str,
    *,
    now: datetime | None = None,
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
    current_watts: float | None = None,
) -> tuple[NormalizedUtterance, RuntimeContext, PowerLoad]:
    """Trả (normalized, runtime_context, power_load) cho một lượt.

    `current_watts=None` → ước lượng từ trạng thái thiết bị đang bật (spec §42), nếu
    không có snapshot thì coi như 0 W (NORMAL).
    """
    now = now or datetime.now(UTC)
    nu = analyze(
        utterance,
        focus_room=focus_room,
        speaker_home_room=speaker_home_room,
        speaker_private_room=speaker_private_room,
    )
    ctx = _build_nlu_context(
        nu,
        now=now,
        timezone=timezone,
        focus_room=focus_room,
        speaker_location=speaker_location,
        speaker_location_source=speaker_location_source,
        speaker_location_confidence=speaker_location_confidence,
        speaker_home_room=speaker_home_room,
        speaker_private_room=speaker_private_room,
        recent_dialogue=recent_dialogue,
        live_device_states=live_device_states,
        live_sensors=live_sensors,
    )
    # Tải điện: chưa có cơ sở đo (không current_watts VÀ không snapshot thiết bị) → unknown, KHÔNG
    # coi mặc định 0W/NORMAL là quan sát thật (spec §7.4).
    power_unknown = current_watts is None and not live_device_states
    watts = current_watts if current_watts is not None else estimate_baseline_watts(live_device_states)
    power = build_power_load(watts, unknown=power_unknown)
    return nu, ctx, power
