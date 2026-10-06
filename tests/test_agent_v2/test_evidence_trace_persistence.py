"""Regression coverage for context-resolution provenance across state and audit boundaries."""

from __future__ import annotations

from datetime import UTC, datetime

from src.agent.cognitive.ledger import LedgerStore
from src.agent.cognitive.ledger_updater import update_ledger
from src.agent.pipeline import PipelineDeps, run_turn
from src.agent.schemas import SemanticGoal, SufficiencyDecision
from src.core.reasoning import FakeReasoningModel
from src.nlu.ontology import UtteranceType
from src.services.agent_runner import _audit_envelope
from src.services.pipeline_bridge import reason


def _state(conversation_id: str) -> dict:
    return {
        "conversation_id": conversation_id,
        "user_id": "alice",
        "user_message": "trong phòng chói mắt quá",
        "speaker_role": "owner",
        "speaker_location": "Phòng ngủ con",
        "now": datetime(2026, 8, 30, 20, 0, tzinfo=UTC),
    }


def test_pipeline_persists_exact_resolution_trace_to_ledger_and_audit():
    deps = PipelineDeps(model_client=FakeReasoningModel())

    out = run_turn(_state("trace-pipeline"), deps)

    assert out["sufficiency_decision"] is SufficiencyDecision.RESOLVE_CONTEXT
    assert out["evidence_trace"][0]["source"] == "runtime_context"
    ledger = deps.ledger_store.load("trace-pipeline")
    assert ledger.last_resolution_evidence_trace == out["evidence_trace"]
    room_evidence = [
        item
        for item in ledger.evidence
        if item.turn == ledger.last_updated_turn and item.field in {"room", "area"}
    ]
    assert [item.source for item in room_evidence] == ["runtime_context"]
    assert out["audit"].evidence_trace == out["evidence_trace"]
    assert out["audit"].ledger_snapshot["last_resolution_evidence_trace"] == out["evidence_trace"]


def test_update_ledger_preserves_supporting_trace_without_generic_source():
    goal = SemanticGoal(
        intent="tăng sáng",
        raw_utterance="tối quá",
        utterance_type=UtteranceType.ENVIRONMENT_REQUEST,
        target_area="Phòng khách",
        confidence=0.9,
    )
    trace = [
        {
            "resolved_field": "room",
            "value": "Phòng khách",
            "source": "episodic_memory",
            "confidence": 0.72,
            "evidence": [{"event_id": "event-1"}],
        }
    ]

    ledger = update_ledger(
        LedgerStore().load("trace-ledger"),
        goal,
        turn=1,
        conversation_id="trace-ledger",
        evidence_trace=trace,
    )

    assert ledger.last_resolution_evidence_trace == trace
    assert any(
        item.field == "room"
        and item.source == "episodic_memory"
        and item.confidence == 0.72
        for item in ledger.evidence
    )
    assert not any(item.field == "room" and item.source == "semantic_goal" for item in ledger.evidence)


def test_pipeline_persists_real_episodic_event_provenance():
    deps = PipelineDeps(model_client=FakeReasoningModel())
    conversation_id = "trace-episodic"

    run_turn(_state(conversation_id) | {"speaker_location": "Phòng khách"}, deps)
    deps.ledger_store.reset(conversation_id)
    out = run_turn(_state(conversation_id) | {"speaker_location": None}, deps)

    assert out["sufficiency_decision"] is SufficiencyDecision.RESOLVE_CONTEXT
    assert out["semantic_goal"].target_area == "Phòng khách"
    assert out["evidence_trace"][0]["source"] == "episodic_memory"
    assert out["evidence_trace"][0]["evidence"][0]["event_id"] == "trace-episodic-e1"
    ledger = deps.ledger_store.load(conversation_id)
    assert ledger.last_resolution_evidence_trace == out["evidence_trace"]


def test_pipeline_bridge_returns_only_the_current_turn_resolution_trace():
    deps = PipelineDeps(model_client=FakeReasoningModel())
    result = reason(
        message="trong phòng chói mắt quá",
        conversation_id="trace-bridge",
        role="owner",
        user_id="alice",
        speaker_location="Phòng ngủ con",
        now=datetime(2026, 8, 30, 20, 0, tzinfo=UTC),
        deps=deps,
    )

    assert result.evidence_trace[0]["source"] == "runtime_context"

    social = reason(
        message="cảm ơn",
        conversation_id="trace-bridge",
        role="owner",
        user_id="alice",
        now=datetime(2026, 8, 30, 20, 1, tzinfo=UTC),
        deps=deps,
    )
    assert social.evidence_trace == []


def test_production_audit_envelope_carries_resolution_trace():
    trace = [{"resolved_field": "room", "value": "Phòng khách", "source": "recent_dialogue"}]

    envelope = _audit_envelope(
        None,
        "candidate_plan",
        [],
        [],
        {"decision": "PROCEED"},
        evidence_trace=trace,
    )

    assert envelope["evidence_trace"] == trace
