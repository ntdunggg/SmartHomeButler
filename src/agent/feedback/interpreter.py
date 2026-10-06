"""Feedback interpreter (spec §34, §54) — quan sát feedback → FeedbackKind.

Nguồn feedback (spec §34): explicit accept/reject, manual correction/override, relative
adjustment, repeated unchanged usage. Ở đây chỉ CHUẨN HOÁ tín hiệu quan sát được thành
nhãn; reward number do `reward.py` quyết theo config (LLM không tự sinh, spec §34).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.agent.preference.reward import FeedbackKind


@dataclass(frozen=True, slots=True)
class FeedbackSignal:
    """Tín hiệu feedback đã diễn giải cho một lượt."""

    kind: FeedbackKind
    # Nếu là correction/adjustment: giá trị người dùng chỉnh sang (để RL học đúng action).
    corrected_value: int | None = None
    dimension: str | None = None


def interpret_feedback(
    *,
    accepted: bool | None = None,
    rejected: bool = False,
    corrected_value: int | None = None,
    dimension: str | None = None,
    unchanged: bool = False,
) -> FeedbackSignal:
    """Diễn giải feedback từ tín hiệu có cấu trúc (không đoán từ chuỗi câu).

    Ưu tiên: explicit reject > correction/adjustment > explicit accept > no_correction.
    """
    if rejected:
        return FeedbackSignal(kind=FeedbackKind.EXPLICIT_REJECT, dimension=dimension)
    if corrected_value is not None:
        return FeedbackSignal(
            kind=FeedbackKind.SLIGHT_ADJUSTMENT, corrected_value=corrected_value, dimension=dimension
        )
    if accepted:
        return FeedbackSignal(kind=FeedbackKind.EXPLICIT_ACCEPT, dimension=dimension)
    if unchanged:
        return FeedbackSignal(kind=FeedbackKind.NO_CORRECTION, dimension=dimension)
    return FeedbackSignal(kind=FeedbackKind.NO_CORRECTION, dimension=dimension)
