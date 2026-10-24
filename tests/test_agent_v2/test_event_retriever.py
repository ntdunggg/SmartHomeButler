"""Event Retriever (spec §26) — score_events/retrieve_events.

Test trực tiếp công thức chấm điểm (semantic + location + actor + recency), ngưỡng
min_score, cắt top_k, và HARD FILTER quyền riêng tư (spec §P5, QC-06). CodeGraph gắn cờ
"no covering tests found" cho file này dù `retrieve_events` được gọi từ
`hierarchical_retriever.retrieve_memory` — component đang chạy trong pipeline production
(10 caller trong `src/agent/pipeline.py`) — nên trước đây chỉ được bao phủ GIÁN TIẾP qua
`test_memory.py` (1 event/case, không test composition điểm số hay hard filter).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.agent.memory.event_extractor import embed_query
from src.agent.memory.event_retriever import retrieve_events, score_events
from src.agent.schemas import MemoryEvent


def _event(
    event_id: str,
    *,
    text: str = "",
    location: list[str] | None = None,
    actors: list[str] | None = None,
    start_time: datetime | None = None,
) -> MemoryEvent:
    return MemoryEvent(
        event_id=event_id,
        actors=actors or [],
        location=location or [],
        start_time=start_time,
        summary=text,
        embedding=list(embed_query(text).vector) if text else [],
        embedding_space=embed_query(text).space if text else "",
    )


# ---------------------------------------------------------------------------
# score_events — công thức chấm điểm
# ---------------------------------------------------------------------------
def test_score_events_ranks_by_semantic_similarity():
    more_relevant = _event("e1", text="bật đèn phòng khách buổi tối")
    less_relevant = _event("e2", text="bật đèn")
    # Không có tín hiệu chung nào (không token trùng với query) -> score=0 -> bị loại hẳn,
    # nên dùng 2 event CÙNG liên quan nhưng khác mức để so sánh thứ hạng, thay vì 1 liên
    # quan + 1 hoàn toàn không liên quan (trường hợp đó xem test_zero_evidence_event_excluded_entirely).
    scored = score_events([more_relevant, less_relevant], query_text="bật đèn phòng khách buổi tối")
    assert [s.event.event_id for s in scored] == ["e1", "e2"]
    assert scored[0].score > scored[1].score
    assert any(r.startswith("semantic_similarity=") for r in scored[0].reasons)


def test_score_events_excludes_completely_unrelated_event():
    relevant = _event("e1", text="bật đèn phòng khách buổi tối")
    unrelated = _event("e2", text="mua sắm cuối tuần ở siêu thị")
    scored = score_events([relevant, unrelated], query_text="bật đèn phòng khách")
    assert [s.event.event_id for s in scored] == ["e1"]


def test_contextual_boosts_cannot_create_topical_relevance():
    """Same room/user/recency must not revive an event from another topic."""
    now = datetime(2026, 8, 29, 12, 0, 0)
    stale = _event(
        "stale",
        text="bật nhạc bằng loa phòng khách",
        location=["Phòng khách"],
        actors=["user_A"],
        start_time=now,
    )
    assert retrieve_events(
        [stale],
        query_text="bật điều hòa phòng khách",
        room="Phòng khách",
        actor="user_A",
        now=now,
    ) == []


def test_room_match_adds_fixed_boost():
    same_text = "đón khách"
    ev_room = _event("e1", text=same_text, location=["Phòng khách"])
    ev_other_room = _event("e2", text=same_text, location=["Phòng bếp"])
    scored = score_events([ev_room, ev_other_room], query_text=same_text, room="Phòng khách")
    by_id = {s.event.event_id: s for s in scored}
    assert by_id["e1"].score - by_id["e2"].score == pytest.approx(0.3, abs=1e-6)
    assert any("location=Phòng khách" in r for r in by_id["e1"].reasons)
    assert not any(r.startswith("location=") for r in by_id["e2"].reasons)


def test_actor_match_adds_fixed_boost():
    ev = _event("e1", text="đặt lịch họp", actors=["user_A"])
    with_actor = score_events([ev], query_text="đặt lịch họp", actor="user_A")
    without_actor = score_events([ev], query_text="đặt lịch họp", actor=None)
    assert with_actor[0].score - without_actor[0].score == pytest.approx(0.1, abs=1e-6)
    assert any("actor=user_A" in r for r in with_actor[0].reasons)


def test_recency_boost_favors_recent_event_over_old_one():
    now = datetime(2026, 8, 18, 12, 0, 0)
    recent = _event("e1", text="tưới cây", start_time=now - timedelta(hours=1))
    old = _event("e2", text="tưới cây", start_time=now - timedelta(days=30))
    scored = score_events([recent, old], query_text="tưới cây", now=now)
    by_id = {s.event.event_id: s for s in scored}
    assert by_id["e1"].score > by_id["e2"].score
    assert any(r.startswith("recency=") for r in by_id["e1"].reasons)


def test_recency_not_applied_without_now_or_start_time():
    ev = _event("e1", text="tưới cây", start_time=None)
    scored = score_events([ev], query_text="tưới cây", now=datetime(2026, 8, 18))
    assert not any(r.startswith("recency=") for r in scored[0].reasons)


def test_zero_evidence_event_excluded_entirely():
    """Không semantic, không room, không actor, không recency -> score=0 -> KHÔNG trả về
    (tránh nhiễu retrieval bằng event không có bằng chứng liên quan nào)."""
    ev = _event("e1", text="")
    assert score_events([ev], query_text="bật đèn") == []


# ---------------------------------------------------------------------------
# HARD FILTER quyền riêng tư (spec §P5, QC-06)
# ---------------------------------------------------------------------------
def test_privacy_hard_filter_excludes_other_actors_event_even_with_perfect_match():
    """Event có actor KHÁC actor đang hỏi -> loại HẲN, kể cả khi nội dung/phòng khớp 100%
    — đây là hard filter, không phải trừ điểm (spec §P5: không lộ ký ức người khác)."""
    ev_other = _event("e1", text="mật khẩu wifi nhà", actors=["user_B"], location=["Phòng ngủ bố mẹ"])
    scored = score_events([ev_other], query_text="mật khẩu wifi nhà", room="Phòng ngủ bố mẹ", actor="user_A")
    assert scored == []


def test_shared_event_without_actors_visible_to_everyone():
    ev_shared = _event("e1", text="lịch dọn nhà chung", actors=[])
    scored = score_events([ev_shared], query_text="lịch dọn nhà chung", actor="user_A")
    assert len(scored) == 1


def test_own_event_still_visible_when_actor_matches():
    ev_mine = _event("e1", text="ghi chú riêng", actors=["user_A"])
    scored = score_events([ev_mine], query_text="ghi chú riêng", actor="user_A")
    assert len(scored) == 1


def test_privacy_filter_keeps_only_matching_actor_among_many_events():
    events = [
        _event("mine", text="lịch cá nhân", actors=["user_A"]),
        _event("other", text="lịch cá nhân", actors=["user_B"]),
        _event("shared", text="lịch cá nhân", actors=[]),
    ]
    scored = score_events(events, query_text="lịch cá nhân", actor="user_A")
    ids = {s.event.event_id for s in scored}
    assert ids == {"mine", "shared"}


# ---------------------------------------------------------------------------
# retrieve_events — ngưỡng min_score + cắt top_k
# ---------------------------------------------------------------------------
def test_retrieve_events_filters_below_min_score():
    relevant = _event("e1", text="bật đèn phòng khách", location=["Phòng khách"])
    irrelevant = _event("e2", text="mua rau ngoài chợ")
    result = retrieve_events(
        [relevant, irrelevant], query_text="bật đèn phòng khách", room="Phòng khách", min_score=0.15
    )
    ids = [s.event.event_id for s in result]
    assert "e1" in ids
    assert "e2" not in ids


def test_retrieve_events_truncates_to_top_k():
    events = [_event(f"e{i}", text="bật đèn phòng khách", location=["Phòng khách"]) for i in range(5)]
    result = retrieve_events(events, query_text="bật đèn phòng khách", room="Phòng khách", top_k=2, min_score=0.0)
    assert len(result) == 2


def test_retrieve_events_empty_input_returns_empty():
    assert retrieve_events([], query_text="bất kỳ câu gì") == []
