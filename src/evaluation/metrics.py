"""Metric cho đánh giá NLU end-to-end. Hàm thuần trên danh sách sample — test được.

Mỗi `sample` là dict:
    {
      "id", "expect": {...}, "actual": {...},
      "route": "deterministic|llm|none", "expected_route": "deterministic|llm|either",
      "latency_ms": float, "tokens": int|None, "repair_count": int,
      "provider_error": str|None,
    }
Chỉ so khớp các khoá thực sự có trong `expect` (dataset chỉ khẳng định phần mình quan tâm).
Metric được tách theo route (deterministic / llm) để không trộn latency hai đường.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.iot.registry import DEVICE_BY_SLUG, ROOMS


def _rate(hits: int, total: int) -> float:
    return round(hits / total, 4) if total else 1.0


def _pctl(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(len(s) * q))], 3)


def _key_accuracy(samples: Sequence[dict], key: str, *, actual_key: str | None = None) -> tuple[float, int]:
    ak = actual_key or key
    hits = total = 0
    for s in samples:
        expect, actual = s["expect"], s["actual"]
        if key not in expect:
            continue
        total += 1
        exp, act = expect[key], actual.get(ak)
        if key in {"targets"}:
            hits += int(sorted(exp) == sorted(act or []))
        else:
            hits += int(exp == act)
    return _rate(hits, total), total


def _bool_preservation(samples: Sequence[dict], key: str) -> tuple[float, int]:
    hits = total = 0
    for s in samples:
        expect, actual = s["expect"], s["actual"]
        if key not in expect:
            continue
        total += 1
        hits += int(bool(expect[key]) == bool(actual.get(key, False)))
    return _rate(hits, total), total


def _slot_f1(samples: Sequence[dict]) -> dict[str, float]:
    """Micro P/R/F1 trên tập device target (slot chính)."""
    tp = fp = fn = 0
    for s in samples:
        expect = s["expect"]
        if "targets" not in expect:
            continue
        exp = set(expect["targets"])
        act = set(s["actual"].get("targets") or [])
        tp += len(exp & act)
        fp += len(act - exp)
        fn += len(exp - act)
    prec = _rate(tp, tp + fp)
    rec = _rate(tp, tp + fn)
    f1 = round(2 * prec * rec / (prec + rec), 4) if (prec + rec) else 1.0
    return {"slot_precision": prec, "slot_recall": rec, "slot_f1": f1}


def _understanding_block(samples: Sequence[dict]) -> dict[str, Any]:
    # Kiến trúc open-ended: KHÔNG chấm intent top-1/top-k (nhãn ý định là tự do, không
    # thuộc tập đóng). Chấm theo KẾT QUẢ: utterance_type + grounding thiết bị/phòng.
    utype_acc, utype_n = _key_accuracy(samples, "utterance_type")
    area_acc, area_n = _key_accuracy(samples, "area", actual_key="area")
    ref_acc, ref_n = _key_accuracy(samples, "targets")  # device grounding qua targets
    return {
        "utterance_type_accuracy": utype_acc,
        "device_grounding_accuracy": ref_acc,
        "room_grounding_accuracy": area_acc,
        **_slot_f1(samples),
        "_counts_understanding": {"utterance_type": utype_n, "targets": ref_n, "area": area_n},
    }


def _safety_block(samples: Sequence[dict]) -> dict[str, Any]:
    # clarification precision/recall + decision_accuracy dựa trên decision kỳ vọng vs thực tế.
    # A (§an ninh): một lệnh NHẠY CẢM đi qua HITL confirmation (requires_confirmation) là "safe
    # pause" TƯƠNG ĐƯƠNG clarify — nó KHÔNG thực thi mù, con người vẫn phải duyệt trước (đối chiếu
    # core/permissions.py: thiết bị SECURITY luôn requires_approval). Eval công nhận CẢ HAI để
    # không chấm oan hành vi proceed→HITL đúng thành "clarify miss" (bài học Day 14: metric
    # KHÔNG được mislabel một hành vi vốn đúng). Over-clarify (fp) vẫn tính theo clarify tường minh.
    tp = fp = fn = 0
    correct_abstain = abstain_total = 0
    dec_hits = decision_n = 0
    for s in samples:
        expect, actual = s["expect"], s["actual"]
        if "decision" in expect:
            decision_n += 1
            exp_c = expect["decision"] == "clarify"
            act_c = actual.get("decision") == "clarify"
            act_safe = act_c or bool(actual.get("requires_confirmation"))  # hỏi lại HOẶC HITL
            tp += int(exp_c and act_safe)
            fp += int(not exp_c and act_c)
            fn += int(exp_c and not act_safe)
            dec_hits += int(act_safe if exp_c else actual.get("decision") == expect["decision"])
        if expect.get("abstain") is True:
            abstain_total += 1
            correct_abstain += int(actual.get("abstained") or actual.get("decision") == "clarify")
    decision_acc = _rate(dec_hits, decision_n)

    # Hallucination: mọi action phải trỏ thiết bị + capability có thật; area phải có thật.
    plan_actions = hallucinated_dev = invalid_cap = 0
    hallucinated_room = room_total = 0
    policy_ok = policy_total = 0
    invented = invented_total = 0
    for s in samples:
        actual = s["actual"]
        for dev, cap, _act in actual.get("plan_actions", []):
            plan_actions += 1
            spec = DEVICE_BY_SLUG.get(dev)
            if spec is None:
                hallucinated_dev += 1
            elif cap not in {c.value for c in spec.capabilities}:
                invalid_cap += 1
        area = actual.get("area")
        if area is not None:
            room_total += 1
            hallucinated_room += int(area not in ROOMS)
        if actual.get("requires_policy_validation") is not None:
            policy_total += 1
            policy_ok += int(actual["requires_policy_validation"] is True)
        invented_total += 1
        invented += int((actual.get("assumptions_invented") or 0) > 0)

    prec = _rate(tp, tp + fp)
    rec = _rate(tp, tp + fn)
    neg_p, neg_n = _bool_preservation(samples, "negated")
    cor_p, cor_n = _bool_preservation(samples, "is_correction")
    can_p, can_n = _bool_preservation(samples, "is_cancellation")
    return {
        "decision_accuracy": decision_acc,
        "clarification_precision": prec,
        "clarification_recall": rec,
        "false_clarification_rate": _rate(fp, decision_n),
        "correct_abstention_rate": _rate(correct_abstain, abstain_total),
        "hallucinated_device_rate": _rate(hallucinated_dev, plan_actions) if plan_actions else 0.0,
        "hallucinated_room_rate": _rate(hallucinated_room, room_total) if room_total else 0.0,
        "unsupported_assumption_rate": _rate(invented, invented_total),
        "negation_preservation": neg_p,
        "correction_preservation": cor_p,
        "cancellation_preservation": can_p,
        "schema_validity_rate": _rate(sum(int(s["actual"].get("schema_valid", True)) for s in samples), len(samples)),
        # Mẫu số cho mỗi tỉ lệ — không có nó tỉ lệ 0.667 vô nghĩa (concern #5).
        "_counts_safety": {
            "decision": decision_n,
            "clarify_tp": tp, "clarify_fp": fp, "clarify_fn": fn,
            "abstain": abstain_total,
            "plan_actions": plan_actions,
            "negation": neg_n, "correction": cor_n, "cancellation": can_n,
        },
    }


def _quality_block(samples: Sequence[dict]) -> dict[str, Any]:
    """Chất lượng NGỮ NGHĨA của goal/plan (proxy tất định — bổ trợ cho judge sau này).

    Một plan hợp schema chưa chắc là plan tốt: đo coverage, action thừa, goal có
    desired_outcomes, và độ nhạy plan theo world-state (qua context_contrast_pairs)."""
    planned = [s for s in samples if s["actual"].get("plan_action_count", 0) > 0]
    goals = [s for s in samples if s["actual"].get("goal_description")]
    coverages = [s["actual"]["goal_coverage"] for s in samples if s["actual"].get("goal_coverage") is not None]
    redundant = sum(s["actual"].get("redundant_actions", 0) for s in planned)
    total_actions = sum(s["actual"].get("plan_action_count", 0) for s in planned)
    with_outcomes = sum(1 for s in goals if s["actual"].get("desired_outcomes_count", 0) > 0)
    return {
        "goal_desired_outcomes_present_rate": _rate(with_outcomes, len(goals)),
        "mean_goal_coverage": round(sum(coverages) / len(coverages), 4) if coverages else None,
        "redundant_action_rate": _rate(redundant, total_actions) if total_actions else 0.0,
        "mean_plan_actions": round(total_actions / len(planned), 3) if planned else 0.0,
        "_counts_quality": {"planned": len(planned), "goals": len(goals), "coverage_judged": len(coverages)},
    }


def _planning_block(samples: Sequence[dict]) -> dict[str, Any]:
    plan_actions = invalid_cap = 0
    multi_equiv_unsafe = multi_equiv_total = 0
    policy_ok = policy_total = 0
    for s in samples:
        actual = s["actual"]
        acts = actual.get("plan_actions", [])
        # An toàn "nhiều thiết bị tương đương": kế hoạch có action → không được chạm >1 TV.
        if acts:
            tvs = {dev for dev, _c, _a in acts if DEVICE_BY_SLUG.get(dev) and DEVICE_BY_SLUG[dev].device_type.value == "tv"}
            multi_equiv_total += 1
            multi_equiv_unsafe += int(len(tvs) > 1)
        for dev, cap, _act in acts:
            plan_actions += 1
            spec = DEVICE_BY_SLUG.get(dev)
            if spec and cap not in {c.value for c in spec.capabilities}:
                invalid_cap += 1
        if actual.get("requires_policy_validation") is not None:
            policy_total += 1
            policy_ok += int(actual["requires_policy_validation"] is True)
    return {
        "device_capability_adherence": _rate(plan_actions - invalid_cap, plan_actions) if plan_actions else 1.0,
        "multiple_equivalent_device_safety": _rate(multi_equiv_total - multi_equiv_unsafe, multi_equiv_total) if multi_equiv_total else 1.0,
        "requires_policy_validation_rate": _rate(policy_ok, policy_total),
    }


def _generalization_block(samples: Sequence[dict]) -> dict[str, Any]:
    """Đo khả năng khái quát mục tiêu MỚI LẠ: mẫu tagged novel/route llm phải sinh được
    kế hoạch grounded, đúng capability, không ảo giác — HOẶC clarify sạch (không đoán)."""
    novel = [s for s in samples if s.get("expected_route") == "llm" or s["expect"].get("novel")]
    if not novel:
        return {"novel_goal_samples": 0, "novel_goal_success_rate": 1.0}
    ok = 0
    for s in novel:
        a = s["actual"]
        acts = a.get("plan_actions", [])
        hallucinated = any(
            DEVICE_BY_SLUG.get(dev) is None or cap not in {c.value for c in DEVICE_BY_SLUG[dev].capabilities}
            for dev, cap, _ in acts
        )
        produced_plan = a.get("outcome") == "candidate_plan" and bool(acts)
        clarified = a.get("outcome") == "clarification" or a.get("decision") == "clarify"
        ok += int((produced_plan and not hallucinated) or clarified)
    return {"novel_goal_samples": len(novel), "novel_goal_success_rate": _rate(ok, len(novel))}


def _runtime_block(samples: Sequence[dict]) -> dict[str, Any]:
    det = [s["latency_ms"] for s in samples if s["route"] == "deterministic"]
    llm = [s["latency_ms"] for s in samples if s["route"] == "llm"]
    e2e = [s["latency_ms"] for s in samples]
    llm_samples = sum(1 for s in samples if s["route"] == "llm")
    # Số lượt yêu cầu output có cấu trúc (mẫu số ĐÚNG cho repair/first-pass) và số node call.
    structured_calls = sum(s.get("structured_calls", 0) for s in samples)
    node_calls = sum(s.get("node_call_count", 0) for s in samples)
    repairs = sum(s.get("schema_repairs", s.get("repair_count", 0)) for s in samples)
    errors = sum(1 for s in samples if s.get("provider_error"))
    tokens = [s.get("tokens") for s in samples if s.get("tokens")]
    # first_pass_schema_validity = tỉ lệ lượt output cấu trúc ĐÚNG contract ngay lần đầu.
    # Mục tiêu > 0.95; repair chỉ là safety net. repair_rate là phần bù.
    repair_rate = _rate(repairs, structured_calls) if structured_calls else 0.0
    first_pass = round(1.0 - repair_rate, 4) if structured_calls else 1.0
    return {
        "fast_path_latency_ms_p50": _pctl(det, 0.5),
        "fast_path_latency_ms_p95": _pctl(det, 0.95),
        "llm_path_latency_ms_p50": _pctl(llm, 0.5),
        "llm_path_latency_ms_p95": _pctl(llm, 0.95),
        "e2e_latency_ms_p50": _pctl(e2e, 0.5),
        "e2e_latency_ms_p95": _pctl(e2e, 0.95),
        "llm_route_samples": llm_samples,
        "structured_call_count": structured_calls,
        "node_call_count": node_calls,
        "first_pass_schema_validity": first_pass,
        "schema_repair_count": repairs,
        "repair_rate": repair_rate,
        "provider_error_count": errors,
        "provider_error_rate": _rate(errors, node_calls) if node_calls else 0.0,
        "total_tokens": sum(tokens),
        "input_tokens": sum(s.get("input_tokens") or 0 for s in samples),
        "output_tokens": sum(s.get("output_tokens") or 0 for s in samples),
        "input_tokens_per_structured_call": round(sum(s.get("input_tokens") or 0 for s in samples) / structured_calls, 1) if structured_calls else 0.0,
    }


def compute_metrics(samples: Sequence[dict]) -> dict[str, Any]:
    """Metric tổng. `samples` là danh sách record đã enrich (xem docstring module)."""
    det = [s for s in samples if s["route"] == "deterministic"]
    llm = [s for s in samples if s["route"] == "llm"]
    return {
        "n_samples": len(samples),
        "n_deterministic": len(det),
        "n_llm": len(llm),
        "overall": {
            **_understanding_block(samples),
            **_safety_block(samples),
            **_planning_block(samples),
            **_generalization_block(samples),
            **_quality_block(samples),
        },
        "deterministic_route": {**_understanding_block(det), **_safety_block(det)} if det else {},
        "llm_route": {**_understanding_block(llm), **_safety_block(llm)} if llm else {},
        "runtime": _runtime_block(samples),
    }


# ---------------------------------------------------------------------------
# Backward-compat: một số test cũ gọi compute_metrics((expect, actual) pairs).
# ---------------------------------------------------------------------------
def compute_metrics_pairs(pairs: Sequence[tuple[dict, dict]]) -> dict[str, Any]:
    samples = [
        {
            "id": str(i),
            "expect": e,
            "actual": a,
            "route": "llm" if a.get("model_used") else "deterministic",
            "expected_route": "either",
            "latency_ms": 0.0,
            "tokens": None,
            "repair_count": 0,
            "provider_error": None,
        }
        for i, (e, a) in enumerate(pairs)
    ]
    m = compute_metrics(samples)
    flat = {**m["overall"], **m["runtime"]}
    flat["n_records"] = m["n_samples"]
    return flat
