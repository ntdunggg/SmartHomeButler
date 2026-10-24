"""Feedback Service (FR-15 & FR-08) — Xử lý feedback production độc lập 2 nhánh Memory và RL.

Bảo đảm:
1. Idempotency qua (household_id, user_id, idempotency_key).
2. Provenance từ LearningDecision (ngữ cảnh và action tại thời điểm thực thi thật).
3. Độc lập hoàn toàn giữa MemoryProcessor và RLProcessor qua Savepoints (begin_nested):
   lỗi một nhánh không gây rollback hoặc commit sai nhánh kia.
4. Credit assignment tất định, không dùng LLM để sinh reward.
5. Hỗ trợ retry từng nhánh thất bại với attempt_count và state machine rõ ràng.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.agent.preference.repository import ACTION_SPACES, SqlPreferenceRepository
from src.agent.preference.reward import FeedbackKind, reward_for
from src.agent.preference.state_encoder import RLState
from src.config import get_settings
from src.domain.models import EpisodicMemory, LearningDecision, LearningFeedback, User
from src.models.schemas import FeedbackIn, FeedbackOut

logger = logging.getLogger("services.feedback")

# Thiết bị an ninh không học preference
_SECURITY_DEVICE_TYPES = frozenset({"lock", "door_lock", "camera", "alarm", "security_system"})


def _extract_dimension_and_action(
    decision: LearningDecision,
    dimension: str | None,
) -> tuple[str | None, int | None, bool]:
    """Tìm dimension và giá trị action đã thực thi từ LearningDecision.

    Trả về (dimension, executed_action, is_security).
    Bảo đảm không nuốt giá trị 0 (ví dụ brightness=0).
    """
    actions = decision.executed_actions or []
    if not actions:
        return dimension, None, False

    # Kiểm tra action an ninh
    for a in actions:
        if a.get("risk_level") == "security" or any(sec in (a.get("device_slug") or "") for sec in ("lock", "door", "alarm")):
            return dimension, None, True

    # Tìm action phù hợp dimension
    matching_actions = []
    for a in actions:
        params = a.get("params") or {}
        act_name = a.get("action", "")

        # Nhiệt độ: AC / Heater
        if "temperature" in params or "temp" in params:
            val = params.get("temperature") if params.get("temperature") is not None else params.get("temp")
            if isinstance(val, int | float) and not isinstance(val, bool):
                matching_actions.append(("temperature", int(val)))
        elif act_name in ("set_temperature", "set_temp") and "value" in params:
            val = params.get("value")
            if isinstance(val, int | float) and not isinstance(val, bool):
                matching_actions.append(("temperature", int(val)))

        # Độ sáng: Light (Kiểm tra `is not None` để không nuốt 0)
        elif any(k in params for k in ("brightness", "percent", "level")):
            val = (
                params.get("brightness")
                if params.get("brightness") is not None
                else (params.get("percent") if params.get("percent") is not None else params.get("level"))
            )
            if isinstance(val, int | float) and not isinstance(val, bool):
                matching_actions.append(("brightness", int(val)))
        elif act_name in ("set_brightness",) and "value" in params:
            val = params.get("value")
            if isinstance(val, int | float) and not isinstance(val, bool):
                matching_actions.append(("brightness", int(val)))

    if dimension is not None:
        for dim, val in matching_actions:
            if dim == dimension:
                return dim, val, False
        return dimension, None, False

    # Nếu không chỉ định dimension nhưng có đúng 1 action số
    if len(matching_actions) == 1:
        return matching_actions[0][0], matching_actions[0][1], False

    return None, None, False


class MemoryProcessor:
    """Xử lý nhánh cập nhật EpisodicMemory và quy tụ ResidentProfile."""

    @staticmethod
    def process(
        session: Session,
        *,
        feedback: LearningFeedback,
        decision: LearningDecision,
    ) -> tuple[str, dict[str, Any] | None]:
        """Thực thi cập nhật memory. Trả về (status, error_dict)."""
        from src.memory import store

        if not decision.episode_id:
            return "skipped", {"reason": "No targeted episode_id linked to decision"}

        episode = session.scalar(
            select(EpisodicMemory).where(
                EpisodicMemory.id == decision.episode_id,
                EpisodicMemory.household_id == feedback.household_id,
            )
        )

        if episode is None:
            return "skipped", {"reason": f"Targeted episode_id {decision.episode_id} not found"}

        store.update_outcome(
            session,
            episode.id,
            outcome=feedback.kind,
            correction_note=f"Corrected: {feedback.corrected_value}" if feedback.corrected_value is not None else "",
        )
        store.consolidate(session, household_id=feedback.household_id, now=datetime.now(UTC))
        session.flush()

        return "succeeded", None


class RLProcessor:
    """Xử lý nhánh cập nhật Q-values qua SqlPreferenceRepository (FR-08)."""

    @staticmethod
    def process(
        session: Session,
        *,
        feedback: LearningFeedback,
        decision: LearningDecision,
    ) -> tuple[str, dict[str, Any] | None]:
        """Thực thi cập nhật RL preference. Trả về (status, error_dict)."""
        if not get_settings().learning_v2_write:
            return "skipped", {"reason": "learning_v2_write is disabled"}

        dim, executed_action, is_security = _extract_dimension_and_action(decision, feedback.dimension)

        if is_security:
            return "skipped", {"reason": "Security actions do not update RL preferences"}

        if dim is None:
            return "skipped", {"reason": "No single unambiguous dimension found for RL update"}

        action_space = ACTION_SPACES.get(dim)
        if not action_space:
            return "skipped", {"reason": f"Unsupported dimension: {dim}"}

        # Kiểm tra giá trị sửa đổi
        if feedback.kind == "corrected":
            if feedback.corrected_value is None:
                raise ValueError("Correction requires corrected_value")
            if feedback.corrected_value not in action_space:
                raise ValueError(f"Corrected value {feedback.corrected_value} outside action space {action_space}")

        rl_state_dict = decision.rl_state or {}
        state = RLState.from_dict(rl_state_dict)
        repo = SqlPreferenceRepository(session)

        # Credit assignment tất định (FR08-03)
        if feedback.kind in ("accepted", "helpful"):
            if executed_action is not None and executed_action in action_space:
                reward = reward_for(FeedbackKind.EXPLICIT_ACCEPT)
                repo.update(
                    household_id=feedback.household_id,
                    user_id=feedback.user_id,
                    dimension=dim,
                    state=state,
                    action=executed_action,
                    reward=reward,
                    feedback_id=feedback.id,
                )
        elif feedback.kind in ("rejected", "unhelpful"):
            if executed_action is not None and executed_action in action_space:
                reward = reward_for(FeedbackKind.EXPLICIT_REJECT)
                repo.update(
                    household_id=feedback.household_id,
                    user_id=feedback.user_id,
                    dimension=dim,
                    state=state,
                    action=executed_action,
                    reward=reward,
                    feedback_id=feedback.id,
                )
        elif feedback.kind == "corrected":
            # Phạt action cũ (slight_adjustment) nếu có
            if executed_action is not None and executed_action in action_space and executed_action != feedback.corrected_value:
                penalize_reward = reward_for(FeedbackKind.SLIGHT_ADJUSTMENT)
                repo.update(
                    household_id=feedback.household_id,
                    user_id=feedback.user_id,
                    dimension=dim,
                    state=state,
                    action=executed_action,
                    reward=penalize_reward,
                    feedback_id=feedback.id,
                )
            # Thưởng action mới (explicit_accept)
            reward = reward_for(FeedbackKind.EXPLICIT_ACCEPT)
            repo.update(
                household_id=feedback.household_id,
                user_id=feedback.user_id,
                dimension=dim,
                state=state,
                action=feedback.corrected_value,  # type: ignore[arg-type]
                reward=reward,
                feedback_id=feedback.id,
            )
        elif feedback.kind == "no_correction":
            if executed_action is not None and executed_action in action_space:
                reward = reward_for(FeedbackKind.NO_CORRECTION)
                repo.update(
                    household_id=feedback.household_id,
                    user_id=feedback.user_id,
                    dimension=dim,
                    state=state,
                    action=executed_action,
                    reward=reward,
                    feedback_id=feedback.id,
                )

        session.flush()
        return "succeeded", None


class FeedbackService:
    """Orchestrator chính cho Feedback Loop (FR-15 & FR-08)."""

    @classmethod
    def handle_feedback(
        cls,
        session: Session,
        *,
        user: User,
        payload: FeedbackIn,
        idempotency_key: str,
    ) -> FeedbackOut:
        """Tiếp nhận và xử lý feedback với idempotency, ownership check và savepoint isolation."""
        # 1. Tìm LearningDecision kèm kiểm tra ownership (Finding 5)
        decision = session.scalar(
            select(LearningDecision).where(
                LearningDecision.execution_id == payload.execution_id,
                LearningDecision.household_id == user.household_id,
                LearningDecision.user_id == user.id,
            )
        )
        if decision is None:
            raise ValueError(f"Không tìm thấy quyết định thực thi hợp lệ cho user với execution_id: {payload.execution_id}")

        # Validate corrected_value against action space upfront (Finding 3)
        target_dim, executed_action, is_security = _extract_dimension_and_action(decision, payload.dimension)
        if payload.outcome == "corrected":
            if is_security:
                raise ValueError("Không hỗ trợ hiệu chỉnh cho các hành động thuộc nhóm an ninh.")
            if target_dim is None:
                raise ValueError(f"Không thể xác định dimension tương ứng cho execution_id: {payload.execution_id}")
            action_space = ACTION_SPACES.get(target_dim)
            if not action_space or payload.corrected_value not in action_space:
                raise ValueError(
                    f"Giá trị hiệu chỉnh {payload.corrected_value} không nằm trong miền giá trị hợp lệ {action_space} của dimension '{target_dim}'."
                )

        # 2. Kiểm tra Idempotency / Retry
        existing = session.scalar(
            select(LearningFeedback).where(
                LearningFeedback.household_id == user.household_id,
                LearningFeedback.user_id == user.id,
                LearningFeedback.idempotency_key == idempotency_key,
            )
        )

        if existing is not None:
            # Nếu cả hai nhánh đều đã kết thúc thành công hoặc bỏ qua hợp lệ -> Trả về duplicate
            if existing.memory_status in ("succeeded", "skipped") and existing.rl_status in ("succeeded", "skipped"):
                return FeedbackOut(
                    feedback_id=existing.id,
                    duplicate=True,
                    memory_status=existing.memory_status,
                    rl_status=existing.rl_status,
                    memory_attempt_count=existing.memory_attempt_count,
                    rl_attempt_count=existing.rl_attempt_count,
                    last_error=existing.last_error,
                )
            receipt = existing
            is_duplicate = True
        else:
            # Tạo receipt mới
            feedback_id = f"fb_{uuid.uuid4().hex[:16]}"
            receipt = LearningFeedback(
                id=feedback_id,
                idempotency_key=idempotency_key,
                execution_id=payload.execution_id,
                household_id=user.household_id,
                user_id=user.id,
                kind=payload.outcome,
                dimension=payload.dimension,
                corrected_value=payload.corrected_value,
                memory_status="pending",
                rl_status="pending",
                memory_attempt_count=0,
                rl_attempt_count=0,
                error_details={},
                created_at=datetime.now(UTC),
            )
            session.add(receipt)
            try:
                session.flush()
                is_duplicate = False
            except Exception:  # noqa: BLE001
                session.rollback()
                decision = session.scalar(
                    select(LearningDecision).where(
                        LearningDecision.execution_id == payload.execution_id,
                        LearningDecision.household_id == user.household_id,
                        LearningDecision.user_id == user.id,
                    )
                )
                assert decision is not None  # cùng query đã validate tồn tại ở line 258; rollback chỉ expire ORM
                existing = session.scalar(
                    select(LearningFeedback).where(
                        LearningFeedback.household_id == user.household_id,
                        LearningFeedback.user_id == user.id,
                        LearningFeedback.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None:
                    if existing.memory_status in ("succeeded", "skipped") and existing.rl_status in ("succeeded", "skipped"):
                        return FeedbackOut(
                            feedback_id=existing.id,
                            duplicate=True,
                            memory_status=existing.memory_status,
                            rl_status=existing.rl_status,
                            memory_attempt_count=existing.memory_attempt_count,
                            rl_attempt_count=existing.rl_attempt_count,
                            last_error=existing.last_error,
                        )
                    receipt = existing
                    is_duplicate = True
                else:
                    raise

        error_details = dict(receipt.error_details or {})

        # 3. Chạy Memory Processor trong Savepoint độc lập (Finding 2)
        if receipt.memory_status not in ("succeeded", "skipped"):
            receipt.memory_attempt_count += 1
            try:
                with session.begin_nested():
                    mem_status, mem_err = MemoryProcessor.process(session, feedback=receipt, decision=decision)
                    receipt.memory_status = mem_status
                    if mem_err:
                        error_details.update(mem_err)
            except Exception as exc:  # noqa: BLE001
                receipt.memory_status = "failed"
                receipt.last_error = f"Memory error: {exc}"
                error_details["memory_error"] = str(exc)

        # 4. Chạy RL Processor trong Savepoint độc lập (Finding 2)
        if receipt.rl_status not in ("succeeded", "skipped"):
            receipt.rl_attempt_count += 1
            try:
                with session.begin_nested():
                    rl_status, rl_err = RLProcessor.process(session, feedback=receipt, decision=decision)
                    receipt.rl_status = rl_status
                    if rl_err:
                        error_details.update(rl_err)
            except Exception as exc:  # noqa: BLE001
                receipt.rl_status = "failed"
                receipt.last_error = f"RL error: {exc}"
                error_details["rl_error"] = str(exc)

        receipt.error_details = error_details if error_details else None
        receipt.updated_at = datetime.now(UTC)
        session.commit()

        return FeedbackOut(
            feedback_id=receipt.id,
            duplicate=is_duplicate,
            memory_status=receipt.memory_status,
            rl_status=receipt.rl_status,
            memory_attempt_count=receipt.memory_attempt_count,
            rl_attempt_count=receipt.rl_attempt_count,
            last_error=receipt.last_error,
        )
