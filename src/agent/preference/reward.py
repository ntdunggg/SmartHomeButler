"""Reward model (spec §34) — mapping feedback → reward, deterministic/configurable.

LLM KHÔNG tự sinh arbitrary reward number (spec §34). Reward đọc từ
``config/energy.yaml`` (`reward:`). Feedback loại "relative adjustment"/"manual
correction" ánh xạ về slight_adjustment; "explicit reject" về giá trị âm mạnh.
"""

from __future__ import annotations

from enum import StrEnum

from src.agent.config import get_energy_config


class FeedbackKind(StrEnum):
    """Các loại feedback quan sát được (spec §34)."""

    EXPLICIT_ACCEPT = "explicit_accept"
    NO_CORRECTION = "no_correction"
    SLIGHT_ADJUSTMENT = "slight_adjustment"
    EXPLICIT_REJECT = "explicit_reject"


# Feedback thô (spec §34) → khoá reward config.
_FEEDBACK_ALIASES: dict[str, FeedbackKind] = {
    "explicit acceptance": FeedbackKind.EXPLICIT_ACCEPT,
    "accept": FeedbackKind.EXPLICIT_ACCEPT,
    "repeated unchanged usage": FeedbackKind.NO_CORRECTION,
    "no_correction": FeedbackKind.NO_CORRECTION,
    "manual correction": FeedbackKind.SLIGHT_ADJUSTMENT,
    "relative adjustment": FeedbackKind.SLIGHT_ADJUSTMENT,
    "manual override": FeedbackKind.SLIGHT_ADJUSTMENT,
    "explicit rejection": FeedbackKind.EXPLICIT_REJECT,
    "reject": FeedbackKind.EXPLICIT_REJECT,
}


def normalize_feedback(raw: str) -> FeedbackKind:
    """Chuẩn hoá nhãn feedback thô về FeedbackKind (mặc định NO_CORRECTION)."""
    token = (raw or "").strip().lower()
    if token in _FEEDBACK_ALIASES:
        return _FEEDBACK_ALIASES[token]
    try:
        return FeedbackKind(token)
    except ValueError:
        return FeedbackKind.NO_CORRECTION


def reward_for(feedback: FeedbackKind | str) -> float:
    """Reward số cho một loại feedback, đọc từ config (spec §34)."""
    kind = feedback if isinstance(feedback, FeedbackKind) else normalize_feedback(feedback)
    table = get_energy_config()["reward"]
    return float(table.get(kind.value, 0.0))
