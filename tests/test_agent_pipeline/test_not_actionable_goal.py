"""Regression tất định: goal RỖNG (không outcome/hint/target) → CLARIFY, KHÔNG PROCEED.

Bối cảnh (LIVE §2026-08-10, no1/no4): câu quan sát/xã giao ("trời đẹp thật") bị Semantic
bịa perceived_state rồi Planner tạo action. Lưới tất định: nếu goal không có gì để hành
động thì validator phải CLARIFY thay vì cho xuống Planner (nơi trước đó goal rỗng lại được
chấm entity_grounding=1.0 → PROCEED → bịa).
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.core.interfaces import DeviceSelector
from src.nlu.context import build_runtime_context
from src.nlu.normalizer import analyze
from src.nlu.ontology import UtteranceType
from src.nlu.schemas import DesiredOutcome, SemanticGoal, ValidationDecision
from src.nlu.validator import validate

_NOW = datetime(2026, 8, 5, 22, 0, tzinfo=UTC)


def _ctx(nu, focus_room="Phòng khách"):
    return build_runtime_context(nu, now=_NOW, timezone="Asia/Ho_Chi_Minh", focus_room=focus_room)


def test_empty_goal_clarifies_not_proceeds() -> None:
    utt = "hôm nay trời đẹp thật"
    nu = analyze(utt, focus_room="Phòng khách")
    goal = SemanticGoal(
        intent="environment.request", raw_utterance=utt, confidence=0.8,
        utterance_type=UtteranceType.ENVIRONMENT_REQUEST, goal_description="thời tiết đẹp",
        desired_outcomes=[], target_device_ids=[],
    )
    res = validate(goal, nu=nu, ctx=_ctx(nu))
    assert res.decision == ValidationDecision.CLARIFY
    assert any(e.code == "NOT_ACTIONABLE" for e in res.errors)


def test_goal_with_outcomes_still_proceeds() -> None:
    """Không hồi quy: câu có nhu cầu thật ('ngột ngạt') vẫn PROCEED."""
    utt = "ở đây ngột ngạt quá"
    nu = analyze(utt, focus_room="Phòng khách")
    goal = SemanticGoal(
        intent="environment.request", raw_utterance=utt, confidence=0.85,
        utterance_type=UtteranceType.ENVIRONMENT_REQUEST, goal_description="làm thoáng hơn",
        target_area="Phòng khách",
        desired_outcomes=[
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng khách", domain="air_purifier"),
                perceived_state="stuffy", relative_change={"fan_speed": "increase_slight"},
                cardinality="any",
            )
        ],
    )
    res = validate(goal, nu=nu, ctx=_ctx(nu))
    assert res.decision == ValidationDecision.PROCEED
    assert not any(e.code == "NOT_ACTIONABLE" for e in res.errors)
