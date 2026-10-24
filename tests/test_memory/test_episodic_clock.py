"""Episodic memory bám ĐỒNG HỒ MÔ PHỎNG + giờ VN (option 2).

Khoá các bất biến của bản migrate:
- Ghi episode theo `clock.now()` (đồng hồ demo), KHÔNG giờ thực (Finding 3).
- `ep.hour` theo giờ VN → khớp `Habit.hour` và `now_hour` ở retrieval, và ra đúng
  buổi cho `part_of_day` (Finding 1).
- `consolidate`/`load_candidates` có cận trên `created_at <= now` → nhảy đồng hồ LÙI
  không kéo ký ức "tương lai" vào (Finding 2).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from src.core import clock
from src.memory import store
from src.memory.types import KIND_EPISODE

_BASE_VN = datetime(2026, 8, 15, tzinfo=clock.LOCAL_TZ)


def _freeze_vn(days_from_base: int, hour: int = 22, minute: int = 30) -> None:
    """Đóng băng đồng hồ backend ở ``hour:minute`` giờ VN, cách mốc gốc ``days_from_base`` ngày."""
    vn = (_BASE_VN + timedelta(days=days_from_base)).replace(hour=hour, minute=minute)
    clock.set_clock(frozen_ms=int(vn.timestamp() * 1000), mode="frozen")


def _record_accepted(session, *, label: str = "cool_living_room"):
    # KHÔNG truyền now -> buộc record_episode tự lấy clock.now() (đúng thứ đang kiểm).
    return store.record_episode(
        session,
        household_id=1,
        user_id=1,
        utterance="nóng quá",
        goal_description="làm mát phòng khách",
        situation_label=label,
        room="Phòng khách",
        outcome="accepted",
        signals={"devices": ["dieu_hoa_phong_khach"]},
    )


def test_record_episode_follows_frozen_clock_and_local_hour(seeded) -> None:
    """Ghi theo đồng hồ mô phỏng (không giờ thực) và ep.hour theo giờ VN."""
    _freeze_vn(0, hour=22, minute=30)  # 22:30 VN (= 15:30 UTC)
    try:
        ep = _record_accepted(seeded)
        # Dấu thời gian bám đồng hồ mô phỏng.
        assert clock.to_local(ep.created_at).hour == 22
        # ep.hour theo giờ VN (22), KHÔNG phải giờ UTC (15).
        assert ep.hour == 22
        # Đúng công thức now_hour dùng ở retrieval (graph.py) -> write/read cùng quy ước,
        # hết lệch 7 tiếng giữa ep.hour/Habit.hour và now_hour (Finding 1).
        assert clock.to_local(clock.now()).hour == ep.hour
    finally:
        clock.reset()


def test_consolidate_excludes_future_after_backward_jump(seeded) -> None:
    """Cận trên: nhảy đồng hồ LÙI thì consolidate không gom bằng chứng 'tương lai' (Finding 2)."""
    try:
        for d in (10, 12, 14):  # 3 ngày VN riêng biệt
            _freeze_vn(d)
            _record_accepted(seeded)

        # Đồng hồ ở ngày 4 — TRƯỚC mọi episode: tất cả là "tương lai" -> loại hết.
        _freeze_vn(4)
        assert store.consolidate(seeded, household_id=1) == []

        # Đối chứng: đồng hồ ở ngày 15 (sau episode) -> đủ 3 ngày -> nâng thành routine.
        _freeze_vn(15)
        assert len(store.consolidate(seeded, household_id=1)) == 1
    finally:
        clock.reset()


def test_load_candidates_excludes_future_after_backward_jump(seeded) -> None:
    """Cận trên ở load_candidates: ký ức 'tương lai' không lọt vào recall (Finding 2)."""
    try:
        _freeze_vn(10)
        _record_accepted(seeded)  # episode ở "tương lai" so với mốc sẽ nhảy về

        _freeze_vn(4)
        items = store.load_candidates(seeded, household_id=1)
        assert [i for i in items if i.kind == KIND_EPISODE] == []

        # Đối chứng: khi đồng hồ ở ngày 11 (sau episode) thì nó là bằng chứng hợp lệ.
        _freeze_vn(11)
        items = store.load_candidates(seeded, household_id=1)
        assert [i for i in items if i.kind == KIND_EPISODE]
    finally:
        clock.reset()
