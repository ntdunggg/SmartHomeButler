"""Seed dữ liệu mẫu: một hộ gia đình 4 thành viên đủ 3 nhóm tuổi + 15 thiết bị.

Bốn tài khoản được thiết kế để demo trực tiếp ma trận phân quyền:
bố và mẹ (chủ hộ — quyền cao nhất, mệnh lệnh thực thi ngay không cần duyệt),
vị thành niên (cần chủ hộ duyệt thiết bị công suất lớn), trẻ nhỏ (bị từ chối thẳng).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.security import hash_password
from src.db.session import create_tables, session_scope
from src.domain.enums import Role
from src.domain.models import Device, Household, Preference, Room, Sensor, User
from src.iot.registry import DEVICE_SPECS, ROOMS, SENSOR_SPECS

DEMO_PASSWORD = "demo1234"

# (username, họ tên, vai trò, tuổi, tên phòng riêng)
# Bố VÀ mẹ đều là chủ hộ (OWNER) — quyền cao nhất, ngang nhau; con cái là thành viên.
# Phòng riêng: bố/mẹ ở phòng ngủ bố mẹ, hai con chung phòng ngủ con (phòng riêng
# có thể trùng nhau). Thành viên được quyền mọi thiết bị trong phòng riêng của mình.
# (username, họ tên, vai trò, tên phòng riêng) — không còn tuổi (bỏ age theo plan).
DEMO_USERS: tuple[tuple[str, str, Role, str], ...] = (
    ("bo", "Trần Văn Bố", Role.OWNER, "Phòng ngủ bố mẹ"),
    ("me", "Nguyễn Thị Mẹ", Role.OWNER, "Phòng ngủ bố mẹ"),
    ("con_lon", "Trần Minh Anh", Role.MEMBER, "Phòng ngủ con"),
    ("con_nho", "Trần Bảo Bảo", Role.MEMBER, "Phòng ngủ con"),
)

# Sở thích trái ngược nhau giữa bố và mẹ — dùng để demo phát hiện xung đột
DEMO_PREFERENCES: tuple[tuple[str, str, float, str], ...] = (
    ("bo", "air_conditioner.temperature", 26.0, "Bố thích điều hoà 26°C"),
    ("me", "air_conditioner.temperature", 24.0, "Mẹ thích điều hoà 24°C"),
    ("bo", "light.brightness", 70.0, "Bố thích đèn sáng vừa"),
    ("con_lon", "air_conditioner.temperature", 25.0, "Minh Anh thích 25°C"),
)


def seed_household(session: Session) -> Household:
    """Tạo hộ mẫu nếu chưa có. Chạy lại nhiều lần không nhân bản dữ liệu."""
    household = session.scalar(select(Household).where(Household.name == "Căn hộ A-1203"))
    if household is None:
        household = Household(name="Căn hộ A-1203", address="Chung cư X, TP. Hồ Chí Minh")
        session.add(household)
        session.flush()

    rooms = _seed_rooms(session, household)
    _seed_users(session, household, rooms)
    _seed_devices(session, household, rooms)
    _seed_sensors(session, household)
    _seed_preferences(session, household)
    # Phải chạy SAU _seed_devices (cần thiết bị đã tồn tại để cấp quyền theo phòng).
    _seed_member_access(session, household)
    return household


def _seed_rooms(session: Session, household: Household) -> dict[str, Room]:
    """Tạo các phòng mặc định, trả về map tên → Room để gắn thiết bị."""
    from sqlalchemy import delete
    session.execute(delete(Room).where(Room.household_id == household.id, ~Room.name.in_(ROOMS)))
    by_name: dict[str, Room] = {}
    for order, name in enumerate(ROOMS):
        room = session.scalar(select(Room).where(Room.household_id == household.id, Room.name == name))
        if room is None:
            room = Room(household_id=household.id, name=name, sort_order=order)
            session.add(room)
        by_name[name] = room
    session.flush()
    return by_name


def _seed_users(session: Session, household: Household, rooms: dict[str, Room]) -> None:
    import uuid

    for username, full_name, role, private_room in DEMO_USERS:
        room_id = rooms[private_room].id if private_room in rooms else None
        existing = session.scalar(select(User).where(User.username == username))
        if existing is not None:
            # Đồng bộ vai trò cho tài khoản demo có sẵn khi seed lại.
            if existing.role != role:
                existing.role = role
            if existing.account_id is None:
                existing.account_id = uuid.uuid4().hex[:12]
            # Gán phòng riêng cho DB cũ chưa có (không đè nếu chủ hộ đã đổi tay).
            if existing.home_room_id is None:
                existing.home_room_id = room_id
            if existing.private_room_id is None:
                existing.private_room_id = room_id
            continue
        user = User(
            household_id=household.id,
            username=username,
            account_id=uuid.uuid4().hex[:12],
            password_hash=hash_password(DEMO_PASSWORD),
            role=role,
            home_room_id=room_id,
            private_room_id=room_id,
        )
        # full_name là property mã hoá — phải gán sau khi có household_id
        user.full_name = full_name
        session.add(user)
    session.flush()


def _seed_devices(session: Session, household: Household, rooms: dict[str, Room]) -> None:
    from sqlalchemy import delete
    valid_slugs = [s.slug for s in DEVICE_SPECS]
    session.execute(delete(Device).where(Device.household_id == household.id, ~Device.slug.in_(valid_slugs)))
    for spec in DEVICE_SPECS:
        existing = session.scalar(select(Device).where(Device.household_id == household.id, Device.slug == spec.slug))
        if existing is not None:
            # Gắn room_id cho DB đã seed từ trước khi có bảng Room
            if existing.room_id is None and existing.room in rooms:
                existing.room_id = rooms[existing.room].id
            continue
        room = rooms.get(spec.room)
        if room is None:
            raise ValueError(
                f"Thiết bị seed '{spec.slug}' có room={spec.room!r} không nằm trong ROOMS. "
                f"Sửa registry, hoặc chuyển nó sang _CATALOG_ONLY_SPECS nếu chỉ để thêm từ thư viện."
            )
        session.add(
            Device(
                household_id=household.id,
                slug=spec.slug,
                name=spec.name,
                room=spec.room,
                room_id=room.id,
                device_type=spec.device_type,
                risk_level=spec.risk_level,
                capabilities=[c.value for c in spec.capabilities],
                state=dict(spec.initial_state),
            )
        )
    session.flush()


def _seed_sensors(session: Session, household: Household) -> None:
    for spec in SENSOR_SPECS:
        existing = session.scalar(select(Sensor).where(Sensor.household_id == household.id, Sensor.slug == spec.slug))
        if existing is not None:
            continue
        session.add(
            Sensor(
                household_id=household.id,
                slug=spec.slug,
                name=spec.name,
                sensor_type=spec.sensor_type,
                value=spec.value,
                unit=spec.unit,
                room=spec.room,
            )
        )
    session.flush()


def _seed_preferences(session: Session, household: Household) -> None:
    for username, key, value, note in DEMO_PREFERENCES:
        user = session.scalar(select(User).where(User.username == username))
        if user is None:
            continue
        existing = session.scalar(select(Preference).where(Preference.user_id == user.id, Preference.key == key))
        if existing is not None:
            continue
        session.add(
            Preference(
                household_id=household.id,
                user_id=user.id,
                key=key,
                value=value,
                note=note,
            )
        )
    session.flush()


def _seed_member_access(session: Session, household: Household) -> None:
    """Cấp sẵn cho mỗi thành viên quyền các thiết bị trong phòng riêng của họ.

    Hiện thực hoá ý định "thành viên có ít nhất quyền của phòng mình": khi deploy
    lần đầu, con cái không còn trắng quyền. Idempotent — re-seed không nhân bản và
    không đè luật chủ hộ đã chỉnh tay (xem ``grant_private_room_access``).
    """
    from src.core.permissions import grant_private_room_access

    members = session.scalars(
        select(User).where(User.household_id == household.id, User.role == Role.MEMBER)
    ).all()
    for member in members:
        grant_private_room_access(session, user=member)
    session.flush()


def run_seed() -> None:
    """Tạo bảng và seed — gọi khi khởi động app hoặc chạy trực tiếp file này."""
    create_tables()
    with session_scope() as session:
        seed_household(session)


if __name__ == "__main__":
    run_seed()
    print("Seed xong. Đăng nhập thử: bo / me / con_lon / con_nho — mật khẩu:", DEMO_PASSWORD)
