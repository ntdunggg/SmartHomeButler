"""Hạ tầng RAGAS eval (eval/eval_ragas.py) — dựng samples đúng, KHÔNG gọi mạng.

Chỉ kiểm phần offline: chạy pipeline bằng FakeReasoningModel → bản ghi RAGAS đủ trường
(user_input/response/reference/retrieved_contexts) và cờ decision tất định đúng. Phần chấm
LLM-judge (build_judge/score_*) cần API thật nên KHÔNG test ở đây.
"""

from __future__ import annotations

from eval.eval_ragas import (
    _HOME_CONTEXTS,
    build_records,
    render_reference,
    render_response,
    run_case,
)
from src.core.reasoning import FakeReasoningModel

_CLARIFY_CASE = {
    "id": "T-1",
    "category": "ambiguity_detection",
    "messages": [{"role": "user", "content": "Bật lên đi."}],
    "expected": {"decision": "CLARIFY", "canonical_goal": "Resolve underspecified request", "missing_fields": ["target"]},
}
_PROCEED_CASE = {
    "id": "T-2",
    "category": "context_resolution",
    "messages": [{"role": "user", "content": "Bật đèn phòng khách."}],
    "expected": {"decision": "PROCEED", "canonical_goal": "Turn on living-room light", "room": "phòng khách", "target": "đèn"},
}


def test_home_contexts_seeded_from_registry() -> None:
    # retrieved_contexts phải là kho thiết bị thật (mỗi phòng một chuỗi), không rỗng.
    assert len(_HOME_CONTEXTS) >= 4
    assert any("Phòng khách" in c and "thiết bị" in c for c in _HOME_CONTEXTS)


def test_build_records_has_ragas_fields() -> None:
    records = build_records([_CLARIFY_CASE, _PROCEED_CASE], FakeReasoningModel(), map_rooms=True)
    assert len(records) == 2
    for r in records:
        assert r["user_input"] and r["response"] and r["reference"]
        assert r["retrieved_contexts"] == _HOME_CONTEXTS
        assert r["response"].startswith("[QUYẾT ĐỊNH:")
        assert isinstance(r["deterministic_decision_ok"], bool)


def test_clarify_case_deterministic_ok() -> None:
    (rec,) = build_records([_CLARIFY_CASE], FakeReasoningModel(), map_rooms=True)
    # "Bật lên đi" thiếu target → agent phải hỏi lại → outcome clarification khớp CLARIFY.
    assert rec["got_outcome"] == "clarification"
    assert rec["deterministic_decision_ok"] is True
    assert "CLARIFY" in rec["reference"]


def test_render_reference_carries_expected_signals() -> None:
    ref = render_reference(_PROCEED_CASE)
    assert "PROCEED" in ref and "phòng khách" in ref and "Turn on living-room light" in ref


def test_run_case_uses_final_turn_result() -> None:
    result, transcript = run_case(_PROCEED_CASE, FakeReasoningModel(), map_rooms=True)
    assert transcript[-1]["role"] == "user"
    # Câu trả lời render được từ kết quả cuối, luôn có nhãn quyết định.
    assert "QUYẾT ĐỊNH" in render_response(result)
