"""Test Feature Flags Contracts (Cutover Backlog)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.agent.preference.repository import SqlPreferenceRepository
from src.agent.preference.reward import FeedbackKind, reward_for
from src.config import get_settings
from src.core.reasoning import FakeReasoningModel
from src.domain.models import Base, Device, Household, LearningDecision, User
from src.models.schemas import FeedbackIn
from src.services import agent_runner, feedback_service, pipeline_bridge
from src.services.agent_execution import ActionExecutionResult, AgentExecutionResult


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    h = Household(name="Nhà Contract")
    session.add(h)
    session.flush()

    u = User(household_id=h.id, username="contract_owner", password_hash="hash", role="owner")
    dev = Device(
        household_id=h.id,
        slug="dieu_hoa_phong_bo_me",
        name="Điều hoà phòng bố mẹ",
        room="Phòng ngủ bố mẹ",
        device_type="air_conditioner",
        risk_level="normal",
        capabilities=["temperature", "power"],
        state={"power": "off", "temperature": 24},
        online=True,
    )
    session.add_all([u, dev])
    session.commit()

    yield session
    session.close()


@pytest.mark.asyncio
async def test_learning_v2_write_disabled_skips_recording_and_updates(db_session: Session, monkeypatch):
    """Khi LEARNING_V2_WRITE=false: không ghi LearningDecision và RLProcessor trả về skipped."""
    monkeypatch.setenv("LEARNING_V2_WRITE", "false")
    get_settings.cache_clear()

    user = db_session.query(User).first()
    dispatch_result = AgentExecutionResult(
        execution_id="exec_write_disabled",
        status="SUCCESS",
        actions=[
            ActionExecutionResult(
                device_slug="dieu_hoa_phong_bo_me",
                action="turn_on",
                status="SUCCESS",
                reason_code="EXECUTED",
                detail_vi="Đã bật điều hoà.",
            )
        ],
    )
    with patch("src.services.agent_runner.agent_execution.execute", new=AsyncMock(return_value=dispatch_result)):
        _res = await agent_runner.run_command(
            db_session,
            user=user,
            message="bật điều hoà phòng bố mẹ",
            conversation_id="conv_write_off",
        )
    # Không tạo bản ghi LearningDecision trong DB
    count = db_session.query(LearningDecision).count()
    assert count == 0

    # Nếu tạo một decision thủ công và gửi feedback, RLProcessor cũng bỏ qua
    dec = LearningDecision(
        execution_id="exec_manual",
        household_id=user.household_id,
        user_id=user.id,
        conversation_id="conv_m",
        rl_state={},
        executed_actions=[{"device_slug": "dev", "action": "set_temperature", "params": {"temperature": 24}}],
    )
    db_session.add(dec)
    db_session.commit()

    payload = FeedbackIn(execution_id="exec_manual", outcome="accepted", dimension="temperature")
    fb_out = feedback_service.FeedbackService.handle_feedback(
        db_session, user=user, payload=payload, idempotency_key="key_w_off"
    )
    assert fb_out.rl_status == "skipped"

    get_settings.cache_clear()


def test_learning_v2_read_disabled_skips_preference_inference(db_session: Session, monkeypatch):
    """Khi LEARNING_V2_READ=false: pipeline không nạp preference distribution vào planning."""
    user = db_session.query(User).first()
    repo = SqlPreferenceRepository(db_session)
    now = datetime(2026, 8, 14, 22, 0, tzinfo=UTC)
    room = "Phòng ngủ bố mẹ"
    live_states = {"dieu_hoa_phong_bo_me": {"power": "off", "temperature": 24}}
    from src.agent.perception.context_builder import build_perception
    from src.agent.pipeline import _make_infer_preferences_node

    # Lấy đúng RL state của pipeline (bao gồm occupancy/weather mặc định của context
    # builder), tránh tự dựng state khác với state mà lượt reason() sẽ tra cứu.
    _nu, ctx, power = build_perception(
        "làm mát phòng bố mẹ",
        now=now,
        speaker_location=room,
        live_device_states=live_states,
        live_sensors=[],
    )
    probe = _make_infer_preferences_node(pipeline_bridge._get_or_create_deps(user.household_id))
    s = probe({
        "runtime_context": ctx,
        "power_load": power,
        "household_id": user.household_id,
        "user_id": str(user.id),
    })["rl_state"]
    # 26°C khác heuristic giảm 2°C từ current=24, nên test thực sự chứng minh RL được đọc.
    for _ in range(5):
        repo.update(
            household_id=user.household_id,
            user_id=user.id,
            dimension="temperature",
            state=s,
            action=26,
            reward=reward_for(FeedbackKind.EXPLICIT_ACCEPT),
        )
    db_session.commit()

    # 1. Bật read flag -> preference 26 độ được dùng
    monkeypatch.setenv("LEARNING_V2_READ", "true")
    get_settings.cache_clear()
    res_on = pipeline_bridge.reason(
        message="làm mát phòng bố mẹ",
        conversation_id="c_on",
        role="owner",
        user_id=str(user.id),
        household_id=user.household_id,
        session=db_session,
        now=datetime(2026, 8, 14, 22, 0, tzinfo=UTC),
        speaker_location="Phòng ngủ bố mẹ",
        live_device_states={"dieu_hoa_phong_bo_me": {"power": "off", "temperature": 24}},
        live_sensors=[],
        model_client=FakeReasoningModel(),
    )
    assert res_on.candidate_plan.actions[0].params.get("temperature") == 26

    # 2. Tắt read flag -> preference không được nạp (rơi về heuristic 24 - 2 = 22, nhưng preference_distribution rỗng)
    monkeypatch.setenv("LEARNING_V2_READ", "false")
    get_settings.cache_clear()
    from src.agent.pipeline import _make_infer_preferences_node
    infer_fn = _make_infer_preferences_node(pipeline_bridge._get_or_create_deps(user.household_id))
    from src.agent.perception.context_builder import build_perception
    _nu, ctx, p = build_perception("làm mát phòng bố mẹ", speaker_location="Phòng ngủ bố mẹ")
    out = infer_fn({"runtime_context": ctx, "power_load": p, "household_id": user.household_id, "user_id": str(user.id)})
    assert out["preference_distribution"] == {}

    get_settings.cache_clear()


def test_learning_v2_read_separates_preference_by_user_id(db_session: Session, monkeypatch):
    """Hai thành viên CÙNG hộ, CÙNG phòng, CÙNG trạng thái (giờ/thời tiết) nhưng đã học sở
    thích KHÁC nhau -> mỗi người phải nhận đúng preference của MÌNH qua pipeline, không bị
    trộn lẫn (FR-08 tenant/user isolation, khác test_sql_preference_repository_persistence_and_isolation
    vốn chỉ test tầng repository — đây test xuyên suốt end-to-end qua pipeline_bridge.reason())."""
    owner = db_session.query(User).first()
    member = User(household_id=owner.household_id, username="contract_member", password_hash="hash", role="member")
    db_session.add(member)
    db_session.commit()

    now = datetime(2026, 8, 14, 22, 0, tzinfo=UTC)
    room = "Phòng ngủ bố mẹ"
    live_states = {"dieu_hoa_phong_bo_me": {"power": "off", "temperature": 24}}

    # Lấy CHÍNH XÁC rl_state mà pipeline sẽ tự tính cho lượt này (thay vì tự dựng bằng
    # encode_state() độc lập) — occupancy/weather suy từ live_sensors=[] có quy tắc riêng
    # (vd không có cảm biến hiện diện -> "empty", không phải "unknown"); tự dựng tay dễ lệch
    # state_key với pipeline thật và khiến cả hai user đều tra bảng Q trống (che mất bug).
    from src.agent.perception.context_builder import build_perception
    from src.agent.pipeline import _make_infer_preferences_node

    _nu, ctx, power = build_perception(
        "làm mát phòng bố mẹ", speaker_location=room, now=now, live_device_states=live_states, live_sensors=[]
    )
    probe = _make_infer_preferences_node(pipeline_bridge._get_or_create_deps(owner.household_id))
    s = probe({"runtime_context": ctx, "power_load": power, "household_id": owner.household_id, "user_id": None})["rl_state"]

    repo = SqlPreferenceRepository(db_session)
    for _ in range(5):
        repo.update(household_id=owner.household_id, user_id=owner.id, dimension="temperature", state=s,
                     action=26, reward=reward_for(FeedbackKind.EXPLICIT_ACCEPT))
        repo.update(household_id=owner.household_id, user_id=member.id, dimension="temperature", state=s,
                     action=20, reward=reward_for(FeedbackKind.EXPLICIT_ACCEPT))
    db_session.commit()

    monkeypatch.setenv("LEARNING_V2_READ", "true")
    get_settings.cache_clear()
    try:
        res_owner = pipeline_bridge.reason(
            message="làm mát phòng bố mẹ", conversation_id="c_owner",
            user_id=str(owner.id), household_id=owner.household_id, session=db_session, now=now,
            speaker_location=room, live_device_states={"dieu_hoa_phong_bo_me": {"power": "off", "temperature": 24}},
            live_sensors=[], model_client=FakeReasoningModel(),
        )
        res_member = pipeline_bridge.reason(
            message="làm mát phòng bố mẹ", conversation_id="c_member",
            user_id=str(member.id), household_id=owner.household_id, session=db_session, now=now,
            speaker_location=room, live_device_states={"dieu_hoa_phong_bo_me": {"power": "off", "temperature": 24}},
            live_sensors=[], model_client=FakeReasoningModel(),
        )
        assert res_owner.candidate_plan.actions[0].params.get("temperature") == 26
        assert res_member.candidate_plan.actions[0].params.get("temperature") == 20
    finally:
        get_settings.cache_clear()
