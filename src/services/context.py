"""Dựng ảnh chụp ngữ cảnh ngôi nhà.

Gom trạng thái thiết bị và cảm biến thành một đối tượng duy nhất để truyền vào
bộ phát hiện xung đột và bộ automation. Tách riêng ở đây nên hai bên kia không
cần biết gì về SQLAlchemy và test được bằng dữ liệu giả.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.agent.planning.conflict import ContextSnapshot
from src.config import get_settings
from src.core import clock
from src.domain.enums import SensorType
from src.domain.models import ActionLog, Device, Room, Sensor, User


def sensor_values(session: Session, *, household_id: int) -> dict[str, float]:
    """Trả về ``{sensor_type: giá trị}`` cho cả hộ.

    Lưu ý: presence giờ là NHIỀU cảm biến theo phòng — dict này gộp mất phòng và chỉ
    giữ một giá trị. Đừng dùng nó để suy "có người ở nhà"; dùng ``someone_home`` /
    ``presence_rooms`` thay thế."""
    rows = session.scalars(select(Sensor).where(Sensor.household_id == household_id))
    return {row.sensor_type: row.value for row in rows}


def presence_rooms(session: Session, *, household_id: int) -> list[str]:
    """Các phòng đang phát hiện có người (cảm biến hiện diện ≥ 1). Đầu vào cho Location Resolver."""
    rows = session.scalars(
        select(Sensor).where(Sensor.household_id == household_id, Sensor.sensor_type == SensorType.PRESENCE)
    )
    return [row.room for row in rows if row.value >= 1.0 and row.room]


def someone_home(session: Session, *, household_id: int) -> bool:
    """Có người ở nhà = BẤT KỲ cảm biến hiện diện nào ≥ 1 (gộp mọi phòng)."""
    rows = session.scalars(
        select(Sensor).where(Sensor.household_id == household_id, Sensor.sensor_type == SensorType.PRESENCE)
    )
    values = [row.value for row in rows]
    # Không có cảm biến presence nào → mặc định coi như có người (fail-safe, không tự tắt đồ).
    return any(v >= 1.0 for v in values) if values else True


def device_states(session: Session, *, household_id: int) -> dict[str, dict]:
    rows = session.scalars(select(Device).where(Device.household_id == household_id))
    return {row.slug: dict(row.state or {}) for row in rows}


def sensor_readings(session: Session, *, household_id: int) -> list[dict]:
    """Chỉ số cảm biến đầy đủ (kèm phòng) để pipeline scope theo vị trí người nói.

    Khác `sensor_values` (chỉ trả {sensor_type: value}, mất phòng): ở đây giữ slug,
    tên, phòng để context builder dựng ``SensorReading`` live thay cho registry tĩnh."""
    rows = session.scalars(select(Sensor).where(Sensor.household_id == household_id))
    return [
        {
            "slug": row.slug,
            "name": row.name,
            "sensor_type": row.sensor_type,
            "value": row.value,
            "unit": row.unit,
            "room": row.room or "",
        }
        for row in rows
    ]


def away_minutes(session: Session, *, household_id: int, someone_home: bool) -> int:
    """Ước lượng nhà đã vắng bao lâu, dựa vào hành động gần nhất có người thao tác.

    Cảm biến hiện diện chỉ cho biết "hiện có ai không", không cho biết "vắng bao
    lâu"; log hành động lấp được khoảng trống đó mà không cần thêm bảng mới.
    """
    if someone_home:
        return 0
    last = session.scalar(
        select(ActionLog.created_at)
        .where(ActionLog.household_id == household_id, ActionLog.source == "user")
        .order_by(ActionLog.created_at.desc())
        .limit(1)
    )
    if last is None:
        return 0
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    delta: timedelta = clock.now() - last
    return max(0, int(delta.total_seconds() // 60))


def build_snapshot(session: Session, *, household_id: int, actor_name: str = "") -> ContextSnapshot:
    home = someone_home(session, household_id=household_id)
    return ContextSnapshot(
        device_states=device_states(session, household_id=household_id),
        someone_home=home,
        away_minutes=away_minutes(session, household_id=household_id, someone_home=home),
        actor_name=actor_name,
        away_threshold_minutes=get_settings().away_minutes_threshold,
    )


def describe_for_llm(session: Session, *, household_id: int) -> str:
    """Mô tả ngắn gọn ngữ cảnh để đính vào prompt của LLM."""
    sensors = sensor_values(session, household_id=household_id)
    states = device_states(session, household_id=household_id)
    lines = [f"- Thời gian hiện tại: {datetime.now().strftime('%H:%M, thứ %w')}"]
    if SensorType.PM25 in sensors:
        lines.append(f"- Bụi PM2.5: {sensors[SensorType.PM25]:.0f} µg/m³")
    if SensorType.AQI in sensors:
        lines.append(f"- Chỉ số AQI: {sensors[SensorType.AQI]:.0f}")
    if SensorType.TEMPERATURE in sensors:
        lines.append(f"- Nhiệt độ ngoài trời: {sensors[SensorType.TEMPERATURE]:.0f}°C")
    if SensorType.HUMIDITY in sensors:
        lines.append(f"- Độ ẩm ngoài trời: {sensors[SensorType.HUMIDITY]:.0f}%")
    if SensorType.RAIN in sensors:
        lines.append(f"- Trời mưa: {'có' if sensors[SensorType.RAIN] >= 1 else 'không'}")
    if SensorType.WIND_SPEED in sensors:
        lines.append(f"- Tốc độ gió: {sensors[SensorType.WIND_SPEED]:.0f} km/h")
    if SensorType.UV_INDEX in sensors:
        lines.append(f"- Chỉ số UV: {sensors[SensorType.UV_INDEX]:.1f}")
    rooms_here = presence_rooms(session, household_id=household_id)
    lines.append(f"- Có người ở nhà: {'có' if rooms_here else 'không'}")
    if rooms_here:
        lines.append(f"- Phòng đang có người: {', '.join(rooms_here)}")

    on_devices = [slug for slug, state in states.items() if state.get("power") == "on"]
    lines.append(f"- Thiết bị đang bật: {', '.join(on_devices) if on_devices else 'không có'}")
    return "\n".join(lines)


def speaker_home_room_for(role: str) -> str:
    """Phòng RIÊNG của người nói, suy từ vai trò trong hộ.

    Dùng để giải "phòng của tôi" LẪN cụm phòng chung mơ hồ ("phòng ngủ" khi nhà có hai
    phòng ngủ). Đặt ở một chỗ vì cả `agent_runner` (đường chat/lệnh) lẫn `nlu_gateway`
    (đường /agent/nlu) đều cần: trước đây chỉ agent_runner suy, nên CÙNG một người hỏi
    cùng một câu lại được phân giải khác nhau tuỳ endpoint mà UI gọi.

    Hộ demo có 2 phòng ngủ: chủ hộ (bố/mẹ) → phòng ngủ bố mẹ, còn lại → phòng ngủ con.
    Heuristic theo vai trò vì User chưa có trường gán phòng; thêm Room-per-user thì thay
    ĐÚNG hàm này.
    """
    from src.domain.enums import Role
    from src.iot.registry import KIDS_ROOM, PARENTS_ROOM

    try:
        is_owner = Role(role) is Role.OWNER
    except ValueError:
        is_owner = False
    return PARENTS_ROOM if is_owner else KIDS_ROOM


@dataclass(frozen=True, slots=True)
class SpeakerRoomContext:
    """Hai ngữ nghĩa phòng cá nhân đã resolve thành tên phòng canonical."""

    home_room: str
    private_room: str


def _assigned_room_name(session: Session, user: User, room_id: int | None) -> str:
    """Resolve room id trong đúng household; dữ liệu chéo hộ/stale bị bỏ qua an toàn."""
    if room_id is None:
        return ""
    room = session.get(Room, room_id)
    if room is None or room.household_id != user.household_id:
        return ""
    return room.name


def speaker_room_context_for(session: Session, user: User) -> SpeakerRoomContext:
    """Phòng cá nhân của user; role chỉ là fallback tương thích dữ liệu cũ.

    ``private_room`` ground cụm sở hữu ("phòng của tôi"). ``home_room`` là mặc định
    cho cụm phòng chung mơ hồ và các mục tiêu gắn với không gian sinh hoạt của user.
    Khi chỉ một trường được gán, dùng nó cho cả hai ngữ nghĩa thay vì quay về role.
    """
    fallback = speaker_home_room_for(str(user.role))
    assigned_home = _assigned_room_name(session, user, user.home_room_id)
    assigned_private = _assigned_room_name(session, user, user.private_room_id)
    return SpeakerRoomContext(
        home_room=assigned_home or assigned_private or fallback,
        private_room=assigned_private or assigned_home or fallback,
    )
