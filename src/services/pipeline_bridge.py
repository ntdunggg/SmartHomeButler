"""Adapter production cho pipeline multi-agent 5 tầng.

Pipeline chịu trách nhiệm understanding → planning → deterministic validation.
Authorization, HITL, live-state guardrail và execution vẫn do ``agent_runner`` quản lý.
``ReasoningResult`` là contract ổn định giữa hai tầng; không còn đường runtime legacy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.agent.cognitive.ledger import LedgerStore
from src.agent.cognitive.ledger_updater import explain_constraint
from src.agent.pipeline import PipelineDeps, run_planning
from src.agent.schemas import CandidateEnergyPlan, ProposalAction, SemanticGoal, SufficiencyDecision
from src.agent.state import AgentState
from src.domain.enums import ActionType, Capability
from src.nlu.ontology import UtteranceType
from src.nlu.schemas import CandidateAction, CandidatePlan

# capability số → khoá param mà `execution.translate_action` mong đợi (percent/temperature/level).
_NUM_PARAM_KEY = {
    "brightness": "percent",
    "position": "percent",
    "volume": "percent",
    "temperature": "temperature",
    "fan_speed": "level",
    "color_temp": "color_temp",
}
_CHOICE_PARAM_KEY = {
    "hvac_mode": "mode",
    "operation_mode": "mode",
    "away_mode": "away",
    "preset_mode": "preset",
    "media_source": "source",
    "program": "program",
}


def is_pure_prohibition(goal: SemanticGoal | None) -> bool:
    """Câu này có phải LỆNH CẤM THUẦN TUÝ không — tức chỉ "đừng làm X với thiết bị Y", không kèm
    mục tiêu tích cực nào? (KI-01, spec §13).

    Chỉ khi ĐỦ CẢ:
      - phủ định (negated / polarity=negative), VÀ
      - có ĐÍCH cụ thể đã phân giải để từ chối (target_device_ids), VÀ
      - KHÔNG có desired_outcome tích cực nào để theo đuổi.

    Ngược lại KHÔNG phải cấm thuần tuý — KHÔNG short-circuit sang câu trả lời "mình sẽ không...":
      - Câu cảm thán/sentiment ("ồn ào quá không nghỉ được"): phủ định là thành ngữ, không có đích
        thiết bị → phải clarify/act.
      - Mục tiêu TÍCH CỰC + loại trừ ("cho mát hơn nhưng đừng bật điều hoà"): phủ định là RÀNG BUỘC
        của mục tiêu tích cực → phải plan (áp exclusion) hoặc clarify, không từ chối.

    Tổng quát theo cấu trúc goal (đã resolve thiết bị? có mục tiêu tích cực?), KHÔNG theo từ khoá."""
    if goal is None:
        return False
    negated = bool(goal.negated) or goal.polarity == "negative"
    return negated and bool(goal.target_device_ids) and not goal.desired_outcomes


def has_unresolved_exclusion(goal: SemanticGoal | None) -> bool:
    """Mục tiêu TÍCH CỰC kèm phủ định NHƯNG chưa phân giải được loại trừ ("X nhưng đừng Y" với Y mơ
    hồ — loại thiết bị chung, chưa rõ phòng) → ràng buộc chưa chắc chắn.

    Khi đó KHÔNG được lập kế hoạch mù: dễ vô tình làm ĐÚNG thứ người dùng vừa cấm (vd "cho mát hơn
    nhưng đừng bật điều hoà" mà vẫn bật điều hoà). Phải clarify để chốt ràng buộc/cách làm.

    Điều kiện: có desired_outcome tích cực + phủ định (negated/polarity âm) + excluded_device_ids
    RỖNG (không resolve được Y). Nếu Y đã resolve (excluded populated) thì planner tự tránh — không
    rơi vào đây. Tổng quát theo cấu trúc goal, KHÔNG theo từ khoá."""
    if goal is None:
        return False
    negated = bool(goal.negated) or goal.polarity == "negative"
    return bool(goal.desired_outcomes) and negated and not goal.excluded_device_ids


@dataclass(slots=True)
class _Clarification:
    """Khớp interface `.question_vi` mà agent_runner đọc từ clarification cũ."""

    question_vi: str


@dataclass(slots=True)
class ReasoningResult:
    semantic_goal: SemanticGoal | None
    candidate_plan: CandidatePlan | None
    outcome: str  # candidate_plan | clarification | cancelled | answer | no_action
    reply: str = ""
    clarification: _Clarification | None = None
    rl_state: Any = None
    decision_context: dict | None = None
    diagnostics: dict | None = None
    # Durable deterministic constraints are surfaced for the execution boundary;
    # this does not alter understanding/planning behavior.
    explicit_constraints: list[str] = field(default_factory=list)
    rejected_assumptions: list[str] = field(default_factory=list)
    evidence_trace: list[dict[str, Any]] = field(default_factory=list)
    memory_written: dict[str, Any] = field(default_factory=dict)


# Stores partitioned by household_id để đảm bảo tenant boundary và sống qua nhiều lượt.
_HOUSEHOLD_DEPS: dict[int, PipelineDeps] = {}


def _get_or_create_deps(household_id: int | None) -> PipelineDeps:
    hid = household_id or 0
    if hid not in _HOUSEHOLD_DEPS:
        _HOUSEHOLD_DEPS[hid] = PipelineDeps()
    return _HOUSEHOLD_DEPS[hid]


def _to_candidate_action(prop: ProposalAction) -> CandidateAction | None:
    """Dịch ProposalAction (pipeline mới) → CandidateAction (agent_runner cũ tiêu thụ)."""
    try:
        cap = Capability(prop.capability)
    except ValueError:
        return None
    target = prop.target or {}
    # Specialist có thể giữ key bề mặt của NLU (`level`/`percent`) trong target,
    # trong khi capability đã được ground thành `fan_speed`/`brightness`/...
    # Chấp nhận cả hai biểu diễn tương đương; nếu không action SET bị
    # rới xuống fallback TURN_ON và mất giá trị.
    param_name = _NUM_PARAM_KEY.get(prop.capability)
    val = target.get(prop.capability)
    if val is None and param_name is not None:
        val = target.get(param_name)
    if val is None:
        # `value` là khoá SỐ TRUNG TÍNH mà `_extract_params` dùng cho con số trần đi kèm một
        # danh từ dimension ("đặt âm lượng xuống 15"). Thiếu nó ở đây thì đúng cái hỏng mà
        # comment trên mô tả vẫn xảy ra: SET rơi xuống TURN_ON và con số biến mất.
        val = target.get("value")
    is_num = isinstance(val, int | float) and not isinstance(val, bool)
    if prop.capability in _NUM_PARAM_KEY and is_num:
        return CandidateAction(
            device_id=prop.device_id,
            capability=cap,
            action=ActionType.SET,
            params={_NUM_PARAM_KEY[prop.capability]: val},
            reason_vi=prop.reason_vi or "",
        )
    choice_key = _CHOICE_PARAM_KEY.get(prop.capability)
    if choice_key is not None:
        choice = target.get(prop.capability)
        if choice is None:
            choice = target.get(choice_key)
        if choice is not None:
            return CandidateAction(
                device_id=prop.device_id,
                capability=cap,
                action=ActionType.SET,
                params={choice_key: choice},
                reason_vi=prop.reason_vi or "",
            )
    for num_cap, param_name in _NUM_PARAM_KEY.items():
        if num_cap in target and isinstance(target[num_cap], int | float) and not isinstance(target[num_cap], bool):
            target_cap = Capability(num_cap) if num_cap in [c.value for c in Capability] else cap
            return CandidateAction(
                device_id=prop.device_id,
                capability=target_cap,
                action=ActionType.SET,
                params={param_name: target[num_cap]},
                reason_vi=prop.reason_vi or "",
            )
    try:
        at = ActionType(prop.action)
    except ValueError:
        at = ActionType.TURN_ON
    if at == ActionType.SET:
        at = ActionType.TURN_ON
    return CandidateAction(
        device_id=prop.device_id, capability=cap, action=at, params={}, reason_vi=prop.reason_vi or ""
    )


def _candidate_plan_from(
    selected: CandidateEnergyPlan, goal: SemanticGoal | None, validated_ids: set[str]
) -> CandidatePlan:
    """Dịch CandidateEnergyPlan (đã chọn) → CandidatePlan cho downstream."""
    actions: list[CandidateAction] = []
    for prop in selected.actions:
        if prop.device_id not in validated_ids:
            continue
        ca = _to_candidate_action(prop)
        if ca is not None:
            actions.append(ca)
    utype = goal.utterance_type if goal else UtteranceType.UNKNOWN
    return CandidatePlan(
        goal_summary=(goal.goal_description or goal.raw_utterance) if goal else "",
        utterance_type=utype,
        actions=actions,
    )


def _bind_session_deps(
    deps: PipelineDeps,
    *,
    session: Any = None,
    household_id: int | None = None,
    model_client: Any = None,
) -> PipelineDeps:
    """Gắn các adapter cần DB session vào deps (idempotent — gọi lại không đổi gì).

    Ledger là trạng thái hội thoại CANONICAL (§17), nên khi có session nó phải nằm trên
    `SqlLedgerStore`: bản in-memory chết theo tiến trình, kéo theo slot-fill đa lượt và
    các ràng buộc durable (§73 #8/#9) của mọi hội thoại đang dở. Không có session
    (eval offline, unit test) thì giữ nguyên store in-memory đã truyền vào.
    """
    ledger_store: Any = deps.ledger_store
    if session is not None and isinstance(ledger_store, LedgerStore):
        from src.agent.cognitive.ledger_repository import SqlLedgerStore

        ledger_store = SqlLedgerStore(household_id=household_id)

    pref_repo = deps.preference_repo
    if pref_repo is None and session is not None:
        from src.agent.preference.repository import SqlPreferenceRepository

        pref_repo = SqlPreferenceRepository(session)

    if ledger_store is deps.ledger_store and pref_repo is deps.preference_repo and model_client is None:
        return deps
    return PipelineDeps(
        ledger_store=ledger_store,
        event_store=deps.event_store,
        turn_store=deps.turn_store,
        preference_store=deps.preference_store,
        preference_repo=pref_repo,
        profile_store=deps.profile_store,
        knowledge_base=deps.knowledge_base,
        gateway_factory=deps.gateway_factory,
        model_client=model_client if model_client is not None else deps.model_client,
        environment_client=deps.environment_client,
        semantic_cache=deps.semantic_cache,
    )


def _reason_impl(
    *,
    message: str,
    conversation_id: str,
    role: str = "owner",
    user_id: str | None = None,
    household_id: int | None = None,
    session: Any = None,
    now: datetime | None = None,
    speaker_location: str | None = None,
    speaker_location_source: str = "",
    speaker_location_confidence: float = 0.0,
    speaker_home_room: str | None = None,
    speaker_private_room: str | None = None,
    focus_room: str | None = None,
    last_device_id: str | None = None,
    recent_dialogue: list[str] | None = None,
    live_device_states: dict[str, dict] | None = None,
    live_sensors: list[dict] | None = None,
    model_client: Any = None,
    deps: PipelineDeps | None = None,
) -> ReasoningResult:
    """Chạy pipeline mới tới hết validate và ánh xạ về ReasoningResult cho agent_runner.

    `last_device_id`/`recent_dialogue` là tín hiệu ngữ cảnh tuỳ chọn caller có thể cấp
    trực tiếp (spec §14 tier-2/tier-4 của Context Resolver) — trước bản vá này, `reason()`
    không nhận hai tham số này nên pipeline chỉ suy `last_device_id` được từ Ledger cùng
    conversation_id (tier-3), bỏ sót anaphora khi tín hiệu đến từ nơi khác (client hint,
    lịch sử hội thoại do caller tự giữ)."""
    deps = _bind_session_deps(
        deps or _get_or_create_deps(household_id),
        session=session,
        household_id=household_id,
        model_client=model_client,
    )

    state: AgentState = {
        "conversation_id": conversation_id,
        "household_id": household_id,
        "user_id": user_id or "",
        "user_message": message,
        "speaker_role": role,
        "now": now or datetime.now(UTC),
        "speaker_location": speaker_location,
        "speaker_location_source": speaker_location_source,
        "speaker_location_confidence": speaker_location_confidence,
        "speaker_home_room": speaker_home_room,
        "speaker_private_room": speaker_private_room,
        "focus_room": focus_room,
        "last_device_id": last_device_id,
        "recent_dialogue": recent_dialogue or [],
        "live_device_states": live_device_states,
        "live_sensors": live_sensors,
    }
    out = run_planning(state, deps)
    memory_written = dict(out.get("memory_written") or {})

    goal: SemanticGoal | None = out.get("semantic_goal")
    decision = out.get("sufficiency_decision")
    rl_state = out.get("rl_state")
    raw_decision_context = out.get("decision_context")
    decision_context = raw_decision_context if isinstance(raw_decision_context, dict) else None

    # Câu hỏi trạng thái/năng lực đã được trả lời trực tiếp từ snapshot (không lập kế hoạch).
    if out.get("final_status") == "answered":
        return ReasoningResult(
            semantic_goal=goal,
            candidate_plan=None,
            outcome="answer",
            reply=out.get("reply", ""),
            rl_state=rl_state,
            decision_context=decision_context,
            memory_written=memory_written,
        )

    # Huỷ thao tác (normalizer/understanding đánh dấu).
    if goal is not None and getattr(goal, "is_cancellation", False):
        return ReasoningResult(
            semantic_goal=goal,
            candidate_plan=None,
            outcome="cancelled",
            reply="Đã huỷ thao tác.",
            rl_state=rl_state,
            decision_context=decision_context,
        )

    selected: CandidateEnergyPlan | None = out.get("selected_plan")
    validated = out.get("validated_plan") or []
    validated_ids = {a.entity_id for a in validated}

    # Lệnh PHỦ ĐỊNH TƯỜNG MINH thuần tuý ("đừng tắt đèn phòng khách"): trả lời xác nhận sẽ KHÔNG làm.
    if goal is not None and is_pure_prohibition(goal) and not validated_ids:
        from src.iot.registry import DEVICE_BY_SLUG

        action_hint = goal.action_hint or ""
        verb = {
            "turn_off": "tắt",
            "turn_on": "bật",
            "increase": "tăng",
            "decrease": "giảm",
            "set": "chỉnh",
            "lock": "khoá",
            "unlock": "mở khoá",
            "open": "mở",
            "close": "đóng",
        }.get(action_hint, action_hint or "thao tác")
        names = [DEVICE_BY_SLUG[d].name for d in goal.target_device_ids if d in DEVICE_BY_SLUG]
        what = ", ".join(names) if names else (goal.target_area or "thiết bị")
        reply = f"Được ạ, mình sẽ không {verb} {what}."
        return ReasoningResult(
            semantic_goal=goal,
            candidate_plan=None,
            outcome="answer",
            reply=reply,
            rl_state=rl_state,
            decision_context=decision_context,
        )

    # Mục tiêu tích cực + phủ định chưa resolve được loại trừ → clarify, KHÔNG plan mù (§13, an toàn).
    if has_unresolved_exclusion(goal):
        q = (
            out.get("clarification_question")
            or "Bạn muốn mình đạt điều đó bằng cách nào, và tránh dùng thiết bị nào ạ?"
        )
        return ReasoningResult(
            semantic_goal=goal,
            candidate_plan=None,
            outcome="clarification",
            reply=q,
            clarification=_Clarification(q),
            rl_state=rl_state,
            decision_context=decision_context,
            memory_written=memory_written,
        )

    # Cần hỏi lại (thiếu thông tin không resolve được / không diễn giải được mục tiêu).
    if decision in (SufficiencyDecision.CLARIFY, SufficiencyDecision.ABSTAIN):
        q = out.get("clarification_question") or "Bạn nói rõ hơn giúp mình nhé?"
        return ReasoningResult(
            semantic_goal=goal,
            candidate_plan=None,
            outcome="clarification",
            reply=q,
            clarification=_Clarification(q),
            rl_state=rl_state,
            decision_context=decision_context,
            memory_written=memory_written,
        )

    if selected is None or not validated_ids:
        if goal is not None and goal.user_defined_routine and goal.routine_constraint_only:
            # A constraint-only routine is a valid reviewed plan with zero physical
            # actions.  Preserve it as a candidate plan so the caller can show the
            # applied constraint instead of asking for a device that the user
            # deliberately prohibited.
            return ReasoningResult(
                semantic_goal=goal,
                candidate_plan=CandidatePlan(
                    goal_summary=goal.goal_description or goal.raw_utterance,
                    utterance_type=goal.utterance_type,
                    actions=[],
                ),
                outcome="candidate_plan",
                reply="Mình đã áp dụng các ràng buộc trong hướng dẫn đã lưu.",
                rl_state=rl_state,
                decision_context=decision_context,
            )
        # Hành động bị chặn bởi ràng buộc CHÍNH người dùng đã đặt: phải nói đúng lý do đó.
        # Trước bản vá, nhánh này rơi xuống "đã ở trạng thái phù hợp" / "chưa có hành động cụ
        # thể" — cả hai đều SAI SỰ THẬT và giấu mất việc người dùng đang bị chặn, nên họ không
        # có cách nào biết phải nói gì để gỡ.
        blocked = [
            err
            for err in (out.get("validation_errors") or [])
            if err.code == "EXPLICIT_CONSTRAINT_VIOLATION" and err.subject
        ]
        if blocked:
            reasons = list(dict.fromkeys(explain_constraint(err.subject) for err in blocked))
            reply = f"Mình chưa làm vì {', '.join(reasons)}. Nếu bạn đổi ý, nhắn thẳng cho mình thiết bị đó nhé."
            return ReasoningResult(
                semantic_goal=goal,
                candidate_plan=None,
                outcome="no_action",
                reply=reply,
                rl_state=rl_state,
                decision_context=decision_context,
                memory_written=memory_written,
            )

        # Chỉ lệnh TƯỜNG MINH thiếu đích mới cần hỏi phòng/thiết bị. Goal open-ended
        # cố ý để target_device_ids rỗng vì specialists mới là tầng ground thiết bị;
        # coi field đó là thiếu scope sẽ tạo câu hỏi vô lý dù target_area đã rõ.
        explicit_missing_target = bool(
            goal is not None
            and not goal.desired_outcomes
            and (
                not getattr(goal, "target_device_ids", [])
                or not getattr(goal, "target_area", None)
                or len(getattr(goal, "target_device_ids", [])) > 1
            )
        )
        if (
            out.get("clarification_question")
            or explicit_missing_target
            or (decision in (SufficiencyDecision.CLARIFY, SufficiencyDecision.ABSTAIN))
        ):
            q = out.get("clarification_question") or "Bạn muốn thực hiện ở phòng nào hoặc thiết bị nào ạ?"
            return ReasoningResult(
                semantic_goal=goal,
                candidate_plan=None,
                outcome="clarification",
                reply=q,
                clarification=_Clarification(q),
                rl_state=rl_state,
                decision_context=decision_context,
                memory_written=memory_written,
            )
        if goal is not None and goal.desired_outcomes:
            plans = out.get("candidate_plans") or []
            if plans and all(getattr(candidate, "infeasible", False) for candidate in plans):
                reply = "Mình đã hiểu kế hoạch, nhưng hiện chưa thể thực hiện vì giới hạn an toàn hoặc tải điện."
            elif selected is not None:
                area = goal.target_area or "Không gian này"
                reply = f"{area} hiện đã ở trạng thái phù hợp; không cần thêm thao tác."
            else:
                reply = "Mình đã hiểu mục tiêu, nhưng hiện không có thiết bị phù hợp để thực hiện."
            return ReasoningResult(
                semantic_goal=goal,
                candidate_plan=None,
                outcome="no_action",
                reply=reply,
                rl_state=rl_state,
                decision_context=decision_context,
                memory_written=memory_written,
            )
        return ReasoningResult(
            semantic_goal=goal,
            candidate_plan=None,
            outcome="no_action",
            reply="Mình đã hiểu, nhưng chưa có hành động cụ thể nào để đề xuất.",
            rl_state=rl_state,
            decision_context=decision_context,
            memory_written=memory_written,
        )

    plan = _candidate_plan_from(selected, goal, validated_ids)
    if not plan.actions:
        return ReasoningResult(
            semantic_goal=goal,
            candidate_plan=plan,
            outcome="no_action",
            reply="Mình đã hiểu, nhưng chưa có hành động cụ thể nào để đề xuất.",
            rl_state=rl_state,
            decision_context=decision_context,
            memory_written=memory_written,
        )
    return ReasoningResult(
        semantic_goal=goal,
        candidate_plan=plan,
        outcome="candidate_plan",
        reply=out.get("scope_refusal", ""),
        rl_state=rl_state,
        decision_context=decision_context,
        memory_written=memory_written,
    )


def reason(**kwargs: Any) -> ReasoningResult:
    """Public wrapper that always carries local pipeline telemetry to the API."""
    # Bind ở ĐÂY (không chỉ trong `_reason_impl`) để wrapper và impl dùng CHUNG một
    # ledger store: đọc constraints bên dưới phải thấy đúng ledger mà lượt này vừa ghi.
    deps = _bind_session_deps(
        kwargs.get("deps") or _get_or_create_deps(kwargs.get("household_id")),
        session=kwargs.get("session"),
        household_id=kwargs.get("household_id"),
        model_client=kwargs.get("model_client"),
    )
    kwargs["deps"] = deps
    conversation_id = kwargs.get("conversation_id") or ""
    previous_ledger_turn = deps.ledger_store.load(conversation_id).last_updated_turn
    result = _reason_impl(**kwargs)
    ledger = deps.ledger_store.load(conversation_id)
    result.explicit_constraints = list(ledger.constraints)
    result.rejected_assumptions = list(ledger.rejected_assumptions)
    result.evidence_trace = (
        [dict(item) for item in ledger.last_resolution_evidence_trace]
        if ledger.last_updated_turn > previous_ledger_turn
        else []
    )
    model = kwargs.get("model_client") or deps.model_client
    result.diagnostics = getattr(model, "last_pipeline_diagnostics", {}) or {}
    return result
