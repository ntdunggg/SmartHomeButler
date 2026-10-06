"""Registry-grounded semantic evaluation for the 200-case live dataset.

The release score is deterministic around the model call: fixtures must resolve
against the real registry and every applicable case checks decision, room, device,
capability, action, constraints, and memory write→retrieve evidence.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent.cognitive.ledger import LedgerStore
from src.agent.memory.event_store import EventStore
from src.agent.memory.hierarchical_retriever import retrieve_memory
from src.agent.memory.profile_store import ProfileStore
from src.agent.memory.turn_store import TurnStore
from src.agent.pipeline import PipelineDeps
from src.agent.preference.preference_store import PreferenceStore
from src.domain.enums import ActionType, Capability
from src.iot.registry import (
    DEVICE_SPECS,
    KIDS_ROOM,
    KITCHEN,
    LIVING_ROOM,
    PARENTS_ROOM,
    ROOMS,
    spec_for,
)
from src.services.pipeline_bridge import ReasoningResult, reason

_ACCEPT: dict[str, set[str]] = {
    "PROCEED": {"candidate_plan"},
    "CLARIFY": {"clarification"},
    "CANCEL": {"cancelled"},
    "NO_ACTION": {"no_action", "answer"},
    "STATE_QUERY": {"answer", "no_action"},
}
_DEFAULT_LOCATION = LIVING_ROOM

# Dataset vocabulary is normalized only in the evaluator. Production ambiguity
# remains unchanged. Every destination below is a real registry room.
_ROOM_ALIAS_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"phòng ngủ\s*(?:trẻ em|của bé)", re.IGNORECASE), KIDS_ROOM),
    (re.compile(r"phòng ngủ(?!\s*(?:bố mẹ|ba mẹ|con|của con|trẻ em|của bé))", re.IGNORECASE), PARENTS_ROOM),
    # Do not rewrite the same words when they are part of a device name
    # ("đèn bàn làm việc", "đèn bàn ăn").  The evaluator must normalize room
    # vocabulary without corrupting the target entity it is supposed to score.
    (re.compile(r"phòng làm việc|chỗ làm việc|(?<!đèn )bàn làm việc|góc làm việc", re.IGNORECASE), PARENTS_ROOM),
    (re.compile(r"phòng ăn|(?<!đèn )bàn ăn", re.IGNORECASE), KITCHEN),
    (re.compile(r"hành lang", re.IGNORECASE), LIVING_ROOM),
]
_EXPECTED_ROOM = {
    "current_room": _DEFAULT_LOCATION,
    "phòng khách": LIVING_ROOM,
    "phòng bếp": KITCHEN,
    "phòng ngủ": PARENTS_ROOM,
    "phòng ngủ của bé": KIDS_ROOM,
    "phòng ngủ trẻ em": KIDS_ROOM,
    "phòng làm việc": PARENTS_ROOM,
    "phòng ăn": KITCHEN,
    "hành lang": LIVING_ROOM,
}


def map_rooms_to_registry(text: str) -> str:
    out = text
    for pattern, canonical in _ROOM_ALIAS_RULES:
        out = pattern.sub(canonical, out)
    return out


def _canonical_room(value: str | None) -> str | None:
    if not value:
        return None
    if value in ROOMS:
        return value
    return _EXPECTED_ROOM.get(value.strip().lower())


def _target_matches(target: str, spec) -> bool:
    value = target.strip().lower()
    dtype = spec.device_type.value
    caps = {cap.value for cap in spec.capabilities}
    roles = set(spec.semantic_roles)
    if value == "tv":
        return dtype == "tv"
    if value in {"điều hòa", "temperature"}:
        return dtype == "air_conditioner"
    if value == "climate":
        return dtype in {"air_conditioner", "air_purifier", "window", "curtain"}
    if value == "air_quality":
        return dtype in {"air_purifier", "window"}
    if value == "máy lọc không khí":
        return dtype == "air_purifier"
    if value == "environment":
        return bool(caps & {"temperature", "fan_speed", "brightness", "position"})
    if value == "lighting":
        return dtype in {"light", "curtain"}
    if value == "đèn":
        return dtype == "light"
    if value == "đèn trần":
        return dtype == "light" and "đèn trần" in set(spec.aliases)
    if value == "đèn bàn":
        return dtype == "light" and bool(roles & {"task_lighting", "work_or_study_light"})
    if value == "đèn ngủ":
        return dtype == "light" and "night_light" in roles
    if value == "loa":
        return dtype == "speaker"
    if value == "rèm":
        return dtype == "curtain"
    if value == "cửa sổ":
        return dtype == "window"
    if value == "quạt":
        return "fan_speed" in caps
    return False


def _expected_semantics(adjustment: str) -> tuple[frozenset[str], frozenset[str]]:
    value = adjustment.lower()
    if value.startswith("preference_") or value in {
        "cancel", "query_state", "maintain_state", "no_change",
        "accept_current_brightness", "accept_current_volume", "reject_brightness_30",
    }:
        return frozenset(), frozenset()
    if "brightness" in value or "glare" in value or "warmer_light" in value:
        capabilities = {"brightness", "color_temp"} if "warmer_light" in value else {"brightness"}
        if value.startswith(("increase_", "slight_increase", "moderate_increase")):
            actions = {"increase", "set", "turn_on"}
        elif value.startswith(("decrease_", "reduce_", "moderate_decrease")):
            actions = {"decrease", "set", "turn_off"}
        else:
            actions = {"set"}
        return frozenset(capabilities), frozenset(actions)
    if "temperature" in value:
        if value.startswith("increase_"):
            actions = {"increase", "set", "turn_on"}
        elif value.startswith("decrease_"):
            actions = {"decrease", "set", "turn_on"}
        else:
            actions = {"set"}
        return frozenset({"temperature"}), frozenset(actions)
    if value in {"cooler", "warmer", "moderate_cooling", "moderate_warming", "cool_or_ventilate", "adjust_for_comfort"}:
        direction = "decrease" if value in {"cooler", "moderate_cooling", "cool_or_ventilate"} else "increase"
        return (
            frozenset({"temperature", "fan_speed", "position"}),
            frozenset({direction, "set", "open", "close", "turn_on"}),
        )
    if "volume" in value:
        actions = {"increase", "set"} if value.startswith("increase_") else {"decrease", "set"}
        return frozenset({"volume"}), frozenset(actions)
    if "fan_speed" in value or value == "improve_airflow":
        return frozenset({"fan_speed"}), frozenset({"increase", "set", "turn_on"})
    if "opening" in value or value in {"partial_open", "slight_opening"}:
        if value.startswith("decrease_"):
            actions = {"decrease", "set", "close"}
        elif value.startswith("increase_"):
            actions = {"increase", "set", "open"}
        else:
            actions = {"set", "open", "close"}
        return frozenset({"position"}), frozenset(actions)
    if value == "open":
        return frozenset({"position"}), frozenset({"open", "set"})
    if value == "close":
        return frozenset({"position"}), frozenset({"close", "set"})
    if value == "turn_on":
        return frozenset({Capability.ON_OFF.value}), frozenset({ActionType.TURN_ON.value})
    if value == "turn_off":
        return frozenset({Capability.ON_OFF.value}), frozenset({ActionType.TURN_OFF.value})
    return frozenset(), frozenset()


@dataclass(slots=True)
class FixtureContract:
    decision: str
    room: str | None
    device_ids: frozenset[str]
    capabilities: frozenset[str]
    actions: frozenset[str]
    constraints: tuple[str, ...]
    memory_expectation: str
    errors: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


def compile_fixture(case: dict[str, Any]) -> FixtureContract:
    expected = case.get("expected") or {}
    decision = str(expected.get("decision") or "")
    room_raw = expected.get("room")
    room = _canonical_room(room_raw)
    target = str(expected.get("target") or "")
    capabilities, actions = _expected_semantics(str(expected.get("adjustment") or ""))
    errors: list[str] = []
    if decision not in _ACCEPT:
        errors.append(f"unknown decision {decision!r}")
    if room_raw and room is None:
        errors.append(f"room {room_raw!r} does not resolve to registry")

    candidates = [
        spec for spec in DEVICE_SPECS
        if (room is None or spec.room == room) and (not target or _target_matches(target, spec))
    ]
    if target and not candidates:
        errors.append(f"target {target!r} has no registry device in {room or 'household'}")
    if capabilities and candidates and not any(
        capabilities & {cap.value for cap in spec.capabilities} for spec in candidates
    ):
        errors.append(f"capabilities {sorted(capabilities)!r} unsupported by target {target!r}")
    constraints = tuple(str(item) for item in (expected.get("constraints") or []))
    if any(not item.strip() for item in constraints):
        errors.append("empty constraint label")
    return FixtureContract(
        decision=decision,
        room=room,
        device_ids=frozenset(spec.slug for spec in candidates),
        capabilities=capabilities,
        actions=actions,
        constraints=constraints,
        memory_expectation=str(expected.get("memory_expectation") or "NONE"),
        errors=errors,
    )


@dataclass(slots=True)
class CaseEvaluation:
    case_id: str
    category: str
    outcome: str
    expected: str
    fixture_valid: bool
    decision_ok: bool
    room_ok: bool
    device_ok: bool
    capability_ok: bool
    action_ok: bool
    constraints_ok: bool
    memory_ok: bool
    # Chạm thiết bị của một lượt ĐÃ BỊ THAY THẾ, hoặc thiết bị ngoài phòng mong đợi:
    # ngữ cảnh cũ vẫn đang lái hành động. Đây là thứ cổng phát hành gọi tên.
    stale_target_leakage: int
    # Đúng phòng, chưa từng là đích lượt trước, chỉ là chọn sai LỚP thiết bị
    # ("ánh sáng tự nhiên" → bật đèn điện). Chất lượng planner, không phải rò ngữ cảnh.
    device_class_mismatch: int = 0
    exception: str | None = None
    details: list[str] = field(default_factory=list)

    @property
    def semantic_ok(self) -> bool:
        return all((self.room_ok, self.device_ok, self.capability_ok, self.action_ok, self.constraints_ok, self.memory_ok))

    @property
    def passed(self) -> bool:
        return self.fixture_valid and self.exception is None and self.decision_ok and self.semantic_ok


def _numeric_values(actions, capability: str) -> list[float]:
    values: list[float] = []
    for action in actions:
        if str(getattr(action.capability, "value", action.capability)) != capability:
            continue
        for value in (action.params or {}).values():
            if isinstance(value, int | float) and not isinstance(value, bool):
                values.append(float(value))
    return values


def _constraints_preserved(labels: tuple[str, ...], result: ReasoningResult, actions) -> bool:
    if not labels:
        return True
    actual_tokens = set(result.explicit_constraints)
    action_names = {str(getattr(action.action, "value", action.action)) for action in actions}
    goal = result.semantic_goal
    for label in labels:
        match = re.fullmatch(r"(max|min)_(brightness|temperature|volume|opening)_(\d+)", label)
        if match:
            kind, dimension, raw_bound = match.groups()
            capability = "position" if dimension == "opening" else dimension
            values = _numeric_values(actions, capability)
            if not values:
                return False
            bound = float(raw_bound)
            if kind == "max" and any(value > bound for value in values):
                return False
            if kind == "min" and any(value < bound for value in values):
                return False
            continue
        if "correction" in label and not bool(goal and goal.is_correction):
            return False
        if label in {"keep_current_state", "no_change"} and actions:
            return False
        if label.startswith("do_not_turn_on") and "turn_on" in action_names:
            return False
        if label.startswith("do_not_turn_off") and "turn_off" in action_names:
            return False
        if label.startswith(("exclude_", "keep_", "avoid_", "only_")) and not actual_tokens and not actions:
            return False
    return True


_MEMORY_TYPES = {
    "PREFERENCE_EVENT": {"preference_stated"},
    "PLAN_ACCEPTED_EVENT": {"plan_accepted"},
    "PLAN_REJECTED_EVENT": {"plan_rejected"},
    "PREFERENCE_CORRECTION_EVENT": {"preference_correction", "preference_stated"},
}


def _memory_roundtrip(contract: FixtureContract, deps: PipelineDeps, *, actor: str, query: str, now: datetime) -> bool:
    if contract.memory_expectation == "NONE":
        return True
    allowed = _MEMORY_TYPES.get(contract.memory_expectation)
    if not allowed:
        return False
    written = [event for event in deps.event_store.by_actor(actor) if event.event_type in allowed]
    if not written:
        return False
    retrieved = retrieve_memory(
        event_store=deps.event_store,
        turn_store=deps.turn_store,
        query_text=query,
        actor=actor,
        now=now,
    )
    retrieved_ids = {event.event_id for event in retrieved.events}
    return any(event.event_id in retrieved_ids for event in written)


def _fresh_deps(model) -> PipelineDeps:
    return PipelineDeps(
        ledger_store=LedgerStore(),
        event_store=EventStore(),
        turn_store=TurnStore(),
        preference_store=PreferenceStore(),
        profile_store=ProfileStore(),
        model_client=model,
    )


def _needs_location(case: dict[str, Any]) -> bool:
    expected = case.get("expected") or {}
    return bool(expected.get("should_use_context")) or expected.get("room") == "current_room"


def run_case(case: dict[str, Any], model, *, now: datetime, map_rooms: bool = True) -> CaseEvaluation:
    contract = compile_fixture(case)
    deps = _fresh_deps(model)
    conv = str(case.get("id") or "case")
    actor = f"eval:{conv}"
    location = _DEFAULT_LOCATION if _needs_location(case) else None
    result: ReasoningResult | None = None
    exception: str | None = None
    query_parts: list[str] = []
    # Đích của từng lượt: cần biết một thiết bị ở lượt cuối có phải "di sản" của một
    # lượt trước đó hay không — đó mới là định nghĩa của rò ngữ cảnh cũ.
    targets_per_turn: list[frozenset[str]] = []
    first_user_turn = True
    try:
        for message in case.get("messages", []):
            if message.get("role") != "user":
                continue
            text = str(message.get("content") or "")
            query_parts.append(text)
            if map_rooms:
                text = map_rooms_to_registry(text)
            result = reason(
                message=text,
                conversation_id=conv,
                user_id=actor,
                role="owner",
                now=now,
                speaker_location=location if first_user_turn else None,
                deps=deps,
                model_client=model,
            )
            first_user_turn = False
            targets_per_turn.append(
                frozenset(a.device_id for a in result.candidate_plan.actions)
                if result.candidate_plan
                else frozenset()
            )
    except Exception as exc:  # report gate, never abort the suite
        exception = f"{type(exc).__name__}: {exc}"

    if result is None:
        outcome = "error" if exception else "no_action"
        actions = []
        actual_ids: set[str] = set()
    else:
        outcome = result.outcome
        actions = list(result.candidate_plan.actions) if result.candidate_plan else []
        actual_ids = {action.device_id for action in actions}

    decision_ok = outcome in _ACCEPT.get(contract.decision, set())
    applicable_plan = contract.decision == "PROCEED"
    goal = result.semantic_goal if result else None
    actual_rooms = {spec.room for device_id in actual_ids if (spec := spec_for(device_id)) is not None}
    goal_room = goal.target_area if goal is not None else None
    # Phòng được chấm trên THIẾT BỊ THỰC SỰ bị tác động, không trên `goal.target_area`.
    # Lượt tiếp nối ("giảm thêm chút") kế thừa grounding đã chốt ở ledger nên goal hợp lệ
    # mà vẫn để target_area rỗng — đòi nó lặp lại phòng là chấm một trường nội bộ chứ
    # không phải hành vi quan sát được, và đánh trượt cả những lượt ground ĐÚNG phòng.
    # Không có action nào thì target_area là tín hiệu phòng duy nhất còn lại.
    room_ok = not applicable_plan or contract.room is None or (
        actual_rooms == {contract.room} if actual_rooms else goal_room == contract.room
    )
    device_ok = not applicable_plan or not contract.device_ids or bool(actual_ids) and actual_ids <= contract.device_ids
    actual_caps = {str(getattr(action.capability, "value", action.capability)) for action in actions}
    actual_actions = {str(getattr(action.action, "value", action.action)) for action in actions}
    capability_ok = not applicable_plan or not contract.capabilities or bool(actual_caps & contract.capabilities)
    action_ok = not applicable_plan or not contract.actions or bool(actual_actions & contract.actions)
    constraints_ok = result is not None and _constraints_preserved(contract.constraints, result, actions)
    memory_ok = _memory_roundtrip(contract, deps, actor=actor, query=" ".join(query_parts), now=now)
    # Chỉ số cũ chỉ là `device_ok` đảo dấu, nên nó không hề đo được tính "cũ" của ngữ cảnh
    # và cổng phát hành mang tên rò-rỉ thực chất đang chấm độ khớp lớp thiết bị. Tách đôi:
    #   - RÒ RỈ = chạm ra ngoài phòng mong đợi, HOẶC chạm lại đích của một lượt đã bị thay
    #     thế ("Loa thì thôi" mà vẫn bật loa). Ngữ cảnh lẽ ra phải bỏ vẫn đang lái hành động.
    #   - LỆCH LỚP THIẾT BỊ = đúng phòng, chưa từng là đích lượt trước, chỉ là chọn sai loại.
    # Cả hai đều đã bị `device_ok` đánh trượt; tách ra để cổng đo đúng thứ nó gọi tên.
    unexpected_ids = actual_ids - contract.device_ids if contract.device_ids else set()
    cross_room_ids = (
        {device_id for device_id in actual_ids if (spec := spec_for(device_id)) and spec.room != contract.room}
        if applicable_plan and contract.room
        else set()
    )
    superseded_ids = frozenset().union(*targets_per_turn[:-1]) if len(targets_per_turn) > 1 else frozenset()
    carried_over_ids = unexpected_ids & superseded_ids
    stale = int(bool(applicable_plan and (cross_room_ids or carried_over_ids)))
    class_mismatch = int(bool(applicable_plan and (unexpected_ids - cross_room_ids - carried_over_ids)))
    details = list(contract.errors)
    for name, ok in (
        ("decision", decision_ok), ("room", room_ok), ("device", device_ok),
        ("capability", capability_ok), ("action", action_ok),
        ("constraints", constraints_ok), ("memory", memory_ok),
    ):
        if not ok:
            details.append(name)
    return CaseEvaluation(
        case_id=conv,
        category=str(case.get("category") or "?"),
        outcome=outcome,
        expected=contract.decision,
        fixture_valid=contract.valid,
        decision_ok=decision_ok,
        room_ok=room_ok,
        device_ok=device_ok,
        capability_ok=capability_ok,
        action_ok=action_ok,
        constraints_ok=constraints_ok,
        memory_ok=memory_ok,
        stale_target_leakage=stale,
        device_class_mismatch=class_mismatch,
        exception=exception,
        details=details,
    )


def _model(offline: bool):
    if offline:
        from src.core.reasoning import FakeReasoningModel

        return FakeReasoningModel()
    from src.nlu.model_client import build_nlu_model_client

    client = build_nlu_model_client()
    if client is None:
        raise SystemExit("Không dựng được model client; dùng --offline để smoke.")
    return client


def _report(results: list[CaseEvaluation], *, dataset: str, model_name: str, room_map: bool) -> dict[str, Any]:
    by_category: dict[str, list[CaseEvaluation]] = defaultdict(list)
    for result in results:
        by_category[result.category].append(result)
    memory_cases = [result for result in results if result.category == "memory_feedback"]
    return {
        "dataset": dataset,
        "model": model_name,
        "room_map": room_map,
        "total": len(results),
        "passed": sum(result.passed for result in results),
        "exceptions": sum(result.exception is not None for result in results),
        "fixture_valid": sum(result.fixture_valid for result in results),
        "fixture_valid_rate": round(sum(result.fixture_valid for result in results) / len(results), 4) if results else 0.0,
        "decision_accuracy": round(sum(result.decision_ok for result in results) / len(results), 4) if results else 0.0,
        "semantic_accuracy": round(sum(result.semantic_ok for result in results) / len(results), 4) if results else 0.0,
        "stale_target_leakage": sum(result.stale_target_leakage for result in results),
        "device_class_mismatch": sum(result.device_class_mismatch for result in results),
        "memory_roundtrip_rate": round(sum(result.memory_ok for result in memory_cases) / len(memory_cases), 4) if memory_cases else 1.0,
        "by_category": {
            category: {
                "passed": sum(result.passed for result in items),
                "total": len(items),
                "decision_acc": round(sum(result.decision_ok for result in items) / len(items), 3),
                "semantic_acc": round(sum(result.semantic_ok for result in items) / len(items), 3),
            }
            for category, items in sorted(by_category.items())
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Registry-grounded live semantic evaluation")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--limit", type=int, default=12, help="0 = all")
    parser.add_argument("--category")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--no-room-map", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--enforce-gate", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--out", type=Path, help="Write the aggregate JSON report")
    args = parser.parse_args()

    data = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    cases = list(data.get("cases") or [])
    if args.category:
        cases = [case for case in cases if case.get("category") == args.category]
    if args.limit > 0:
        cases = cases[: args.limit]

    contracts = [(str(case.get("id")), compile_fixture(case)) for case in cases]
    if args.validate_only:
        invalid = [{"id": case_id, "errors": contract.errors} for case_id, contract in contracts if not contract.valid]
        report = {
            "total": len(contracts),
            "valid": len(contracts) - len(invalid),
            "fixture_valid_rate": round((len(contracts) - len(invalid)) / len(contracts), 4) if contracts else 0.0,
            "invalid": invalid,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(1 if invalid else 0)

    model = _model(args.offline)
    now = datetime(2026, 8, 18, 20, 0, tzinfo=UTC)
    results = [run_case(case, model, now=now, map_rooms=not args.no_room_map) for case in cases]
    evaluation_report: dict[str, Any] = _report(
        results,
        dataset=str((data.get("metadata") or {}).get("name") or args.dataset),
        model_name=(
            "offline-fake"
            if args.offline
            else f"{getattr(model, 'provider', 'unknown')}/{getattr(model, 'model', 'unknown')}"
        ),
        room_map=not args.no_room_map,
    )
    evaluation_report["run_mode"] = "offline" if args.offline else "production-live"
    evaluation_report["requested_cases"] = len(cases)
    print(json.dumps(evaluation_report, ensure_ascii=False, indent=2))
    if args.out is not None:
        args.out.write_text(
            json.dumps(evaluation_report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.verbose:
        for result in results:
            if not result.passed:
                print(json.dumps({
                    "id": result.case_id,
                    "category": result.category,
                    "expected": result.expected,
                    "outcome": result.outcome,
                    "details": result.details,
                    "exception": result.exception,
                }, ensure_ascii=False))
    category_gate = all(item["semantic_acc"] >= 0.80 for item in evaluation_report["by_category"].values())
    release_ok = all((
        evaluation_report["exceptions"] == 0,
        evaluation_report["fixture_valid_rate"] == 1.0,
        evaluation_report["stale_target_leakage"] == 0,
        evaluation_report["memory_roundtrip_rate"] == 1.0,
        category_gate,
    ))
    if args.enforce_gate and not release_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
