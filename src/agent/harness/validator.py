"""Deterministic Validator (spec §47) — soi từng action trước khi cho qua harness.

Kiểm ĐỦ 9 bất biến (spec §47): device exists · device online · capability exists · target
in allowed range · room valid · action supported · no contradictory actions · explicit
constraints preserved · no unauthorized security action.

Mã lỗi tương ứng: DEVICE_NOT_FOUND · DEVICE_OFFLINE · CAPABILITY_NOT_SUPPORTED ·
TARGET_OUT_OF_RANGE · ROOM_INVALID · ACTION_NOT_SUPPORTED · CONTRADICTORY_ACTIONS ·
EXPLICIT_CONSTRAINT_VIOLATION · (security → dropped_security).

"LLM proposes, code decides" (spec §P2): validator là code tất định, chặn ảo giác thiết
bị/khả năng và mọi hành động an ninh lọt từ mục tiêu suy diễn. Trả ValidatedAction (đã
ground) + danh sách lỗi cứng.
"""

from __future__ import annotations

from src.agent.cognitive.ledger_updater import violates_constraint
from src.agent.schemas import (
    ProposalAction,
    RequirementLedger,
    RuntimeContext,
    ValidatedAction,
    ValidationError,
)
from src.domain.action_registry import semantic_expected_state, semantic_target_issue
from src.domain.enums import ActionType, Capability
from src.iot.registry import DEVICE_BY_SLUG

_OFF_LIKE = {"turn_off", "close"}
_ON_LIKE = {"turn_on", "open", "set", "increase", "decrease", "lock", "unlock"}


def validate_plan(
    actions: list[ProposalAction],
    ctx: RuntimeContext,
    *,
    ledger: RequirementLedger | None = None,
    is_inferred_goal: bool = False,
) -> tuple[list[ValidatedAction], list[ValidationError], list[str]]:
    """Trả (validated_actions, hard_errors, dropped_security).

    `is_inferred_goal=True` (mục tiêu suy diễn, không phải lệnh tường minh) → loại mọi
    action lên thiết bị an ninh (spec §38, invariant an ninh), TRỪ hành động khoá — hướng
    làm an toàn hơn, và được đánh dấu `requires_confirmation` để luôn qua HITL.
    """
    validated: list[ValidatedAction] = []
    errors: list[ValidationError] = []
    dropped_security: list[str] = []
    device_direction: dict[str, str] = {}

    for i, action in enumerate(actions):
        slug = action.device_id
        spec = DEVICE_BY_SLUG.get(slug)
        if spec is None:
            errors.append(ValidationError(code="DEVICE_NOT_FOUND", message_vi=f"Thiết bị '{slug}' không có trong catalog."))
            continue

        # Thiết bị OFFLINE trên live context → invalid TRƯỚC execute (spec §47, QC-05), không để
        # fail tận gateway. Chỉ chặn khi context LIVE báo offline (default online=True → không ảnh hưởng).
        ctx_device = ctx.device(slug)
        if ctx_device is not None and not ctx_device.online:
            errors.append(ValidationError(code="DEVICE_OFFLINE", message_vi=f"Thiết bị '{spec.name}' đang offline."))
            continue

        # An ninh do mục tiêu suy diễn tự kèm → loại (không vượt rào từ câu mơ hồ).
        # NGOẠI LỆ DUY NHẤT: hành động KHOÁ. Nếp sinh hoạt "cả nhà ra ngoài" author
        # `door_lock -> locked`; bỏ bước này khiến kịch bản thiếu đúng hành động người
        # dùng mong đợi nhất, trong khi khoá là hướng làm AN TOÀN HƠN và hoàn tác được
        # bằng một lệnh tường minh. Mọi hướng còn lại (mở khoá, chạm camera) vẫn bị loại
        # tuyệt đối. Ngoại lệ này KHÔNG tự cấp quyền thực thi: action bị đánh dấu cần xác
        # nhận, nên nó luôn đi qua HITL/policy thay vì chạy thẳng.
        inferred_lock = is_inferred_goal and str(action.action) == ActionType.LOCK.value
        if is_inferred_goal and spec.risk_level.value == "security" and not inferred_lock:
            dropped_security.append(slug)
            continue

        # Capability phải được thiết bị hỗ trợ (chặn ảo giác khả năng).
        try:
            cap_enum = Capability(action.capability)
        except ValueError:
            errors.append(ValidationError(code="CAPABILITY_NOT_SUPPORTED", message_vi=f"Khả năng '{action.capability}' không hợp lệ."))
            continue
        if cap_enum not in spec.capabilities:
            errors.append(
                ValidationError(code="CAPABILITY_NOT_SUPPORTED", message_vi=f"Thiết bị '{spec.name}' không hỗ trợ {action.capability}.")
            )
            continue

        # Room hợp lệ (spec §47 "room valid"): phòng của thiết bị phải là phòng CÓ THẬT trong
        # ngữ cảnh runtime. Thiết bị cả-nhà/không gắn phòng (room rỗng) được bỏ qua. Chặn thiết bị
        # (thường do thêm lúc chạy) mang phòng rác không nằm trong danh mục phòng của hộ.
        if spec.room and ctx.rooms and spec.room not in ctx.rooms:
            errors.append(
                ValidationError(code="ROOM_INVALID", message_vi=f"Phòng '{spec.room}' của thiết bị '{spec.name}' không hợp lệ.")
            )
            continue

        # Action/capability/parameter contract comes from the single deterministic registry.
        contract_issue = semantic_target_issue(
            cap_enum,
            action.action,
            dict(action.target),
            device_type=spec.device_type,
        )
        if contract_issue is not None:
            errors.append(ValidationError(code=contract_issue.code, message_vi=contract_issue.message_vi))
            continue

        # Ràng buộc người dùng (ledger) — không phá explicit constraint / rejected assumption.
        if ledger is not None:
            hit = violates_constraint(ledger, device_id=slug, action=action.action)
            if hit is not None:
                errors.append(
                    ValidationError(
                        code="EXPLICIT_CONSTRAINT_VIOLATION",
                        message_vi=f"Vi phạm ràng buộc người dùng: {hit}.",
                        subject=hit,
                    )
                )
                continue

        # Không có action mâu thuẫn trên cùng thiết bị trong một plan.
        direction = "on" if action.action in _ON_LIKE else ("off" if action.action in _OFF_LIKE else "")
        prev = device_direction.get(slug)
        if prev and direction and prev != direction:
            errors.append(ValidationError(code="CONTRADICTORY_ACTIONS", message_vi=f"Hành động mâu thuẫn trên '{spec.name}'."))
            continue
        if direction:
            device_direction[slug] = direction

        validated.append(
            ValidatedAction(
                action_id=f"step_{i+1}",
                entity_id=spec.slug,
                domain=spec.device_type.value,
                service=str(action.action),
                parameters=dict(action.target),
                desired_state=semantic_expected_state(cap_enum, action.action, dict(action.target)),
                reason=action.reason_vi or "Thực hiện theo kế hoạch",
                risk_level=spec.risk_level.value,
                requires_confirmation=inferred_lock,
            )
        )

    return validated, errors, dropped_security
