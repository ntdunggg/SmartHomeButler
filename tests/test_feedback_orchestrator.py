"""Test Feedback Orchestrator (FR-15 & FR-08) — Ownership, Independence, Retry, and Invariants."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.agent.preference.repository import SqlPreferenceRepository
from src.agent.preference.state_encoder import encode_state
from src.domain.models import Base, EpisodicMemory, Household, LearningDecision, User
from src.models.schemas import FeedbackIn
from src.services.feedback_service import FeedbackService, _extract_dimension_and_action


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    h = Household(name="Nhà Test")
    session.add(h)
    session.flush()

    u1 = User(household_id=h.id, username="user_a", password_hash="hash", role="owner")
    u2 = User(household_id=h.id, username="user_b", password_hash="hash", role="member")
    session.add_all([u1, u2])
    session.commit()

    yield session
    session.close()


def _create_decision(
    session: Session,
    *,
    household_id: int,
    user_id: int,
    action_val: int = 24,
    dim: str = "temperature",
) -> tuple[str, int]:
    exec_id = f"exec_test_{datetime.now(UTC).timestamp()}"
    state = encode_state(
        resident=str(user_id),
        room="bedroom",
        now=datetime.now(UTC),
        activity="resting",
        outside_temp_c=15.0,
        power_mode="NORMAL",
    )

    param_key = "temperature" if dim == "temperature" else "brightness"
    ep = EpisodicMemory(
        household_id=household_id,
        user_id=user_id,
        utterance="test command",
        goal_description="test goal",
        outcome="proposed",
    )
    session.add(ep)
    session.flush()

    decision = LearningDecision(
        execution_id=exec_id,
        household_id=household_id,
        user_id=user_id,
        episode_id=ep.id,
        conversation_id="conv_123",
        rl_state=state.to_dict(),
        executed_actions=[
            {
                "device_slug": "dev_1",
                "device_name": "Thiết bị test",
                "action": f"set_{param_key}",
                "params": {param_key: action_val},
                "risk_level": "normal",
            }
        ],
        created_at=datetime.now(UTC),
    )
    session.add(decision)
    session.commit()
    return exec_id, ep.id


def test_ownership_enforcement(db_session: Session):
    """User B không thể gửi feedback cho execution_id của User A."""
    u1 = db_session.query(User).filter_by(username="user_a").first()
    u2 = db_session.query(User).filter_by(username="user_b").first()

    exec_id, _ = _create_decision(db_session, household_id=u1.household_id, user_id=u1.id)

    payload = FeedbackIn(execution_id=exec_id, outcome="accepted", dimension="temperature")

    # User A gửi -> thành công
    res_a = FeedbackService.handle_feedback(db_session, user=u1, payload=payload, idempotency_key="key_u1")
    assert res_a.rl_status == "succeeded"

    # User B gửi cho execution của User A -> Bị từ chối
    with pytest.raises(ValueError, match="Không tìm thấy quyết định thực thi hợp lệ"):
        FeedbackService.handle_feedback(db_session, user=u2, payload=payload, idempotency_key="key_u2")


def test_strict_schema_validation():
    """Kiểm tra validation chặt chẽ của schema FeedbackIn."""
    # Outcome không hợp lệ
    with pytest.raises(ValidationError):
        FeedbackIn(execution_id="exec_1", outcome="banana")  # type: ignore[arg-type]

    # Corrected thiếu corrected_value
    with pytest.raises(ValidationError, match="Trường 'corrected_value' là bắt buộc"):
        FeedbackIn(execution_id="exec_1", outcome="corrected", dimension="temperature")

    # Corrected ngoài action space nhiệt độ (20..26)
    with pytest.raises(ValidationError, match="Nhiệt độ hiệu chỉnh phải nằm trong khoảng 20-26°C"):
        FeedbackIn(execution_id="exec_1", outcome="corrected", dimension="temperature", corrected_value=35)


def test_brightness_zero_not_lost(db_session: Session):
    """Giá trị brightness=0 không bị nuốt thành None do falsy check."""
    u = db_session.query(User).first()
    exec_id, _ = _create_decision(db_session, household_id=u.household_id, user_id=u.id, action_val=0, dim="brightness")

    dec = db_session.query(LearningDecision).filter_by(execution_id=exec_id).first()
    dim, val, is_sec = _extract_dimension_and_action(dec, "brightness")

    assert dim == "brightness"
    assert val == 0
    assert is_sec is False


def test_memory_failure_does_not_corrupt_rl(db_session: Session):
    """Failure Injection: MemoryProcessor lỗi không làm rollback hoặc hủy cập nhật của RLProcessor."""
    u = db_session.query(User).first()
    exec_id, ep_id = _create_decision(db_session, household_id=u.household_id, user_id=u.id, action_val=24)

    payload = FeedbackIn(execution_id=exec_id, outcome="accepted", dimension="temperature")

    # Mock MemoryProcessor ném ngoại lệ
    with patch("src.services.feedback_service.MemoryProcessor.process", side_effect=RuntimeError("Memory store crash")):
        res = FeedbackService.handle_feedback(db_session, user=u, payload=payload, idempotency_key="key_fail_mem")

    assert res.memory_status == "failed"
    assert res.rl_status == "succeeded"

    # Kiểm tra Q-value đã được cập nhật thành công và commit
    repo = SqlPreferenceRepository(db_session)
    dec = db_session.query(LearningDecision).filter_by(execution_id=exec_id).first()
    from src.agent.preference.state_encoder import RLState

    state = RLState.from_dict(dec.rl_state)
    q24 = repo.get_q(household_id=u.household_id, user_id=u.id, dimension="temperature", state_key=state.key(), action=24)
    assert q24 > 0.0

    # Kiểm tra episode không bị mutate bậy
    ep = db_session.get(EpisodicMemory, ep_id)
    assert ep.outcome == "proposed"


def test_retry_on_failed_branch(db_session: Session):
    """Retry khi một nhánh bị thất bại: chỉ chạy lại nhánh chưa thành công."""
    u = db_session.query(User).first()
    exec_id, ep_id = _create_decision(db_session, household_id=u.household_id, user_id=u.id, action_val=24)
    payload = FeedbackIn(execution_id=exec_id, outcome="accepted", dimension="temperature")

    # Lần 1: Memory fail
    with patch("src.services.feedback_service.MemoryProcessor.process", side_effect=RuntimeError("Transient DB error")):
        res1 = FeedbackService.handle_feedback(db_session, user=u, payload=payload, idempotency_key="key_retry")
    assert res1.memory_status == "failed"
    assert res1.rl_status == "succeeded"
    assert res1.memory_attempt_count == 1
    assert res1.rl_attempt_count == 1

    # Lần 2: Gửi lại cùng idempotency key (hệ thống phục hồi)
    res2 = FeedbackService.handle_feedback(db_session, user=u, payload=payload, idempotency_key="key_retry")
    assert res2.duplicate is True
    assert res2.memory_status == "succeeded"
    assert res2.rl_status == "succeeded"
    # Memory đã tăng attempt count lên 2, RL vẫn là 1 (không chạy lại RL đã succeeded)
    assert res2.memory_attempt_count == 2
    assert res2.rl_attempt_count == 1

    # Episode đã được cập nhật thành công
    ep = db_session.get(EpisodicMemory, ep_id)
    assert ep.outcome == "accepted"


def test_partial_mutation_rollback_in_savepoint(db_session: Session):
    """Savepoint rollback: lỗi xảy ra sau khi đã mutate episode phải rollback toàn bộ thay đổi của memory."""
    u = db_session.query(User).first()
    exec_id, ep_id = _create_decision(db_session, household_id=u.household_id, user_id=u.id, action_val=24)
    payload = FeedbackIn(execution_id=exec_id, outcome="accepted", dimension="temperature")

    # Giả lập store.consolidate bị crash sau khi store.update_outcome đã chạy
    with patch("src.memory.store.consolidate", side_effect=RuntimeError("Consolidate crash mid-way")):
        res = FeedbackService.handle_feedback(db_session, user=u, payload=payload, idempotency_key="key_partial_crash")

    assert res.memory_status == "failed"
    assert res.rl_status == "succeeded"
    assert "Consolidate crash" in (res.last_error or "")

    # Đảm bảo episode.outcome không bị đổi dở dang (vẫn giữ "proposed" do savepoint rollback)
    ep = db_session.get(EpisodicMemory, ep_id)
    assert ep.outcome == "proposed"


@pytest.mark.asyncio
async def test_hitl_exact_episode_linkage(db_session: Session):
    """HITL: Khi resume, LearningDecision phải gắn chính xác episode_id từ Approval, không query latest."""
    from src.domain.enums import ActionStatus
    from src.domain.models import Approval
    from src.services.agent_execution import ActionExecutionResult, AgentExecutionResult
    from src.services.agent_runner import resume_after_approval

    u = db_session.query(User).first()

    # Episode 1 của chính approval này
    ep1 = EpisodicMemory(household_id=u.household_id, user_id=u.id, utterance="lệnh 1", goal_description="goal 1", outcome="proposed")
    db_session.add(ep1)
    db_session.flush()

    appr = Approval(
        household_id=u.household_id,
        requested_by_id=u.id,
        conversation_id="conv_hitl",
        command_text="bật khoá cửa",
        steps=[{"device_slug": "khoa_cua_chinh", "action": "unlock", "params": {}, "risk_level": "security", "allowed": True, "skipped": False, "requires_approval": True}],
        status=ActionStatus.PENDING_APPROVAL,
        episode_id=ep1.id,
    )
    db_session.add(appr)
    db_session.flush()

    # Episode 2 của một tương tác khác sinh ra sau đó
    ep2 = EpisodicMemory(household_id=u.household_id, user_id=u.id, utterance="lệnh 2", goal_description="goal 2", outcome="proposed")
    db_session.add(ep2)
    db_session.commit()

    # Resume approval
    dispatch_result = AgentExecutionResult(
        execution_id="exec_hitl_linkage",
        status="SUCCESS",
        actions=[
            ActionExecutionResult(
                device_slug="khoa_cua_chinh",
                action="unlock",
                status="SUCCESS",
                reason_code="EXECUTED",
                detail_vi="Đã mở khóa.",
            )
        ],
    )
    with patch("src.services.agent_runner.agent_execution.execute", new=AsyncMock(return_value=dispatch_result)):
        await resume_after_approval(db_session, approval=appr, approved=True)

    dec = db_session.query(LearningDecision).filter_by(plan_id=str(appr.id)).first()
    assert dec is not None
    assert dec.episode_id == ep1.id
    assert dec.episode_id != ep2.id


def test_correction_outside_action_space_rejected_before_mutation(db_session: Session):
    """Correction ngoài action space bị từ chối trước khi làm bẩn Memory."""
    u = db_session.query(User).first()
    exec_id, ep_id = _create_decision(db_session, household_id=u.household_id, user_id=u.id, action_val=24, dim="temperature")

    # Sửa độ sáng / số ngoài action space
    with pytest.raises((ValueError, ValidationError)):
        payload = FeedbackIn(execution_id=exec_id, outcome="corrected", dimension=None, corrected_value=999)
        FeedbackService.handle_feedback(db_session, user=u, payload=payload, idempotency_key="key_invalid_val")

    ep = db_session.get(EpisodicMemory, ep_id)
    assert ep.outcome == "proposed"


def test_outdoor_temperature_sensor_resolution():
    """Cảm biến ngoài trời không bị nhầm với cảm biến trong phòng dù cảm biến phòng đứng trước."""
    from src.agent.perception.sensor_adapter import outdoor_sensor_by_type
    from src.agent.schemas import SensorReading

    s1_indoor = SensorReading(slug="temp_bed", name="Nhiệt độ phòng ngủ", sensor_type="temperature", value=24.0, unit="°C", room="Phòng ngủ", is_stale=False, source="live")
    s2_outdoor = SensorReading(slug="temp_outdoor", name="Nhiệt độ ngoài trời", sensor_type="temperature", value=35.0, unit="°C", room="", is_stale=False, source="live")

    picked = outdoor_sensor_by_type([s1_indoor, s2_outdoor], "temperature")
    assert picked is not None
    assert picked.slug == "temp_outdoor"
    assert picked.value == 35.0
