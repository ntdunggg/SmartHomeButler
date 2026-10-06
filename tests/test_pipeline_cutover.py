"""Test Pipeline Cutover & Shadow Comparison (Cutover Backlog)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.agent.preference.repository import SqlPreferenceRepository
from src.agent.preference.reward import FeedbackKind, reward_for
from src.config import Settings
from src.core.reasoning import FakeReasoningModel
from src.domain.models import Base, Device, Household, User
from src.services import pipeline_bridge


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    h = Household(name="Nhà Cutover")
    session.add(h)
    session.flush()

    u = User(household_id=h.id, username="owner_user", password_hash="hash", role="owner")
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


def test_learning_feature_flags_exist():
    settings = Settings()
    assert hasattr(settings, "learning_v2_write")
    assert hasattr(settings, "learning_v2_read")
    assert not hasattr(settings, "pipeline_v2_shadow")
    assert not hasattr(settings, "agent_pipeline_v2")


def test_persistent_preference_affects_pipeline_planning(db_session: Session):
    user = db_session.query(User).first()
    repo = SqlPreferenceRepository(db_session)
    now = datetime(2026, 8, 14, 22, 0, tzinfo=UTC)
    room = "Phòng ngủ bố mẹ"
    live_states = {"dieu_hoa_phong_bo_me": {"power": "off", "temperature": 24}}
    from src.agent.perception.context_builder import build_perception
    from src.agent.pipeline import _make_infer_preferences_node

    # Probe cùng node infer_preferences mà reason() dùng, để Q-value được ghi vào
    # đúng state key thực tế của RL pipeline (không tự thêm activity/resting).
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

    # Dạy user thích 26 độ; heuristic từ 24°C sẽ là 22°C, nên không có false positive.
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

    res = pipeline_bridge.reason(
        message="làm mát phòng bố mẹ",
        conversation_id="conv_pref",
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

    assert res.outcome == "candidate_plan"
    assert res.candidate_plan is not None
    action = res.candidate_plan.actions[0]
    # Kế hoạch phải sử dụng preference 26 độ
    assert action.params.get("temperature") == 26


def test_explicit_user_target_overrides_rl_preference(db_session: Session):
    user = db_session.query(User).first()
    repo = SqlPreferenceRepository(db_session)
    now = datetime(2026, 8, 14, 22, 0, tzinfo=UTC)
    room = "Phòng ngủ bố mẹ"
    live_states = {"dieu_hoa_phong_bo_me": {"power": "off", "temperature": 24}}
    from src.agent.perception.context_builder import build_perception
    from src.agent.pipeline import _make_infer_preferences_node

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

    # Preference 26°C khác heuristic 22°C, nhưng setpoint explicit 25°C vẫn phải thắng.
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

    # User nói rõ "đặt điều hoà phòng bố mẹ 25 độ"
    res = pipeline_bridge.reason(
        message="đặt điều hoà phòng bố mẹ 25 độ",
        conversation_id="conv_explicit",
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

    assert res.outcome == "candidate_plan"
    action = res.candidate_plan.actions[0]
    # Explicit constraint phải thắng RL preference
    assert action.params.get("temperature") == 25
