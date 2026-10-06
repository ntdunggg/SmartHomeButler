"""Sensor adapter (spec §7.2, §7.4) — chuẩn hoá cảm biến vào Unified Runtime Context.

Layer 1 rules (spec §7.4): sensor values có timestamp; stale phải đánh dấu; occupancy
không chắc phải kèm confidence; unknown giữ `unknown`, KHÔNG tự đoán. Ở đây ta chỉ
đọc snapshot đã chuẩn hoá (live nếu có, tĩnh nếu không) — không suy luận ngữ nghĩa.
"""

from __future__ import annotations

from typing import Any

from src.agent.schemas import SensorReading


def sensor_by_type(sensors: list[SensorReading], sensor_type: str, room: str | None = None) -> SensorReading | None:
    """Lấy chỉ số cảm biến theo loại (và phòng nếu nêu). None nếu không có (unknown)."""
    for s in sensors:
        if s.sensor_type != sensor_type:
            continue
        if room is not None and s.room and s.room != room:
            continue
        return s
    return None


def outdoor_sensor_by_type(sensors: list[SensorReading], sensor_type: str) -> SensorReading | None:
    """Lấy chỉ số cảm biến ngoài trời hoặc không gắn với phòng cụ thể (Finding 4)."""
    for s in sensors:
        if s.sensor_type != sensor_type:
            continue
        # Định danh cảm biến là `slug` (SensorReading không có `sensor_id`). Ngoài trời nhận diện
        # qua phòng rỗng HOẶC slug chứa ngoai_troi/outdoor.
        sslug = str(getattr(s, "slug", "") or "")
        sroom = getattr(s, "room", None)
        if not sroom or "ngoai_troi" in sslug or "outdoor" in sslug:
            return s
    return None


def occupancy_for_room(sensors: list[SensorReading], room: str) -> dict[str, Any]:
    """Trạng thái hiện diện của một phòng.

    Trả `{"occupied": bool | None, "confidence": float}`; occupied=None nghĩa là
    unknown (không có cảm biến) — KHÔNG tự đoán là trống (spec §7.4).
    """
    reading = sensor_by_type(sensors, "presence", room=room)
    if reading is None:
        return {"occupied": None, "confidence": 0.0}
    # Confidence lấy từ NGUỒN cảm biến (spec §7.4), KHÔNG hardcode 1.0: cảm biến default/stale
    # (chưa có snapshot live) → độ tin cậy thấp; unknown confidence → 0.0.
    if reading.confidence is not None:
        conf = reading.confidence
    else:
        conf = 0.0 if (reading.is_stale or reading.source == "default") else 1.0
    return {"occupied": reading.value >= 0.5, "confidence": conf}
