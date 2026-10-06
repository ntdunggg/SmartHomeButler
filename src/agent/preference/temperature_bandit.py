"""TemperaturePreferenceAgent (spec §29, §31) — bandit cho nhiệt độ.

Action space rời rạc 20–26°C (spec §31). V1 MUST hỗ trợ agent này (§29).
"""

from __future__ import annotations

from src.agent.preference.bandit import ContextualBandit

# Spec §31 — Temperature action space.
TEMPERATURE_ACTIONS = [20, 21, 22, 23, 24, 25, 26]


class TemperaturePreferenceAgent(ContextualBandit):
    def __init__(self, *, learning_rate: float | None = None) -> None:
        super().__init__("temperature", TEMPERATURE_ACTIONS, learning_rate=learning_rate)
