"""Phân quyền: quản-lý-theo-thiết-bị + khoá trẻ em.

Đây là nơi duy nhất quyết định "ai được làm gì" và "lệnh nào phải qua HITL".
Cả API điều khiển trực tiếp lẫn agent đều đi qua ``resolve_access()``.

Không còn phân nhóm theo tuổi: chủ hộ luôn toàn quyền; thành viên theo quyền
per-device (accepted/alert/request) chủ hộ cấp. Việc chặn thiết bị nhạy cảm với
thành viên do **khoá trẻ em** (child lock) quyết định, không do tuổi.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.domain.enums import AccessEffect, RiskLevel, Role
from src.domain.models import AccessRule, Device, Household, User


@dataclass(frozen=True, slots=True)
class Decision:
    """Kết quả kiểm tra quyền cho một hành động lên một thiết bị."""

    allowed: bool
    requires_approval: bool
    reason_vi: str
    # Ai có thể duyệt yêu cầu HITL (nếu cần)
    approver_role: Role | None = None
    # Trạng thái ALERT: cho thực thi ngay nhưng gửi thông báo cho chủ hộ (không chặn).
    notify_owner: bool = False
    # Bị chặn vì KHOÁ TRẺ EM đang bật (phân biệt với "chưa cấp quyền").
    child_locked: bool = False


def evaluate(*, role: Role | str, risk: RiskLevel | str) -> Decision:
    """Quyết định cho CHỦ HỘ (và fallback theo role × risk).

    Ép kiểu đầu vào tại đây có chủ đích: SQLAlchemy/LangGraph trả enum ra ``str``.
    """
    role, risk = Role(role), RiskLevel(risk)
    if role is Role.OWNER:
        return Decision(
            allowed=True,
            requires_approval=False,
            reason_vi="Chủ hộ — toàn quyền, thực thi ngay.",
            approver_role=Role.OWNER,
        )
    # Không phải chủ hộ mà đi qua đây (hiếm — quyền thật của thành viên đi qua access_rules):
    # thiết bị thường cho dùng, còn lại coi như chưa cấp.
    if risk is RiskLevel.NORMAL:
        return Decision(allowed=True, requires_approval=False, reason_vi="Thiết bị thông thường — được phép.")
    return Decision(
        allowed=False,
        requires_approval=False,
        reason_vi="Bạn chưa được chủ hộ cấp quyền dùng thiết bị này.",
    )


def _child_lock_on(session: Session, household_id: int) -> bool:
    household = session.get(Household, household_id)
    return bool(household and household.child_lock_enabled)


def resolve_access(session: Session, *, user: User, device: Device) -> Decision:
    """Quyết định quyền của một người với một thiết bị cụ thể.

    1. Chủ hộ: toàn quyền — ``evaluate()`` (thực thi ngay).
    2. Thành viên: theo luật riêng ``access_rules`` (accepted/alert/request). Không
       có luật = CHƯA CẤP → không điều khiển được, ẩn khỏi giao diện.
    3. KHOÁ TRẺ EM: khi bật, thành viên bị chặn thiết bị công suất lớn/an ninh DÙ
       đã được cấp quyền (child_locked), cho tới khi chủ hộ tắt khoá.
    """
    role = Role(user.role)
    risk = RiskLevel(device.risk_level)

    if role is Role.OWNER:
        return evaluate(role=role, risk=risk)

    rule = session.scalar(
        select(AccessRule).where(AccessRule.user_id == user.id, AccessRule.device_id == device.id)
    )
    if rule is None:
        return Decision(
            allowed=False,
            requires_approval=False,
            reason_vi=f"Bạn chưa được chủ hộ cấp quyền dùng {device.name}.",
        )

    decision = _apply_rule(AccessEffect(rule.effect), device=device)

    if (
        decision.allowed
        and risk in (RiskLevel.HIGH_POWER, RiskLevel.SECURITY)
        and _child_lock_on(session, user.household_id)
    ):
        return Decision(
            allowed=False,
            requires_approval=False,
            child_locked=True,
            reason_vi=f"Khoá trẻ em đang bật — {device.name} tạm thời bị khoá với thành viên.",
        )
    return decision


def _apply_rule(effect: AccessEffect, *, device: Device) -> Decision:
    """Chuyển một luật quyền (accepted/alert/request) thành quyết định cụ thể.

    Chủ hộ cấp trạng thái nào thì tôn trọng trạng thái đó. Ma sát chống bấm nhầm cho
    thiết bị an ninh nằm ở bước CẤP QUYỀN (tầng API), không ở đây.
    """
    if effect is AccessEffect.REQUEST:
        return Decision(
            allowed=True,
            requires_approval=True,
            reason_vi=f"Cần chủ hộ duyệt trước khi bạn dùng {device.name}.",
            approver_role=Role.OWNER,
        )
    if effect is AccessEffect.ALERT:
        return Decision(
            allowed=True,
            requires_approval=False,
            reason_vi=f"Bạn được dùng {device.name}; chủ hộ sẽ nhận thông báo.",
            notify_owner=True,
        )
    return Decision(
        allowed=True,
        requires_approval=False,
        reason_vi=f"Bạn được chủ hộ cấp quyền dùng {device.name}.",
    )


def grant_private_room_access(
    session: Session, *, user: User, set_by_id: int | None = None
) -> int:
    """Cấp sẵn quyền ``accepted`` cho thành viên với các thiết bị trong PHÒNG RIÊNG.

    Ý định gốc: "thành viên có ít nhất quyền của phòng mình" — trước đây chỉ ghi
    trong seed comment nhưng chưa được cài đặt, nên khi deploy lần đầu thành viên
    trắng quyền. Ta hiện thực hoá bằng cách tạo ``AccessRule`` TƯỜNG MINH (chủ hộ
    thấy và sửa/thu hồi được), thay vì để ``resolve_access`` suy ra từ phòng — giữ
    ``resolve_access`` là nguồn quyết định duy nhất (đọc luật tường minh).

    Quy tắc an toàn:

    - Chỉ cấp trong đúng phòng riêng của user; không rộng hơn.
    - **Loại thiết bị an ninh (SECURITY)**: KHÔNG tự cấp — giữ ma sát an ninh, để
      chủ hộ cấp thủ công qua ``set_access`` (có xác nhận ``confirm_security``).
    - **Chỉ thêm khi CHƯA có luật** cho cặp (user, device) — không đè quyền chủ hộ
      đã chỉnh tay. Nhờ vậy hàm idempotent, gọi lại (re-seed) không nhân bản.
    - Không đụng khoá trẻ em: high_power vẫn bị ``resolve_access`` chặn khi khoá bật.

    Trả về số luật vừa thêm. Caller tự ``flush``/``commit``.
    """
    if user.private_room_id is None:
        return 0

    devices = session.scalars(
        select(Device).where(
            Device.household_id == user.household_id,
            Device.room_id == user.private_room_id,
        )
    ).all()
    existing_device_ids = set(
        session.scalars(select(AccessRule.device_id).where(AccessRule.user_id == user.id))
    )

    added = 0
    for device in devices:
        if RiskLevel(device.risk_level) is RiskLevel.SECURITY:
            continue
        if device.id in existing_device_ids:
            continue
        session.add(
            AccessRule(
                household_id=user.household_id,
                user_id=user.id,
                device_id=device.id,
                effect=AccessEffect.ACCEPTED,
                set_by_id=set_by_id,
            )
        )
        added += 1
    return added


def can_approve(*, approver_role: Role | str, required_role: Role | str | None) -> bool:
    """Ai đủ tư cách duyệt một yêu cầu HITL — chỉ theo vai trò (không theo tuổi).

    Yêu cầu cần chủ hộ → chỉ chủ hộ duyệt được. Còn lại → ai cũng được.
    """
    if required_role is not None and Role(required_role) is Role.OWNER:
        return Role(approver_role) is Role.OWNER
    return True
