"""Preference RL (spec §28-34, §67) — Q-update + preference distribution."""

from __future__ import annotations

from datetime import UTC, datetime

from src.agent.preference.preference_store import PreferenceStore
from src.agent.preference.reward import FeedbackKind, reward_for
from src.agent.preference.state_encoder import RLState, encode_state


def _state() -> RLState:
    return encode_state(
        resident="user_A",
        room="bedroom",
        now=datetime(2026, 8, 14, 22, 30, tzinfo=UTC),
        activity="resting",
        outside_temp_c=15,
        power_mode="NORMAL",
    )


def test_reward_from_config():
    assert reward_for(FeedbackKind.EXPLICIT_ACCEPT) == 1.0
    assert reward_for(FeedbackKind.EXPLICIT_REJECT) == -1.0
    assert reward_for("explicit acceptance") == 1.0  # alias


def test_correction_shifts_distribution_toward_new_value():
    """Spec §67: chọn 24 nhưng user chỉnh 23 → reward(24)↓, reward(23)↑."""
    store = PreferenceStore(learning_rate=0.3)
    s = _state()
    store.record("temperature", s, 24, FeedbackKind.SLIGHT_ADJUSTMENT)
    for _ in range(4):
        store.record("temperature", s, 23, FeedbackKind.EXPLICIT_ACCEPT)
    agent = store.agent("temperature")
    assert agent.q(s.key(), 23) > agent.q(s.key(), 24)
    top = store.distribution("temperature", s).top()
    assert top is not None and top[0] == "23"


def test_unseen_state_uniform_zero_confidence():
    store = PreferenceStore()
    dist = store.distribution("temperature", RLState(resident="nobody"))
    assert dist.confidence == 0.0
    probs = list(dist.distribution.values())
    assert abs(probs[0] - probs[-1]) < 1e-9  # đều nhau


def test_action_outside_space_ignored():
    store = PreferenceStore()
    s = _state()
    # 99°C không thuộc action space temperature → update bỏ qua.
    store.record("temperature", s, 99, FeedbackKind.EXPLICIT_ACCEPT)
    assert store.agent("temperature").q(s.key(), 99) == 0.0
