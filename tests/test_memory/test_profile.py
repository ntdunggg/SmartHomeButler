"""Hồ sơ + lịch trình theo thành viên: chỉ TỔNG HỢP những gì đã học, không bịa thêm.

Hồ sơ là read-model — nếu chưa học được gì thì trả rỗng, không đoán ra thói quen. Lịch trình
suy tất định từ giờ sang khung buổi."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from src.core import clock
from src.domain.models import Preference
from src.memory import store
from src.memory.profile import (
    PART_EVENING,
    PART_NIGHT,
    build_member_profile,
    build_schedule,
    part_of_day,
)

# Mốc gốc theo giờ VN; episode quy về UTC để round-trip ổn định và ep.hour ra đúng giờ VN.
_BASE_VN = datetime(2026, 8, 9, tzinfo=clock.LOCAL_TZ)


def _vn(days_ago: int, hour: int, minute: int = 0) -> datetime:
    return (_BASE_VN - timedelta(days=days_ago)).replace(hour=hour, minute=minute).astimezone(UTC)


_NOW = _vn(0, 23)  # sau mọi episode để cận trên không loại nhầm

_GOAL = {
    "goal_description": "làm mát phòng khách",
    "utterance_type": "ENVIRONMENT_REQUEST",
    "desired_outcomes": [{"selector": {"domain": "air_conditioner"}, "relative_change": {"temperature": "decrease"}}],
}


def _add_goal(session, *, days_ago: int, hour: int = 22) -> None:
    store.record_episode(
        session,
        household_id=1,
        user_id=1,
        utterance="nóng quá",
        goal_description="làm mát phòng khách",
        situation_label="cool_living_room",
        room="Phòng khách",
        now=_vn(days_ago, hour),
        outcome="accepted",
        signals={"devices": ["dieu_hoa_phong_khach"], "goal": _GOAL},
    )


def test_part_of_day_boundaries() -> None:
    assert part_of_day(8) == "morning"
    assert part_of_day(14) == "afternoon"
    assert part_of_day(19) == PART_EVENING
    assert part_of_day(23) == PART_NIGHT
    assert part_of_day(2) == PART_NIGHT  # vòng qua nửa đêm


def test_no_routines_or_schedule_before_anything_is_learned(seeded) -> None:
    """Chưa quy tụ được routine nào → hồ sơ không bịa ra thói quen/lịch trình.

    (Seed có sẵn vài Preference tĩnh; phần agent HỌC — routine + lịch — phải rỗng.)"""
    profile = build_member_profile(seeded, household_id=1, user_id=1)
    assert profile.routines == []
    assert profile.schedule == []


def test_profile_aggregates_preference_routine_and_schedule(seeded) -> None:
    # Seed đã có sẵn Preference tĩnh cho user 1 — cập nhật giá trị thay vì insert trùng khoá.
    pref = seeded.scalar(
        select(Preference).where(Preference.user_id == 1, Preference.key == "air_conditioner.temperature")
    )
    if pref is None:
        seeded.add(Preference(household_id=1, user_id=1, key="air_conditioner.temperature", value=25.0))
    else:
        pref.value = 25.0
    for day in (0, 2, 5):
        _add_goal(seeded, days_ago=day, hour=22)
    store.consolidate(seeded, household_id=1, now=_NOW)

    profile = build_member_profile(seeded, household_id=1, user_id=1)
    assert profile.preferences["air_conditioner.temperature"] == 25.0
    assert len(profile.routines) == 1
    assert profile.routines[0]["has_reusable_goal"] is True

    # Routine quan sát lúc 22h → rơi vào khung 'đêm' của lịch trình.
    night = [s for s in profile.schedule if s.part == PART_NIGHT]
    assert night and 22 in night[0].hours
    assert night[0].routines, "khung đêm phải liệt kê routine đã học"


def test_build_schedule_only_returns_populated_parts() -> None:
    habits = [{"device_slug": "den_bep", "action": "turn_off", "hour": 19, "description_vi": "tắt đèn bếp"}]
    slots = build_schedule(habits, routines=[])
    assert {s.part for s in slots} == {PART_EVENING}
    assert slots[0].habits == ("tắt đèn bếp",)
