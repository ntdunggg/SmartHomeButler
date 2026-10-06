"""Explicit user-defined routines use event memory, not a closed scene table."""

from __future__ import annotations

from datetime import UTC, datetime

from src.agent.pipeline import PipelineDeps
from src.services.pipeline_bridge import reason

_NOW = datetime(2026, 8, 20, 20, 0, tzinfo=UTC)


def _turn(deps: PipelineDeps, text: str, *, conv: str = "routine-test"):
    return reason(message=text, conversation_id=conv, user_id="user-a", now=_NOW, deps=deps)


def _actions(result):
    return {
        (a.device_id, a.capability.value, a.action.value, tuple(sorted(a.params.items())))
        for a in (result.candidate_plan.actions if result.candidate_plan else [])
    }


def test_instruction_is_stored_with_turn_provenance_then_recalled():
    deps = PipelineDeps()
    taught = _turn(deps, "Khi tôi nói học bài thì bật đèn bàn học 80%.")
    assert taught.outcome == "answer"
    assert taught.candidate_plan is None

    events = deps.event_store.all_events()
    assert len(events) == 1 and events[0].event_type == "user_instruction"
    assert events[0].source_turn_ids
    assert deps.turn_store.get(events[0].source_turn_ids[0]).text.startswith("Khi tôi nói")

    recalled = _turn(deps, "Học bài thôi.")
    actions = _actions(recalled)
    assert recalled.outcome == "candidate_plan"
    assert ("den_ban_hoc", "brightness", "set", (("percent", 80),)) in actions


def test_latest_numeric_correction_replaces_old_value():
    deps = PipelineDeps()
    _turn(deps, "Khi học bài bật đèn bàn học 80%.")
    _turn(deps, "Không, 70% thôi, không phải 80%.")
    recalled = _turn(deps, "Học bài.")
    actions = _actions(recalled)
    assert ("den_ban_hoc", "brightness", "set", (("percent", 70),)) in actions
    assert ("den_ban_hoc", "brightness", "set", (("percent", 80),)) not in actions


def test_deleted_routine_cannot_be_recalled():
    deps = PipelineDeps()
    _turn(deps, "Khi tôi nói học bài thì bật đèn bàn học.")
    deleted = _turn(deps, "Quên thói quen bật đèn khi tôi nói học bài nhé.")
    recalled = _turn(deps, "Học bài thôi.")
    assert deleted.outcome == "answer"
    assert deps.event_store.all_events() == []
    assert recalled.outcome == "clarification"
    assert recalled.candidate_plan is None


def test_long_routine_continuations_keep_positive_and_negative_constraints():
    deps = PipelineDeps()
    _turn(deps, "Bật TV phòng bố mẹ.", conv="long-routine")
    _turn(deps, "Tôi thích khi đi ngủ thì TV phải tắt.", conv="long-routine")
    _turn(deps, "Và đèn ngủ để 20%.", conv="long-routine")
    _turn(deps, "Nhưng nhớ đừng tắt điều hoà phòng bố mẹ.", conv="long-routine")
    recalled = _turn(deps, "Đi ngủ thôi.", conv="long-routine")
    actions = _actions(recalled)
    assert ("tv_phong_bo_me", "on_off", "turn_off", ()) in actions
    assert ("den_ngu_bo_me", "brightness", "set", (("percent", 20),)) in actions
    assert not any(device == "dieu_hoa_phong_bo_me" for device, *_ in actions)
