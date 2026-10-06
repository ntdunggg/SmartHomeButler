"""Hội thoại ≥3 lượt phải giữ được chỗ đứng (§17.1).

Phòng đã xác lập ở lượt 1 là FACT của ledger. Một lượt sau không nhắc phòng — kể cả khi
tầng hiểu tự báo "còn thiếu phòng" — không phải mâu thuẫn, nên không được thu hồi fact đó.
Trước bản vá, lượt 3 mất phòng và rơi xuống hỏi lại "Phòng khách hay Phòng bếp ạ?".

Kèm ngữ nghĩa chấm điểm của harness: phòng chấm trên THIẾT BỊ bị tác động, vì lượt tiếp
nối kế thừa grounding từ ledger nên `goal.target_area` rỗng một cách hợp lệ.
"""

from __future__ import annotations

from datetime import UTC, datetime

from scripts.eval_live_semantic import run_case
from src.agent.pipeline import PipelineDeps
from src.core.reasoning import FakeReasoningModel
from src.iot.registry import spec_for
from src.services.pipeline_bridge import reason

_NOW = datetime(2026, 8, 28, 20, 0, tzinfo=UTC)


def _run(conversation_id: str, *turns: str, location: str | None = None):
    model = FakeReasoningModel()
    deps = PipelineDeps(model_client=model)
    results = []
    for index, text in enumerate(turns):
        results.append(
            reason(
                message=text,
                conversation_id=conversation_id,
                user_id=f"test:{conversation_id}",
                role="owner",
                now=_NOW,
                speaker_location=location if index == 0 else None,
                deps=deps,
                model_client=model,
            )
        )
    return deps, results


def _rooms_touched(result) -> set[str]:
    plan = result.candidate_plan
    return {
        spec.room
        for action in (plan.actions if plan else [])
        if (spec := spec_for(action.device_id)) is not None
    }


def test_third_turn_keeps_the_room_established_on_the_first():
    deps, (_first, _second, third) = _run(
        "keep-room",
        "Phòng khách tối quá.",
        "Đừng bật đèn bàn.",
        "Tăng thêm một chút nữa.",
        location="Phòng khách",
    )

    assert deps.ledger_store.load("keep-room").confirmed_facts["room"] == "Phòng khách"
    assert third.outcome != "clarification", "lượt tiếp nối không được hỏi lại phòng đã biết"
    # The only living-room light is intentionally excluded by the previous turn, so
    # there is no executable action.  Room persistence is still observable from the
    # grounded semantic goal and ledger; a separate harness case below verifies room
    # scoring from touched devices when a plan exists.
    assert third.semantic_goal is not None
    assert third.semantic_goal.target_area == "Phòng khách"
    assert deps.ledger_store.load("keep-room").confirmed_facts["room"] == "Phòng khách"


def test_a_middle_turn_naming_only_a_device_does_not_erase_the_room():
    deps, (_first, second) = _run(
        "device-turn",
        "Phòng khách tối quá.",
        "Đừng bật đèn bàn.",
        location="Phòng khách",
    )

    facts = deps.ledger_store.load("device-turn").confirmed_facts
    assert facts["room"] == "Phòng khách"
    assert facts["devices"], "lượt này vẫn phải chốt được thiết bị của nó"
    assert second is not None


def _case(room: str, *turns: str) -> dict:
    return {
        "id": f"synthetic-{room}",
        "category": "long_conversation",
        "messages": [{"role": "user", "content": t} for t in turns],
        "expected": {
            "decision": "PROCEED", "room": room, "target": "điều hòa",
            "adjustment": "set_temperature_24", "constraints": [],
            "should_use_context": True, "memory_expectation": "NONE",
        },
    }


def test_harness_scores_the_room_on_devices_touched_not_on_goal_target_area():
    """Lượt tiếp nối kế thừa grounding từ ledger nên `target_area` rỗng một cách hợp lệ.

    Chấm phòng trên trường nội bộ đó sẽ đánh trượt cả lượt ground ĐÚNG phòng; chấm trên
    thiết bị bị tác động mới là hành vi quan sát được.
    """
    case = _case("phòng ngủ", "Bật điều hoà phòng ngủ 25 độ.", "Giảm còn 24.")
    evaluation = run_case(case, FakeReasoningModel(), now=_NOW)

    assert evaluation.exception is None
    assert evaluation.room_ok
    assert evaluation.device_ok


def test_harness_still_fails_a_turn_that_touches_another_room():
    """Chặt hơn chứ không lỏng hơn: chạm thiết bị ngoài phòng mong đợi vẫn phải trượt."""
    case = _case("phòng bếp", "Bật điều hoà phòng ngủ 25 độ.", "Giảm còn 24.")
    evaluation = run_case(case, FakeReasoningModel(), now=_NOW)

    assert evaluation.exception is None
    assert not evaluation.room_ok
