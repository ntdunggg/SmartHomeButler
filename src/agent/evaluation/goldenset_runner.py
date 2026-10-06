"""Goldenset runner cho pipeline 5 tầng MỚI (spec §59, §72) — đo generalization.

Chạy pipeline mới (qua bridge `pipeline_bridge.reason`) trên goldenset đa lượt và chấm:
- outcome ∈ expect (candidate_plan | clarification | answer | cancelled | no_goal)
- dev_any: mọi thiết bị yêu cầu phải xuất hiện trong plan
- dev_none: không thiết bị cấm nào được xuất hiện

Offline (FakeReasoningModel) dùng cho REGRESSION/smoke; số generalization THẬT cần model
live (model cấu hình qua `MODEL_NAME`) — đặt `deps.model_client` = client thật khi đo chính thức (spec §72:
pass bằng hardcoded pattern là fail; số phải đo bằng semantic).

CHÚ Ý (spec §71): KHÔNG đưa goldenset vào production prompt / few-shot. Runner này CHỈ
đọc goldenset để CHẤM, không nạp vào model.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.agent.pipeline import PipelineDeps
from src.iot.registry import DEVICE_BY_SLUG
from src.services.pipeline_bridge import reason

# reason() outcome → từ vựng goldenset.
_OUTCOME_MAP = {
    "candidate_plan": "candidate_plan",
    "clarification": "clarification",
    "answer": "answer",
    "cancelled": "cancelled",
    "no_action": "no_goal",
}


@dataclass(slots=True)
class TurnResult:
    utterance: str
    expected: list[str]
    got: str
    passed: bool
    plan_devices: list[str] = field(default_factory=list)
    hallucinated_devices: int = 0
    unsupported_capabilities: int = 0
    explicit_constraint_loss: int = 0
    schema_valid: bool = True


@dataclass(slots=True)
class CaseResult:
    case_id: str
    category: str
    passed: bool
    turns: list[TurnResult] = field(default_factory=list)


@dataclass(slots=True)
class GoldensetReport:
    total: int
    passed: int
    by_category: dict[str, tuple[int, int]]  # cat → (passed, total)
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return round(self.passed / self.total, 4) if self.total else 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "accuracy": self.accuracy,
            "by_category": {c: {"passed": p, "total": t, "acc": round(p / t, 3) if t else 0.0} for c, (p, t) in self.by_category.items()},
        }


def _eval_turn(turn: dict, result) -> TurnResult:
    got = _OUTCOME_MAP.get(result.outcome, result.outcome)
    expected = list(turn.get("expect", []))
    plan_devices = [a.device_id for a in result.candidate_plan.actions] if result.candidate_plan else []
    outcome_ok = got in expected
    dev_any = set(turn.get("dev_any", []))
    dev_none = set(turn.get("dev_none", []))
    dev_any_ok = (not dev_any) or dev_any.issubset(set(plan_devices))
    lost_constraints = dev_none & set(plan_devices)
    dev_none_ok = not lost_constraints
    hallucinated = 0
    unsupported = 0
    if result.candidate_plan:
        for action in result.candidate_plan.actions:
            spec = DEVICE_BY_SLUG.get(action.device_id)
            if spec is None:
                hallucinated += 1
            elif action.capability not in spec.capabilities:
                unsupported += 1
    return TurnResult(
        utterance=turn.get("utterance", ""),
        expected=expected,
        got=got,
        passed=outcome_ok and dev_any_ok and dev_none_ok,
        plan_devices=plan_devices,
        hallucinated_devices=hallucinated,
        unsupported_capabilities=unsupported,
        explicit_constraint_loss=len(lost_constraints),
        # CandidatePlan/SemanticGoal cross this boundary as Pydantic models. A
        # malformed structured model response raises before reaching this point.
        schema_valid=True,
    )


def run_case(case: dict, deps: PipelineDeps, *, now: datetime | None = None) -> CaseResult:
    """Chạy một case đa lượt qua bridge (cùng conversation_id để giữ ledger đa lượt)."""
    now = now or datetime(2026, 8, 15, 20, 0, tzinfo=UTC)
    conv = case.get("id", "case")
    turns: list[TurnResult] = []
    for i, turn in enumerate(case.get("turns", [])):
        ctx = turn.get("ctx", {}) or {}
        result = reason(
            message=turn.get("utterance", ""),
            conversation_id=conv,
            role="owner",
            now=now,
            focus_room=ctx.get("focus_room"),
            speaker_location=ctx.get("speaker_location"),
            speaker_home_room=ctx.get("speaker_home_room"),
            last_device_id=ctx.get("last_device_id"),
            recent_dialogue=ctx.get("recent_dialogue"),
            live_device_states=ctx.get("live_device_states"),
            live_sensors=ctx.get("live_sensors"),
            model_client=deps.model_client,
            deps=deps,
        )
        turns.append(_eval_turn(turn, result))
    return CaseResult(
        case_id=conv,
        category=case.get("category", ""),
        passed=all(t.passed for t in turns),
        turns=turns,
    )


def run_goldenset(path: str | Path, *, deps: PipelineDeps) -> GoldensetReport:
    """Chạy toàn bộ goldenset .jsonl và tổng hợp báo cáo."""
    lines = [json.loads(ln) for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    # Cases are a blind holdout, not one household conversation. Isolate all
    # mutable stores so an instruction learned in one case cannot improve or
    # contaminate another case's score. The live model client is intentionally
    # shared to preserve provider telemetry and schema-repair counters.
    cases = [
        run_case(
            c,
            PipelineDeps(
                knowledge_base=deps.knowledge_base,
                gateway_factory=deps.gateway_factory,
                model_client=deps.model_client,
            ),
        )
        for c in lines
    ]
    by_cat: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    passed = 0
    for cr in cases:
        by_cat[cr.category][1] += 1
        if cr.passed:
            passed += 1
            by_cat[cr.category][0] += 1
    return GoldensetReport(
        total=len(cases),
        passed=passed,
        by_category={c: (p, t) for c, (p, t) in by_cat.items()},
        cases=cases,
    )
