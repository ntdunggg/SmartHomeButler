"""Compatibility facade over the shared deterministic action registry."""

from __future__ import annotations

from src.domain.action_registry import (
    matches_expected_state as _matches_expected_state,
)
from src.domain.action_registry import (
    resolve_delta as _resolve_delta,
)
from src.domain.enums import DeviceType


def resolve_delta(
    action: str,
    params: dict,
    current: dict,
    *,
    device_type: DeviceType | str | None = None,
) -> dict:
    return _resolve_delta(action, params, current, device_type=device_type)


def matches_expected_state(action: str, params: dict, current: dict) -> bool:
    return _matches_expected_state(action, params, current)
