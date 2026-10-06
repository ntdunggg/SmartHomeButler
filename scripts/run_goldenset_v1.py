"""Runner chấm `smart_home_agent_goldenset_v1.jsonl` ở MỨC GIÁ TRỊ (device+capability+value).

Khác `src/agent/evaluation/goldenset_runner.py` (chỉ chấm outcome + có/không thiết bị), runner
này đọc schema goldenset MỚI (`gold.mode`, `gold.required_actions`/`forbidden_actions` kèm
`value`, `context` sensor/device overrides) và kiểm ĐÚNG capability + giá trị mà plan đề xuất.

CHÚ Ý (spec §71): CHỈ đọc goldenset để CHẤM — không nạp câu/ý vào model/few-shot.

Chạy:  python scripts/run_goldenset_v1.py [--objective context_resolution] [--verbose] [--out report.json]
Mặc định offline (FakeReasoningModel qua PipelineDeps); thêm --real để dùng model live.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.agent.pipeline import PipelineDeps
from src.iot.registry import SENSOR_BY_SLUG
from src.services.pipeline_bridge import reason

_ROOT = Path(__file__).resolve().parents[1]
_DATASET = _ROOT / "scripts" / "smart_home_agent_goldenset_v1.jsonl"
_NOW = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)

# gold.mode → outcome mà reason() phát ra. act/plan đều sinh candidate_plan (act=thực thi ngay,
# plan=cần duyệt — khác nhau ở policy gate, không ở outcome của bridge).
_MODE_TO_OUTCOME = {
    "act": {"candidate_plan"},
    "plan": {"candidate_plan"},
    "clarify": {"clarification"},
    # A negative imperative may be represented as `cancelled` or as an explicit
    # no-side-effect acknowledgement (`answer`); both satisfy the cancellation
    # contract, unlike a candidate plan.
    "cancel": {"cancelled", "answer"},
    "answer": {"answer"},
}
# capability số → khoá param trong CandidateAction.params (parity với pipeline_bridge._NUM_PARAM_KEY).
_NUM_PARAM_KEY = {"BRIGHTNESS": "percent", "POSITION": "percent", "VOLUME": "percent",
                  "TEMPERATURE": "temperature", "FAN_SPEED": "level"}


def _live_sensors_from(overrides: dict[str, float] | None) -> list[dict] | None:
    """{slug: value} → list[dict] cho reason(), làm giàu type/room/unit từ registry."""
    if not overrides:
        return None
    out: list[dict] = []
    for slug, value in overrides.items():
        spec = SENSOR_BY_SLUG.get(slug)
        out.append({
            "slug": slug,
            "name": spec.name if spec else slug,
            "sensor_type": spec.sensor_type.value if spec else "",
            "value": float(value),
            "unit": spec.unit if spec else "",
            "room": (spec.room if spec else "") or "",
        })
    return out


def _plan_actions(result) -> list[dict]:
    """Rút (device, capability, action, value) từ candidate_plan để so với gold."""
    if result.candidate_plan is None:
        return []
    rows: list[dict] = []
    for a in result.candidate_plan.actions:
        cap = a.capability.value.upper() if hasattr(a.capability, "value") else str(a.capability).upper()
        val: Any = None
        key = _NUM_PARAM_KEY.get(cap)
        if key and key in (a.params or {}):
            val = a.params[key]
        elif cap in {"HVAC_MODE", "OPERATION_MODE"}:
            val = (a.params or {}).get("mode")
        act = a.action.value if hasattr(a.action, "value") else str(a.action)
        rows.append({"device": a.device_id, "capability": cap, "action": act, "value": val})
    return rows


# Giá trị trạng thái (gold) → tập action tương ứng mà plan phải phát. Bao ON_OFF (on/off),
# POSITION (open/close) và LOCK (locked/unlocked) — trước đây runner chỉ chấm on/off nên POSITION/
# LOCK bị chấm SÓT dù hành vi đúng (vd rèm open, khoá unlocked).
_VALUE_TO_ACTIONS = {
    "on": {"turn_on"}, "off": {"turn_off"},
    "open": {"open"}, "opened": {"open"}, "close": {"close"}, "closed": {"close"},
    "lock": {"lock"}, "locked": {"lock"}, "unlock": {"unlock"}, "unlocked": {"unlock"},
    "reduce_opening": {"close", "decrease", "set"},
}


def _on_off_matches(plan_act: dict, want_value: str) -> bool:
    return plan_act["action"] in _VALUE_TO_ACTIONS.get(want_value, set())


def _action_satisfied(want: dict, plan: list[dict]) -> bool:
    dev = want.get("device")
    cap = (want.get("capability") or "").upper()
    val = want.get("value")
    for pa in plan:
        if pa["device"] != dev:
            continue
        # Setting a positive operating parameter (brightness/temperature/fan/
        # volume) powers the device on in execution._desired_state.  Count that
        # semantic state transition as ON_OFF=on instead of demanding a redundant
        # TURN_ON action that the validator intentionally deduplicates.
        if cap == "ON_OFF" and val == "on" and pa["action"] == "set" and pa["capability"] in {
            "BRIGHTNESS", "TEMPERATURE", "FAN_SPEED", "VOLUME",
        }:
            return True
        if pa["capability"] != cap:
            continue
        if isinstance(val, str):  # ON_OFF / LOCK: "on"/"off"
            if cap == "HVAC_MODE" and val == "dry" and pa.get("value") == "dry":
                return True
            if _on_off_matches(pa, val):
                return True
        elif isinstance(val, int | float):
            if pa["value"] is not None and float(pa["value"]) == float(val):
                return True
        else:
            return True  # value None = chỉ cần đúng device+capability
    return False


def _score_case(case: dict, *, real: bool) -> dict:
    gold = case["gold"]
    ctx = case.get("context") or {}
    live_sensors = _live_sensors_from(ctx.get("sensor_overrides"))
    live_states = ctx.get("device_state_overrides") or None
    location = ctx.get("conversation_location")

    deps = PipelineDeps()
    if real:
        from src.nlu.model_client import build_nlu_model_client
        client = build_nlu_model_client()
        if client is not None:
            deps = PipelineDeps(model_client=client)
    conv = f"gs-{case['id']}"

    result = None
    for turn in case["turns"]:
        if turn["role"] != "user":
            continue
        result = reason(
            message=turn["text"], conversation_id=conv, now=_NOW,
            speaker_location=location, live_device_states=live_states, live_sensors=live_sensors,
            deps=deps,
        )

    plan = _plan_actions(result) if result is not None else []
    got_outcome = result.outcome if result is not None else "none"
    reasons: list[str] = []

    outcome_ok = got_outcome in _MODE_TO_OUTCOME.get(gold.get("mode", ""), set())
    if not outcome_ok:
        reasons.append(f"mode: want {gold.get('mode')}→{_MODE_TO_OUTCOME.get(gold.get('mode'))}, got {got_outcome}")

    missing = [w for w in (gold.get("required_actions") or []) if not _action_satisfied(w, plan)]
    if missing:
        reasons.append(f"missing {len(missing)} required: " + "; ".join(
            f"{w.get('device')}.{w.get('capability')}={w.get('value')}" for w in missing))

    present_forbidden = [w for w in (gold.get("forbidden_actions") or []) if _action_satisfied(w, plan)]
    if present_forbidden:
        reasons.append("forbidden present: " + "; ".join(
            f"{w.get('device')}.{w.get('capability')}={w.get('value')}" for w in present_forbidden))

    passed = outcome_ok and not missing and not present_forbidden
    return {"id": case["id"], "objective": case["objective"], "difficulty": case["difficulty"],
            "passed": passed, "got_outcome": got_outcome, "plan": plan, "reasons": reasons,
            "last_user": [t["text"] for t in case["turns"] if t["role"] == "user"][-1]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(_DATASET))
    ap.add_argument("--objective", default=None, help="Chỉ chấm một objective")
    ap.add_argument("--real", action="store_true", help="Dùng model live thay FakeReasoningModel")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    rows = [json.loads(ln) for ln in Path(args.dataset).read_text(encoding="utf-8").splitlines() if ln.strip()]
    if args.objective:
        rows = [r for r in rows if r["objective"] == args.objective]

    results = [_score_case(c, real=args.real) for c in rows]

    by_obj: dict[str, list[int]] = defaultdict(list)
    for r in results:
        by_obj[r["objective"]].append(1 if r["passed"] else 0)

    print(f"\n=== GOLDENSET v1 ({'LIVE' if args.real else 'offline/Fake'}) — {len(results)} case ===")
    gok = gn = 0
    for obj in sorted(by_obj):
        res = by_obj[obj]
        p, n = sum(res), len(res)
        gok += p
        gn += n
        print(f"  {obj:24} {p:2}/{n:<2}  ({100*p/n:3.0f}%)")
    print(f"  {'TỔNG':24} {gok:2}/{gn:<2}  ({100*gok/gn:3.0f}%)")

    fails = [r for r in results if not r["passed"]]
    if fails:
        print(f"\n=== {len(fails)} CASE KHÔNG ĐẠT ===")
        for r in fails:
            print(f"  [{r['id']}] ({r['difficulty']}) {r['last_user']!r} → {r['got_outcome']}")
            for why in r["reasons"]:
                print(f"        · {why}")
            if args.verbose:
                print(f"        plan={r['plan']}")

    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[report] {args.out}")


if __name__ == "__main__":
    main()
