"""Mandatory SPEC v2 release acceptance against an external blind holdout.

The holdout must live outside the repository so it cannot leak into prompts,
fixtures, or implementation rules. This command intentionally has no offline
mode: an FR-16 release claim requires a real configured model.

Example::

    RUN_LIVE_LLM=true LLM_DISABLED=false \
      python -m scripts.spec_v2_acceptance \
      --dataset /secure/holdouts/spec-v2-blind.jsonl --report /tmp/report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.agent.evaluation.families import hard_gate_report, run_families
from src.agent.evaluation.goldenset_runner import GoldensetReport, run_goldenset
from src.agent.pipeline import PipelineDeps
from src.core.reasoning import FakeReasoningModel
from src.nlu.model_client import build_nlu_model_client

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_MIN_CASES = 30
_DEFAULT_MIN_ACCURACY = 0.95
_DEFAULT_MIN_FAMILY_SUCCESS = 0.8


class AcceptanceConfigurationError(ValueError):
    """The acceptance run is not blind/live enough to support a release claim."""


def validate_external_dataset(path: str | Path, *, min_cases: int) -> tuple[Path, list[dict[str, Any]]]:
    """Validate the blind JSONL contract and reject repository-local holdouts."""
    resolved = Path(path).expanduser().resolve(strict=True)
    try:
        resolved.relative_to(_REPO_ROOT.resolve())
    except ValueError:
        pass
    else:
        raise AcceptanceConfigurationError(
            f"Blind dataset must be outside the repository: {resolved}"
        )

    cases: list[dict[str, Any]] = []
    for line_no, raw in enumerate(resolved.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            case = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AcceptanceConfigurationError(f"Invalid JSON on line {line_no}: {exc.msg}") from exc
        if not isinstance(case, dict) or not isinstance(case.get("id"), str):
            raise AcceptanceConfigurationError(f"Line {line_no} must contain a case with string id")
        turns = case.get("turns")
        if not isinstance(turns, list) or not turns:
            raise AcceptanceConfigurationError(f"Case {case['id']} has no turns")
        for turn in turns:
            if not isinstance(turn, dict) or not isinstance(turn.get("utterance"), str):
                raise AcceptanceConfigurationError(f"Case {case['id']} has an invalid turn")
            expected = turn.get("expect")
            if not isinstance(expected, list) or not expected:
                raise AcceptanceConfigurationError(f"Case {case['id']} turn has no expected outcomes")
        cases.append(case)

    if len(cases) < min_cases:
        raise AcceptanceConfigurationError(
            f"Blind dataset has {len(cases)} cases; at least {min_cases} are required"
        )
    ids = [str(case["id"]) for case in cases]
    if len(ids) != len(set(ids)):
        raise AcceptanceConfigurationError("Blind dataset contains duplicate case ids")
    return resolved, cases


def _live_gate_metrics(report: GoldensetReport) -> dict[str, int | float]:
    turns = [turn for case in report.cases for turn in case.turns]
    return {
        "hallucinated_device": sum(turn.hallucinated_devices for turn in turns),
        "unsupported_capability": sum(turn.unsupported_capabilities for turn in turns),
        "schema_validity": round(sum(turn.schema_valid for turn in turns) / max(1, len(turns)), 4),
        "explicit_constraint_loss": sum(turn.explicit_constraint_loss for turn in turns),
    }


def evaluate_gates(
    *,
    live_report: GoldensetReport,
    family_report: dict[str, int | float],
    min_accuracy: float,
    min_family_success: float,
) -> tuple[dict[str, Any], list[str]]:
    """Return machine-readable gates and human-readable failure reasons."""
    live = _live_gate_metrics(live_report)
    gates = {
        "blind_accuracy": {
            "value": live_report.accuracy,
            "threshold": min_accuracy,
            "passed": live_report.accuracy >= min_accuracy,
        },
        "hallucinated_device": {
            "value": live["hallucinated_device"], "threshold": 0,
            "passed": live["hallucinated_device"] == 0,
        },
        "unsupported_capability": {
            "value": live["unsupported_capability"], "threshold": 0,
            "passed": live["unsupported_capability"] == 0,
        },
        "schema_validity": {
            "value": live["schema_validity"], "threshold": 1.0,
            "passed": live["schema_validity"] == 1.0,
        },
        "explicit_constraint_loss": {
            "value": live["explicit_constraint_loss"], "threshold": 0,
            "passed": live["explicit_constraint_loss"] == 0,
        },
        "semantic_family_safety": {
            "value": {
                "hallucinated_device": family_report["hallucinated_device"],
                "unsupported_capability": family_report["unsupported_capability"],
                "task_success_rate": family_report["task_success_rate"],
            },
            "threshold": {
                "hallucinated_device": 0,
                "unsupported_capability": 0,
                "task_success_rate": min_family_success,
            },
            "passed": (
                family_report["hallucinated_device"] == 0
                and family_report["unsupported_capability"] == 0
                and family_report["task_success_rate"] >= min_family_success
            ),
        },
    }
    failures = [name for name, gate in gates.items() if not gate["passed"]]
    return gates, failures


def _report_payload(
    *, dataset: Path, model: Any, report: GoldensetReport, family_report: dict[str, Any],
    gates: dict[str, Any], failures: list[str],
) -> dict[str, Any]:
    failed_cases = [
        {
            "id": case.case_id,
            "category": case.category,
            "turns": [asdict(turn) for turn in case.turns if not turn.passed],
        }
        for case in report.cases if not case.passed
    ]
    return {
        "release_gate": "spec-v2-acceptance",
        "dataset": str(dataset),
        "model": getattr(model, "model", type(model).__name__),
        "live_model": True,
        "generalization": report.summary(),
        "semantic_families": family_report,
        "gates": gates,
        "passed": not failures,
        "failed_gates": failures,
        "failed_cases": failed_cases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the mandatory live blind SPEC v2 gate")
    parser.add_argument("--dataset", required=True, help="External blind JSONL path")
    parser.add_argument("--report", required=True, help="Where to write the JSON report")
    parser.add_argument("--min-cases", type=int, default=_DEFAULT_MIN_CASES)
    parser.add_argument("--min-accuracy", type=float, default=_DEFAULT_MIN_ACCURACY)
    parser.add_argument("--min-family-success", type=float, default=_DEFAULT_MIN_FAMILY_SUCCESS)
    args = parser.parse_args(argv)

    try:
        dataset, _ = validate_external_dataset(args.dataset, min_cases=args.min_cases)
        model = build_nlu_model_client()
        if model is None or isinstance(model, FakeReasoningModel):
            raise AcceptanceConfigurationError(
                "A live model is required (set RUN_LIVE_LLM=true, LLM_DISABLED=false, and credentials)"
            )
        live_report = run_goldenset(dataset, deps=PipelineDeps(model_client=model))
        family_report = hard_gate_report(run_families(PipelineDeps()))
        gates, failures = evaluate_gates(
            live_report=live_report,
            family_report=family_report,
            min_accuracy=args.min_accuracy,
            min_family_success=args.min_family_success,
        )
        payload = _report_payload(
            dataset=dataset, model=model, report=live_report, family_report=family_report,
            gates=gates, failures=failures,
        )
    except (AcceptanceConfigurationError, OSError) as exc:
        print(f"SPEC v2 acceptance configuration error: {exc}", file=sys.stderr)
        return 2

    report_path = Path(args.report).expanduser()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("model", "generalization", "gates", "passed")}, indent=2))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
