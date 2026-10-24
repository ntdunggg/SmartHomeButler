"""Preference store (spec §28-33) — giữ các bandit + query preference distribution.

Tập hợp TemperaturePreferenceAgent + BrightnessPreferenceAgent (spec §29). Cung cấp:
- `distribution(dimension, state)` → PreferenceDistribution (spec §33) cho planner.
- `record(dimension, state, action, feedback)` → cập nhật Q (spec §32) cho feedback loop.

Bất biến: RL update (ở đây) ĐỘC LẬP với memory update (spec §54). Store này KHÔNG
đụng tới live device state hay safety.
"""

from __future__ import annotations

from src.agent.preference.bandit import ContextualBandit
from src.agent.preference.brightness_bandit import BrightnessPreferenceAgent
from src.agent.preference.reward import FeedbackKind, reward_for
from src.agent.preference.state_encoder import RLState
from src.agent.preference.temperature_bandit import TemperaturePreferenceAgent
from src.agent.schemas import PreferenceDistribution


class PreferenceStore:
    """Bộ các preference bandit theo dimension (spec §29)."""

    def __init__(self, *, learning_rate: float | None = None) -> None:
        self._agents: dict[str, ContextualBandit] = {
            "temperature": TemperaturePreferenceAgent(learning_rate=learning_rate),
            "brightness": BrightnessPreferenceAgent(learning_rate=learning_rate),
        }

    def dimensions(self) -> list[str]:
        return list(self._agents)

    def agent(self, dimension: str) -> ContextualBandit | None:
        return self._agents.get(dimension)

    def distribution(self, dimension: str, state: RLState) -> PreferenceDistribution | None:
        """Preference distribution cho một dimension trong một state (spec §33)."""
        agent = self._agents.get(dimension)
        if agent is None:
            return None
        return agent.distribution(state)

    def record(
        self,
        dimension: str,
        state: RLState,
        action: int,
        feedback: FeedbackKind | str,
    ) -> float | None:
        """Cập nhật Q từ feedback (spec §32, §67). Trả Q mới hoặc None nếu dimension lạ."""
        agent = self._agents.get(dimension)
        if agent is None:
            return None
        return agent.update(state, action, reward_for(feedback))

    # -- Persistence ------------------------------------------------------
    def export(self) -> dict[str, dict]:
        return {dim: agent.export_q() for dim, agent in self._agents.items()}

    def load(self, data: dict[str, dict]) -> None:
        for dim, q in (data or {}).items():
            agent = self._agents.get(dim)
            if agent is not None:
                agent.load_q(q)


_DEFAULT_STORE = PreferenceStore()


def get_default_store() -> PreferenceStore:
    return _DEFAULT_STORE
