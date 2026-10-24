"""Model proposals are normalized against live environmental evidence."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.agent.perception.context_builder import build_perception
from src.agent.understanding.environment_goal import repair_environment_goal
from src.agent.understanding.goal_author import author_goal
from src.core.interfaces import DeviceSelector
from src.nlu.ontology import UtteranceType
from src.nlu.schemas import DesiredOutcome, SemanticGoal

_NOW = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)


class _BadEnvironmentalModel:
    """Mimics a small model that understands the concern but violates the contract."""

    def __init__(self, domain: str) -> None:
        self.domain = domain

    def structured_generate_sync(self, _prompt, output_schema, **_kwargs):
        assert output_schema is SemanticGoal
        return SemanticGoal(
            intent="request_control",
            raw_utterance="placeholder",
            confidence=0.95,
            utterance_type=UtteranceType.SOCIAL_UTTERANCE,
            goal_description="Improve the observed physical environment",
            target_device_ids=["dieu_hoa_phong_khach"],
            action_hint="invented free-form command",
            polarity="negative",
            excluded_device_ids=["den_chum_phong_khach"],
            routine_constraint_only=True,
            desired_outcomes=[
                DesiredOutcome(
                    selector=DeviceSelector(domain=self.domain),
                    target_state={"brightness": None} if self.domain == "light" else {},
                    relative_change={"amount": "do something"},
                )
            ],
        )


@pytest.mark.parametrize(
    "text,sensor,domain,expected_relative,expected_target,expected_state",
    [
        (
            "Không khí phòng khách có vấn đề, xử lý giúp tôi.",
            {"slug": "pm", "name": "PM2.5", "sensor_type": "pm25", "value": 85, "unit": "µg/m³", "room": "Phòng khách"},
            "air_quality",
            {},
            {},
            "poor_air_quality",
        ),
        (
            "Nhiệt độ phòng khách đang không dễ chịu.",
            {"slug": "temp", "name": "Nhiệt độ", "sensor_type": "temperature", "value": 31, "unit": "°C", "room": "Phòng khách"},
            "climate",
            {"temperature": "decrease"},
            {},
            "hot",
        ),
        (
            "Độ ẩm phòng khách không ổn, xử lý giúp tôi.",
            {"slug": "humid", "name": "Độ ẩm", "sensor_type": "humidity", "value": 85, "unit": "%", "room": "Phòng khách"},
            "air_conditioner",
            {},
            {"hvac_mode": "dry"},
            "humid",
        ),
        (
            "Ánh sáng phòng khách gây khó chịu, xử lý giúp tôi.",
            {"slug": "sun", "name": "Nắng", "sensor_type": "sunlight", "value": 95, "unit": "%", "room": ""},
            "light",
            {"brightness": "decrease"},
            {},
            "too_bright",
        ),
    ],
)
def test_author_goal_repairs_bad_model_output_from_live_sensor(
    text, sensor, domain, expected_relative, expected_target, expected_state,
):
    nu, ctx, _ = build_perception(
        text,
        now=_NOW,
        focus_room="Phòng khách",
        live_sensors=[sensor],
    )
    goal = author_goal(_BadEnvironmentalModel(domain), nu, ctx, utterance=text)
    assert goal is not None
    assert goal.utterance_type == UtteranceType.ENVIRONMENT_REQUEST
    assert goal.target_area == "Phòng khách"
    assert goal.target_device_ids == []
    assert goal.action_hint is None
    assert goal.polarity == "affirmative"
    assert goal.excluded_device_ids == []
    assert not goal.routine_constraint_only
    assert len(goal.desired_outcomes) == 1
    outcome = goal.desired_outcomes[0]
    assert outcome.relative_change == expected_relative
    assert outcome.target_state == expected_target
    assert outcome.perceived_state == expected_state


def test_external_weather_comment_is_not_repaired_into_indoor_action():
    nu, ctx, _ = build_perception(
        "Ngoài trời nóng quá.",
        now=_NOW,
        focus_room="Phòng khách",
        live_sensors=[
            {"slug": "temp", "name": "Nhiệt độ", "sensor_type": "temperature", "value": 31, "unit": "°C", "room": "Phòng khách"}
        ],
    )
    original = SemanticGoal(
        intent="weather_comment",
        raw_utterance=nu.raw,
        confidence=0.9,
        utterance_type=UtteranceType.SOCIAL_UTTERANCE,
        desired_outcomes=[
            DesiredOutcome(
                selector=DeviceSelector(domain="climate"),
                relative_change={"temperature": "decrease"},
            )
        ],
    )
    repaired = repair_environment_goal(original, nu=nu, ctx=ctx)
    assert repaired == original


def test_live_sensor_does_not_collapse_multi_domain_activity_plan():
    nu, ctx, _ = build_perception(
        "Chuẩn bị phòng khách cho một buổi gặp gỡ",
        now=_NOW,
        focus_room="Phòng khách",
        live_sensors=[
            {
                "slug": "sun",
                "name": "Nắng",
                "sensor_type": "sunlight",
                "value": 5,
                "unit": "%",
                "room": "Phòng khách",
            },
            {
                "slug": "temp",
                "name": "Nhiệt độ",
                "sensor_type": "temperature",
                "value": 31,
                "unit": "°C",
                "room": "Phòng khách",
            },
        ],
    )
    original = SemanticGoal(
        intent="prepare social space",
        raw_utterance=nu.raw,
        confidence=0.95,
        utterance_type=UtteranceType.ROUTINE_INTENT,
        target_area="Phòng khách",
        desired_outcomes=[
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="light"),
                target_state={"power": "on"},
            ),
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="vacuum"),
                target_state={"power": "on"},
            ),
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="speaker"),
                target_state={"power": "on"},
            ),
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="tv"),
                target_state={"power": "on"},
            ),
        ],
    )

    repaired = repair_environment_goal(original, nu=nu, ctx=ctx)

    assert repaired == original
    assert [outcome.selector.domain for outcome in repaired.desired_outcomes] == [
        "light",
        "vacuum",
        "speaker",
        "tv",
    ]


def test_schema_drops_invalid_relative_change_and_action_hint():
    goal = SemanticGoal.model_validate(
        {
            "raw_utterance": "x",
            "confidence": 0.9,
            "utterance_type": "ENVIRONMENT_REQUEST",
            "action_hint": "make everything comfortable",
            "desired_outcomes": [
                {
                    "selector": {},
                    "relative_change": {
                        "amount": "adjust",
                        "brightness": "default",
                        "temperature": "increase_slight",
                    },
                }
            ],
        }
    )
    assert goal.action_hint is None
    assert goal.desired_outcomes[0].relative_change == {"temperature": "increase_slight"}
