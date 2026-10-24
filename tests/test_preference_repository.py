"""Test Preference Repository (FR-08) — InMemory & SqlPreferenceRepository."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.agent.preference.repository import (
    InMemoryPreferenceRepository,
    SqlPreferenceRepository,
)
from src.agent.preference.reward import FeedbackKind, reward_for
from src.agent.preference.state_encoder import RLState, encode_state
from src.domain.models import Base, Household, User


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    # Tạo sample household & users
    h1 = Household(name="Nhà 1")
    session.add(h1)
    session.flush()

    u1 = User(household_id=h1.id, username="user_a", password_hash="hash", role="owner")
    u2 = User(household_id=h1.id, username="user_b", password_hash="hash", role="member")
    session.add_all([u1, u2])
    session.commit()

    yield session
    session.close()


def _state() -> RLState:
    return encode_state(
        resident="user_a",
        room="bedroom",
        now=datetime(2026, 8, 14, 22, 30, tzinfo=UTC),
        activity="resting",
        outside_temp_c=15,
        power_mode="NORMAL",
    )


def test_in_memory_preference_repository_update_and_distribution():
    repo = InMemoryPreferenceRepository(learning_rate=0.3)
    s = _state()

    # Chưa học gì -> confidence = 0.0, phân phối đều
    dist = repo.distribution(household_id=1, user_id=1, dimension="temperature", state=s)
    assert dist is not None
    assert dist.confidence == 0.0

    # Học: 24 -> phạt, 23 -> thưởng
    repo.update(
        household_id=1,
        user_id=1,
        dimension="temperature",
        state=s,
        action=24,
        reward=reward_for(FeedbackKind.SLIGHT_ADJUSTMENT),
    )
    for _ in range(4):
        repo.update(
            household_id=1,
            user_id=1,
            dimension="temperature",
            state=s,
            action=23,
            reward=reward_for(FeedbackKind.EXPLICIT_ACCEPT),
        )

    q23 = repo.get_q(household_id=1, user_id=1, dimension="temperature", state_key=s.key(), action=23)
    q24 = repo.get_q(household_id=1, user_id=1, dimension="temperature", state_key=s.key(), action=24)
    assert q23 > q24

    dist2 = repo.distribution(household_id=1, user_id=1, dimension="temperature", state=s)
    assert dist2 is not None
    assert dist2.confidence > 0.0
    top = dist2.top()
    assert top is not None and top[0] == "23"


def test_sql_preference_repository_persistence_and_isolation(db_session: Session):
    repo = SqlPreferenceRepository(db_session, learning_rate=0.3)
    s = _state()

    # User 1 học 23
    repo.update(
        household_id=1,
        user_id=1,
        dimension="temperature",
        state=s,
        action=23,
        reward=reward_for(FeedbackKind.EXPLICIT_ACCEPT),
    )

    # User 2 học 25
    repo.update(
        household_id=1,
        user_id=2,
        dimension="temperature",
        state=s,
        action=25,
        reward=reward_for(FeedbackKind.EXPLICIT_ACCEPT),
    )

    db_session.commit()

    # Xác minh cô lập tenant / user
    q_u1_23 = repo.get_q(household_id=1, user_id=1, dimension="temperature", state_key=s.key(), action=23)
    q_u1_25 = repo.get_q(household_id=1, user_id=1, dimension="temperature", state_key=s.key(), action=25)
    q_u2_23 = repo.get_q(household_id=1, user_id=2, dimension="temperature", state_key=s.key(), action=23)
    q_u2_25 = repo.get_q(household_id=1, user_id=2, dimension="temperature", state_key=s.key(), action=25)

    assert q_u1_23 > 0.0
    assert q_u1_25 == 0.0
    assert q_u2_23 == 0.0
    assert q_u2_25 > 0.0

    dist_u1 = repo.distribution(household_id=1, user_id=1, dimension="temperature", state=s)
    dist_u2 = repo.distribution(household_id=1, user_id=2, dimension="temperature", state=s)

    assert dist_u1.top()[0] == "23"
    assert dist_u2.top()[0] == "25"


def test_action_outside_bounds_ignored():
    repo = InMemoryPreferenceRepository()
    s = _state()

    # 99 ngoài action space nhiệt độ (20..26)
    q = repo.update(household_id=1, user_id=1, dimension="temperature", state=s, action=99, reward=1.0)
    assert q == 0.0
    assert repo.get_q(household_id=1, user_id=1, dimension="temperature", state_key=s.key(), action=99) == 0.0
