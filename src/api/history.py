"""Lịch sử hành động — deliverable bắt buộc của đề bài."""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from src.api import ws
from src.api.deps import CurrentUser, DbSession
from src.domain.models import ActionLog, Device, User
from src.models.schemas import ActionLogOut

router = APIRouter(tags=["history"])


def _build_log_out(log: ActionLog, device: Device | None, actor: User | None) -> ActionLogOut:
    """Dựng ActionLogOut từ một log + thiết bị/người đã có sẵn (dùng chung cho list & realtime)."""
    return ActionLogOut(
        id=log.id,
        approval_id=log.approval_id,
        device_slug=device.slug if device else "",
        device_name=device.name if device else "",
        username=(actor.full_name or actor.username) if actor else "",
        command_text=log.command_text,
        action=log.action,
        params=log.params or {},
        status=str(log.status),
        detail=log.detail,
        source=log.source,
        latency_ms=log.latency_ms,
        created_at=log.created_at,
    )


def action_log_out(session, log: ActionLog) -> ActionLogOut:
    """Serialize MỘT log (tự resolve thiết bị + người) — dùng cho đẩy WebSocket realtime.

    Yêu cầu ``log`` đã được commit (có ``id`` và ``created_at``).
    """
    device = session.get(Device, log.device_id) if log.device_id else None
    actor = session.get(User, log.user_id) if log.user_id else None
    return _build_log_out(log, device, actor)


async def push_log(session, household_id: int, entry: ActionLog) -> None:
    """Đẩy một log vừa GHI (đã commit) tới cả hộ để hiện realtime. Best-effort — không có
    kết nối WS thì là no-op, không ảnh hưởng request."""
    await ws.notify_action_log(household_id, action_log_out(session, entry).model_dump(mode="json"))


@router.get("/history/summary")
async def history_summary(user: CurrentUser, session: DbSession) -> dict:
    """Đếm tổng bản ghi theo trạng thái cho cả hộ — không bị giới hạn bởi phân
    trang của ``/history`` nên các nút lọc luôn hiển thị con số thật."""
    rows = session.execute(
        select(ActionLog.status, func.count())
        .where(ActionLog.household_id == user.household_id)
        .group_by(ActionLog.status)
    ).all()
    by_status = {str(status): count for status, count in rows}
    return {
        "total": sum(by_status.values()),
        "executed": by_status.get("executed", 0),
        "denied": by_status.get("denied", 0),
        "pending_approval": by_status.get("pending_approval", 0),
        "failed": by_status.get("failed", 0),
        "approved": by_status.get("approved", 0),
        "rejected": by_status.get("rejected", 0),
    }


@router.get("/history", response_model=list[ActionLogOut])
async def history(
    user: CurrentUser,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, description="Bỏ qua bao nhiêu bản ghi đầu (phân trang)"),
    source: str = Query(default="", description="Lọc theo nguồn: user / agent / automation"),
) -> list[ActionLogOut]:
    """Nhật ký mọi hành động của hộ, mới nhất trước."""
    query = (
        select(ActionLog, Device, User)
        .outerjoin(Device, Device.id == ActionLog.device_id)
        .outerjoin(User, User.id == ActionLog.user_id)
        .where(ActionLog.household_id == user.household_id)
        .order_by(ActionLog.created_at.desc(), ActionLog.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if source:
        query = query.where(ActionLog.source == source)

    return [_build_log_out(log, device, actor) for log, device, actor in session.execute(query).all()]
