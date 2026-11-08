"""Giao thức đo khách quan (evals/protocol.py): tách tập, chấm điểm, sổ holdout, tổng hợp."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from evals import protocol, run


def _case(case_id: str, **kw) -> protocol.Case:
    return protocol.parse_case({"id": case_id, "text": "bật đèn", "expect": {"outcome": "candidate_plan"}, **kw})


# --- dataset và tách tập ------------------------------------------------------------
def test_split_is_deterministic_and_stable_when_cases_are_added():
    first = [_case(f"c{i}") for i in range(200)]
    before = {c.id: protocol.assign_split(c) for c in first}
    grown = first + [_case(f"new{i}") for i in range(50)]
    after = {c.id: protocol.assign_split(c) for c in grown}
    assert all(after[k] == v for k, v in before.items())
    share = sum(v == "holdout" for v in before.values()) / len(before)
    assert 0.2 < share < 0.4  # mặc định 30%


def test_explicit_split_in_dataset_wins():
    assert protocol.assign_split(_case("x", split="holdout"), holdout_pct=0) == "holdout"
    assert protocol.assign_split(_case("y", split="dev"), holdout_pct=100) == "dev"


def test_dataset_rejects_duplicates_and_cases_without_expectation(tmp_path):
    dup = tmp_path / "dup.jsonl"
    dup.write_text('{"id":"a","text":"x","expect":{}}\n{"id":"a","text":"y","expect":{}}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="trùng id"):
        protocol.load_dataset(dup)
    bare = tmp_path / "bare.jsonl"
    bare.write_text('{"id":"a","turns":[{"text":"x"}]}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="không có lượt nào"):
        protocol.load_dataset(bare)


def test_dataset_hash_changes_with_content(tmp_path):
    p = tmp_path / "d.jsonl"
    p.write_text('# chú thích\n{"id":"a","text":"x","expect":{}}\n', encoding="utf-8")
    cases, sha1 = protocol.load_dataset(p)
    p.write_text('{"id":"a","text":"y","expect":{}}\n', encoding="utf-8")
    _, sha2 = protocol.load_dataset(p)
    assert len(cases) == 1 and sha1 != sha2


# --- chấm điểm ---------------------------------------------------------------------
LIGHT_ON = {"device_id": "den", "action": "turn_on", "params": {}}


def test_exact_actions_flag_missing_and_extra():
    expect = {"outcome": "candidate_plan", "actions": [{"device_id": "den", "action": "turn_on"}]}
    assert protocol.score_turn(expect, "candidate_plan", [LIGHT_ON]) == []
    assert any("thiếu" in r for r in protocol.score_turn(expect, "candidate_plan", []))
    extra = {"device_id": "quat", "action": "turn_on", "params": {}}
    assert any("thừa" in r for r in protocol.score_turn(expect, "candidate_plan", [LIGHT_ON, extra]))


def test_include_allows_extra_and_params_use_tolerance():
    act = {"device_id": "ac", "action": "set", "params": {"temperature": 24.4, "mode": "cool"}}
    expect = {"actions_include": [{"device_id": "ac", "action": "set", "params": {"temperature": 24}}], "tol": 0.5}
    assert protocol.score_turn(expect, "candidate_plan", [act, LIGHT_ON]) == []
    strict = {"actions_include": [{"device_id": "ac", "action": "set", "params": {"temperature": 24}}]}
    assert protocol.score_turn(strict, "candidate_plan", [act]) != []


def test_outcome_accepts_a_list():
    expect = {"outcome": ["answer", "clarification"], "actions": []}
    assert protocol.score_turn(expect, "clarification", []) == []
    assert protocol.score_turn(expect, "candidate_plan", []) != []


# --- sổ holdout --------------------------------------------------------------------
CLEAN = {"commit": "abc1234", "dirty": False}


def _guard(log, **kw):
    args = {"dataset_path": "d.jsonl", "dataset_sha": "sha", "git": CLEAN, "reason": "báo cáo", "allow_rerun": False, "config": {}}
    args.update(kw)
    return protocol.guard_holdout(log, **args)


def test_holdout_refuses_dirty_tree_and_missing_reason(tmp_path):
    log = tmp_path / "log.jsonl"
    with pytest.raises(protocol.HoldoutGuardError, match="worktree sạch"):
        _guard(log, git={"commit": "x", "dirty": True})
    with pytest.raises(protocol.HoldoutGuardError, match="--reason"):
        _guard(log, reason="  ")
    assert not log.exists()


def test_holdout_runs_once_per_dataset_and_reruns_are_logged(tmp_path):
    log = tmp_path / "log.jsonl"
    _guard(log)
    with pytest.raises(protocol.HoldoutGuardError, match="--rerun-holdout"):
        _guard(log)
    entry = _guard(log, allow_rerun=True)
    assert entry["rerun"] is True and entry["previous_runs"] == 1
    _guard(log, dataset_sha="other")  # dataset khác là một holdout khác
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [r["dataset_sha256"] for r in rows] == ["sha", "sha", "other"]


# --- tổng hợp ----------------------------------------------------------------------
def test_aggregate_reports_spread_flaky_and_failures():
    recs = []
    for run_idx, outcomes in enumerate([(True, True, False), (True, False, False), (True, True, False)], start=1):
        for cid, ok in zip(("a", "b", "c"), outcomes, strict=True):
            recs.append({"run": run_idx, "case_id": cid, "tags": ["t"], "passed": ok,
                         "turns": [{"latency_s": 1.0, "llm_calls": 1 if cid == "b" else 0, "tokens": 10}]})
    s = protocol.aggregate(recs, runs=3)
    assert s["accuracy_per_run"] == pytest.approx([2 / 3, 1 / 3, 2 / 3])
    assert s["accuracy_std"] > 0
    assert (s["always_pass"], s["flaky"], s["always_fail"]) == (["a"], ["b"], ["c"])
    assert s["turns_with_llm_pct"] == pytest.approx(1 / 3)


# --- lỗi hạ tầng không bị chấm là sai ----------------------------------------------
def test_provider_error_aborts_instead_of_scoring_a_false_failure(monkeypatch):
    class _Client:
        def __init__(self):
            self.calls = []

        def reset(self):
            self.calls = []

    client = _Client()

    def _fake_reason(**_kw):
        client.calls.append(SimpleNamespace(provider_error_type="RateLimitError", total_tokens=0))
        return SimpleNamespace(outcome="clarification", candidate_plan=None, reply="Bạn muốn phòng nào?")

    monkeypatch.setattr("src.services.pipeline_bridge.reason", _fake_reason)
    with pytest.raises(run.ProviderUnavailableError, match="RateLimitError"):
        run._run_case(_case("q"), run=1, tag="t", model_client=client, min_interval=0)
