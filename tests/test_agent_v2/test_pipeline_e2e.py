"""End-to-end pipeline (spec §56, §60, §67) — vertical slice offline.

Chạy hoàn toàn offline với FakeReasoningModel + store/gateway inject (không đụng
singleton mặc định) để mỗi test tất định.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.agent.cognitive.ledger import LedgerStore
from src.agent.feedback.interpreter import interpret_feedback
from src.agent.feedback.rl_update import apply_feedback
from src.agent.memory.event_store import EventStore
from src.agent.memory.profile_store import ProfileStore
from src.agent.memory.turn_store import TurnStore
from src.agent.pipeline import PipelineDeps, run_turn
from src.agent.preference.preference_store import PreferenceStore
from src.agent.preference.state_encoder import encode_state
from src.agent.schemas import DesiredOutcome, SemanticGoal
from src.core.interfaces import DeviceSelector
from src.core.reasoning import FakeReasoningModel
from src.nlu.ontology import UtteranceType


@pytest.fixture
def deps() -> PipelineDeps:
    return PipelineDeps(
        ledger_store=LedgerStore(),
        event_store=EventStore(),
        turn_store=TurnStore(),
        preference_store=PreferenceStore(),
        profile_store=ProfileStore(),
        model_client=FakeReasoningModel(),
    )


def _base_state(**kw) -> dict:
    base = {
        "conversation_id": "c",
        "user_id": "user_A",
        "speaker_role": "owner",
        "now": datetime(2026, 8, 14, 20, 0, tzinfo=UTC),
        "speaker_location": "Phòng khách",
    }
    base.update(kw)
    return base


def _illumination_goal() -> SemanticGoal:
    return SemanticGoal(
        intent="tăng sáng",
        raw_utterance="tối quá",
        utterance_type=UtteranceType.ENVIRONMENT_REQUEST,
        goal_description="increase illumination",
        confidence=0.85,
        desired_outcomes=[
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách"),
                perceived_state="too_dark",
                relative_change={"brightness": "increase"},
            )
        ],
    )


def test_explicit_command_executes_end_to_end(deps):
    out = run_turn(_base_state(conversation_id="c1", user_message="bật đèn phòng khách"), deps)
    assert out["final_status"] == "SUCCESS"
    assert out["execution_result"].actions[0].status == "SUCCESS"
    assert out.get("audit") is not None  # mọi hành động vật lý được audit (§53)


def test_context_perturbation_same_utterance_different_plan(deps):
    """Spec §60: 'tối quá' ban ngày → mở rèm (0 W); ban đêm → bật đèn. Không keyword-rule."""
    day = run_turn(
        _base_state(
            conversation_id="day",
            user_message="tối quá",
            now=datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
            semantic_goal=_illumination_goal(),
            live_device_states={"den_chum_phong_khach": {"power": "off", "brightness": 0}, "rem_phong_khach": {"power": "off", "position": 0}},
            live_sensors=[{"slug": "cam_bien_nang", "name": "nắng", "sensor_type": "sunlight", "value": 80.0, "unit": "%", "room": ""}],
        ),
        deps,
    )
    night = run_turn(
        _base_state(
            conversation_id="night",
            user_message="tối quá",
            semantic_goal=_illumination_goal(),
            live_device_states={"den_chum_phong_khach": {"power": "off", "brightness": 0}, "rem_phong_khach": {"power": "off", "position": 0}},
            live_sensors=[{"slug": "cam_bien_nang", "name": "nắng", "sensor_type": "sunlight", "value": 0.0, "unit": "%", "room": ""}],
        ),
        deps,
    )
    assert "shutter" in day["selected_plan"].source_agents
    assert "lighting" in night["selected_plan"].source_agents


def test_member_high_power_rejected(deps):
    out = run_turn(
        _base_state(conversation_id="c3", user_message="bật điều hoà phòng khách", speaker_role="member", user_id="con_lon"),
        deps,
    )
    assert out["final_status"] == "rejected"
    assert out["policy_decision"]["priority_hit"] == "P1_authorization"


def test_empty_utterance_clarifies(deps):
    out = run_turn(_base_state(conversation_id="c5", user_message=""), deps)
    assert out["final_status"] == "clarification_required"
    assert out["reply"]


def test_feedback_loop_updates_preference(deps):
    """Spec §67: sau khi hệ chọn 24 nhưng user chỉnh 23, preference dịch về 23."""
    s = encode_state(resident="user_A", room="Phòng khách", now=datetime(2026, 8, 14, 20, 0, tzinfo=UTC), power_mode="NORMAL")
    signal = interpret_feedback(corrected_value=23, dimension="temperature")
    for _ in range(4):
        apply_feedback(deps.preference_store, dimension="temperature", state=s, chosen_action=24, feedback=signal)
    dist = deps.preference_store.distribution("temperature", s)
    assert dist.top()[0] == "23"


def test_memory_write_path_records_turn_and_event(deps):
    """Spec §20, §25, §54: lượt đã execute phải ghi Turn + Event (write path đã nối vào graph)."""
    out = run_turn(_base_state(conversation_id="mw", user_message="bật đèn phòng khách"), deps)
    assert out["final_status"] == "SUCCESS"
    assert out["observation"]["status"] == "SUCCESS"  # §56 observe cô đọng kết quả thật
    assert out["memory_written"]["turn_id"] and out["memory_written"]["event_id"]
    # Turn Store + Event Store thật sự có bản ghi (trước đây write path mồ côi → luôn rỗng).
    assert [t.text for t in deps.turn_store.for_conversation("mw")] == ["bật đèn phòng khách"]
    events = deps.event_store.all_events()
    assert len(events) == 1 and "Phòng khách" in events[0].location


def test_written_event_is_retrievable_next_turn(deps):
    """Spec §26, FR-07: event ghi ở lượt trước trở thành memory_evidence cho lượt sau."""
    run_turn(_base_state(conversation_id="rt", user_message="bật đèn phòng khách"), deps)
    out2 = run_turn(_base_state(conversation_id="rt", user_message="bật đèn phòng khách"), deps)
    assert len(out2.get("memory_evidence") or []) >= 1  # read path không còn đói dữ liệu


def test_rl_update_fires_through_graph_on_correction(deps):
    """Spec §32, §54, §67: feedback correction qua graph phạt action đã chọn, thưởng giá trị mới."""
    out = run_turn(
        _base_state(
            conversation_id="rl",
            user_message="bật đèn bếp",
            speaker_location="Phòng bếp",
            feedback={"corrected_value": 40, "dimension": "brightness", "chosen_action": 60},
        ),
        deps,
    )
    res = out["rl_update_result"]
    assert res is not None and res["corrected"] > res["chosen"]  # 40 được thưởng > 60 bị phạt


def test_no_feedback_still_writes_memory_independently(deps):
    """Spec §54: memory_update và rl_update độc lập — không feedback thì RL no-op, memory vẫn ghi."""
    out = run_turn(_base_state(conversation_id="ind", user_message="bật đèn phòng khách"), deps)
    assert out["rl_update_result"] is None
    assert out["memory_written"]["event_id"]  # memory không phụ thuộc RL


def test_profile_promoted_from_events_and_retrieved_next_turn(deps):
    """Spec §24, §14 tier-6, §26: đủ evidence → promote stable fact; lượt sau retrieve làm profile_evidence.

    Mô phỏng các lượt set số THẬT bằng event có numeric fact (offline planner chưa ground SET số)."""
    from src.agent.schemas import MemoryEvent, MemoryFact

    for i in range(3):
        deps.event_store.add(MemoryEvent(
            event_id=f"seed{i}", actors=["user_A"], location=["Phòng khách"],
            facts=[MemoryFact(subject="den_chum_phong_khach", relation="preferred_brightness", value=30 + i * 2)],
        ))
    # Lượt A: memory_update consolidate → promote (avg 30,32,34 = 32).
    a = run_turn(_base_state(conversation_id="pa", user_message="bật đèn phòng khách"), deps)
    assert a["memory_written"]["profiles_promoted"] == 1
    pf = deps.profile_store.get("den_chum_phong_khach", "preferred_brightness")
    assert pf is not None and pf.value["value"] == 32.0 and len(pf.supporting_events) == 3
    # Lượt B: retrieve_memory neo profile theo scope thiết bị → profile_evidence + audit.profile_used.
    b = run_turn(_base_state(conversation_id="pb", user_message="bật đèn phòng khách"), deps)
    assert any(p["subject"] == "den_chum_phong_khach" for p in b["profile_evidence"])
    assert b["audit"].profile_used and b["audit"].profile_used[0]["fact"] == "preferred_brightness"
