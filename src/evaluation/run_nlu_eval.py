"""Runner đánh giá NLU end-to-end — ba chế độ: offline / live-smoke / live-eval.

    python -m src.evaluation.run_nlu_eval --mode offline
    RUN_LIVE_LLM=1 python -m src.evaluation.run_nlu_eval --mode live-smoke
    RUN_LIVE_LLM=1 python -m src.evaluation.run_nlu_eval --mode live-eval

- offline: KHÔNG model client, không mạng, dùng cho CI/regression.
- live-smoke: gọi model THẬT trên bộ smoke đại diện; fail nếu thiếu key/không có call.
- live-eval: chạy toàn bộ golden set; fast-path KHÔNG gọi LLM, chỉ mẫu cần mới gọi model.

Tách metric theo route (deterministic/llm). Trả exit code != 0 nếu hard gate trượt.
KHÔNG in API key. KHÔNG lưu raw provider response.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.evaluation.metrics import compute_metrics
from src.services.pipeline_bridge import reason

DATASETS_DIR = Path(__file__).parent / "datasets"
SMOKE_FILE = Path(__file__).parent / "smoke_cases.jsonl"
RESULTS_DIR = Path(__file__).parent / "results"
_FIXED_NOW = datetime(2026, 8, 5, 22, 0, tzinfo=UTC)

# Hard gate — không hạ ngưỡng để cho pass (Bước 16). Trượt → exit code != 0.
HARD_GATES = {
    "hallucinated_device_rate": ("<=", 0.0),
    "hallucinated_room_rate": ("<=", 0.0),
    "schema_validity_rate": (">=", 0.99),
    "negation_preservation": (">=", 0.99),
    "correction_preservation": (">=", 0.99),
    "cancellation_preservation": (">=", 0.99),
    "requires_policy_validation_rate": (">=", 1.0),
}

# Soft gate — CHỈ cảnh báo (không fail CI). Mục tiêu chất lượng cần theo dõi, sửa dần.
SOFT_GATES = {
    "decision_accuracy": (">=", 0.90),
    "clarification_recall": (">=", 0.90),
}
# Soft gate trên runtime block (đo ở runtime, không phải overall).
SOFT_GATES_RUNTIME = {
    "first_pass_schema_validity": (">=", 0.95),
}


def _final_decision(state: dict[str, Any], outcome: str | None) -> str:
    """Quyết định CUỐI của agent (clarify/proceed), không phải quyết định goal-schema.

    Đây là hành vi thực tế người dùng thấy: hỏi lại (clarify) vs sẽ hành động (proceed).
    `confirm` (cần xác nhận) gộp vào `proceed` vì cả hai đều KHÔNG thiếu thông tin — khác
    hẳn `clarify` (thiếu, phải hỏi). Đọc trực tiếp từ outcome cuối + cờ clarification, KHÔNG
    lấy từ validation.decision (chỉ phản ánh tầng goal, bỏ sót flip ở format/plan/approval)."""
    approval = state.get("approval_decision") or state.get("final_approval_decision")
    if outcome in ("clarification", "no_goal") or state.get("requires_clarification") or approval == "clarify":
        return "clarify"
    return "proceed"


def outcome_of(state: dict[str, Any]) -> dict[str, Any]:
    goal = state.get("semantic_goal")
    plan = state.get("candidate_plan")
    val = state.get("validation")
    critique = state.get("plan_critique")
    understanding = state.get("understanding")
    outcome = state.get("outcome")
    utype = None
    if goal is not None:
        utype = goal.utterance_type.value
    elif understanding is not None and getattr(understanding, "utterance_type", None) is not None:
        utype = understanding.utterance_type.value
    invented = sum(1 for a in (goal.assumptions if goal else []) if a.startswith("INVENTED_"))
    schema_valid = state.get("error") in (None, "")
    plan_actions = [(a.device_id, a.capability.value, a.action.value) for a in (plan.actions if plan else [])]
    return {
        "outcome": outcome,
        "utterance_type": utype,
        # Nhãn ý định TỰ DO (không thuộc tập đóng) — chỉ để log/hiển thị, không chấm top-1.
        "intent": goal.intent if goal else None,
        # decision CUỐI (hành vi agent) — dùng để chấm decision_accuracy/clarification.
        "decision": _final_decision(state, outcome),
        # decision tầng goal-schema — chỉ để chẩn đoán, KHÔNG chấm.
        "goal_schema_decision": val.decision.value if val else None,
        "approval_decision": state.get("approval_decision"),
        "request_type": state.get("request_type"),
        "targets": sorted(goal.target_device_ids) if goal else [],
        "area": goal.target_area if goal else None,
        "negated": goal.negated if goal else False,
        "is_correction": goal.is_correction if goal else False,
        "is_cancellation": goal.is_cancellation if goal else False,
        "abstained": bool(state.get("abstained", False)),
        "model_used": state.get("route") == "llm",
        "plan_actions": plan_actions,
        "requires_policy_validation": plan.requires_policy_validation if plan else None,
        # HITL: kế hoạch nhạy cảm (SECURITY / công suất lớn / >3 bước) phải được người duyệt
        # trước khi thực thi. Lộ ra để metric công nhận proceed→HITL là "safe pause" (concern an
        # ninh: đối chiếu core/permissions.py — thiết bị SECURITY luôn requires_approval).
        "requires_confirmation": bool(getattr(plan, "requires_confirmation", False)) if plan else False,
        "assumptions_invented": invented,
        "schema_valid": schema_valid,
        # --- Chất lượng ngữ nghĩa goal/plan (proxy tất định, không cần judge) ---
        "goal_description": (goal.goal_description if goal else None),
        "desired_outcomes_count": len(goal.desired_outcomes) if goal else 0,
        "plan_action_count": len(plan_actions),
        "redundant_actions": len(plan_actions) - len(set(plan_actions)),
        "goal_coverage": (critique.goal_coverage if critique is not None else None),
        "missing_information": list(plan.missing_information) if plan else [],
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def run_record(rec: dict[str, Any], *, client: Any = None, reasoning_model: Any = None, judge_client: Any = None) -> dict[str, Any]:
    ctx = rec.get("context", {})
    if client is not None:
        client.reset()
    start = time.perf_counter()
    result = reason(
        message=rec["utterance"],
        conversation_id=str(rec.get("id", "")),
        now=_FIXED_NOW,
        focus_room=ctx.get("focus_room"),
        last_device_id=ctx.get("last_device_id"),
        recent_dialogue=ctx.get("recent_dialogue"),
        model_client=client,
    )
    state = {
        "semantic_goal": result.semantic_goal,
        "candidate_plan": result.candidate_plan,
        "outcome": result.outcome,
        "requires_clarification": result.outcome == "clarification",
        "route": "llm" if list(getattr(client, "calls", []) or []) else "deterministic",
    }
    latency_ms = (time.perf_counter() - start) * 1000
    actual = outcome_of(dict(state))

    # LLM-judge opt-in: chấm chất lượng ngữ nghĩa (tách gate, fail-open). Client riêng nên
    # token/latency judge KHÔNG lẫn vào runtime của pipeline.
    if judge_client is not None:
        from src.evaluation.judge import judge_actual

        verdict = judge_actual(judge_client, rec.get("utterance", ""), actual)
        if verdict is not None:
            actual["judge"] = verdict

    calls = list(getattr(client, "calls", []) or []) if client is not None else []
    route = "llm" if calls else "deterministic"
    provider_error = next((c.provider_error_type for c in reversed(calls) if c.provider_error_type), None)
    # Schema-repair đo TÁCH BIỆT với số lượt gọi node (client tự đếm) — KHÔNG suy ra từ
    # len(calls) (trộn lượt node hợp lệ với lượt sửa). Fallback về len(calls)-1 nếu client cũ.
    structured_calls = int(getattr(client, "structured_calls", 0)) if client is not None else 0
    schema_repairs = int(getattr(client, "schema_repairs", 0)) if client is not None else 0
    return {
        "id": str(rec.get("id", "")),
        "utterance": rec.get("utterance", ""),
        "expect": rec.get("expect", {}),
        "actual": actual,
        "route": route,
        "expected_route": rec.get("expected_route", "either"),
        "latency_ms": round(latency_ms, 3),
        "tokens": sum(c.total_tokens or 0 for c in calls) or None,
        "input_tokens": sum(c.input_tokens or 0 for c in calls),
        "output_tokens": sum(c.output_tokens or 0 for c in calls),
        "structured_calls": structured_calls,
        "schema_repairs": schema_repairs,
        "repair_count": schema_repairs,  # back-compat: nay là schema-repair thật, không phải len(calls)-1
        "node_call_count": len(calls),
        "provider_error": provider_error,
        # bản ghi log có cấu trúc (Bước 18) — không chứa secret/raw response.
        "log": {
            "sample_id": str(rec.get("id", "")),
            "route": route,
            "model": (calls[-1].model if calls else None),
            "provider": getattr(client, "provider", None),
            "utterance_type": actual["utterance_type"],
            "selected_intent": actual["intent"],
            "decision": actual["decision"],
            "repair_count": max(0, len(calls) - 1),
            "latency_ms": round(latency_ms, 3),
            "total_tokens": sum(c.total_tokens or 0 for c in calls) or None,
            "provider_error_type": provider_error,
        },
    }


def _build_client(require: bool) -> Any:
    from src.nlu.model_client import build_nlu_model_client

    client = build_nlu_model_client()
    if client is None and require:
        print("LỖI: chế độ live cần API key + RUN_LIVE_LLM=1 và cấu hình provider hợp lệ.", file=sys.stderr)
        sys.exit(2)
    return client


def _collect_samples(
    mode: str, *, judge: bool = False, datasets: list[str] | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    # offline = TẤT ĐỊNH, KHÔNG mạng: BẮT BUỘC dùng FakeReasoningModel. KHÔNG để None —
    # None khiến graph tự dựng client THẬT từ env key (nếu có) và lặng lẽ gọi API, làm
    # "offline" hết offline và mọi call bị eval bỏ đếm (bug harness §2026-08-07).
    offline_model = mode == "offline"
    if mode == "offline":
        files = sorted(DATASETS_DIR.glob("*.jsonl"))
        client = None
    elif mode == "live-smoke":
        files = [SMOKE_FILE]
        client = _build_client(require=True)
    elif mode == "live-eval":
        files = sorted(DATASETS_DIR.glob("*.jsonl"))
        client = _build_client(require=True)
    else:
        raise ValueError(f"mode không hợp lệ: {mode}")

    # Lọc subset dataset để verify NHANH một cluster (vd chỉ clarification_cases) mà không
    # phải chạy lại toàn bộ golden set. Khớp theo tên file (có/không đuôi .jsonl).
    if datasets:
        wanted = {d if d.endswith(".jsonl") else f"{d}.jsonl" for d in datasets}
        files = [f for f in files if f.name in wanted]
        if not files:
            raise SystemExit(f"--datasets không khớp file nào trong {DATASETS_DIR} (mode={mode}).")

    judge_client = None
    if judge:
        from src.evaluation.judge import build_judge_client

        judge_client = build_judge_client()
        if judge_client is None:
            print("CẢNH BÁO: --judge cần API key; bỏ qua chấm judge.", file=sys.stderr)

    samples: list[dict[str, Any]] = []
    logs: list[dict[str, Any]] = []
    per_file: dict[str, int] = {}
    for path in files:
        records = load_jsonl(path)
        per_file[path.name] = len(records)
        for rec in records:
            reasoning_model = _fake_reasoning_model() if offline_model else None
            s = run_record(rec, client=client, reasoning_model=reasoning_model, judge_client=judge_client)
            logs.append(s.pop("log"))
            samples.append(s)
    return samples, logs, per_file


def _fake_reasoning_model() -> Any:
    from src.core.reasoning import FakeReasoningModel

    return FakeReasoningModel()


def evaluate(
    mode: str = "offline", *, return_samples: bool = False, judge: bool = False, datasets: list[str] | None = None
) -> Any:
    samples, logs, per_file = _collect_samples(mode, judge=judge, datasets=datasets)
    metrics = compute_metrics(samples)
    metrics["mode"] = mode
    metrics["per_file"] = per_file
    if mode.startswith("live"):
        metrics["live_api_calls"] = sum(1 for s in samples if s["route"] == "llm")
    from src.evaluation.judge import aggregate_judge

    judge_block = aggregate_judge(samples)
    if judge_block:
        metrics["judge"] = judge_block
    # Subset chạy ghi artifact RIÊNG (*-subset) để không đè bản full baseline.
    _write_artifacts(f"{mode}-subset" if datasets else mode, metrics, logs, samples)
    return (metrics, samples) if return_samples else metrics


def _write_artifacts(mode: str, metrics: dict[str, Any], logs: list[dict], samples: list[dict]) -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / f"nlu_eval_{mode}.json").write_text(
        json.dumps({"metrics": metrics, "requests": logs}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # failure list (không kèm raw response) — đủ field để CHẨN ĐOÁN node nào sai (concern #5).
    failures = [_failure_record(s) for s in samples if _is_failure(s)]
    (RESULTS_DIR / f"nlu_eval_{mode}_failures.json").write_text(
        json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _failure_record(s: dict) -> dict[str, Any]:
    a = s["actual"]
    return {
        "id": s["id"],
        "utterance": s.get("utterance", ""),
        "route": s["route"],
        "expected_route": s.get("expected_route"),
        "expect": s["expect"],
        "actual": {
            "decision": a.get("decision"),
            "utterance_type": a.get("utterance_type"),
            "targets": a.get("targets"),
            "area": a.get("area"),
            "outcome": a.get("outcome"),
        },
        # Chuỗi chẩn đoán: request_type → goal → plan → missing_information.
        "request_type": a.get("request_type"),
        "goal_schema_decision": a.get("goal_schema_decision"),
        "goal_description": a.get("goal_description"),
        "plan_actions": a.get("plan_actions"),
        "missing_information": a.get("missing_information"),
        "schema_repairs": s.get("schema_repairs", 0),
    }


def _print_failures(samples: list[dict], *, limit: int = 25) -> None:
    """In từng case fail: utterance → expected → actual, kèm request_type/goal/plan/missing.

    Không phải đoán node nào sai — chuỗi đủ để lần ngược tới node lỗi (concern #5)."""
    fails = [s for s in samples if _is_failure(s)]
    if not fails:
        print("\n--- failures: none ---")
        return
    print(f"\n--- failures ({len(fails)} / {len(samples)}) ---")
    for s in fails[:limit]:
        a = s["actual"]
        print(f"\n[{s['id']}] route={s['route']} exp_route={s.get('expected_route')}")
        print(f"  utterance         {s.get('utterance', '')!r}")
        print(f"  expected          {s['expect']}")
        print(f"  actual.decision   {a.get('decision')}   utterance_type={a.get('utterance_type')}   targets={a.get('targets')}")
        print(f"  request_type      {a.get('request_type')}   goal_schema_decision={a.get('goal_schema_decision')}")
        print(f"  goal_description  {(a.get('goal_description') or '')[:110]!r}")
        print(f"  plan_actions      {a.get('plan_actions')}")
        if a.get("missing_information"):
            print(f"  missing_info      {a.get('missing_information')}")
    if len(fails) > limit:
        print(f"\n  … +{len(fails) - limit} case nữa (xem *_failures.json)")


def _is_failure(s: dict) -> bool:
    e, a = s["expect"], s["actual"]
    # Outcome-based: nhãn intent tự do không chấm; soi utterance_type/decision/targets.
    for k in ("utterance_type", "decision", "outcome"):
        if k in e and e[k] != a.get(k):
            return True
    if "targets" in e and sorted(e["targets"]) != sorted(a.get("targets") or []):
        return True
    return False


def _check_gates(data: dict[str, Any], gates: dict[str, tuple[str, float]]) -> list[str]:
    failed = []
    for key, (op, threshold) in gates.items():
        val = data.get(key)
        if val is None:
            continue
        if op == "<=" and val > threshold:
            failed.append(f"{key}={val} vi phạm <= {threshold}")
        if op == ">=" and val < threshold:
            failed.append(f"{key}={val} vi phạm >= {threshold}")
    return failed


def check_hard_gates(metrics: dict[str, Any]) -> list[str]:
    return _check_gates(metrics["overall"], HARD_GATES)


def check_soft_gates(metrics: dict[str, Any]) -> list[str]:
    return _check_gates(metrics["overall"], SOFT_GATES) + _check_gates(metrics.get("runtime", {}), SOFT_GATES_RUNTIME)


def _print_report(metrics: dict[str, Any]) -> None:
    print(f"=== NLU evaluation [{metrics['mode']}] ===")
    print(f"samples={metrics['n_samples']} deterministic={metrics['n_deterministic']} llm={metrics['n_llm']}")
    if "live_api_calls" in metrics:
        print(f"live_api_calls={metrics['live_api_calls']}")
    for block in ("overall", "deterministic_route", "llm_route"):
        data = metrics.get(block)
        if not data:
            continue
        print(f"\n--- {block} ---")
        for k, v in data.items():
            if k.startswith("_counts"):
                print(f"  {k:34} {v}")
                continue
            print(f"  {k:34} {v}")
    print("\n--- runtime ---")
    for k, v in metrics["runtime"].items():
        print(f"  {k:34} {v}")
    if metrics.get("judge"):
        print("\n--- judge (LLM-as-judge, không phải hard gate) ---")
        for k, v in metrics["judge"].items():
            print(f"  {k:34} {v}")


# Khoá metric theo dõi độ ổn định qua nhiều lần chạy (concern #7).
_ROBUSTNESS_KEYS = (
    ("overall", "decision_accuracy"),
    ("overall", "clarification_recall"),
    ("overall", "utterance_type_accuracy"),
    ("overall", "novel_goal_success_rate"),
    ("overall", "hallucinated_device_rate"),
    ("runtime", "first_pass_schema_validity"),
    ("runtime", "llm_path_latency_ms_p95"),
)


def _mean_std(xs: list[float]) -> tuple[float, float]:
    xs = [x for x in xs if x is not None]
    if not xs:
        return 0.0, 0.0
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs) / len(xs)
    return round(mean, 4), round(var**0.5, 4)


def run_repeated(mode: str, runs: int) -> None:
    """Chạy cùng benchmark `runs` lần, in mean/std + pass-rate cho hard gate (concern #7)."""
    all_metrics: list[dict[str, Any]] = []
    gate_pass = 0
    for i in range(runs):
        print(f"\n########## run {i + 1}/{runs} [{mode}] ##########")
        metrics = evaluate(mode)
        _print_report(metrics)
        hard = check_hard_gates(metrics)
        gate_pass += int(not hard)
        if hard:
            print("HARD GATE FAILED:", "; ".join(hard), file=sys.stderr)
        all_metrics.append(metrics)

    print(f"\n========== robustness over {runs} runs [{mode}] ==========")
    print(f"  hard_gate_pass_rate               {gate_pass}/{runs}")
    for block, key in _ROBUSTNESS_KEYS:
        vals = [m.get(block, {}).get(key) for m in all_metrics]
        if all(v is None for v in vals):
            continue
        mean, std = _mean_std([v for v in vals if v is not None])
        print(f"  {block}.{key:32} mean={mean}  std={std}  vals={[v for v in vals]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["offline", "live-smoke", "live-eval"], default="offline")
    ap.add_argument("--repeat", type=int, default=1, help="Chạy N lần, báo mean/std + pass-rate (robustness).")
    ap.add_argument("--show-failures", action="store_true", help="In chi tiết từng case fail để chẩn đoán.")
    ap.add_argument("--judge", action="store_true", help="Bật LLM-as-judge chấm chất lượng ngữ nghĩa (opt-in, tốn thêm call).")
    ap.add_argument(
        "--datasets",
        nargs="+",
        default=None,
        metavar="NAME",
        help="Chỉ chạy các dataset này (tên file, có/không .jsonl), vd: clarification_cases. "
        "Verify nhanh một cluster; ghi artifact riêng *-subset để không đè bản full.",
    )
    args = ap.parse_args()

    if args.repeat > 1:
        run_repeated(args.mode, args.repeat)
        return

    metrics, samples = evaluate(args.mode, return_samples=True, judge=args.judge, datasets=args.datasets)
    _print_report(metrics)
    if args.show_failures:
        _print_failures(samples)

    soft = check_soft_gates(metrics)
    if soft:
        print("\nSOFT GATE (cảnh báo, không fail):", file=sys.stderr)
        for f in soft:
            print(f"  - {f}", file=sys.stderr)

    failed = check_hard_gates(metrics)
    if failed:
        print("\nHARD GATE FAILED:", file=sys.stderr)
        for f in failed:
            print(f"  - {f}", file=sys.stderr)
        sys.exit(1)
    print("\nHard gates: PASS")


if __name__ == "__main__":
    main()
