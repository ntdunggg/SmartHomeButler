"""Helper dùng chung cho test thói quen: log bằng chứng, đồng hồ mô phỏng, vòng học.

``learn_confirmed`` thay cho vòng học cũ. Production nay chia làm hai bước:
``detect_new_habits`` tìm ứng viên và cập nhật thói quen đã có, còn thói quen MỚI chỉ
được ghi sau khi người dùng xác nhận (``commit_new_habit``). Helper coi như người dùng
xác nhận hết mọi ứng viên.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import get_settings
from src.core import clock
from src.domain.enums import ActionStatus
from src.domain.models import ActionLog, Device, Habit, Household, User
from src.memory import habits
from src.services.audit import SOURCE_USER

DEMO_DEVICE_SLUG = "dieu_hoa_phong_khach"

_BASE = datetime(2026, 8, 1, tzinfo=clock.LOCAL_TZ)


def ids(session: Session) -> tuple[int, int, int]:
    """(household_id, user_id 'bo', device_id của máy lạnh phòng khách) trong dữ liệu seed."""
    hh = session.scalar(select(Household))
    user = session.scalar(select(User).where(User.username == "bo"))
    device = session.scalar(select(Device).where(Device.household_id == hh.id, Device.slug == DEMO_DEVICE_SLUG))
    return hh.id, user.id, device.id


def vn(day: int, hour: int = 22, minute: int = 30) -> datetime:
    """Mốc UTC ứng với ``day`` ngày sau mốc gốc, lúc ``hour:minute`` giờ VN."""
    return (_BASE + timedelta(days=day)).replace(hour=hour, minute=minute).astimezone(UTC)


def freeze_vn(day: int, hour: int = 22, minute: int = 30) -> None:
    clock.set_clock(frozen_ms=int(vn(day, hour, minute).timestamp() * 1000), mode="frozen")


def log_evidence(session, *, hh, user, device, action, when, params=None, source=SOURCE_USER) -> None:
    session.add(
        ActionLog(
            household_id=hh,
            user_id=user,
            device_id=device,
            action=action,
            params=params or {},
            status=ActionStatus.EXECUTED,
            source=source,
            created_at=when,
        )
    )
    session.flush()


def learn_confirmed(session: Session, *, household_id: int) -> list[Habit]:
    """Chạy một vòng quét rồi coi như người dùng xác nhận mọi thói quen mới.

    Trả về các thói quen học được đủ tin cậy, không kể thói quen tạo tay.
    """
    for candidate in habits.detect_new_habits(session, household_id=household_id):
        habits.commit_new_habit(session, household_id=household_id, candidate=candidate)
    session.flush()
    threshold = get_settings().habit_min_confidence
    return list(
        session.scalars(
            select(Habit)
            .where(
                Habit.household_id == household_id,
                Habit.source != habits.SOURCE_MANUAL,
                Habit.confidence >= threshold,
            )
            .order_by(Habit.id)
        )
    )
