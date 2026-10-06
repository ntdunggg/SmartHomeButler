"""Registry contract for the 200-case live semantic goldenset."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from scripts.eval_live_semantic import compile_fixture
from src.iot.registry import DEVICE_SPECS, ROOMS

_DATASET = (
    Path(__file__).resolve().parents[2]
    / "src/evaluation/goldensets/smart_home_live_semantic_200.json"
)
_EXPECTED_CATEGORIES = {
    "ambiguity_detection": 25,
    "context_resolution": 30,
    "environmental_adjustment": 30,
    "long_conversation": 30,
    "memory_feedback": 25,
    "negation_cancel_correction": 25,
    "targeted_clarification": 20,
    "topic_switch_stale_context": 15,
}
_NON_REGISTRY_VOCABULARY = (
    "hành lang",
    "phòng làm việc",
    "phòng ăn",
    "đèn dưới tủ",
    "quạt thông gió",
    "turbo",
)


def _cases() -> list[dict]:
    return json.loads(_DATASET.read_text(encoding="utf-8"))["cases"]


def test_dataset_keeps_200_unique_stratified_cases():
    cases = _cases()

    assert len(cases) == 200
    assert len({case["id"] for case in cases}) == 200
    assert Counter(case["category"] for case in cases) == _EXPECTED_CATEGORIES


def test_expected_rooms_targets_and_capabilities_resolve_to_registry():
    errors: list[str] = []
    for case in _cases():
        room = case["expected"].get("room")
        if room not in (None, "current_room") and room not in ROOMS:
            errors.append(f"{case['id']}: non-canonical room {room!r}")
        contract = compile_fixture(case)
        errors.extend(f"{case['id']}: {error}" for error in contract.errors)

    assert not errors, "\n" + "\n".join(errors)


def test_user_turns_do_not_name_inventory_that_the_registry_does_not_have():
    errors: list[str] = []
    for case in _cases():
        for message in case.get("messages", []):
            if message.get("role") != "user":
                continue
            text = str(message.get("content") or "").lower()
            for phrase in _NON_REGISTRY_VOCABULARY:
                if phrase in text:
                    errors.append(f"{case['id']}: unsupported inventory phrase {phrase!r}")
            if re.search(r"\bquạt\b", text) and not any(
                owner in text for owner in ("điều hòa", "điều hoà", "máy lọc không khí")
            ):
                errors.append(f"{case['id']}: standalone fan is not a registered device")

    assert not errors, "\n" + "\n".join(errors)


def test_constraints_do_not_reference_removed_inventory_concepts():
    obsolete = ("hallway", "under_cabinet", "ventilation_fan", "turbo")
    obsolete_exact = {"keep_fan_on", "prefer_fan_before_ac", "keep_fan_unchanged"}
    errors: list[str] = []
    for case in _cases():
        for constraint in case["expected"].get("constraints", []):
            if constraint in obsolete_exact or any(token in constraint for token in obsolete):
                errors.append(f"{case['id']}: obsolete constraint {constraint!r}")

    assert not errors, "\n" + "\n".join(errors)


def test_inventory_specific_constraints_have_a_matching_device_in_the_expected_room():
    requirements = {
        "night_light": lambda spec: "night_light" in spec.semantic_roles,
        "desk_lamp": lambda spec: bool(
            {"task_lighting", "work_or_study_light"} & set(spec.semantic_roles)
        ),
        "air_purifier": lambda spec: spec.device_type.value == "air_purifier",
        "air_conditioner": lambda spec: spec.device_type.value == "air_conditioner",
        "_ac_": lambda spec: spec.device_type.value == "air_conditioner",
        "window": lambda spec: spec.device_type.value == "window",
        "curtain": lambda spec: spec.device_type.value == "curtain",
        "speaker": lambda spec: spec.device_type.value == "speaker",
        "dining_light": lambda spec: spec.slug == "den_ban_an",
        "kitchen_light": lambda spec: spec.slug == "den_bep",
        "ceiling_light": lambda spec: "đèn trần" in spec.aliases,
    }
    errors: list[str] = []
    for case in _cases():
        room = case["expected"].get("room")
        if room not in ROOMS:
            continue
        room_specs = [spec for spec in DEVICE_SPECS if spec.room == room]
        for constraint in case["expected"].get("constraints", []):
            for token, predicate in requirements.items():
                if token in constraint and not any(predicate(spec) for spec in room_specs):
                    errors.append(
                        f"{case['id']}: {constraint!r} has no matching device in {room}"
                    )

    assert not errors, "\n" + "\n".join(errors)


def test_numeric_bounds_leave_the_expected_adjustment_feasible():
    state_field = {
        "brightness": "brightness",
        "temperature": "temperature",
        "volume": "volume",
        "opening": "position",
    }
    errors: list[str] = []
    for case in _cases():
        expected = case["expected"]
        adjustment = str(expected.get("adjustment") or "")
        contract = compile_fixture(case)
        candidates = [spec for spec in DEVICE_SPECS if spec.slug in contract.device_ids]
        for constraint in expected.get("constraints", []):
            match = re.fullmatch(r"(max|min)_(brightness|temperature|volume|opening)_(\d+)", constraint)
            if not match:
                continue
            kind, dimension, raw_bound = match.groups()
            bound = float(raw_bound)
            explicit = re.fullmatch(r"set_[a-z_]+_(\d+)", adjustment)
            if explicit:
                value = float(explicit.group(1))
                valid = value <= bound if kind == "max" else value >= bound
            else:
                values = [
                    float(spec.initial_state[state_field[dimension]])
                    for spec in candidates
                    if isinstance(spec.initial_state.get(state_field[dimension]), int | float)
                ]
                if kind == "max":
                    valid = bool(values) and any(value < bound for value in values)
                else:
                    valid = bool(values) and any(value > bound for value in values)
            if not valid:
                errors.append(
                    f"{case['id']}: {adjustment!r} is infeasible under {constraint!r}"
                )

    assert not errors, "\n" + "\n".join(errors)
