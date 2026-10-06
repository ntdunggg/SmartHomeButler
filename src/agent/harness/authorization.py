"""Authorization (spec §49, P1) — ai được làm gì, lệnh nào cần duyệt.

Nguồn chân lý duy nhất về quyền per-device là `src.core.permissions.resolve_access`
(AccessRule + khoá trẻ em) — CÙNG một hàm mà đường API điều khiển trực tiếp
(`src/api/devices.py`) và đường thực thi agent cũ (`src/agent/execution.py`) đã
dùng. Khi caller cấp đủ `session` + `user` thật (đường có DB), `authorize()` đi
qua đúng `resolve_access()` cho từng action để KHÔNG lệch quyết định với hai
đường trên. Khi thiếu `session`/`user` (ví dụ eval harness/test đồ thị chạy
không có DB thật), rơi về `evaluate()` tất định theo vai trò × mức rủi ro như
trước — giữ tương thích ngược cho các đường không có DB. Hành động an ninh/công
suất lớn có thể trả ``WAITING_FOR_USER_APPROVAL``; KHÔNG execute tới khi có
approval hợp lệ (spec §49).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.agent.schemas import ValidatedAction
from src.core.permissions import Decision, evaluate, resolve_access
from src.domain.enums import RiskLevel, Role
from src.domain.models import Device, User


@dataclass(slots=True)
class AuthorizationResult:
    decision: str  # PROCEED | WAITING_FOR_USER_APPROVAL | BLOCKED
    allowed_actions: list[ValidatedAction] = field(default_factory=list)
    approval_actions: list[ValidatedAction] = field(default_factory=list)
    blocked: list[tuple[str, str]] = field(default_factory=list)  # (entity_id, reason)
    reasons: list[str] = field(default_factory=list)


def _decide_for_action(
    a: ValidatedAction,
    *,
    role: Role | str,
    risk: RiskLevel,
    session: Session | None,
    user: User | None,
) -> Decision:
    """Quyết định quyền cho một action — ưu tiên `resolve_access()` per-device khi
    có đủ session+user thật; nếu không (hoặc thiết bị không tồn tại trong DB của
    hộ đó, ví dụ entity giả lập trong test đồ thị), rơi về `evaluate()` theo vai
    trò × mức rủi ro (hành vi cũ, không regress đường không có DB)."""
    if session is not None and user is not None:
        device = session.scalar(
            select(Device).where(Device.household_id == user.household_id, Device.slug == a.entity_id)
        )
        if device is not None:
            return resolve_access(session, user=user, device=device)
    return evaluate(role=role, risk=risk)


def authorize(
    actions: list[ValidatedAction],
    *,
    role: Role | str,
    session: Session | None = None,
    user: User | None = None,
) -> AuthorizationResult:
    """Phân loại từng action: cho chạy / cần duyệt / bị chặn (spec §49)."""
    result = AuthorizationResult(decision="PROCEED")
    for a in actions:
        try:
            risk = RiskLevel(a.risk_level)
        except ValueError:
            risk = RiskLevel.NORMAL
        decision = _decide_for_action(a, role=role, risk=risk, session=session, user=user)
        if not decision.allowed:
            result.blocked.append((a.entity_id, decision.reason_vi))
            result.reasons.append(decision.reason_vi)
            continue
        # `requires_confirmation` do validator đặt sẵn (ví dụ khoá cửa đến từ mục tiêu SUY
        # DIỄN) phải được tôn trọng: vai trò cao không được rút ngắn HITL cho một hành động
        # mà tầng tất định đã yêu cầu xác nhận. Chỉ THÊM yêu cầu duyệt, không bao giờ gỡ.
        if decision.requires_approval or a.requires_confirmation:
            a.requires_confirmation = True
            result.approval_actions.append(a)
            result.reasons.append(decision.reason_vi)
        else:
            result.allowed_actions.append(a)

    if result.approval_actions:
        result.decision = "WAITING_FOR_USER_APPROVAL"
    elif not result.allowed_actions and result.blocked:
        result.decision = "BLOCKED"
    return result
