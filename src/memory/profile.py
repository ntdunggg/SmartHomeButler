"""Hồ sơ hành vi + lịch trình theo TỪNG THÀNH VIÊN — read-model thuần.

Đây KHÔNG phải một tầng bộ nhớ mới: nó chỉ TỔNG HỢP những gì đã có (`Preference`,
`ResidentProfile kind='routine'`, `Habit`) thành một cái nhìn gọn cho một thành viên, để
trả về API / hiển thị / bơm vào prompt như bằng chứng. Không ghi gì, không quyết định gì.

Lịch trình được suy TẤT ĐỊNH từ giờ (integer `hour`) → khung buổi. Chưa có trường thứ trong
tuần ở schema, nên lịch trình hiện chỉ theo buổi trong ngày — mở rộng theo tuần cần thêm cột
(ghi chú để lần sau làm, không đoán ngầm).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.domain.models import Habit, Preference, ResidentProfile

# Khung buổi trong ngày, suy tất định từ giờ. Ranh giới chọn theo sinh hoạt thường: sáng
# 5-11, chiều 11-17, tối 17-22, đêm 22-5 (vòng qua nửa đêm).
PART_MORNING = "morning"
PART_AFTERNOON = "afternoon"
PART_EVENING = "evening"
PART_NIGHT = "night"


def part_of_day(hour: int) -> str:
    """Giờ (0-23) → khung buổi. Tất định, vòng qua nửa đêm cho khung 'đêm'."""
    h = int(hour) % 24
    if 5 <= h < 11:
        return PART_MORNING
    if 11 <= h < 17:
        return PART_AFTERNOON
    if 17 <= h < 22:
        return PART_EVENING
    return PART_NIGHT


@dataclass(frozen=True)
class ScheduleSlot:
    """Một khung buổi trong lịch trình của thành viên, kèm việc quan sát được ở buổi đó."""

    part: str
    hours: tuple[int, ...] = ()
    habits: tuple[str, ...] = ()  # mô tả tiếng Việt của Habit
    routines: tuple[str, ...] = ()  # trait_key của routine gắn với buổi này


@dataclass(frozen=True)
class MemberProfile:
    """Cái nhìn gộp về một thành viên — sở thích, thói quen đã học, lịch trình theo buổi."""

    user_id: int
    preferences: dict[str, float] = field(default_factory=dict)
    routines: list[dict[str, Any]] = field(default_factory=list)
    habits: list[dict[str, Any]] = field(default_factory=list)
    schedule: list[ScheduleSlot] = field(default_factory=list)


def build_member_profile(session: Session, *, household_id: int, user_id: int) -> MemberProfile:
    """Gộp Preference + ResidentProfile(routine) + Habit của một thành viên thành hồ sơ.

    Chỉ đọc — không quy tụ, không ghi. Việc học/nâng thói quen vẫn ở `store.consolidate` và
    `habits.learn_habits`; đây chỉ trình bày lại kết quả."""
    prefs = session.scalars(
        select(Preference).where(Preference.household_id == household_id, Preference.user_id == user_id)
    ).all()
    preferences = {p.key: p.value for p in prefs}

    routine_rows = session.scalars(
        select(ResidentProfile).where(
            ResidentProfile.household_id == household_id,
            ResidentProfile.user_id == user_id,
            ResidentProfile.kind == "routine",
        )
    ).all()
    routines = [
        {
            "trait_key": r.trait_key,
            "statement_vi": r.statement_vi,
            "confidence": r.confidence,
            "room": (r.value or {}).get("room"),
            "has_reusable_goal": bool((r.value or {}).get("goal", {}).get("desired_outcomes")),
            "hours": {int(h): int(c) for h, c in ((r.value or {}).get("hours") or {}).items()},
        }
        for r in routine_rows
    ]

    # Thói quen đã tắt không hiện trong hồ sơ: người dùng bỏ nó rồi mà vẫn thấy trong
    # "Hồ sơ AI" thì tưởng thao tác bỏ không ăn. Xem lại chúng qua GET /habits?include_disabled=true.
    habit_rows = session.scalars(
        select(Habit).where(
            Habit.household_id == household_id,
            Habit.user_id == user_id,
            Habit.enabled.is_(True),
        )
    ).all()
    habits = [
        {
            "device_slug": h.device_slug,
            "action": h.action,
            "hour": h.hour,
            "confidence": h.confidence,
            "description_vi": h.description_vi,
        }
        for h in habit_rows
    ]

    schedule = build_schedule(habits, routines)
    return MemberProfile(
        user_id=user_id,
        preferences=preferences,
        routines=routines,
        habits=habits,
        schedule=schedule,
    )


def build_schedule(habits: list[dict[str, Any]], routines: list[dict[str, Any]]) -> list[ScheduleSlot]:
    """Nhóm thói quen + routine theo khung buổi (suy từ giờ). Trả về theo thứ tự buổi ổn định.

    Habit có một giờ rõ ràng → rơi đúng một buổi. Routine có histogram giờ → xuất hiện ở mọi
    buổi mà nó từng được quan sát. Buổi không có gì thì không trả (lịch thưa, không bịa)."""
    slots: dict[str, dict[str, Any]] = {}

    def _slot(part: str) -> dict[str, Any]:
        return slots.setdefault(part, {"hours": set(), "habits": [], "routines": []})

    for h in habits:
        hour = h.get("hour")
        if hour is None:
            continue
        slot = _slot(part_of_day(int(hour)))
        slot["hours"].add(int(hour))
        label = h.get("description_vi") or f"{h.get('device_slug')} · {h.get('action')}"
        slot["habits"].append(label)

    for r in routines:
        for hour in (r.get("hours") or {}):
            slot = _slot(part_of_day(int(hour)))
            slot["hours"].add(int(hour))
            if r["trait_key"] not in slot["routines"]:
                slot["routines"].append(r["trait_key"])

    order = [PART_MORNING, PART_AFTERNOON, PART_EVENING, PART_NIGHT]
    return [
        ScheduleSlot(
            part=part,
            hours=tuple(sorted(slots[part]["hours"])),
            habits=tuple(slots[part]["habits"]),
            routines=tuple(slots[part]["routines"]),
        )
        for part in order
        if part in slots
    ]
