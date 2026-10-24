"""Policy Engine (spec §48) — thứ tự ưu tiên P0..P5, lower KHÔNG override higher.

    P0 Hard Safety · P1 Authorization · P2 Explicit User Constraint · P3 Comfort ·
    P4 Energy Efficiency · P5 Convenience

Policy Gate quyết định cuối: REJECT (vi phạm P0/P2, hoặc rỗng), CONFIRM (P1 cần duyệt),
hoặc PROCEED. LangGraph KHÔNG phải policy engine (spec §46) — quyết định nằm ở đây, tất định.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.agent.harness.authorization import AuthorizationResult
from src.agent.schemas import PolicyPriority, ValidationError


@dataclass(slots=True)
class PolicyDecision:
    decision: str  # PROCEED | CONFIRM | REJECT
    priority_hit: str | None = None  # PolicyPriority bị chạm khiến không PROCEED
    reasons: list[str] = field(default_factory=list)


def decide(
    *,
    hard_errors: list[ValidationError],
    authorization: AuthorizationResult,
    has_actions: bool,
    constraint_violations: list[str] | None = None,
) -> PolicyDecision:
    """Áp thứ tự ưu tiên policy (spec §48). Dừng ở ưu tiên cao nhất bị chạm."""
    constraint_violations = constraint_violations or []

    # P0 — Hard Safety: lỗi validator cứng (thiết bị/khả năng/range) là chặn tuyệt đối.
    safety_codes = {"DEVICE_NOT_FOUND", "CAPABILITY_NOT_SUPPORTED", "TARGET_OUT_OF_RANGE", "CONTRADICTORY_ACTIONS"}
    hard = [e for e in hard_errors if e.code in safety_codes]
    if hard:
        return PolicyDecision(
            decision="REJECT",
            priority_hit=PolicyPriority.P0_HARD_SAFETY.value,
            reasons=[e.message_vi for e in hard],
        )

    # P1 — Authorization: bị chặn hẳn → REJECT; cần duyệt → CONFIRM.
    if authorization.decision == "BLOCKED":
        return PolicyDecision(
            decision="REJECT",
            priority_hit=PolicyPriority.P1_AUTHORIZATION.value,
            reasons=authorization.reasons or ["Không đủ quyền thực hiện."],
        )

    # P2 — Explicit User Constraint: action phá ràng buộc người dùng.
    constraint_errs = [e for e in hard_errors if e.code == "EXPLICIT_CONSTRAINT_VIOLATION"]
    if constraint_errs or constraint_violations:
        return PolicyDecision(
            decision="REJECT",
            priority_hit=PolicyPriority.P2_EXPLICIT_CONSTRAINT.value,
            reasons=[e.message_vi for e in constraint_errs] + list(constraint_violations),
        )

    if not has_actions:
        return PolicyDecision(decision="REJECT", priority_hit=None, reasons=["Không có hành động hợp lệ nào."])

    if authorization.decision == "WAITING_FOR_USER_APPROVAL":
        return PolicyDecision(
            decision="CONFIRM",
            priority_hit=PolicyPriority.P1_AUTHORIZATION.value,
            reasons=authorization.reasons or ["Cần xác nhận trước khi thực hiện."],
        )

    return PolicyDecision(decision="PROCEED", priority_hit=None, reasons=[])
