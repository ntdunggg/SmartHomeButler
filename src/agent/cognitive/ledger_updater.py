"""Ledger updater (spec §17.1, §18) — read → interpret delta → merge.

Bất biến (spec §73):
- #8 Explicit user constraint sống qua nhiều lượt → constraints CHỈ tích luỹ, không bị
  một lượt sau vô tình xoá.
- #9 Rejected assumption giữ nguyên đến khi user đổi rõ ràng → planner turn sau KHÔNG
  tái tạo assumption đã bị bác (§18).

Ràng buộc được suy ra từ TÍN HIỆU CÓ CẤU TRÚC của SemanticGoal (polarity/action_hint/
excluded_device_ids do normalizer + understanding trích), KHÔNG từ so khớp chuỗi câu
(spec §8, §71: không biến ví dụ thành keyword rule). Token ràng buộc có dạng
``<kind>:<slug>`` để validator/planner tra cứu tất định.
"""

from __future__ import annotations

from typing import Any

from src.agent.schemas import (
    LedgerBoundState,
    LedgerEvidence,
    LedgerGroupExclusionState,
    LedgerNoChangeState,
    RequirementLedger,
    SemanticGoal,
)
from src.domain.action_registry import action_for_semantic, semantic_parameter_key
from src.domain.enums import ActionType, Capability
from src.nlu.ontology import UtteranceType

# Action hint hàm ý "tắt/đóng/giảm" — phủ định các động từ này = ràng buộc GIỮ nguyên.
_OFF_LIKE_ACTIONS = frozenset({"turn_off", "close", "decrease", "unlock"})
_ON_LIKE_ACTIONS = frozenset({"turn_on", "open", "increase", "set", "lock"})


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _dimensions_for_sub_action(
    *,
    device_id: str,
    action: str | None,
    parameters: dict[str, Any],
) -> list[str]:
    """Return canonical capability dimensions carried by one explicit clause."""
    from src.iot.registry import spec_for

    dimensions = {
        key for key in parameters
        if key in {capability.value for capability in Capability}
    }
    spec = spec_for(device_id)
    if spec is None:
        return sorted(dimensions)

    try:
        semantic_action = ActionType(action) if action else None
    except ValueError:
        semantic_action = None
    for capability in spec.capabilities:
        value = capability.value
        parameter_key = semantic_parameter_key(capability)
        if value in parameters or (parameter_key and parameter_key in parameters):
            dimensions.add(value)
        # Power/open/close/lock clauses carry their dimension without a numeric
        # parameter.  Numeric SET/relative clauses are selected by the parameter
        # key above so a multi-capability device does not gain unrelated dimensions.
        if (
            semantic_action is not None
            and semantic_action not in {ActionType.SET, ActionType.INCREASE, ActionType.DECREASE}
            and action_for_semantic(capability, semantic_action) is not None
        ):
            dimensions.add(value)
    return sorted(dimensions)


def _compound_goal_state(goal: SemanticGoal) -> tuple[list[str], list[dict[str, Any]]]:
    """Serialize room/dimensions/per-target clauses without re-reading raw chat."""
    dimensions: set[str] = set()
    for outcome in goal.desired_outcomes:
        dimensions.update(
            key for key in (*outcome.target_state, *outcome.relative_change)
            if key in {capability.value for capability in Capability}
        )

    sub_actions: list[dict[str, Any]] = []
    for device_id in goal.target_device_ids:
        action = goal.target_actions.get(device_id, goal.action_hint)
        parameters = dict(
            goal.target_parameters.get(device_id) or {}
            if goal.target_parameters
            else goal.parameters
        )
        clause_dimensions = _dimensions_for_sub_action(
            device_id=device_id,
            action=action,
            parameters=parameters,
        )
        dimensions.update(clause_dimensions)
        sub_actions.append(
            {
                "device_id": device_id,
                "action": action,
                "parameters": parameters,
                "dimensions": clause_dimensions,
            }
        )
    return sorted(dimensions), sub_actions


def _record_resolution_evidence(
    ledger: RequirementLedger,
    evidence_trace: list[dict[str, Any]] | None,
    *,
    turn: int,
) -> set[str]:
    """Persist exact current-turn resolution provenance and field-level history."""
    trace = [dict(item) for item in (evidence_trace or []) if isinstance(item, dict)]
    ledger.last_resolution_evidence_trace = trace
    resolved_fields: set[str] = set()
    for item in trace:
        field = str(item.get("resolved_field") or item.get("field") or "")
        if not field:
            continue
        resolved_fields.add(field)
        raw_confidence = item.get("confidence", 1.0)
        try:
            confidence = max(0.0, min(1.0, float(raw_confidence)))
        except (TypeError, ValueError):
            confidence = 1.0
        ledger.evidence.append(
            LedgerEvidence(
                field=field,
                value=item.get("value"),
                source=str(item.get("source") or "context_resolver"),
                confidence=confidence,
                turn=turn,
            )
        )
    return resolved_fields


def _record_first_class_constraints(
    ledger: RequirementLedger,
    goal: SemanticGoal,
    *,
    turn: int,
) -> None:
    """Merge typed bound/exclusion/no-change state; string tokens are mirrors."""
    dimensions, _ = _compound_goal_state(goal)
    inherited_dimensions = list((ledger.current_goal or {}).get("dimensions") or [])
    effective_dimensions = dimensions or inherited_dimensions

    for token in goal.explicit_constraints:
        parts = token.split(":", 4)
        if len(parts) != 5 or parts[0] != "bound" or parts[1] not in {"min", "max"}:
            continue
        _, kind, dimension, raw_value, scope = parts
        try:
            value = float(raw_value)
        except ValueError:
            continue
        bound_state = LedgerBoundState(
            kind=kind,
            dimension=dimension,
            value=value,
            scope=scope or "*",
            turn=turn,
        )
        bound_signature = (
            bound_state.kind,
            bound_state.dimension,
            bound_state.value,
            bound_state.scope,
        )
        if not any(
            (item.kind, item.dimension, item.value, item.scope) == bound_signature
            for item in ledger.bounds
        ):
            ledger.bounds.append(bound_state)

    if goal.excluded_device_ids:
        excluded = list(dict.fromkeys(goal.excluded_device_ids))
        prior_anchor = list(ledger.confirmed_facts.get("devices") or [])
        anchor = list(dict.fromkeys([*prior_anchor, *goal.target_device_ids, *excluded]))
        exclusion_state = LedgerGroupExclusionState(
            excluded_device_ids=excluded,
            anchor_device_ids=anchor,
            room=goal.target_area or ledger.confirmed_facts.get("room"),
            dimensions=effective_dimensions,
            turn=turn,
        )
        exclusion_signature = (
            tuple(exclusion_state.excluded_device_ids),
            tuple(exclusion_state.anchor_device_ids),
            exclusion_state.room,
            tuple(exclusion_state.dimensions),
        )
        if not any(
            (
                tuple(item.excluded_device_ids),
                tuple(item.anchor_device_ids),
                item.room,
                tuple(item.dimensions),
            ) == exclusion_signature
            for item in ledger.group_exclusions
        ):
            ledger.group_exclusions.append(exclusion_state)

    if goal.no_change_device_ids:
        device_ids = list(dict.fromkeys(goal.no_change_device_ids))
        no_change_state = LedgerNoChangeState(
            device_ids=device_ids,
            room=goal.target_area or ledger.confirmed_facts.get("room"),
            dimensions=effective_dimensions,
            turn=turn,
        )
        no_change_signature = (
            tuple(no_change_state.device_ids),
            no_change_state.room,
            tuple(no_change_state.dimensions),
        )
        if not any(
            (tuple(item.device_ids), item.room, tuple(item.dimensions)) == no_change_signature
            for item in ledger.no_change
        ):
            ledger.no_change.append(no_change_state)


def derive_constraints(goal: SemanticGoal) -> tuple[list[str], list[str]]:
    """Suy ra (constraints, rejected_assumptions) từ tín hiệu có cấu trúc của goal.

    - ``excluded_device_ids`` (vd "nóng nhưng đừng bật điều hoà"): mục tiêu DƯƠNG nhưng
      tránh thiết bị Y → constraint ``avoid:Y`` + rejected ``use:Y``.
    - Phủ định một động từ tắt/đóng (vd "đừng tắt điều hoà"): giữ thiết bị đang chạy →
      constraint ``keep_on:Y`` + rejected ``<action>:Y``.
    - Phủ định một động từ bật/mở: constraint ``keep_off:Y`` + rejected ``<action>:Y``.
    """
    constraints: list[str] = []
    rejected: list[str] = []

    for token in goal.explicit_constraints:
        _append_unique(constraints, token)

    for slug in goal.excluded_device_ids:
        _append_unique(constraints, f"avoid:{slug}")
        _append_unique(rejected, f"use:{slug}")

    if goal.negated and goal.action_hint:
        for slug in goal.target_device_ids:
            if goal.action_hint in _OFF_LIKE_ACTIONS:
                _append_unique(constraints, f"keep_on:{slug}")
            elif goal.action_hint in _ON_LIKE_ACTIONS:
                _append_unique(constraints, f"keep_off:{slug}")
            _append_unique(rejected, f"{goal.action_hint}:{slug}")

    return constraints, rejected


def _is_direct_device_command(goal: SemanticGoal) -> bool:
    """Lượt này có phải CHÍNH người dùng ra lệnh trực tiếp lên thiết bị đã ground tất định?

    Đây là bằng chứng duy nhất đủ tư cách LẬT một ràng buộc người dùng đã nêu (§18). Cố ý
    KHÔNG dùng chung với `_is_inferred_goal` của pipeline: hàm đó hỏi "goal này có xuất xứ
    tường minh không" và cố ý coi routine phát lại là tường minh (để giữ security action);
    ở đây câu hỏi khác hẳn — "người dùng có đang tự tay ra lệnh ngược lại NGAY LƯỢT NÀY
    không". Một routine phát lại không phải người dùng đang nói, nên không được quyền gỡ
    lệnh cấm họ đã đặt.
    """
    return (
        not goal.negated
        and not goal.is_cancellation
        and not goal.user_defined_routine
        and goal.utterance_type == UtteranceType.DEVICE_COMMAND
        and goal.action_hint is not None
        and bool(goal.target_device_ids)
        # Ground do LLM đoán không đủ tư cách hất một ràng buộc tất định (§4, §5.5).
        and goal.target_devices_deterministic
    )


def revoke_superseded_constraints(ledger: RequirementLedger, goal: SemanticGoal) -> list[str]:
    """Gỡ các ràng buộc mà chính người dùng vừa lật lại. Mutate `ledger`, trả token đã gỡ.

    Bất biến #8 nói ràng buộc sống qua nhiều lượt để một lượt sau không VÔ TÌNH xoá nó. Nó
    không nói người dùng bị khoá vĩnh viễn vào lệnh cấm của chính mình: "đừng bật đèn chùm"
    rồi sau đó "bật đèn chùm đi" là một lần đổi ý tường minh, không phải tai nạn. Trước bản
    vá này ledger không có ĐƯỜNG THU HỒI nào, nên lệnh thứ hai bị validator chặn im lặng mãi.

    Phạm vi cố ý hẹp, để không mở đường cho agent tự nói mình thoát khỏi lệnh cấm:
    - chỉ lệnh trực tiếp, tất định, gọi đích danh thiết bị (`_is_direct_device_command`);
    - chỉ ràng buộc trên ĐÚNG thiết bị đó;
    - chỉ khi cực tính NGƯỢC nhau (`keep_off` gặp lệnh bật, `keep_on` gặp lệnh tắt), cộng
      `avoid` — vốn nghĩa là "đừng dùng thiết bị này", nên mọi lệnh trực tiếp lên nó đều lật.

    Một mục tiêu SUY DIỄN (làm mát/comfort/routine) không lọt qua cổng trên, nên
    "nóng quá nhưng đừng bật điều hoà" → "làm mát thêm chút nữa" vẫn KHÔNG bật điều hoà.
    """
    if not _is_direct_device_command(goal):
        return []
    action = goal.action_hint or ""
    if action in _ON_LIKE_ACTIONS:
        superseded = {"avoid", "keep_off"}
    elif action in _OFF_LIKE_ACTIONS:
        superseded = {"avoid", "keep_on"}
    else:
        return []

    revoked: list[str] = []
    targets = set(goal.target_device_ids)
    for token in list(ledger.constraints):
        kind, _, slug = token.partition(":")
        if slug in targets and kind in superseded:
            ledger.constraints.remove(token)
            revoked.append(token)
    # Assumption đã bị bác cũng phải nhả ra, nếu không validator vẫn chặn cùng hành động qua
    # nhánh `rejected:` (§18) và bản vá chỉ có tác dụng một nửa.
    for token in list(ledger.rejected_assumptions):
        act, _, slug = token.partition(":")
        if slug in targets and act in (action, "use"):
            ledger.rejected_assumptions.remove(token)
            revoked.append(f"rejected:{token}")
    # Typed no-change/exclusion state is canonical and must be revoked together
    # with its compatibility token when the user explicitly changes that device.
    retained_no_change: list[LedgerNoChangeState] = []
    for no_change_state in ledger.no_change:
        remaining = [device_id for device_id in no_change_state.device_ids if device_id not in targets]
        if len(remaining) != len(no_change_state.device_ids):
            revoked.extend(
                f"no_change:{device_id}"
                for device_id in no_change_state.device_ids
                if device_id in targets
            )
        if remaining:
            retained_no_change.append(no_change_state.model_copy(update={"device_ids": remaining}))
    ledger.no_change = retained_no_change

    retained_exclusions: list[LedgerGroupExclusionState] = []
    for exclusion_state in ledger.group_exclusions:
        remaining = [
            device_id
            for device_id in exclusion_state.excluded_device_ids
            if device_id not in targets
        ]
        if len(remaining) != len(exclusion_state.excluded_device_ids):
            revoked.extend(
                f"group_exclusion:{device_id}"
                for device_id in exclusion_state.excluded_device_ids
                if device_id in targets
            )
        if remaining:
            retained_exclusions.append(
                exclusion_state.model_copy(update={"excluded_device_ids": remaining})
            )
    ledger.group_exclusions = retained_exclusions
    return revoked


def explain_constraint(token: str) -> str:
    """Diễn giải một token ràng buộc thành lý do đọc được cho người dùng.

    Ở cạnh chính nơi ĐỊNH NGHĨA token (§4: một nguồn sự thật cho một luật ngữ nghĩa) — nếu
    đặt bên tầng trình bày thì mỗi lần thêm `kind` mới sẽ có nơi diễn giải sai/thiếu.
    """
    from src.iot.registry import spec_for

    kind, _, slug = token.partition(":")
    spec = spec_for(slug)
    name = spec.name if spec is not None else slug
    if kind == "keep_off":
        return f"bạn đã dặn đừng bật {name}"
    if kind == "keep_on":
        return f"bạn đã dặn đừng tắt {name}"
    if kind == "avoid":
        return f"bạn đã dặn tránh dùng {name}"
    return f"ràng buộc bạn đã đặt cho {name}"


def numeric_bounds_for(
    ledger: RequirementLedger | None,
    *,
    device_id: str,
    capability: str,
) -> tuple[float | None, float | None]:
    """Return durable user min/max bounds applicable to one grounded action."""
    if ledger is None:
        return None, None
    from src.iot.registry import spec_for

    spec = spec_for(device_id)
    room = spec.room if spec is not None else None
    minimum: float | None = None
    maximum: float | None = None
    for bound in ledger.bounds:
        if bound.dimension != capability or bound.scope not in {"*", device_id, room}:
            continue
        if bound.kind == "min":
            minimum = bound.value if minimum is None else max(minimum, bound.value)
        elif bound.kind == "max":
            maximum = bound.value if maximum is None else min(maximum, bound.value)
    # Compatibility fallback for snapshots written before typed bound state.
    for token in ledger.constraints:
        parts = token.split(":", 4)
        if len(parts) != 5 or parts[0] != "bound":
            continue
        _, kind, dimension, raw_value, scope = parts
        if dimension != capability or scope not in {"*", device_id, room}:
            continue
        try:
            value = float(raw_value)
        except ValueError:
            continue
        if kind == "min":
            minimum = value if minimum is None else max(minimum, value)
        elif kind == "max":
            maximum = value if maximum is None else min(maximum, value)
    return minimum, maximum


def update_ledger(
    ledger: RequirementLedger,
    goal: SemanticGoal | None,
    *,
    turn: int,
    conversation_id: str | None = None,
    missing_information: list[str] | None = None,
    pending_clarification: dict | None = None,
    evidence_trace: list[dict[str, Any]] | None = None,
) -> RequirementLedger:
    """Merge delta của lượt hiện tại vào ledger, giữ nguyên constraint/rejected cũ.

    KHÔNG rebuild từ raw history — chỉ áp delta (spec §17.1). Trả một ledger MỚI
    (không mutate đầu vào).

    `pending_clarification` (không None) = lượt này HỎI làm rõ → lưu tường minh câu hỏi treo.
    Khi advance một mục tiêu thực (goal actionable, không phải persist-pending) → XOÁ pending
    (đã được trả lời / thay bằng mục tiêu mới). Huỷ cũng xoá pending.
    """
    merged = ledger.model_copy(deep=True)
    if conversation_id:
        merged.conversation_id = conversation_id
    merged.last_updated_turn = turn
    resolved_evidence_fields = _record_resolution_evidence(
        merged,
        evidence_trace,
        turn=turn,
    )

    if goal is None:
        # Không có mục tiêu mới (câu rỗng/không hiểu): giữ nguyên state, chỉ ghi nhận lượt.
        if missing_information is not None:
            merged.missing_information = list(missing_information)
        if pending_clarification is not None:
            merged.pending_clarification = dict(pending_clarification)
        return merged

    # Huỷ: xoá mục tiêu đang treo NHƯNG giữ constraint & rejected (chúng durable, §18).
    if goal.is_cancellation:
        merged.current_goal = {}
        merged.desired_outcomes = []
        merged.missing_information = []
        merged.pending_clarification = {}
        return merged

    new_constraints, new_rejected = derive_constraints(goal)
    _record_first_class_constraints(merged, goal, turn=turn)
    constraint_overlay = bool(merged.current_goal) and not goal.desired_outcomes and bool(
        goal.explicit_constraints
        or (goal.negated and goal.action_hint and goal.target_device_ids)
    )
    if constraint_overlay:
        # A standalone constraint refines the active objective; it is not a new
        # objective and must not replace its room/device/outcome grounding.
        for constraint in new_constraints:
            _append_unique(merged.constraints, constraint)
        for rejected in new_rejected:
            _append_unique(merged.rejected_assumptions, rejected)
        # Preserve the mentioned entity as the latest discourse referent for a
        # pure prohibition, while leaving the active objective/outcomes intact.
        # Structured preservation overlays (avoid/keep) retain the objective's
        # existing group anchor so a bare continuation can still refine it.
        if goal.negated and goal.target_device_ids:
            merged.confirmed_facts["devices"] = list(goal.target_device_ids)
        if missing_information is not None:
            merged.missing_information = list(missing_information)
        merged.pending_clarification = dict(pending_clarification or {})
        for constraint in new_constraints:
            merged.evidence.append(
                LedgerEvidence(field="constraint", value=constraint, source="user_utterance", turn=turn)
            )
        return merged

    # Delta mục tiêu: lượt lệnh mới thay mục tiêu đang hoạt động; refinement thì goal
    # đã được orchestrator hợp nhất trước khi tới đây (last_utterance re-run).
    dimensions, sub_actions = _compound_goal_state(goal)
    merged.current_goal = {
        "goal_description": goal.goal_description or goal.intent,
        "intent": goal.intent,
        "utterance_type": goal.utterance_type.value,
        "action_hint": goal.action_hint,
        "parameters": dict(goal.parameters),
        "target_area": goal.target_area,
        "target_device_ids": list(goal.target_device_ids),
        "target_actions": dict(goal.target_actions),
        "target_parameters": {
            device_id: dict(parameters)
            for device_id, parameters in goal.target_parameters.items()
        },
        "dimensions": dimensions,
        "sub_actions": sub_actions,
        "negated": goal.negated,
        # Câu gốc lượt này — mốc để tinh chỉnh/slot-fill đa lượt chạy lại đúng mục tiêu (§5, §17).
        "raw_utterance": goal.raw_utterance,
    }
    merged.desired_outcomes = [o.model_dump() for o in goal.desired_outcomes]

    # confirmed_facts: chỉ neo field đã THỰC SỰ giải được. Một goal đang hỏi lại có thể
    # vẫn mang target_area tạm từ presence/focus; field đó không được biến thành fact đã
    # xác nhận trong ledger, nếu không lượt slot-fill sau sẽ kế thừa một giả định mà chính
    # hệ thống vừa thừa nhận là còn thiếu.
    #
    # "Còn thiếu" chỉ chặn việc PROMOTE phán đoán tạm của lượt NÀY thành fact — nó không
    # thu hồi fact lượt TRƯỚC đã chốt. Im lặng không phải mâu thuẫn: một lượt sau nói
    # "đừng bật đèn bàn" mà không nhắc phòng thì phòng đã xác lập vẫn còn nguyên hiệu lực.
    # Xoá ở đây làm mọi hội thoại ≥3 lượt mất chỗ đứng và rơi xuống hỏi lại "phòng nào?".
    # Thu hồi fact là việc của MÂU THUẪN TƯỜNG MINH (câu nêu phòng khác) hoặc topic switch —
    # cả hai đều đi đường ghi đè/reset riêng, không qua nhánh này.
    missing = set(missing_information or [])
    room_missing = bool(missing & {"room", "area"})
    device_missing = bool(missing & {"device", "device_id", "devices"})
    if not room_missing and goal.target_area:
        merged.confirmed_facts["room"] = goal.target_area
    if not device_missing and goal.target_device_ids:
        merged.confirmed_facts["devices"] = list(goal.target_device_ids)

    # Người dùng tự lật lệnh cấm của chính mình bằng một lệnh trực tiếp ngược cực → gỡ
    # ràng buộc TRƯỚC khi tích luỹ, để lượt này thoát validator. Chạy trước cũng để một
    # lượt vừa gỡ vừa đặt ràng buộc mới không tự xoá cái nó vừa đặt.
    revoked = revoke_superseded_constraints(merged, goal)

    # Ràng buộc & assumption bị bác: TÍCH LUỸ, không ghi đè (bất biến #8, #9).
    for c in new_constraints:
        _append_unique(merged.constraints, c)
    for r in new_rejected:
        _append_unique(merged.rejected_assumptions, r)

    for token in revoked:
        merged.evidence.append(
            LedgerEvidence(field="constraint_revoked", value=token, source="user_utterance", turn=turn)
        )

    if missing_information is not None:
        merged.missing_information = list(missing_information)

    # Pending clarification: persist-pending path (clarify) SET; advance path (goal thực) XOÁ.
    if pending_clarification is not None:
        merged.pending_clarification = dict(pending_clarification)
    else:
        merged.pending_clarification = {}

    # Evidence trace (spec §14): ghi lại nguồn cho các field đã chốt lượt này.
    if (
        goal.target_area
        and not room_missing
        and not ({"room", "area"} & resolved_evidence_fields)
    ):
        merged.evidence.append(
            LedgerEvidence(field="room", value=goal.target_area, source="semantic_goal", turn=turn)
        )
    for c in new_constraints:
        merged.evidence.append(LedgerEvidence(field="constraint", value=c, source="user_utterance", turn=turn))

    return merged


def violates_constraint(ledger: RequirementLedger, *, device_id: str, action: str) -> str | None:
    """Một action có phá ràng buộc/assumption đã bác trong ledger không? (spec §18, §47).

    Trả token ràng buộc bị phá (để giải thích), hoặc None nếu hợp lệ. Dùng ở harness
    validator để "explicit constraints preserved" và "no rejected assumption revived".
    """
    off_like = action in _OFF_LIKE_ACTIONS
    on_like = action in _ON_LIKE_ACTIONS
    if any(device_id in state.device_ids for state in ledger.no_change):
        return f"no_change:{device_id}"
    if any(device_id in state.excluded_device_ids for state in ledger.group_exclusions):
        return f"group_exclusion:{device_id}"
    for c in ledger.constraints:
        kind, _, slug = c.partition(":")
        if slug != device_id:
            continue
        if kind == "avoid":
            return c
        if kind == "keep_on" and off_like:
            return c
        if kind == "keep_off" and on_like:
            return c
    # Revive một assumption đã bị bác (vd bật lại đúng thiết bị user cấm dùng).
    if f"{action}:{device_id}" in ledger.rejected_assumptions:
        return f"rejected:{action}:{device_id}"
    return None
