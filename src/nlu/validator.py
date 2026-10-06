"""Deterministic Semantic Goal Validator — code, không phải prompt.

Kiểm tra tất định một SemanticGoal **open-ended** theo CẤU TRÚC (desired_outcomes,
action_hint, target), không theo intent đóng. Tính lại confidence từ thành phần quan
sát được (KHÔNG tin confidence LLM tự khai), rồi ra PROCEED / CLARIFY. Hàm thuần.

Đây là lưới an toàn khiến việc để LLM tổng hợp tự do vẫn an toàn: LLM đề xuất, validator
tất định soi lại grounding, an ninh, phủ định, và tính mơ hồ (nhiều thiết bị tương đương).
"""

from __future__ import annotations

from src.domain.enums import Capability
from src.iot.registry import DEVICE_BY_SLUG
from src.nlu.normalizer import Ambiguity, NormalizedUtterance
from src.nlu.ontology import UtteranceType
from src.nlu.schemas import (
    IntentCandidate,
    RuntimeContext,
    SemanticGoal,
    ValidationDecision,
    ValidationError,
    ValidationResult,
    ValidationSeverity,
)
from src.planning.device_grounder import ground_selector

# Ngưỡng policy (Bước 8).
_PROCEED_THRESHOLD = 0.80
_CLARIFY_FLOOR = 0.55
_CLOSE_CANDIDATE_GAP = 0.15
_WEIGHTS = {
    "intent_support": 0.30,
    "entity_grounding": 0.30,
    "reference_resolution": 0.15,
    "context_support": 0.15,
    "schema_validity": 0.10,
}
_ASSUMPTION_PENALTY = 0.20

# action_hint nào cần một THIẾT BỊ đích cụ thể. MỌI động từ điều khiển tường minh đều tác
# động lên một thiết bị — kể cả khoá/mở khoá/mở/đóng. Thiếu "lock"/"unlock" ở đây từng khiến
# "khoá lại" (không rõ khoá nào) lọt validator rồi sinh plan RỖNG rò ra ngoài như thể có kế
# hoạch (empty-plan leak trên thiết bị an ninh — fail-closed: chưa rõ target thì hỏi lại).
_DEVICE_TARGET_HINTS: frozenset[str] = frozenset(
    {"turn_on", "turn_off", "set", "increase", "decrease", "lock", "unlock", "open", "close"}
)


def _requires_device_target(goal: SemanticGoal) -> bool:
    return goal.action_hint in _DEVICE_TARGET_HINTS


def _required_capability(goal: SemanticGoal) -> Capability | None:
    if goal.parameters.get("temperature") is not None:
        return Capability.TEMPERATURE
    return None


def _ambiguous_outcomes(goal: SemanticGoal) -> list[str]:
    """Với mỗi desired_outcome cần ĐÚNG MỘT thiết bị (cardinality='one'), ground selector
    rồi hỏi: sự mơ hồ này có ĐÁNG hỏi lại không?

    Đáng hỏi khi các ứng viên nằm ở NHIỀU PHÒNG — lúc đó câu hỏi thật sự là "ở phòng nào?"
    và không trả lời được thì dễ bật nhầm phòng ("xem phim" khi có TV ở hai phòng).

    KHÔNG đáng hỏi khi các ứng viên nằm gọn trong CÙNG một phòng: phạm vi đã rõ, và câu
    "bạn muốn đèn nào trong ba đèn phòng khách?" chỉ làm phiền. Trước đây mọi trường hợp
    >1 đều bị hỏi lại, nên mục tiêu cả-phòng ("tối nay có khách") bị chặn ở 3/4 số lượt dù
    Understanding, Semantic Goal và Planner đều đã làm đúng việc của mình (§2026-08-09).
    Chọn cụ thể thiết bị nào vẫn là việc của Planner và vẫn bị Hard Validator soi lại."""
    ambiguous: list[str] = []
    for i, o in enumerate(goal.desired_outcomes):
        if o.cardinality != "one":
            continue
        specs, _warn = ground_selector(o.selector)
        if len(specs) <= 1:
            continue
        if len({s.room for s in specs}) > 1:
            ambiguous.append(f"outcome_{i}")
    return ambiguous


def validate(
    goal: SemanticGoal,
    *,
    nu: NormalizedUtterance,
    ctx: RuntimeContext,
    candidates: tuple[IntentCandidate, ...] = (),
) -> ValidationResult:
    errors: list[ValidationError] = []

    # --- (1) Grounding thiết bị: mọi target phải có thật ---
    unknown_devices = [d for d in goal.target_device_ids if d not in DEVICE_BY_SLUG]
    for dev in unknown_devices:
        errors.append(ValidationError(code="UNKNOWN_DEVICE", message_vi=f"Không có thiết bị '{dev}' trong nhà."))

    # An ninh KHÔNG chặn ở tầng NLU nữa: quyền điều khiển khoá/camera do phân quyền
    # (evaluate: chủ hộ trưởng thành được, con bị chặn) quyết ở agent_runner.

    # --- (2) Grounding phòng ---
    if goal.target_area and not ctx.has_room(goal.target_area):
        errors.append(ValidationError(code="UNKNOWN_ROOM", message_vi=f"Không có phòng '{goal.target_area}'."))

    # --- (3) Khả năng thiết bị hỗ trợ tham số nhiệt độ ---
    need_cap = _required_capability(goal)
    if need_cap is not None:
        for dev in goal.target_device_ids:
            spec = DEVICE_BY_SLUG.get(dev)
            if spec and need_cap not in spec.capabilities:
                errors.append(
                    ValidationError(
                        code="UNSUPPORTED_CAPABILITY",
                        message_vi=f"Thiết bị '{spec.name}' không chỉnh được {need_cap.value}.",
                    )
                )

    # --- (4) Giữ nguyên phủ định / sửa lời / huỷ ---
    if nu.has_negation and not goal.negated:
        errors.append(ValidationError(code="NEGATION_LOST", message_vi="Phủ định trong câu bị bỏ sót."))
    if nu.has_correction and not goal.is_correction:
        errors.append(ValidationError(code="CORRECTION_LOST", message_vi="Câu sửa lời bị hiểu sai."))
    if nu.has_cancellation and not goal.is_cancellation and (goal.action_hint or goal.desired_outcomes):
        errors.append(
            ValidationError(code="CANCELLATION_BECAME_ACTION", message_vi="Câu huỷ bị biến thành hành động thiết bị.")
        )

    # --- (5) Tham chiếu chưa giải được ---
    # CHỈ là lỗi khi câu THỰC SỰ có tham chiếu ("nó/cái đó") mà chưa gắn được thiết bị.
    # Mục tiêu môi trường/open-ended ("nóng quá đi") không có tham chiếu nào để giải —
    # references_resolved=False do LLM khai KHÔNG được biến thành cớ để hỏi lại (đây là
    # gốc của over-clarify env-request §2026-08-08).
    references_ok = goal.references_resolved or not nu.has_reference
    if not references_ok:
        errors.append(
            ValidationError(
                code="UNRESOLVED_REFERENCE",
                message_vi="Chưa rõ 'nó/cái đó' là thiết bị nào.",
                severity=ValidationSeverity.WARNING,
            )
        )

    # --- (6) Đích thiết bị cho lệnh tường minh ---
    needs_device = _requires_device_target(goal)
    # Thiếu target vẫn là một lỗi độc lập khi tham chiếu chưa giải. Ghi cả
    # UNRESOLVED_REFERENCE lẫn MISSING_TARGET giúp clarification biết slot thực thi
    # nào đang thiếu; không được che MISSING_TARGET chỉ vì câu có "lại/nó".
    target_missing = needs_device and not goal.target_device_ids
    if target_missing:
        errors.append(
            ValidationError(code="MISSING_TARGET", message_vi="Chưa rõ thiết bị nào.", severity=ValidationSeverity.WARNING)
        )
    ambiguous_target = needs_device and nu.ambiguity == Ambiguity.UNRESOLVED
    if ambiguous_target:
        errors.append(
            ValidationError(
                code="AMBIGUOUS_TARGET",
                message_vi="Có nhiều thiết bị khớp, chưa rõ chọn cái nào.",
                severity=ValidationSeverity.WARNING,
            )
        )

    # --- (7) Mục tiêu open-ended thiếu phòng: có desired_outcomes theo-phòng mà không
    # có phòng nào (target_area/focus_room) và selector cũng không nêu area/labels ---
    # ENVIRONMENT_REQUEST là tiện nghi của MỘT phòng (nóng/tối/ồn "ở đây") → BẮT BUỘC có phòng,
    # kể cả khi selector có labels (vd "ambient_lighting"): label mô tả VAI TRÒ thiết bị, KHÔNG
    # giải được "phòng nào". Không có phòng grounded (graph đã ép null nếu LLM chỉ đoán) → hỏi lại.
    comfort_needs_room = goal.utterance_type == UtteranceType.ENVIRONMENT_REQUEST
    area_missing = (
        bool(goal.desired_outcomes)
        and not goal.action_hint
        and goal.target_area is None
        and ctx.focus_room is None
        and (
            comfort_needs_room
            or any(o.selector.area is None and not o.selector.labels for o in goal.desired_outcomes)
        )
    )
    if area_missing:
        errors.append(
            ValidationError(code="MISSING_AREA", message_vi="Bạn muốn ở phòng nào?", severity=ValidationSeverity.WARNING)
        )

    # --- (7b) SET nhưng chưa nói giá trị ---
    value_missing = (
        goal.action_hint == "set"
        and bool(goal.target_device_ids)
        and not any(goal.parameters.get(k) is not None for k in ("temperature", "percent", "level"))
    )
    if value_missing:
        errors.append(
            ValidationError(code="MISSING_VALUE", message_vi="Bạn muốn đặt mức bao nhiêu?", severity=ValidationSeverity.WARNING)
        )

    # --- (7d) Câu KHÔNG có gì để hành động: LLM không suy ra được outcome/hint/target và
    # cũng không phải huỷ/sửa/phủ định → đây là quan sát/xã giao ("trời đẹp thật", "nhà
    # mình đẹp quá"), KHÔNG được bịa mục tiêu. Trước đây goal rỗng lại được entity_grounding
    # =1.0 nên PROCEED thẳng xuống Planner rồi bịa hành động (over-act §2026-08-10). Chặn ở
    # đây bằng CLARIFY sạch. Câu phủ định ("đừng tắt đèn") giữ nguyên nhánh riêng, không rơi
    # vào đây. Đây là lưới TẤT ĐỊNH — không phụ thuộc việc prompt có kìm được LLM hay không.
    no_actionable_content = (
        not goal.desired_outcomes
        and not goal.action_hint
        and not goal.target_device_ids
        and not goal.is_cancellation
        and not goal.negated
    )
    if no_actionable_content:
        errors.append(
            ValidationError(
                code="NOT_ACTIONABLE",
                message_vi="Mình chưa rõ bạn muốn mình làm gì với ngôi nhà ạ.",
                severity=ValidationSeverity.WARNING,
            )
        )

    # --- (7c) Nhiều thiết bị tương đương cho outcome cần đúng một cái → hỏi lại ---
    equivalent_ambiguous = _ambiguous_outcomes(goal)
    if equivalent_ambiguous:
        errors.append(
            ValidationError(
                code="MULTIPLE_EQUIVALENT",
                message_vi="Có nhiều thiết bị tương đương, bạn muốn chọn cái nào?",
                severity=ValidationSeverity.WARNING,
            )
        )

    # --- Confidence (quan sát được) ---
    intent_support = candidates[0].confidence if candidates else goal.confidence
    if len(candidates) >= 2 and (candidates[0].confidence - candidates[1].confidence) < _CLOSE_CANDIDATE_GAP:
        intent_support *= 0.7

    if needs_device:
        entity_grounding = 1.0 if (goal.target_device_ids and not unknown_devices and not ambiguous_target) else 0.0
    elif goal.desired_outcomes:
        entity_grounding = 0.0 if (area_missing or equivalent_ambiguous) else 1.0
    else:
        entity_grounding = 1.0

    reference_resolution = 1.0 if references_ok else 0.0
    context_support = 1.0 if (goal.target_area is None or ctx.has_room(goal.target_area)) else 0.0
    schema_validity = 1.0

    components = {
        "intent_support": round(intent_support, 3),
        "entity_grounding": entity_grounding,
        "reference_resolution": reference_resolution,
        "context_support": context_support,
        "schema_validity": schema_validity,
    }
    base = sum(_WEIGHTS[k] * v for k, v in components.items())
    serious_assumptions = sum(1 for a in goal.assumptions if a.startswith("INVENTED_"))
    final = max(0.0, min(1.0, base - _ASSUMPTION_PENALTY * serious_assumptions))
    components["unsupported_assumptions"] = float(serious_assumptions)

    has_error = any(e.severity == ValidationSeverity.ERROR for e in errors)
    close_candidates = len(candidates) >= 2 and (candidates[0].confidence - candidates[1].confidence) < _CLOSE_CANDIDATE_GAP

    if (
        has_error
        or not references_ok
        or target_missing
        or ambiguous_target
        or area_missing
        or bool(equivalent_ambiguous)
        or value_missing
        or no_actionable_content
    ):
        decision = ValidationDecision.CLARIFY
        ok = False
    elif final >= _PROCEED_THRESHOLD and not close_candidates:
        decision = ValidationDecision.PROCEED
        ok = True
    else:
        decision = ValidationDecision.CLARIFY
        ok = False

    return ValidationResult(ok=ok, decision=decision, final_confidence=round(final, 3), components=components, errors=errors)
