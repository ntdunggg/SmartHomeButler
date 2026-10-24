"""Event-based memory từ feedback (spec §21, §24, §25, §34) — hoàn thiện §10/§11.

Kiểm tra phần trước đây còn thiếu: tín hiệu feedback (accept/reject/correction) được
BIỂU DIỄN và LƯU thành MemoryEvent CÓ CẤU TRÚC (không tan vào raw turn), và giá trị bị
BÁC/ĐÃ SỬA không bị học nhầm thành sở thích (preferred).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.agent.cognitive.ledger import LedgerStore
from src.agent.memory.event_extractor import extract_event, extract_feedback_event
from src.agent.memory.event_store import EventStore
from src.agent.memory.profile_store import ProfileStore
from src.agent.memory.turn_store import TurnStore
from src.agent.pipeline import PipelineDeps, run_planning, run_turn
from src.agent.preference.preference_store import PreferenceStore
from src.agent.schemas import ExecutedAction, ExecutionResult
from src.core.reasoning import FakeReasoningModel
from src.services.pipeline_bridge import reason


def _exec(brightness: int = 60) -> ExecutionResult:
    return ExecutionResult(
        plan_id="p",
        status="SUCCESS",
        actions=[ExecutedAction(device_id="den_chum_phong_khach", requested={"brightness": brightness}, status="SUCCESS")],
    )


# --- Unit: extract_feedback_event ------------------------------------------------
def test_correction_event_records_corrected_value_not_executed():
    """Sửa 60→40: event là preference_correction với preferred_brightness = 40 (giá trị user muốn)."""
    ev = extract_feedback_event(
        event_id="fb1", conversation_id="c", actors=["user_A"], kind="slight_adjustment",
        execution=_exec(60), dimension="brightness", corrected_value=40,
    )
    assert ev is not None and ev.event_type == "preference_correction"
    facts = {(f.subject, f.relation, f.value) for f in ev.facts}
    assert ("den_chum_phong_khach", "preferred_brightness", 40.0) in facts
    # KHÔNG học 60 (giá trị vừa bị sửa) thành preferred.
    assert all(f.value != 60.0 for f in ev.facts if f.relation == "preferred_brightness")


def test_rejected_event_uses_rejected_relation_not_preferred():
    """Bác 60: event plan_rejected ghi rejected_brightness=60 — KHÔNG phải preferred (không học nhầm)."""
    ev = extract_feedback_event(
        event_id="fb2", conversation_id="c", actors=["user_A"], kind="explicit_reject",
        execution=_exec(60), dimension="brightness",
    )
    assert ev is not None and ev.event_type == "plan_rejected"
    relations = {f.relation for f in ev.facts}
    assert "rejected_brightness" in relations
    assert "preferred_brightness" not in relations


def test_accept_event_records_preferred():
    ev = extract_feedback_event(
        event_id="fb3", conversation_id="c", actors=["user_A"], kind="explicit_accept",
        execution=_exec(55), dimension="brightness",
    )
    assert ev is not None and ev.event_type == "plan_accepted"
    assert ("den_chum_phong_khach", "preferred_brightness", 55.0) in {(f.subject, f.relation, f.value) for f in ev.facts}


def test_no_correction_produces_no_feedback_event():
    assert extract_feedback_event(
        event_id="fb4", conversation_id="c", actors=["user_A"], kind="no_correction",
        execution=_exec(60), dimension="brightness",
    ) is None


def test_rejected_execution_does_not_learn_preferred_facts():
    """Activity event của lượt bị bác: learn_preferences=False → không sinh preferred fact."""
    ev = extract_event(
        event_id="e1", conversation_id="c", actors=["user_A"], execution=_exec(60),
        summary="bật đèn", learn_preferences=False,
    )
    assert ev is not None  # vẫn giữ event hoạt động (summary/location)
    assert all(f.relation != "preferred_brightness" for f in ev.facts)


def test_rejected_values_not_consolidated_into_preferred_profile():
    """3 lần bác 60 → KHÔNG có stable fact preferred_brightness (rejected_* không được đọc làm prior)."""
    ps = ProfileStore()
    evs = [
        extract_feedback_event(
            event_id=f"r{i}", conversation_id="c", actors=["user_A"], kind="explicit_reject",
            execution=_exec(60), dimension="brightness",
        )
        for i in range(3)
    ]
    ps.consolidate_from_events(evs)
    assert ps.get("den_chum_phong_khach", "preferred_brightness") is None


# --- Pipeline integration --------------------------------------------------------
@pytest.fixture
def deps() -> PipelineDeps:
    return PipelineDeps(
        ledger_store=LedgerStore(), event_store=EventStore(), turn_store=TurnStore(),
        preference_store=PreferenceStore(), profile_store=ProfileStore(), model_client=FakeReasoningModel(),
    )


def _base(**kw) -> dict:
    b = {
        "conversation_id": "c", "user_id": "user_A", "speaker_role": "owner",
        "now": datetime(2026, 8, 14, 20, 0, tzinfo=UTC), "speaker_location": "Phòng khách",
    }
    b.update(kw)
    return b


def test_pipeline_correction_persists_feedback_event(deps):
    out = run_turn(
        _base(conversation_id="corr", user_message="bật đèn phòng khách",
              feedback={"corrected_value": 40, "dimension": "brightness", "chosen_action": 60}),
        deps,
    )
    assert out["memory_written"]["feedback_event_id"]
    types = {e.event_type for e in deps.event_store.all_events()}
    assert "preference_correction" in types
    corr = next(e for e in deps.event_store.all_events() if e.event_type == "preference_correction")
    assert ("den_chum_phong_khach", "preferred_brightness", 40.0) in {(f.subject, f.relation, f.value) for f in corr.facts}


def test_pipeline_rejection_persists_rejected_event(deps):
    out = run_turn(
        _base(conversation_id="rej", user_message="bật đèn phòng khách",
              feedback={"rejected": True, "dimension": "brightness"}),
        deps,
    )
    assert out["memory_written"]["feedback_event_id"]
    assert "plan_rejected" in {e.event_type for e in deps.event_store.all_events()}


def test_pipeline_no_feedback_writes_no_feedback_event(deps):
    """Không feedback → chỉ activity event, feedback_event_id None (không bịa event feedback)."""
    out = run_turn(_base(conversation_id="plain", user_message="bật đèn phòng khách"), deps)
    assert out["memory_written"]["event_id"]
    assert out["memory_written"]["feedback_event_id"] is None
    assert {e.event_type for e in deps.event_store.all_events()} == {"household_activity"}


def test_pipeline_repeated_correction_consolidates_corrected_value(deps):
    """3 lượt sửa →40 promote stable fact preferred_brightness=40 (không phải 60 bị sửa)."""
    for i in range(3):
        run_turn(
            _base(conversation_id=f"c{i}", user_message="bật đèn phòng khách",
                  feedback={"corrected_value": 40, "dimension": "brightness", "chosen_action": 60}),
            deps,
        )
    pf = deps.profile_store.get("den_chum_phong_khach", "preferred_brightness")
    assert pf is not None and pf.value["value"] == 40.0


def test_feedback_event_retrievable_next_turn(deps):
    """Event feedback trở thành memory_evidence cho lượt sau (read path §26)."""
    run_turn(
        _base(conversation_id="rt", user_message="bật đèn phòng khách",
              feedback={"corrected_value": 40, "dimension": "brightness", "chosen_action": 60}),
        deps,
    )
    out2 = run_turn(_base(conversation_id="rt", user_message="chỉnh đèn phòng khách"), deps)
    ids = {e.event_id for e in (out2.get("memory_evidence") or [])}
    assert any("fb" in i for i in ids)


def test_conversational_preference_uses_real_write_and_retrieve_path(deps):
    stored = reason(
        message="Tôi thường để đèn phòng khách 40% vào buổi tối.",
        conversation_id="spoken-pref",
        user_id="user_A",
        now=datetime(2026, 8, 14, 20, 0, tzinfo=UTC),
        deps=deps,
        model_client=deps.model_client,
    )
    assert stored.outcome == "answer"
    assert stored.memory_written["feedback_event_id"]
    event = deps.event_store.get(stored.memory_written["feedback_event_id"])
    assert event is not None and event.event_type == "preference_stated"

    out = run_planning(
        _base(
            conversation_id="spoken-pref",
            user_message="chỉnh đèn phòng khách cho hợp buổi tối",
        ),
        deps,
    )
    assert event.event_id in {item.event_id for item in out.get("memory_evidence") or []}


def test_conversational_reject_and_correction_are_persisted(deps):
    reason(
        message="đặt đèn phòng khách 60%",
        conversation_id="spoken-correction",
        user_id="user_A",
        now=datetime(2026, 8, 14, 20, 0, tzinfo=UTC),
        deps=deps,
        model_client=deps.model_client,
    )
    corrected = reason(
        message="Thực ra 35% hợp hơn.",
        conversation_id="spoken-correction",
        user_id="user_A",
        now=datetime(2026, 8, 14, 20, 1, tzinfo=UTC),
        deps=deps,
        model_client=deps.model_client,
    )
    event = deps.event_store.get(corrected.memory_written["feedback_event_id"])
    assert event is not None and event.event_type == "preference_correction"
    assert any(f.relation == "preferred_brightness" and f.value == 35 for f in event.facts)
