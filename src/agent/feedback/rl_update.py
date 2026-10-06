"""RL update path (spec §32, §54, §67) — cập nhật preference Q từ feedback.

Độc lập với memory update (spec §54). Cập nhật đúng (dimension, state, action) mà lượt
đã chọn, với reward suy từ FeedbackSignal. Ví dụ §67: chọn 24 nhưng user chỉnh 23 →
reward(24)↓ (slight_adjustment), reward(23)↑ (nếu sau đó chấp nhận).

RL KHÔNG override safety/authorization (spec §P6, invariant 4) — nó chỉ đụng Q-table.
"""

from __future__ import annotations

from src.agent.feedback.interpreter import FeedbackSignal
from src.agent.preference.preference_store import PreferenceStore
from src.agent.preference.state_encoder import RLState


def apply_feedback(
    store: PreferenceStore,
    *,
    dimension: str,
    state: RLState,
    chosen_action: int,
    feedback: FeedbackSignal,
) -> dict[str, float | None]:
    """Cập nhật Q cho lượt. Nếu là correction, phạt action đã chọn và thưởng giá trị mới.

    Trả {chosen: q_moi, corrected: q_moi_or_None} để log/kiểm chứng.
    """
    q_chosen = store.record(dimension, state, chosen_action, feedback.kind)
    q_corrected: float | None = None
    if feedback.corrected_value is not None and feedback.corrected_value != chosen_action:
        # Người dùng chỉnh sang giá trị khác = tín hiệu ưa thích giá trị đó → thưởng.
        from src.agent.preference.reward import FeedbackKind

        q_corrected = store.record(dimension, state, feedback.corrected_value, FeedbackKind.EXPLICIT_ACCEPT)
    return {"chosen": q_chosen, "corrected": q_corrected}
