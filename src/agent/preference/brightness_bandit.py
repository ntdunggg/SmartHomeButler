"""BrightnessPreferenceAgent (spec §29, §31) — bandit cho độ sáng.

Action space rời rạc 0–100% bước 10 (spec §31). V1 MUST hỗ trợ agent này (§29).
"""

from __future__ import annotations

from src.agent.preference.bandit import ContextualBandit

# Spec §31 — Brightness action space.
BRIGHTNESS_ACTIONS = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]


class BrightnessPreferenceAgent(ContextualBandit):
    def __init__(self, *, learning_rate: float | None = None) -> None:
        super().__init__("brightness", BRIGHTNESS_ACTIONS, learning_rate=learning_rate)
