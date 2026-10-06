"""Deterministic long-context ledger evaluation (no network/model calls)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from src.agent.pipeline import PipelineDeps
from src.services.pipeline_bridge import reason

_NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
_FILLERS = (
    "Bật đèn bếp.",
    "Tắt đèn bếp.",
    "Bật loa bếp.",
    "Tắt loa bếp.",
)


@dataclass(frozen=True, slots=True)
class LongContextResult:
    case_id: str
    turns: int
    reference_distance: int
    expected_device_id: str
    expected_capability: str
    expected_action: str
    outcome: str
    actual_actions: tuple[tuple[str, str, str], ...]

    @property
    def passed(self) -> bool:
        return self.outcome == "candidate_plan" and self.actual_actions == (
            (self.expected_device_id, self.expected_capability, self.expected_action),
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "passed": self.passed}


def _run_case(
    *,
    case_id: str,
    messages: list[str],
    reference_turn: int,
    expected_device_id: str,
    expected_capability: str,
    expected_action: str,
) -> LongContextResult:
    deps = PipelineDeps()
    result = None
    for message in messages:
        result = reason(
            message=message,
            conversation_id=f"long-context:{case_id}",
            user_id="eval-user",
            role="owner",
            now=_NOW,
            deps=deps,
        )
    assert result is not None
    actions = tuple(
        (action.device_id, action.capability.value, action.action.value)
        for action in (result.candidate_plan.actions if result.candidate_plan else [])
    )
    return LongContextResult(
        case_id=case_id,
        turns=len(messages),
        reference_distance=len(messages) - reference_turn,
        expected_device_id=expected_device_id,
        expected_capability=expected_capability,
        expected_action=expected_action,
        outcome=result.outcome,
        actual_actions=actions,
    )


def run_long_context_eval(turn_counts: tuple[int, ...] = (10, 20, 50)) -> dict[str, Any]:
    """Run 10/20/50-turn reference cases plus an explicit branch-return case."""
    results: list[LongContextResult] = []
    for turn_count in turn_counts:
        if turn_count < 4:
            raise ValueError("turn_count must be at least 4")
        messages = [
            "Máy rửa bát đang tắt đúng không?",
            "Để máy rửa bát lát nữa.",
            *(_FILLERS[index % len(_FILLERS)] for index in range(turn_count - 3)),
            "Giờ bật cái máy mình vừa nói để lát nữa ấy.",
        ]
        results.append(
            _run_case(
                case_id=f"deferred-reference-{turn_count}",
                messages=messages,
                reference_turn=2,
                expected_device_id="may_rua_bat",
                expected_capability="on_off",
                expected_action="turn_on",
            )
        )

    branch_messages = [
        "Bật TV phòng khách.",
        "Để TV phòng khách lát nữa.",
        "Bật điều hoà phòng con.",
        "Nhiệt độ 24 độ.",
        *(_FILLERS[index % len(_FILLERS)] for index in range(7)),
        "Giờ giảm âm lượng cái mình vừa nói để lát nữa xuống 20.",
    ]
    results.append(
        _run_case(
            case_id="branch-return-after-alternate-goal",
            messages=branch_messages,
            reference_turn=2,
            expected_device_id="tv_phong_khach",
            expected_capability="volume",
            expected_action="set",
        )
    )

    passed = sum(result.passed for result in results)
    return {
        "evaluator": "long-context-ledger-v1",
        "deterministic": True,
        "total": len(results),
        "passed": passed,
        "target_action_accuracy": passed / len(results) if results else 0.0,
        "max_turns": max((result.turns for result in results), default=0),
        "max_reference_distance": max(
            (result.reference_distance for result in results),
            default=0,
        ),
        "cases": [result.to_dict() for result in results],
    }

