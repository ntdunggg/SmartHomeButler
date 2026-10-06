"""Release assertions for the deterministic long-context ledger evaluator."""

from __future__ import annotations

from src.evaluation.long_context_ledger import run_long_context_eval


def test_eval_covers_10_20_50_turns_far_reference_and_branch_return():
    report = run_long_context_eval()

    assert report["passed"] == report["total"] == 4
    assert {case["turns"] for case in report["cases"]} >= {10, 20, 50}
    assert report["max_reference_distance"] > 8
    assert report["max_turns"] == 50
    branch = next(
        case for case in report["cases"]
        if case["case_id"] == "branch-return-after-alternate-goal"
    )
    assert branch["passed"] is True
    assert branch["actual_actions"] == (("tv_phong_khach", "volume", "set"),)

