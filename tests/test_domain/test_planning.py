"""Unit test cho domain schema của Milestone 1: input hợp lệ và không hợp lệ."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.domain.enums import (
    ActionType,
    Capability,
    ConfidenceStatus,
    RiskLevel,
    ValidationStatus,
)
from src.domain.planning import (
    ClarificationRequest,
    DeviceAction,
    ExecutionPlan,
    SemanticGoal,
)


# --------------------------------------------------------------------------
# Enum
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0.95, ConfidenceStatus.CONFIDENT),
        (0.88, ConfidenceStatus.CONFIDENT),
        (0.7, ConfidenceStatus.AMBIGUOUS),
        (0.5, ConfidenceStatus.AMBIGUOUS),
        (0.2, ConfidenceStatus.UNCERTAIN),
        (0.0, ConfidenceStatus.UNCERTAIN),
    ],
)
def test_confidence_status_from_score(score: float, expected: ConfidenceStatus) -> None:
    assert ConfidenceStatus.from_score(score) == expected


def test_action_type_is_closed_set() -> None:
    assert ActionType("turn_on") == ActionType.TURN_ON
    with pytest.raises(ValueError):
        ActionType("mo_khoa_cua")  # hành động lạ không nằm trong tập đóng


# --------------------------------------------------------------------------
# DeviceAction
# --------------------------------------------------------------------------
def test_device_action_valid() -> None:
    a = DeviceAction(
        device_id="light.living_ceiling",
        capability=Capability.ON_OFF,
        action=ActionType.TURN_ON,
    )
    assert a.device_id == "light.living_ceiling"
    assert a.risk_level == RiskLevel.NORMAL  # mặc định


def test_device_action_set_carries_params() -> None:
    a = DeviceAction(
        device_id="ac.bedroom",
        capability=Capability.TEMPERATURE,
        action=ActionType.SET,
        params={"temperature": 26},
    )
    assert a.params["temperature"] == 26


def test_device_action_requires_device_id() -> None:
    with pytest.raises(ValidationError):
        DeviceAction(capability=Capability.ON_OFF, action=ActionType.TURN_ON)  # type: ignore[call-arg]


def test_device_action_rejects_empty_device_id() -> None:
    with pytest.raises(ValidationError):
        DeviceAction(device_id="", capability=Capability.ON_OFF, action=ActionType.TURN_ON)


def test_device_action_requires_capability() -> None:
    with pytest.raises(ValidationError):
        DeviceAction(device_id="light.kitchen", action=ActionType.TURN_ON)  # type: ignore[call-arg]


def test_device_action_set_without_params_rejected() -> None:
    with pytest.raises(ValidationError):
        DeviceAction(
            device_id="ac.bedroom",
            capability=Capability.TEMPERATURE,
            action=ActionType.SET,
        )


def test_device_action_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        DeviceAction(
            device_id="light.kitchen",
            capability=Capability.ON_OFF,
            action=ActionType.TURN_ON,
            direct_bus_write=True,  # type: ignore[call-arg]
        )


# --------------------------------------------------------------------------
# SemanticGoal
# --------------------------------------------------------------------------
def test_semantic_goal_valid_and_status_derived() -> None:
    g = SemanticGoal(
        intent="device.control",
        raw_utterance="bật đèn phòng khách",
        confidence=0.91,
    )
    assert g.confidence_status == ConfidenceStatus.CONFIDENT
    assert g.polarity == "affirmative"


def test_semantic_goal_status_tracks_low_score() -> None:
    g = SemanticGoal(intent="scene.execute", raw_utterance="tôi sắp về", confidence=0.2)
    assert g.confidence_status == ConfidenceStatus.UNCERTAIN


def test_semantic_goal_status_not_settable() -> None:
    # confidence_status là computed field: không cho tầng đề xuất tự khai một trạng thái
    # mâu thuẫn với điểm số (ví dụ CONFIDENT trong khi score = 0.2).
    with pytest.raises(ValidationError):
        SemanticGoal(
            intent="scene.execute",
            raw_utterance="tôi sắp về",
            confidence=0.2,
            confidence_status=ConfidenceStatus.CONFIDENT,  # type: ignore[call-arg]
        )


def test_semantic_goal_confidence_out_of_range() -> None:
    with pytest.raises(ValidationError):
        SemanticGoal(intent="x", raw_utterance="y", confidence=1.5)


def test_semantic_goal_bad_polarity() -> None:
    with pytest.raises(ValidationError):
        SemanticGoal(
            intent="device.control",
            raw_utterance="đừng bật",
            confidence=0.9,
            polarity="maybe",
        )


def test_semantic_goal_requires_raw_utterance() -> None:
    with pytest.raises(ValidationError):
        SemanticGoal(intent="device.control", raw_utterance="", confidence=0.9)


# --------------------------------------------------------------------------
# ExecutionPlan
# --------------------------------------------------------------------------
def _action() -> DeviceAction:
    return DeviceAction(
        device_id="light.living_ceiling",
        capability=Capability.ON_OFF,
        action=ActionType.TURN_ON,
    )


def test_execution_plan_valid_defaults_pending_and_not_executable() -> None:
    plan = ExecutionPlan(goal_intent="device.control", actions=[_action()])
    assert plan.validation_status == ValidationStatus.PENDING
    assert plan.requires_confirmation is False
    assert plan.is_executable is False  # proposal chưa qua validator (S1)


def test_execution_plan_validated_is_executable() -> None:
    plan = ExecutionPlan(
        goal_intent="device.control",
        actions=[_action()],
        validation_status=ValidationStatus.VALIDATED,
        requires_confirmation=True,
    )
    assert plan.is_executable is True


def test_execution_plan_empty_actions_rejected() -> None:
    with pytest.raises(ValidationError):
        ExecutionPlan(goal_intent="device.control", actions=[])


def test_execution_plan_rejected_requires_reason() -> None:
    with pytest.raises(ValidationError):
        ExecutionPlan(
            goal_intent="device.control",
            actions=[_action()],
            validation_status=ValidationStatus.REJECTED,
        )


def test_execution_plan_rejected_with_reason_ok() -> None:
    plan = ExecutionPlan(
        goal_intent="device.control",
        actions=[_action()],
        validation_status=ValidationStatus.REJECTED,
        rejection_reason_vi="Vi phạm S2: không mở khoá cửa qua chat",
    )
    assert plan.is_executable is False


# --------------------------------------------------------------------------
# ClarificationRequest
# --------------------------------------------------------------------------
def test_clarification_request_valid() -> None:
    c = ClarificationRequest(
        question_vi="Bạn muốn bật đèn phòng khách hay phòng ngủ?",
        options=["phòng khách", "phòng ngủ"],
        expected_answer_type="device_ref",
    )
    assert c.expected_answer_type == "device_ref"
    assert len(c.options) == 2


def test_clarification_request_requires_question() -> None:
    with pytest.raises(ValidationError):
        ClarificationRequest(question_vi="")


def test_clarification_request_bad_answer_type() -> None:
    with pytest.raises(ValidationError):
        ClarificationRequest(question_vi="mấy độ?", expected_answer_type="temperature")
