"""Pipeline multi-agent production — response shape, authorization và execution."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.agent.pipeline import PipelineDeps
from src.agent.schemas import DesiredOutcome, SemanticGoal, SufficiencyDecision
from src.core.interfaces import DeviceSelector
from src.domain.models import Device, User
from src.nlu.ontology import UtteranceType
from src.services import agent_runner, pipeline_bridge


def _user(session, username: str) -> User:
    return session.scalar(select(User).where(User.username == username))


def test_open_ended_goal_with_known_room_never_asks_for_room_when_already_satisfied(monkeypatch):
    goal = SemanticGoal(
        intent="prepare guests",
        raw_utterance="chuẩn bị phòng khách",
        utterance_type=UtteranceType.ROUTINE_INTENT,
        confidence=0.9,
        target_area="Phòng khách",
        desired_outcomes=[
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="light"),
                target_state={"power": "on"},
            )
        ],
    )

    monkeypatch.setattr(
        pipeline_bridge,
        "run_planning",
        lambda state, deps: {
            "semantic_goal": goal,
            "sufficiency_decision": SufficiencyDecision.PROCEED,
            "selected_plan": None,
            "validated_plan": [],
            "candidate_plans": [],
        },
    )

    result = pipeline_bridge._reason_impl(
        message="chuẩn bị phòng khách",
        conversation_id="known-room-noop",
        deps=PipelineDeps(),
    )

    assert result.outcome == "no_action"
    assert "phòng nào" not in result.reply.lower()


def test_nlu_source_reports_actual_path_instead_of_model_availability():
    assert agent_runner._nlu_source({"model_calls": 0, "structured_calls": 0}) == "rules"
    assert agent_runner._nlu_source({"model_calls": 1}) == "llm"


@pytest.mark.asyncio
async def test_bridge_executes_explicit_command(seeded):
    bo = _user(seeded, "bo")
    result = await agent_runner.run_command(
        seeded, user=bo, message="bật đèn phòng khách", conversation_id="v2-exec"
    )
    assert result["approval"] is None
    executed = result["state"]["executed"]
    assert executed and any(s["device_slug"] == "den_chum_phong_khach" for s in executed)
    # Thiết bị thật đã đổi trạng thái trong DB (execution vẫn ở agent_runner).
    dev = seeded.scalar(select(Device).where(Device.slug == "den_chum_phong_khach"))
    assert dev.state.get("power") == "on"

    # QC-09/§53/FR-14: envelope quyết định đầy đủ được PERSIST (sống qua restart), không chỉ ở state.
    from src.domain.models import AuditEnvelope

    env = seeded.scalars(select(AuditEnvelope).where(AuditEnvelope.conversation_id == "v2-exec")).first()
    assert env is not None
    assert env.envelope["semantic_goal"] and env.envelope["execution_result"]
    assert env.envelope["policy_decision"]["decision"] == "PROCEED"


@pytest.mark.asyncio
async def test_bridge_child_high_power_blocked(seeded):
    con_nho = _user(seeded, "con_nho")
    result = await agent_runner.run_command(
        seeded, user=con_nho, message="bật điều hoà phòng khách", conversation_id="v2-child"
    )
    # Trẻ nhỏ bị chặn thiết bị công suất lớn → bước AC bị DENIED, KHÔNG chạy (như path cũ).
    assert result["approval"] is None
    steps = result["state"].get("executed") or result["state"].get("plan") or []
    ac = next((s for s in steps if s.get("device_slug") == "dieu_hoa_phong_khach"), None)
    assert ac is not None and not ac.get("allowed")
    assert str(ac.get("status", "")).endswith("denied")
    # Thiết bị THẬT không đổi trạng thái.
    dev = seeded.scalar(select(Device).where(Device.slug == "dieu_hoa_phong_khach"))
    assert dev.state.get("power") == "off"


@pytest.mark.asyncio
async def test_bridge_member_request_needs_approval(seeded):
    from src.domain.enums import AccessEffect
    from src.domain.models import AccessRule, Device

    con_lon = _user(seeded, "con_lon")
    ac = seeded.scalar(select(Device).where(Device.slug == "dieu_hoa_phong_khach"))
    seeded.add(AccessRule(household_id=con_lon.household_id, user_id=con_lon.id, device_id=ac.id, effect=AccessEffect.REQUEST))
    seeded.commit()
    result = await agent_runner.run_command(
        seeded, user=con_lon, message="bật điều hoà phòng khách", conversation_id="v2-request"
    )
    assert result["approval"] is not None
    assert not (result["state"].get("executed") or [])


@pytest.mark.asyncio
async def test_bridge_returns_response_shape(seeded):
    bo = _user(seeded, "bo")
    result = await agent_runner.run_command(seeded, user=bo, message="bật đèn phòng khách", conversation_id="v2-shape")
    state = result["state"]
    for key in ("response_vi", "intent", "nlu_source", "conflicts", "plan", "executed"):
        assert key in state
    assert isinstance(result["conversation_id"], str)
    assert isinstance(result["latency_ms"], int)
