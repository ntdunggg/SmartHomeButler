"""Khoá trẻ em (child lock).

Chủ hộ bật/tắt. Khi bật, thành viên bị chặn thiết bị công suất lớn/an ninh DÙ đã
được cấp quyền (thực thi ở ``resolve_access``). Bật khoá cũng vô hiệu hoá các yêu
cầu duyệt đang chờ cho thiết bị nhạy cảm.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from src.api import notifications, ws
from src.api.deps import CurrentUser, DbSession, OwnerUser
from src.domain.enums import ActionStatus, RiskLevel
from src.domain.models import Approval, Household
from src.models.schemas import ChildLockOut, ChildLockUpdate

router = APIRouter(prefix="/household", tags=["childlock"])

_SENSITIVE = {RiskLevel.HIGH_POWER.value, RiskLevel.SECURITY.value}


def _to_out(household: Household) -> ChildLockOut:
    at = household.child_lock_enabled_at
    return ChildLockOut(
        enabled=household.child_lock_enabled,
        enabled_at=at.isoformat() if at is not None else None,
        enabled_by_id=household.child_lock_enabled_by_id,
    )


def _expire_sensitive_pending(session: DbSession, household_id: int) -> None:
    """Bật khoá → các yêu cầu duyệt đang chờ cho thiết bị nhạy cảm bị coi là hết hạn."""
    pendings = session.scalars(
        select(Approval).where(
            Approval.household_id == household_id, Approval.status == ActionStatus.PENDING_APPROVAL
        )
    )
    for approval in pendings:
        if any(step.get("risk_level") in _SENSITIVE for step in (approval.steps or [])):
            approval.status = ActionStatus.EXPIRED


@router.get("/child-lock", response_model=ChildLockOut)
async def get_child_lock(user: CurrentUser, session: DbSession) -> ChildLockOut:
    """Trạng thái khoá trẻ em — mọi thành viên đều xem được."""
    household = session.get(Household, user.household_id)
    if household is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hộ gia đình không tồn tại.")
    return _to_out(household)


@router.put("/child-lock", response_model=ChildLockOut)
async def set_child_lock(payload: ChildLockUpdate, owner: OwnerUser, session: DbSession) -> ChildLockOut:
    """Bật/tắt khoá trẻ em — chỉ chủ hộ."""
    household = session.get(Household, owner.household_id)
    if household is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hộ gia đình không tồn tại.")
    household.child_lock_enabled = payload.enabled
    if payload.enabled:
        household.child_lock_enabled_at = datetime.now(UTC)
        household.child_lock_enabled_by_id = owner.id
        _expire_sensitive_pending(session, owner.household_id)
    session.commit()
    await ws.notify_child_lock_changed(owner.household_id, payload.enabled)
    trang_thai = "bật" if payload.enabled else "tắt"
    msg = f"{owner.full_name or owner.username} vừa {trang_thai} khoá trẻ em."
    all_recipients = notifications.member_ids(session, owner.household_id)
    other_owners = [uid for uid in notifications.owner_ids(session, owner.household_id) if uid != owner.id]
    all_recipients.extend(other_owners)
    await notifications.notify_recipients(
        session,
        household_id=owner.household_id,
        recipient_ids=all_recipients,
        notification_type="child_lock_changed",
        message_vi=msg,
    )
    return _to_out(household)
