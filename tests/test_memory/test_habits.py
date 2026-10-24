"""Học thói quen từ ActionLog: bám đồng hồ mô phỏng + giờ địa phương.

Đường này trước đây không có test. Các case ở đây khoá đúng những lỗi Nhóm 1/4:
- Gom/nhãn/so "tới giờ" theo GIỜ VN người dùng thấy, không phải UTC (#1/#14/#15).
- learn/due bám ĐỒNG HỒ MÔ PHỎNG chứ không giờ thực (#4), có cận trên chống log
  "tương lai" khi nhảy lùi (#12).
- Học kèm THAM SỐ hay dùng (mấy độ) để đề xuất tái hiện đúng (#2).
- Thói quen hết bằng chứng thì confidence về 0, ngừng đề xuất (#3).
- Nhảy đồng hồ lùi không làm thói quen câm vì mốc "đã chạy" cũ (#13).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from src.core import clock
from src.domain.enums import ActionStatus
from src.domain.models import Device, Habit, Household, User
from src.memory import habits
from src.services.audit import SOURCE_AUTOMATION, SOURCE_USER

_AC = "dieu_hoa_phong_khach"


def _ids(session):
    hh = session.scalar(select(Household))
    user = session.scalar(select(User).where(User.username == "bo"))
    device = session.scalar(select(Device).where(Device.household_id == hh.id, Device.slug == _AC))
    return hh.id, user.id, device.id


_BASE = datetime(2026, 8, 1, tzinfo=clock.LOCAL_TZ)


def _vn(day: int, hour: int = 22, minute: int = 30) -> datetime:
    """Mốc UTC ứng với ``day`` ngày sau mốc gốc, lúc ``hour:minute`` giờ VN."""
    return (_BASE + timedelta(days=day)).replace(hour=hour, minute=minute).astimezone(UTC)


def _freeze_vn(day: int, hour: int = 22, minute: int = 30) -> None:
    clock.set_clock(frozen_ms=int(_vn(day, hour, minute).timestamp() * 1000), mode="frozen")


def _log(session, *, hh, user, device, action, when, params=None, source=SOURCE_USER):
    from src.domain.models import ActionLog

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


def _cleanup():
    clock.reset()


def test_learns_local_hour_and_due_matches_wall_clock(seeded) -> None:
    """22:30 VN trên nhiều ngày → thói quen giờ 22 (nhãn '22h'), tới giờ khi đồng hồ 22h."""
    hh, user, device = _ids(seeded)
    try:
        for day in (1, 3, 5, 7):
            _log(seeded, hh=hh, user=user, device=device, action="set_temperature", when=_vn(day), params={"temperature": 26})

        _freeze_vn(9)  # "bây giờ" = 22:30 VN ngày 9
        learned = habits.learn_habits(seeded, household_id=hh)

        assert len(learned) == 1
        habit = learned[0]
        assert habit.hour == 22, "gom theo giờ VN (22), không phải giờ UTC (15)"
        assert "22h" in habit.description_vi and "15h" not in habit.description_vi

        # Tới giờ đúng lúc đồng hồ chỉ 22h VN.
        assert habits.due_habits(seeded, household_id=hh)

        # 15h VN thì KHÔNG tới giờ (chứng minh không còn dính giờ UTC 15).
        _freeze_vn(9, hour=15)
        assert habits.due_habits(seeded, household_id=hh) == []
    finally:
        _cleanup()


def test_learns_most_common_params(seeded) -> None:
    """Học kèm nhiệt độ hay dùng nhất để đề xuất không gửi params rỗng (#2)."""
    hh, user, device = _ids(seeded)
    try:
        for day in (1, 3, 5):
            _log(seeded, hh=hh, user=user, device=device, action="set_temperature", when=_vn(day), params={"temperature": 26})
        _log(seeded, hh=hh, user=user, device=device, action="set_temperature", when=_vn(6), params={"temperature": 24})

        _freeze_vn(9)
        habit = habits.learn_habits(seeded, household_id=hh)[0]
        assert habit.params == {"temperature": 26}
    finally:
        _cleanup()


def test_ignores_automation_and_future_logs(seeded) -> None:
    """Không học đề xuất của chính hệ thống, và không học log 'tương lai' khi nhảy lùi (#12)."""
    hh, user, device = _ids(seeded)
    try:
        # Bằng chứng thật của người dùng ở ngày 1..5.
        for day in (1, 3, 5):
            _log(seeded, hh=hh, user=user, device=device, action="turn_on", when=_vn(day))
        # Log do automation sinh ra — không phải bằng chứng.
        _log(seeded, hh=hh, user=user, device=device, action="turn_on", when=_vn(6), source=SOURCE_AUTOMATION)
        # Log ở "tương lai" so với đồng hồ đóng băng ngày 4.
        _log(seeded, hh=hh, user=user, device=device, action="turn_on", when=_vn(8))

        _freeze_vn(4, hour=23)  # bây giờ = ngày 4, chỉ thấy ngày 1 và 3
        learned = habits.learn_habits(seeded, household_id=hh)
        assert learned == [], "mới 2 ngày (log ngày 5/8 ở tương lai, ngày 6 là automation)"

        # Nhích đồng hồ tới ngày 9 → thấy log người dùng ngày 1,3,5,8 = 4 ngày.
        # Nếu tính nhầm cả log automation ngày 6 thì sẽ là 5 → khoá đúng việc loại nó.
        _freeze_vn(9)
        habit = habits.learn_habits(seeded, household_id=hh)
        assert len(habit) == 1 and habit[0].occurrences == 4
    finally:
        _cleanup()


def test_dead_habit_decays_to_zero(seeded) -> None:
    """Bằng chứng trôi hết khỏi cửa sổ → confidence về 0, ngừng đề xuất (#3)."""
    hh, user, device = _ids(seeded)
    try:
        for day in (1, 3, 5):
            _log(seeded, hh=hh, user=user, device=device, action="turn_on", when=_vn(day))
        _freeze_vn(9)
        assert habits.learn_habits(seeded, household_id=hh), "học được lúc còn bằng chứng"

        # Nhảy tới hơn 30 ngày sau: bằng chứng cũ trôi khỏi cửa sổ, không log mới.
        _freeze_vn(9 + habits.LOOKBACK_DAYS + 5)
        habits.learn_habits(seeded, household_id=hh)

        habit = seeded.scalar(select(Habit).where(Habit.household_id == hh))
        assert habit.confidence == 0.0 and habit.occurrences == 0
        # Và ở đúng giờ đó cũng không còn tới lượt.
        assert habits.due_habits(seeded, household_id=hh) == []
    finally:
        _cleanup()


def test_backward_clock_jump_does_not_mute_habit(seeded) -> None:
    """Đánh dấu đã chạy rồi nhảy đồng hồ LÙI: mốc cũ ở 'tương lai' không được khoá (#13)."""
    hh, user, device = _ids(seeded)
    try:
        for day in (1, 3, 5):
            _log(seeded, hh=hh, user=user, device=device, action="turn_on", when=_vn(day))

        _freeze_vn(9)
        habits.learn_habits(seeded, household_id=hh)
        habit = seeded.scalar(select(Habit).where(Habit.household_id == hh))

        habits.mark_triggered(seeded, habit)  # đóng dấu đã chạy lúc ngày 9
        seeded.flush()
        assert habits.due_habits(seeded, household_id=hh) == [], "đã chạy hôm nay thì thôi"

        # Nhảy LÙI về ngày 8 22h: mốc last_triggered (ngày 9) giờ ở tương lai → bỏ qua.
        _freeze_vn(8)
        assert habits.due_habits(seeded, household_id=hh), "nhảy lùi không được làm thói quen câm"
    finally:
        _cleanup()
