"""Semantic Resolver (spec §9, §56) — điều phối Layer 2 thành một quyết định.

Nối: Context Transducer (§11) → Context Resolver 6-tier (§14) → Sufficiency Gate (§13)
→ Clarification (§15). Trả một `ResolveOutcome` cho pipeline: quyết định sufficiency,
goal (đã áp resolution nếu có), semantic_analysis, và câu hỏi làm rõ khi cần.

Đây là "understanding" đúng spec, thay lớp mỏng tái dùng `nlu.understand` trực tiếp —
nhưng vẫn NHẬN goal ứng viên từ backend đó (hoặc goal do LLM/service author sẵn), giữ
nguyên tắc "LLM proposes semantic, code decides" (spec §P1/§P2).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.agent.schemas import MemoryEvent, RequirementLedger, RuntimeContext, SemanticGoal, SufficiencyDecision
from src.agent.text import Pattern, TextView
from src.agent.understanding.clarification import Clarification, build_clarification
from src.agent.understanding.context_resolver import resolve_missing
from src.agent.understanding.sufficiency import decide as sufficiency_decide
from src.agent.understanding.transducer import SemanticAnalysis, transduce
from src.iot.registry import domain_instance_count, spec_for
from src.nlu.normalizer import NormalizedUtterance
from src.nlu.ontology import UtteranceType


@dataclass(slots=True)
class ResolveOutcome:
    decision: SufficiencyDecision
    goal: SemanticGoal | None
    analysis: SemanticAnalysis
    clarification: Clarification | None = None
    reason: str = ""
    evidence_trace: list[dict] | None = None


# Quan hệ target theo hội thoại, không phải constraint durable: "thiết bị khác/cái còn lại"
# yêu cầu loại target vừa chốt khỏi tập ứng viên của CHÍNH lượt này. Pattern dùng lớp text
# chung để cùng một luật nhận cả tiếng Việt có dấu và input không dấu.
_ALTERNATIVE_TARGET = Pattern(r"(?<!\w)(?:khác|còn\s+lại)(?!\w)")


def _high_risk(nu: NormalizedUtterance) -> bool:
    for slug in nu.matched_device_ids:
        spec = spec_for(slug)
        if spec is not None and spec.risk_level.value in ("security", "high_power"):
            return True
    return False


def _apply_room(goal: SemanticGoal, room: str) -> SemanticGoal:
    """Áp phòng đã giải được vào target_area của goal (spec §14 → grounding dùng được)."""
    return goal.model_copy(update={"target_area": room, "target_area_source": "context_resolver"})


def _is_local_inferred_goal(goal: SemanticGoal | None) -> bool:
    """Goal suy diễn điều chỉnh capability tương đối cần scope phòng thật.

    Selector/target_area do model đề xuất không phải location evidence. Các routine
    không có relative_change không bị ép vào phòng người nói, vì scope của
    chúng có thể là toàn nhà/phòng đích ngữ nghĩa."""
    return bool(
        goal is not None
        and goal.action_hint is None
        and any(outcome.relative_change for outcome in goal.desired_outcomes)
    )


def _requires_explicit_room(
    nu: NormalizedUtterance,
    goal: SemanticGoal | None,
    ledger: RequirementLedger | None = None,
) -> bool:
    """A bare device-type command must not borrow a room from ambient context.

    Presence or capture location is useful for deictic/environmental requests such
    as ``ở đây tối quá``.  It is not an explicit answer to ``tắt đèn``: when the
    house has several lights, silently choosing the current room is a materially
    different action from asking which room the user meant.  A room written in the
    utterance (including a resolved ``ở đây`` rewrite), a concrete device, or a
    group command already has an explicit scope and does not enter this guard.
    """
    confirmed_room = (ledger.confirmed_facts or {}).get("room") if ledger is not None else None
    return bool(
        goal is not None
        and goal.action_hint is not None
        and nu.has_action_verb
        and not nu.room_explicit
        and not nu.matched_device_ids
        and not goal.target_device_ids
        # Lượt đầu vẫn phải hỏi. Sau khi người dùng đã CHỐT phòng trong chính ledger của
        # conversation này, đó là execution scope hợp lệ cho lệnh tiếp nối cùng chủ đề.
        # Topic switch đã nhận một semantic_ledger cô lập nên không thể lọt room cũ vào đây.
        and not confirmed_room
    )


def resolve_semantics(
    nu: NormalizedUtterance,
    ctx: RuntimeContext,
    goal: SemanticGoal | None,
    *,
    ledger: RequirementLedger | None = None,
    events: list[MemoryEvent] | None = None,
) -> ResolveOutcome:
    """Chạy toàn bộ Layer 2 và trả quyết định sufficiency + goal đã bổ khuyết."""
    analysis = transduce(nu, goal)
    empty = not (nu.normalized or "").strip()

    resolved_map: dict = {}
    still_missing: list[str] = list(analysis.missing_information)
    evidence_trace: list[dict] = []

    missing = list(analysis.missing_information)
    explicit_room_required = _requires_explicit_room(nu, goal, ledger)
    if explicit_room_required and "room" not in missing:
        missing.append("room")
        analysis.missing_information.append("room")
        analysis.sufficiency_status = "insufficient"
    # Scope thực thi cho mục tiêu OPEN-ENDED (không có động từ điều khiển tất định) phải đến từ tín
    # hiệu TẤT ĐỊNH: thiết bị cụ thể nêu trong câu, phòng nêu trong câu, hoặc ngữ cảnh phòng
    # (focus/speaker/ledger). KHÔNG tin selector.area/target_area do LLM tự đặt (§P2 "code decides")
    # — nếu tin, câu mơ hồ "tối quá"/"đèn"/"chỉnh nhiệt độ" bị LLM ground bừa vào thiết bị phòng
    # khách thay vì hỏi lại. Thiếu room → thêm vào missing để context-resolver thử giải: giải được
    # (focus/speaker/ledger) → RESOLVE_CONTEXT; không giải được → CLARIFY.
    det_device = bool(nu.matched_device_ids)
    ledger_room = bool(ledger and ledger.confirmed_facts.get("room"))
    det_room = bool(nu.matched_rooms) or bool(ctx.focus_room) or bool(ctx.speaker_location) or ledger_room
    open_ended_no_scope = goal is not None and goal.action_hint is None and not det_device and not det_room
    goal_cut = goal is not None and not goal.target_area and not goal.target_device_ids
    # Goal đã ground từ Ledger qua build_continuation_goal (intent="continuation", §17) mang
    # target_area là BẰNG CHỨNG tier-3 đã CHỐT ở lượt trước — không phải model tự đoán. Re-chạy
    # chuỗi evidence cho goal này sẽ cho runtime_context (tier-2, yếu hơn) đè lên ledger (tier-3,
    # đã chốt) vì resolve_room duyệt tier theo thứ tự §14 chung, gây mất phòng đã ground đúng
    # (Bug A). Turn đổi phòng/thiết bị tường minh đã bị classify() tách thành TOPIC_SWITCH TRƯỚC
    # khi tới build_continuation_goal (xem introduces_new_scope), nên CONTINUATION goal tới đây
    # luôn an toàn để giữ nguyên — không cần escape hatch riêng.
    ledger_grounded_continuation = bool(goal is not None and goal.intent == "continuation" and goal.target_area)
    activity_grounded_home_room = bool(
        goal is not None and goal.target_area and goal.target_area_source == "activity_home_room"
    )
    # Goal điều chỉnh cục bộ luôn resolve room qua chuỗi evidence, kể cả khi
    # model đã tự điền selector.area/target_area. Nhờ vậy speaker_location/explicit
    # room thắng phòng model đoán, và evidence trace được lưu đúng §P3.
    local_scope_must_be_grounded = (
        _is_local_inferred_goal(goal)
        and not det_device
        and not ledger_grounded_continuation
        and not activity_grounded_home_room
    )
    # Nếp nhắm một thiết bị vận hành ĐƠN NHẤT (vd "đi tắm" → bình nóng lạnh): nhà chỉ có một
    # bản nên KHÔNG có phòng nào để hỏi. Không ép room khi MỌI outcome đều thuộc domain đơn
    # nhất và tự nó không nêu phòng — Planning tự tìm bản duy nhất (khớp _room_of_selector).
    singleton_activity_scope = (
        goal is not None
        and goal.utterance_type == UtteranceType.ROUTINE_INTENT
        and bool(goal.desired_outcomes)
        and all(
            not getattr(outcome.selector, "area", None)
            and domain_instance_count(getattr(outcome.selector, "domain", None)) == 1
            for outcome in goal.desired_outcomes
        )
    )
    if singleton_activity_scope:
        missing = [field for field in missing if field != "room"]
    elif (goal_cut or open_ended_no_scope or local_scope_must_be_grounded) and "room" not in missing:
        missing.append("room")

    if missing and not explicit_room_required:
        resolved_map, still_missing = resolve_missing(missing, nu=nu, ctx=ctx, ledger=ledger, events=events)
        evidence_trace = [rf.as_trace() for rf in resolved_map.values()]
        # Áp field đã giải vào goal (hiện hỗ trợ 'room'/'area').
        if goal is not None:
            for fname in ("room", "area"):
                if fname in resolved_map:
                    goal = _apply_room(goal, resolved_map[fname].value)
                    break
    elif explicit_room_required:
        # Deliberately do not resolve this field from speaker/presence/focus.
        # The user must supply the execution scope on the next turn.
        still_missing = missing

    # RETRY ground thiết bị bằng phòng đến từ nguồn ĐÁNG TIN (spec §14/§P3: Resolve phải viết
    # lại thành lệnh tường minh, không chỉ vá một field rồi bỏ đó). BA nguồn, ưu tiên giảm dần:
    # (1) `ctx.speaker_location` — tín hiệu ĐO ĐƯỢC; (2) phòng vừa giải được QUA CHÍNH context
    # resolver 6-tier lượt này (`resolved_map` — CÓ evidence trace, gồm cả recent_dialogue
    # tier-4); (3) phòng đã CHỐT trong Ledger cuộc hội thoại NÀY (tier-3, xác nhận từ lượt
    # trước). KHÔNG dùng `goal.target_area`/`ctx.focus_room` NÓI CHUNG — nó có thể đã bị
    # `understand()` điền thẳng từ carry CHÉO giữa hai hội thoại vô tình dùng chung
    # conversation_id, KHÔNG qua evidence trace nào (xác nhận qua regression
    # `test_lenh_mo_ho_thi_agent_hoi_lai`) — ba nguồn trên đều có căn cứ kiểm chứng được, còn
    # target_area trần thì không. Trước bản vá này, dù phòng đã biết chắc, "đèn"/"rèm" mơ hồ
    # (nhiều cái cùng loại trong phòng) vẫn hỏi lại vô ích. Chỉ chạy khi câu KHÔNG tự nêu thiết
    # bị và goal chưa có đích — không đè lên grounding đã có.
    room_source: str | None = None
    if ctx.speaker_location and ctx.speaker_location in ctx.rooms:
        room_source = ctx.speaker_location
    elif resolved_map.get("room") is not None or resolved_map.get("area") is not None:
        rf = resolved_map.get("room") or resolved_map.get("area")
        if rf is not None:
            room_source = rf.value
    elif ledger is not None and ledger.confirmed_facts.get("room") in ctx.rooms:
        room_source = ledger.confirmed_facts["room"]
    if (
        goal is not None
        and room_source
        and not goal.target_device_ids
        and (goal.action_hint is not None or not goal.desired_outcomes)
    ):
        devices: list[str] = []
        if len(nu.matched_device_ids) > 1:
            # Alias trần đã khớp NHIỀU thiết bị CỤ THỂ ("đèn ngủ" → 2 đèn ngủ, "rèm" → 3 rèm) —
            # LỌC TRONG chính tập ứng viên đó theo phòng, KHÔNG tra device-type độc lập (sẽ mất
            # ý nghĩa của alias — "đèn ngủ" không phải MỌI đèn, "rèm" không phải MỌI rèm mà chỉ
            # đúng loại alias đã khớp). Đây là fix cho regression đã gặp: device-type-in-room
            # từng biến "tắt đèn ngủ" thành tắt NHẦM đèn chùm phòng khách (khác hẳn ý người dùng).
            devices = [d for d in nu.matched_device_ids if (s := spec_for(d)) is not None and s.room == room_source]
        elif not nu.matched_device_ids:
            # Câu KHÔNG tự nêu thiết bị nào (chỉ nêu LOẠI/dimension: "tắt đèn", "chỉnh quạt") —
            # tra theo device-type/capability trong phòng (spec §14/§P3, xem docstring hàm).
            from src.nlu.understanding import retry_device_grounding

            devices = retry_device_grounding(nu, action_hint=goal.action_hint, room=room_source)
        if devices:
            goal = goal.model_copy(update={"target_area": room_source, "target_device_ids": devices})
            evidence_trace.append(
                {
                    "resolved_field": "device",
                    "value": devices,
                    "source": "type_in_room_retry",
                    "evidence": [{"room": room_source}],
                }
            )

    # "đèn/thiết bị khác" là một quan hệ với target gần nhất, không phải lệnh nhóm và cũng
    # không phải ràng buộc `avoid:` sống qua các lượt sau. Resolve tập cùng loại/phòng của lượt
    # hiện tại, loại target đã chốt trong ledger, rồi chỉ tự chọn khi còn ĐÚNG MỘT ứng viên.
    # 0 hoặc >1 ứng viên đều thiếu target thực thi → CLARIFY, không tác động lại thiết bị cũ.
    alternative_requested = (
        _ALTERNATIVE_TARGET.search(TextView(raw=nu.normalized or "", folded=nu.folded or "")) is not None
    )
    prior_devices = set((ledger.confirmed_facts or {}).get("devices") or []) if ledger else set()
    if alternative_requested and prior_devices and goal is not None and goal.action_hint is not None:
        candidates = list(dict.fromkeys(goal.target_device_ids))
        alternative_room = goal.target_area or room_source
        if alternative_room and (not candidates or not any(d not in prior_devices for d in candidates)):
            from src.nlu.understanding import retry_device_grounding

            candidates = retry_device_grounding(nu, action_hint=goal.action_hint, room=alternative_room)
        alternatives = [device_id for device_id in candidates if device_id not in prior_devices]
        if len(alternatives) == 1:
            goal = goal.model_copy(update={"target_area": alternative_room, "target_device_ids": alternatives})
            evidence_trace.append(
                {
                    "resolved_field": "device",
                    "value": alternatives,
                    "source": "alternative_to_requirement_ledger",
                    "evidence": [{"excluded_prior_devices": sorted(prior_devices)}],
                }
            )
        else:
            goal = goal.model_copy(update={"target_device_ids": []})
            if "device" not in still_missing:
                still_missing.append("device")
            if "device" not in analysis.missing_information:
                analysis.missing_information.append("device")
            analysis.sufficiency_status = "insufficient"

    result = sufficiency_decide(
        analysis,
        has_goal=goal is not None,
        resolved_fields=tuple(resolved_map.keys()),
        still_missing=tuple(still_missing),
        empty_utterance=empty,
        high_risk=_high_risk(nu),
    )

    clarification = None
    if result.decision == SufficiencyDecision.CLARIFY and not empty and goal is not None:
        clarification = build_clarification(still_missing or ["room"], nu=nu, ctx=ctx)

    return ResolveOutcome(
        decision=result.decision,
        goal=goal,
        analysis=analysis,
        clarification=clarification,
        reason=result.reason,
        evidence_trace=evidence_trace or None,
    )
