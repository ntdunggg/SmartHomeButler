"""Preference Repository (FR-08) — Giao diện và các cài đặt kho chứa Q-value bền vững.

Hỗ trợ:
- InMemoryPreferenceRepository: Dành cho unit test / isolated testing.
- SqlPreferenceRepository: Dành cho production (lưu bảng preference_q_values).

Bảo đảm:
- Cô lập dữ liệu theo (household_id, user_id).
- Cập nhật Q atomic: Q_new = Q_old + alpha * (reward - Q_old).
- Tính toán softmax ra PreferenceDistribution với confidence calibration.
"""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.agent.config import get_energy_config
from src.agent.preference.brightness_bandit import BRIGHTNESS_ACTIONS
from src.agent.preference.state_encoder import RLState
from src.agent.preference.temperature_bandit import TEMPERATURE_ACTIONS
from src.agent.schemas import PreferenceDistribution
from src.domain.models import PreferenceQValue

logger = logging.getLogger("preference.repository")

# Mapping dimension -> action space rời rạc
ACTION_SPACES: dict[str, list[int]] = {
    "temperature": list(TEMPERATURE_ACTIONS),
    "brightness": list(BRIGHTNESS_ACTIONS),
}


def compute_distribution(
    dimension: str,
    state_key: str,
    q_row: dict[int, float],
    action_space: list[int],
    *,
    temperature: float = 1.0,
) -> PreferenceDistribution:
    """Softmax(Q) trên action space -> PreferenceDistribution (spec §33).

    State chưa từng thấy (mọi Q = 0) -> phân phối đều, confidence = 0.0.
    """
    temp = max(1e-6, temperature)
    row_values = [q_row.get(a, 0.0) for a in action_space]
    max_q = max(row_values) if row_values else 0.0
    exps = {a: math.exp((q_row.get(a, 0.0) - max_q) / temp) for a in action_space}
    total = sum(exps.values()) or 1.0
    dist = {str(a): exps[a] / total for a in action_space}

    non_zero = any(v != 0.0 for v in row_values)
    spread = (max(row_values) - min(row_values)) if non_zero else 0.0
    confidence = 0.0 if not non_zero else min(1.0, spread)

    return PreferenceDistribution(
        dimension=dimension,
        context={"state": state_key},
        distribution=dist,
        confidence=confidence,
    )


class PreferenceRepository(ABC):
    """Interface trừu tượng cho Preference Repository (FR-08)."""

    @abstractmethod
    def distribution(
        self,
        *,
        household_id: int,
        user_id: int | None,
        dimension: str,
        state: RLState,
        temperature: float = 1.0,
    ) -> PreferenceDistribution | None:
        """Lấy PreferenceDistribution cho một chiều trong trạng thái context."""
        ...

    @abstractmethod
    def update(
        self,
        *,
        household_id: int,
        user_id: int | None,
        dimension: str,
        state: RLState,
        action: int,
        reward: float,
        feedback_id: str = "",
    ) -> float:
        """Cập nhật Q-value theo công thức: Q_new = Q_old + alpha * (reward - Q_old). Trả về Q_new."""
        ...

    @abstractmethod
    def get_q(
        self,
        *,
        household_id: int,
        user_id: int | None,
        dimension: str,
        state_key: str,
        action: int,
    ) -> float:
        """Lấy giá trị Q đơn lẻ."""
        ...

    @abstractmethod
    def get_q_row(
        self,
        *,
        household_id: int,
        user_id: int | None,
        dimension: str,
        state_key: str,
    ) -> dict[int, float]:
        """Lấy hàng Q-values cho toàn bộ action space."""
        ...


class InMemoryPreferenceRepository(PreferenceRepository):
    """Bản cài đặt in-memory cho Unit Testing / Mocking."""

    def __init__(self, *, learning_rate: float | None = None) -> None:
        self._alpha = (
            learning_rate if learning_rate is not None else float(get_energy_config()["rl"]["learning_rate"])
        )
        # key: (household_id, user_id, dimension, state_key) -> {action: q_value}
        self._store: dict[tuple[int, int | None, str, str], dict[int, float]] = {}

    def get_q_row(
        self,
        *,
        household_id: int,
        user_id: int | None,
        dimension: str,
        state_key: str,
    ) -> dict[int, float]:
        action_space = ACTION_SPACES.get(dimension, [])
        row = self._store.get((household_id, user_id, dimension, state_key), {})
        return {a: row.get(a, 0.0) for a in action_space}

    def get_q(
        self,
        *,
        household_id: int,
        user_id: int | None,
        dimension: str,
        state_key: str,
        action: int,
    ) -> float:
        return self._store.get((household_id, user_id, dimension, state_key), {}).get(action, 0.0)

    def distribution(
        self,
        *,
        household_id: int,
        user_id: int | None,
        dimension: str,
        state: RLState,
        temperature: float = 1.0,
    ) -> PreferenceDistribution | None:
        action_space = ACTION_SPACES.get(dimension)
        if action_space is None:
            return None
        state_key = state.key()
        row = self.get_q_row(
            household_id=household_id, user_id=user_id, dimension=dimension, state_key=state_key
        )
        return compute_distribution(dimension, state_key, row, action_space, temperature=temperature)

    def update(
        self,
        *,
        household_id: int,
        user_id: int | None,
        dimension: str,
        state: RLState,
        action: int,
        reward: float,
        feedback_id: str = "",
    ) -> float:
        action_space = ACTION_SPACES.get(dimension, [])
        state_key = state.key()
        if action not in action_space:
            return self.get_q(
                household_id=household_id,
                user_id=user_id,
                dimension=dimension,
                state_key=state_key,
                action=action,
            )

        key = (household_id, user_id, dimension, state_key)
        row = self._store.setdefault(key, {})
        old_q = row.get(action, 0.0)
        new_q = old_q + self._alpha * (reward - old_q)
        row[action] = new_q
        return new_q


class SqlPreferenceRepository(PreferenceRepository):
    """Bản cài đặt bền vững dựa trên SQLAlchemy & database bảng `preference_q_values` (FR-08)."""

    def __init__(self, session: Session, *, learning_rate: float | None = None) -> None:
        self.session = session
        self._alpha = (
            learning_rate if learning_rate is not None else float(get_energy_config()["rl"]["learning_rate"])
        )

    def get_q_row(
        self,
        *,
        household_id: int,
        user_id: int | None,
        dimension: str,
        state_key: str,
    ) -> dict[int, float]:
        action_space = ACTION_SPACES.get(dimension, [])
        query = select(PreferenceQValue).where(
            PreferenceQValue.household_id == household_id,
            PreferenceQValue.user_id == user_id,
            PreferenceQValue.dimension == dimension,
            PreferenceQValue.state_key == state_key,
        )
        records = self.session.scalars(query).all()
        q_map = {r.action: r.q_value for r in records}
        return {a: q_map.get(a, 0.0) for a in action_space}

    def get_q(
        self,
        *,
        household_id: int,
        user_id: int | None,
        dimension: str,
        state_key: str,
        action: int,
    ) -> float:
        record = self.session.scalar(
            select(PreferenceQValue).where(
                PreferenceQValue.household_id == household_id,
                PreferenceQValue.user_id == user_id,
                PreferenceQValue.dimension == dimension,
                PreferenceQValue.state_key == state_key,
                PreferenceQValue.action == action,
            )
        )
        return record.q_value if record is not None else 0.0

    def distribution(
        self,
        *,
        household_id: int,
        user_id: int | None,
        dimension: str,
        state: RLState,
        temperature: float = 1.0,
    ) -> PreferenceDistribution | None:
        action_space = ACTION_SPACES.get(dimension)
        if action_space is None:
            return None
        state_key = state.key()
        row = self.get_q_row(
            household_id=household_id, user_id=user_id, dimension=dimension, state_key=state_key
        )
        return compute_distribution(dimension, state_key, row, action_space, temperature=temperature)

    def update(
        self,
        *,
        household_id: int,
        user_id: int | None,
        dimension: str,
        state: RLState,
        action: int,
        reward: float,
        feedback_id: str = "",
    ) -> float:
        action_space = ACTION_SPACES.get(dimension, [])
        state_key = state.key()
        if action not in action_space:
            return self.get_q(
                household_id=household_id,
                user_id=user_id,
                dimension=dimension,
                state_key=state_key,
                action=action,
            )

        record = self.session.scalar(
            select(PreferenceQValue).where(
                PreferenceQValue.household_id == household_id,
                PreferenceQValue.user_id == user_id,
                PreferenceQValue.dimension == dimension,
                PreferenceQValue.state_key == state_key,
                PreferenceQValue.action == action,
            )
        )

        now = datetime.now(UTC)
        if record is None:
            old_q = 0.0
            new_q = old_q + self._alpha * (reward - old_q)
            record = PreferenceQValue(
                household_id=household_id,
                user_id=user_id,
                dimension=dimension,
                state_key=state_key,
                action=action,
                q_value=new_q,
                update_count=1,
                version=1,
                updated_at=now,
            )
            self.session.add(record)
            try:
                self.session.flush()
            except Exception:  # noqa: BLE001
                # Xung đột insert đồng thời: query lại bản ghi vừa được tạo bởi thread khác
                self.session.rollback()
                record = self.session.scalar(
                    select(PreferenceQValue).where(
                        PreferenceQValue.household_id == household_id,
                        PreferenceQValue.user_id == user_id,
                        PreferenceQValue.dimension == dimension,
                        PreferenceQValue.state_key == state_key,
                        PreferenceQValue.action == action,
                    )
                )
                if record is not None:
                    old_q = record.q_value
                    new_q = old_q + self._alpha * (reward - old_q)
                    record.q_value = new_q
                    record.update_count += 1
                    record.version += 1
                    record.updated_at = now
                    self.session.flush()
        else:
            old_q = record.q_value
            new_q = old_q + self._alpha * (reward - old_q)
            record.q_value = new_q
            record.update_count += 1
            record.version += 1
            record.updated_at = now
            self.session.flush()

        logger.debug(
            "Cập nhật Q(%s, %s, %s, %s, %s): %.3f -> %.3f (alpha=%.2f, reward=%.2f, fb=%s)",
            household_id,
            user_id,
            dimension,
            state_key,
            action,
            old_q,
            new_q,
            self._alpha,
            reward,
            feedback_id,
        )
        return new_q
