"""Ghi log lịch sử hành động — deliverable bắt buộc của đề bài.

Mọi thay đổi trạng thái thiết bị đều đi qua đây, bất kể do người dùng bấm nút,
agent thực hiện hay automation tự kích hoạt. Nhờ đó bảng ``action_logs`` vừa là
nhật ký kiểm toán, vừa là dữ liệu để học thói quen.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.domain.enums import ActionStatus
from src.domain.models import ActionLog, AuditEnvelope, Device

SOURCE_USER = "user"
SOURCE_AGENT = "agent"
SOURCE_AUTOMATION = "automation"


def log_action(
    session: Session,
    *,
    household_id: int,
    action: str,
    status: ActionStatus,
    user_id: int | None = None,
    device_slug: str = "",
    command_text: str = "",
    conversation_id: str = "",
    approval_id: int | None = None,
    params: dict | None = None,
    detail: str = "",
    source: str = SOURCE_AGENT,
    latency_ms: int = 0,
) -> ActionLog:
    """Ghi một dòng log. Người gọi chịu trách nhiệm commit."""
    device_id = None
    if device_slug:
        device_id = session.scalar(
            select(Device.id).where(Device.household_id == household_id, Device.slug == device_slug)
        )

    entry = ActionLog(
        household_id=household_id,
        user_id=user_id,
        device_id=device_id,
        conversation_id=conversation_id,
        approval_id=approval_id,
        command_text=command_text,
        action=action,
        params=params or {},
        status=status,
        detail=detail,
        source=source,
        latency_ms=latency_ms,
    )
    session.add(entry)
    return entry


def record_audit_envelope(
    session: Session,
    *,
    household_id: int,
    user_id: int | None,
    conversation_id: str,
    input_text: str,
    envelope: dict[str, Any],
    plan_id: str = "",
) -> AuditEnvelope:
    """Persist envelope quyết định §53 (FR-14). Người gọi chịu trách nhiệm commit."""
    entry = AuditEnvelope(
        household_id=household_id,
        user_id=user_id,
        conversation_id=conversation_id,
        plan_id=plan_id,
        input_text=input_text,
        envelope=envelope,
    )
    session.add(entry)
    return entry


def recent_actions(session: Session, *, household_id: int, limit: int = 50) -> list[ActionLog]:
    return list(
        session.scalars(
            select(ActionLog)
            .where(ActionLog.household_id == household_id)
            .order_by(ActionLog.created_at.desc(), ActionLog.id.desc())
            .limit(limit)
        )
    )
