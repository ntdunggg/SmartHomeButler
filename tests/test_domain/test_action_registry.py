"""Contract tests for the single deterministic action registry."""

from __future__ import annotations

from src.domain.action_registry import (
    ACTION_SPECS,
    action_for_semantic,
    actions_for_capabilities,
    capability_for_action,
    expected_state,
    matches_expected_state,
    parameter_bounds,
    registry_integrity_errors,
    resolve_delta,
    translate_semantic_action,
    validate_action,
)
from src.domain.enums import ActionType, Capability, DeviceType


def test_registry_integrity_and_unique_actions() -> None:
    assert not registry_integrity_errors()
    assert len({spec.name for spec in ACTION_SPECS}) == len(ACTION_SPECS)


def test_every_action_has_one_required_capability() -> None:
    assert capability_for_action("set_temperature") is Capability.TEMPERATURE
    assert capability_for_action("unlock") is Capability.LOCK
    assert capability_for_action("not_real") is None


def test_device_type_specific_temperature_bounds() -> None:
    assert parameter_bounds("set_temperature", DeviceType.AIR_CONDITIONER) == (16, 30)
    assert parameter_bounds("set_temperature", DeviceType.WATER_HEATER) == (30, 60)


def test_absolute_out_of_range_is_rejected_not_clamped() -> None:
    result = validate_action(
        "set_temperature",
        {"temperature": 31},
        capabilities=[Capability.TEMPERATURE],
        device_type=DeviceType.AIR_CONDITIONER,
    )
    assert not result.ok
    assert result.issue is not None and result.issue.code == "TARGET_OUT_OF_RANGE"


def test_relative_delta_is_clamped_to_the_same_registry_bounds() -> None:
    assert resolve_delta(
        "set_temperature",
        {"delta": 2},
        {"temperature": 29},
        device_type=DeviceType.AIR_CONDITIONER,
    ) == {"temperature": 30}


def test_action_capability_and_device_type_are_enforced() -> None:
    missing_cap = validate_action(
        "unlock",
        {},
        capabilities=[Capability.ON_OFF],
        device_type=DeviceType.LIGHT,
    )
    assert missing_cap.issue is not None and missing_cap.issue.code == "CAPABILITY_NOT_SUPPORTED"

    wrong_type = validate_action(
        "set_temperature",
        {"temperature": 25},
        capabilities=[Capability.TEMPERATURE],
        device_type=DeviceType.LIGHT,
    )
    assert wrong_type.issue is not None and wrong_type.issue.code == "DEVICE_TYPE_NOT_SUPPORTED"


def test_parameter_schema_rejects_missing_unknown_and_invalid_choice() -> None:
    missing = validate_action("set_temperature", {}, capabilities=[Capability.TEMPERATURE])
    assert missing.issue is not None and missing.issue.code == "PARAMETER_REQUIRED"

    unknown = validate_action("turn_on", {"temperature": 20}, capabilities=[Capability.ON_OFF])
    assert unknown.issue is not None and unknown.issue.code == "PARAMETER_UNKNOWN"

    choice = validate_action(
        "set_hvac_mode",
        {"mode": "windy"},
        capabilities=[Capability.HVAC_MODE],
        device_type=DeviceType.AIR_CONDITIONER,
    )
    assert choice.issue is not None and choice.issue.code == "CHOICE_NOT_SUPPORTED"


def test_semantic_translation_uses_registry_mapping_and_aliases() -> None:
    assert translate_semantic_action(Capability.BRIGHTNESS, ActionType.SET, {"percent": 65}) == (
        "set_brightness",
        {"brightness": 65},
    )
    assert translate_semantic_action(Capability.FAN_SPEED, ActionType.INCREASE, {}) == (
        "set_fan_speed",
        {"delta": 1},
    )
    assert translate_semantic_action(Capability.TEMPERATURE, ActionType.TURN_ON, {"temperature": 25}) == (
        "set_temperature",
        {"temperature": 25},
    )
    assert action_for_semantic(Capability.TEMPERATURE, ActionType.SET).name == "set_temperature"


def test_expected_state_is_derived_from_action_spec() -> None:
    assert expected_state("set_brightness", {"brightness": 0}) == {"brightness": 0, "power": "off"}
    assert expected_state("unlock", {}) == {"locked": False}
    assert matches_expected_state("set_temperature", {"temperature": 25}, {"temperature": 25, "power": "on"})


def test_habit_actions_are_filtered_from_registry_capabilities() -> None:
    actions = actions_for_capabilities([Capability.ON_OFF, Capability.BRIGHTNESS], habit_only=True)
    assert actions[:2] == ("turn_on", "turn_off")
    assert "set_brightness" in actions
    assert "unlock" not in actions
