"""Shared deterministic guard for every AI-agent approval entry point."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.permissions import can_approve, resolve_access
from src.domain.enums import ActionStatus, Role
from src.domain.models import Approval, Device, User


class ApprovalCheckCode(StrEnum):
    OK = "ok"
    ALREADY_RESOLVED = "already_resolved"
    ALREADY_PROCESSING = "already_processing"
    EXPIRED = "expired"
    FORBIDDEN = "forbidden"
    INVALIDATED = "invalidated"


@dataclass(frozen=True, slots=True)
class ApprovalCheck:
    code: ApprovalCheckCode
    reason_vi: str = ""

    @property
    def ok(self) -> bool:
        return self.code is ApprovalCheckCode.OK


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def check_approval(
    session: Session,
    *,
    approval: Approval,
    approver: User,
    for_execution: bool,
    now: datetime | None = None,
) -> ApprovalCheck:
    """Validate approval freshness, approver role, and current requester access.

    ``for_execution=False`` is used for rejection: the approver still needs the
    required role, but stale requester permissions must not prevent cancellation.
    """
    if approval.status == ActionStatus.EXPIRED:
        return ApprovalCheck(ApprovalCheckCode.EXPIRED, "Yêu cầu đã hết hạn — vui lòng thao tác lại.")
    if approval.status == ActionStatus.EXECUTING:
        return ApprovalCheck(ApprovalCheckCode.ALREADY_PROCESSING, "Yêu cầu này đang được xử lý.")
    if approval.status != ActionStatus.PENDING_APPROVAL:
        return ApprovalCheck(ApprovalCheckCode.ALREADY_RESOLVED, "Yêu cầu này đã được xử lý.")

    current_time = now or datetime.now(UTC)
    if approval.expires_at is not None and current_time > _as_utc(approval.expires_at):
        return ApprovalCheck(ApprovalCheckCode.EXPIRED, "Yêu cầu đã hết hạn — vui lòng thao tác lại.")

    try:
        required = Role(approval.required_role) if approval.required_role else Role.OWNER
    except ValueError:
        return ApprovalCheck(ApprovalCheckCode.INVALIDATED, "Vai trò duyệt của yêu cầu không hợp lệ.")
    if not can_approve(approver_role=approver.role, required_role=required):
        return ApprovalCheck(
            ApprovalCheckCode.FORBIDDEN,
            "Lệnh này cần bố/mẹ (chủ hộ) xác nhận — bạn chưa đủ quyền duyệt.",
        )

    if not for_execution:
        return ApprovalCheck(ApprovalCheckCode.OK)

    if not approval.steps:
        return ApprovalCheck(ApprovalCheckCode.INVALIDATED, "Yêu cầu không có hành động hợp lệ.")

    requester = session.get(User, approval.requested_by_id)
    if requester is None or requester.household_id != approval.household_id:
        return ApprovalCheck(ApprovalCheckCode.INVALIDATED, "Không tìm thấy người yêu cầu.")

    for step in approval.steps or []:
        slug = str(step.get("device_slug") or "")
        if not slug:
            return ApprovalCheck(ApprovalCheckCode.INVALIDATED, "Yêu cầu chứa thiết bị không hợp lệ.")
        device = session.scalar(
            select(Device).where(
                Device.household_id == approval.household_id,
                Device.slug == slug,
            )
        )
        if device is None:
            return ApprovalCheck(ApprovalCheckCode.INVALIDATED, f"Không tìm thấy thiết bị '{slug}'.")
        decision = resolve_access(session, user=requester, device=device)
        if not decision.allowed:
            return ApprovalCheck(ApprovalCheckCode.INVALIDATED, decision.reason_vi)

    return ApprovalCheck(ApprovalCheckCode.OK)


def expire_approval(approval: Approval, *, resolved_by_id: int | None = None) -> None:
    """Move an invalid/expired approval to a terminal, non-executable state."""
    approval.status = ActionStatus.EXPIRED
    approval.resolved_by_id = resolved_by_id
    approval.resolved_at = datetime.now(UTC)
