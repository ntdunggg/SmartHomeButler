"""Quy tụ bằng chứng → hồ sơ dài hạn: ngưỡng phải THẬT, không nâng bừa.

Rủi ro của bất kỳ hệ thống "học thói quen" nào là bịa ra một luật từ một lần quan sát, rồi
áp nó mãi mãi. Các test ở đây khoá ngưỡng đó, và khoá cả chiều ngược lại: bị từ chối nhiều
thì hồ sơ phải tự tụt xuống dưới mức dùng được mà không cần ai xoá tay.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.core import clock
from src.memory import store
from src.memory.types import KIND_EPISODE

# Mốc gốc theo giờ VN; mọi episode quy về UTC để SQLite round-trip ổn định và để
# ep.hour (nay lưu theo giờ VN) ra đúng giờ mong muốn.
_BASE_VN = datetime(2026, 8, 9, tzinfo=clock.LOCAL_TZ)


def _vn(days_ago: int, hour: int, minute: int = 0) -> datetime:
    """Mốc UTC ứng với ``hour:minute`` giờ VN, lùi ``days_ago`` ngày so với mốc gốc."""
    return (_BASE_VN - timedelta(days=days_ago)).replace(hour=hour, minute=minute).astimezone(UTC)


# "Bây giờ" của consolidate = 23:00 VN hôm nay — sau mọi episode để cận trên không loại nhầm.
_NOW = _vn(0, 23)


def _add(session, *, days_ago: int, outcome: str, label: str = "có khách", room: str = "living_room") -> None:
    store.record_episode(
        session,
        household_id=1,
        user_id=1,
        utterance="tối nay có khách",
        goal_description="chuẩn bị phòng khách",
        situation_label=label,
        room=room,
        now=_vn(days_ago, 20),
        outcome=outcome,
        signals={"devices": ["den_phong_khach"]},
    )


def test_single_occurrence_never_becomes_a_habit(seeded) -> None:
    """Một lần KHÔNG phải thói quen — đây là ranh giới chống bịa luật từ một mẫu."""
    _add(seeded, days_ago=0, outcome="accepted")
    assert store.consolidate(seeded, household_id=1, now=_NOW) == []


def test_repeats_on_the_same_day_do_not_count_as_repeats(seeded) -> None:
    """Đếm theo NGÀY riêng biệt: nghịch mười lần một tối không thành thói quen hằng ngày."""
    for _ in range(6):
        _add(seeded, days_ago=0, outcome="accepted")
    assert store.consolidate(seeded, household_id=1, now=_NOW) == []


def test_three_distinct_days_promote_to_profile(seeded) -> None:
    for day in (0, 2, 5):
        _add(seeded, days_ago=day, outcome="accepted")
    promoted = store.consolidate(seeded, household_id=1, now=_NOW)
    assert len(promoted) == 1
    profile = promoted[0]
    assert profile.evidence_count == 3
    assert profile.confidence == 1.0
    assert profile.evidence_ids, "phải truy vết được vì sao agent tin điều này"


def test_rejections_pull_confidence_below_usable_threshold(seeded) -> None:
    """Bị chối nhiều hơn được nhận → hồ sơ vẫn tồn tại nhưng tụt dưới ngưỡng dùng được."""
    for day in (0, 2, 5):
        _add(seeded, days_ago=day, outcome="accepted")
    for day in (1, 3, 4, 6, 7):
        _add(seeded, days_ago=day, outcome="rejected")
    profile = store.consolidate(seeded, household_id=1, now=_NOW)[0]
    assert profile.confidence < store.MIN_TRAIT_CONFIDENCE
    assert profile.contradiction_count == 5


def test_unanswered_turns_are_not_evidence(seeded) -> None:
    """Lượt chưa có phản hồi ('proposed') không nói lên điều gì về sở thích ai cả."""
    for day in (0, 2, 5):
        _add(seeded, days_ago=day, outcome="proposed")
    assert store.consolidate(seeded, household_id=1, now=_NOW) == []
    assert store.load_candidates(seeded, household_id=1, now=_NOW) == []


def test_rejected_episode_loads_as_negative_evidence(seeded) -> None:
    _add(seeded, days_ago=0, outcome="rejected")
    items = [i for i in store.load_candidates(seeded, household_id=1, now=_NOW) if i.kind == KIND_EPISODE]
    assert len(items) == 1
    assert items[0].polarity == "negative"


def test_consolidation_is_scoped_to_the_household(seeded) -> None:
    """Bằng chứng của hộ khác không bao giờ được dùng cho hộ này."""
    for day in (0, 2, 5):
        _add(seeded, days_ago=day, outcome="accepted")
    assert store.consolidate(seeded, household_id=999, now=_NOW) == []
    assert store.load_candidates(seeded, household_id=999, now=_NOW) == []


# ---------------------------------------------------------------------------
# Routine TÁI DÙNG: consolidate giữ lại CÁCH HIỂU + histogram giờ
# ---------------------------------------------------------------------------
_GOAL_PAYLOAD = {
    "goal_description": "làm mát phòng khách",
    "utterance_type": "ENVIRONMENT_REQUEST",
    "desired_outcomes": [
        {
            "selector": {"domain": "air_conditioner", "area": "Phòng khách"},
            "perceived_state": "hot",
            "relative_change": {"temperature": "decrease"},
            "cardinality": "all",
        }
    ],
}


def _add_goal(session, *, days_ago: int, hour: int = 22) -> None:
    """Ghi một episode ĐƯỢC CHẤP NHẬN có kèm goal tái dùng trong signals."""
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
        signals={"devices": ["dieu_hoa_phong_khach"], "goal": _GOAL_PAYLOAD},
    )


def test_consolidated_routine_carries_reusable_goal_and_hours(seeded) -> None:
    """Sau đủ ngày, routine phải mang CÁCH HIỂU (desired_outcomes) + histogram giờ để tái dùng."""
    for day in (0, 2, 5):
        _add_goal(seeded, days_ago=day, hour=22)
    promoted = store.consolidate(seeded, household_id=1, now=_NOW)
    assert len(promoted) == 1
    value = promoted[0].value
    assert value["goal"]["desired_outcomes"], "routine phải giữ lại desired_outcomes để tái dùng"
    assert value["hours"] == {"22": 3}, "histogram giờ đếm đúng số lần quan sát mỗi giờ"


def test_load_routines_returns_only_reusable_high_confidence(seeded) -> None:
    """`load_routines` chỉ trả routine ĐỦ TIN và CÓ goal tái dùng — không trả bằng chứng thô."""
    for day in (0, 2, 5):
        _add_goal(seeded, days_ago=day, hour=22)
    store.consolidate(seeded, household_id=1, now=_NOW)
    routines = store.load_routines(seeded, household_id=1, user_id=1, now=_NOW)
    assert len(routines) == 1
    r = routines[0]
    assert r.goal["desired_outcomes"]
    assert r.confidence >= store.MIN_TRAIT_CONFIDENCE
    assert 22 in r.hours
    # Người khác không thấy routine cá nhân này.
    assert store.load_routines(seeded, household_id=1, user_id=2, now=_NOW) == []
