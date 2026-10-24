"""Contextual bandit / tabular Q-learning base (spec §31-33).

Update rule (spec §32): Q(s,a) ← Q(s,a) + α·[r − Q(s,a)].
Q-values → PreferenceDistribution qua softmax (spec §33). Planner KHÔNG coi top-1 là
chân lý tuyệt đối — nó nhận cả phân phối.

Bất biến (spec §P6, invariant 4): RL CHỈ học preference. Nó không quyết định safety,
authorization hay device limit — action space là các giá trị hợp lệ đã cho sẵn.
"""

from __future__ import annotations

import math

from src.agent.config import get_energy_config
from src.agent.preference.state_encoder import RLState
from src.agent.schemas import PreferenceDistribution


class ContextualBandit:
    """Q-table theo (state_key, action). Action space rời rạc, cố định (spec §31)."""

    def __init__(self, dimension: str, action_space: list[int], *, learning_rate: float | None = None) -> None:
        self.dimension = dimension
        self.action_space = list(action_space)
        self._alpha = learning_rate if learning_rate is not None else float(get_energy_config()["rl"]["learning_rate"])
        # {state_key: {action: q_value}}
        self._q: dict[str, dict[int, float]] = {}

    # -- Q access ---------------------------------------------------------
    def q(self, state_key: str, action: int) -> float:
        return self._q.get(state_key, {}).get(action, 0.0)

    def q_row(self, state_key: str) -> dict[int, float]:
        """Q của mọi action trong action_space cho một state (thiếu → 0.0)."""
        row = self._q.get(state_key, {})
        return {a: row.get(a, 0.0) for a in self.action_space}

    # -- Update (spec §32) ------------------------------------------------
    def update(self, state: RLState, action: int, reward: float) -> float:
        """Q(s,a) ← Q(s,a) + α·[r − Q(s,a)]. Trả Q mới. Action ngoài space → bỏ qua."""
        if action not in self.action_space:
            return self.q(state.key(), action)
        key = state.key()
        row = self._q.setdefault(key, {})
        old = row.get(action, 0.0)
        new = old + self._alpha * (reward - old)
        row[action] = new
        return new

    # -- Distribution (spec §33) -----------------------------------------
    def distribution(self, state: RLState, *, temperature: float = 1.0) -> PreferenceDistribution:
        """Softmax(Q) trên action space → PreferenceDistribution (spec §33).

        State chưa từng thấy (mọi Q = 0) → phân phối đều, confidence = 0.0 (chưa học gì).
        """
        key = state.key()
        row = self.q_row(key)
        temp = max(1e-6, temperature)
        max_q = max(row.values()) if row else 0.0
        exps = {a: math.exp((q - max_q) / temp) for a, q in row.items()}
        total = sum(exps.values()) or 1.0
        dist = {str(a): exps[a] / total for a in self.action_space}

        seen = self._q.get(key, {})
        spread = (max(row.values()) - min(row.values())) if row else 0.0
        confidence = 0.0 if not seen else min(1.0, spread)

        return PreferenceDistribution(
            dimension=self.dimension,
            context={"state": key},
            distribution=dist,
            confidence=confidence,
        )

    # -- Persistence hooks ------------------------------------------------
    def export_q(self) -> dict[str, dict[str, float]]:
        return {sk: {str(a): v for a, v in row.items()} for sk, row in self._q.items()}

    def load_q(self, data: dict[str, dict[str, float]]) -> None:
        self._q = {sk: {int(a): float(v) for a, v in row.items()} for sk, row in (data or {}).items()}
