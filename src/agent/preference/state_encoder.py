"""RL State encoder (spec §30, FR-08) — mã hoá context thành khoá trạng thái rời rạc.

S = (room, time_bucket, activity, weather_category, occupancy, power_mode).
Scope người dùng và hộ gia đình được quản lý ở tầng PreferenceRepository.
KHÔNG đưa raw utterance vào state — RL học PREFERENCE theo hoàn cảnh, không theo câu chữ.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

RL_STATE_VERSION = 1


@dataclass(frozen=True, slots=True)
class RLState:
    """Khoá trạng thái RL (spec §30, FR-08). Hashable để làm key trong Q-table."""

    resident: str = "unknown"
    room: str = "unknown"
    time_bucket: str = "day"
    activity: str = "general"
    weather: str = "unknown"
    occupancy: str = "unknown"
    power_mode: str = "NORMAL"
    version: int = RL_STATE_VERSION

    def key(self) -> str:
        """Khoá trạng thái canonical hoá (không chứa resident/household để tránh trùng lặp scope)."""
        return "|".join(
            (self.room, self.time_bucket, self.activity, self.weather, self.occupancy, self.power_mode)
        )

    def full_key(self) -> str:
        """Khoá đầy đủ kèm resident (dành cho legacy/debug)."""
        return "|".join(
            (self.resident, self.room, self.time_bucket, self.activity, self.weather, self.occupancy, self.power_mode)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "resident": self.resident,
            "room": self.room,
            "time_bucket": self.time_bucket,
            "activity": self.activity,
            "weather": self.weather,
            "occupancy": self.occupancy,
            "power_mode": self.power_mode,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RLState:
        return cls(
            resident=str(data.get("resident", "unknown")),
            room=str(data.get("room", "unknown")),
            time_bucket=str(data.get("time_bucket", "day")),
            activity=str(data.get("activity", "general")),
            weather=str(data.get("weather", "unknown")),
            occupancy=str(data.get("occupancy", "unknown")),
            power_mode=str(data.get("power_mode", "NORMAL")),
            version=int(data.get("version", RL_STATE_VERSION)),
        )


def time_bucket_of(now: datetime) -> str:
    """Chia ngày thành các khung: morning/afternoon/evening/night (spec §30)."""
    h = now.hour
    if 5 <= h < 12:
        return "morning"
    if 12 <= h < 17:
        return "afternoon"
    if 17 <= h < 22:
        return "evening"
    return "night"


def weather_category(outside_temp_c: float | None) -> str:
    """Phân loại thời tiết cho RL state (spec §30). Giá trị thiếu -> 'unknown'."""
    if outside_temp_c is None:
        return "unknown"
    if outside_temp_c <= 18:
        return "cold"
    if outside_temp_c >= 30:
        return "hot"
    return "mild"


def encode_state(
    *,
    resident: str | None,
    room: str | None,
    now: datetime,
    activity: str = "general",
    outside_temp_c: float | None = None,
    occupied: bool | None = None,
    power_mode: str = "NORMAL",
) -> RLState:
    """Dựng RLState từ context. Giá trị thiếu → 'unknown' (KHÔNG tự đoán, spec §7.4)."""
    return RLState(
        resident=resident or "unknown",
        room=room or "unknown",
        time_bucket=time_bucket_of(now),
        activity=activity or "general",
        weather=weather_category(outside_temp_c),
        occupancy=("occupied" if occupied else "empty") if occupied is not None else "unknown",
        power_mode=power_mode or "NORMAL",
        version=RL_STATE_VERSION,
    )


def build_canonical_decision_context(
    *,
    resident: str | None,
    room: str | None,
    now: datetime,
    activity: str = "general",
    outside_temp_c: float | None = None,
    occupied: bool | None = None,
    power_mode: str = "NORMAL",
    live_device_states: dict | None = None,
    sensors: list | None = None,
) -> dict[str, Any]:
    """Tạo snapshot ngữ cảnh chuẩn hoá (DecisionContext) phục vụ cả inference, HITL và feedback."""
    state = encode_state(
        resident=resident,
        room=room,
        now=now,
        activity=activity,
        outside_temp_c=outside_temp_c,
        occupied=occupied,
        power_mode=power_mode,
    )
    return {
        "rl_state": state.to_dict(),
        "resident": resident,
        "room": room,
        "now": now.isoformat() if hasattr(now, "isoformat") else str(now),
        "activity": activity,
        "outside_temp_c": outside_temp_c,
        "occupied": occupied,
        "power_mode": power_mode,
        "live_device_states": live_device_states or {},
        "sensor_count": len(sensors or []),
    }
