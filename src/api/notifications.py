"""Thông báo cho chủ hộ — dùng cho trạng thái quyền ALERT.

Khi thành viên điều khiển một thiết bị mà chủ hộ đã cấp quyền ``alert``, lệnh chạy
ngay (không chặn) nhưng chủ hộ nhận một thông báo để nắm tình hình. Khác với
``Approval`` (chặn chờ duyệt) — ở đây hành động đã xảy ra rồi.
"""

from __future__ import annotations

from datetime import UTC

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, update

from src.api import ws
from src.api.deps import CurrentUser, DbSession
from src.domain.enums import Role
from src.domain.models import Device, Notification, User
from src.models.schemas import NotificationOut

router = APIRouter(prefix="/notifications", tags=["notifications"])

_RECENT_LIMIT = 50


def _to_out(n: Notification) -> NotificationOut:
    created = n.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return NotificationOut(
        id=n.id,
        notification_type=n.notification_type,
        message_vi=n.message_vi,
        approval_id=n.approval_id,
        data=n.data or {},
        read=n.read,
        created_at=created.isoformat() if created is not None else None,
    )


async def notify_user(
    session: DbSession,
    *,
    household_id: int,
    recipient_id: int,
    notification_type: str,
    message_vi: str,
    approval_id: int | None = None,
) -> None:
    """Ghi một thông báo cho MỘT người + đẩy realtime tới đúng máy của họ."""
    notif = Notification(
        household_id=household_id,
        recipient_id=recipient_id,
        notification_type=notification_type,
        message_vi=message_vi,
        approval_id=approval_id,
    )
    session.add(notif)
    session.commit()
    # Lấy id auto-generate để FE thêm được vào dropdown (không có id thì chỉ hiện toast rồi mất)
    session.refresh(notif)
    await ws.manager.broadcast(
        household_id,
        {
            "type": "notification",
            "id": notif.id,
            "notification_type": notification_type,
            "message_vi": message_vi,
        },
        user_id=recipient_id,
    )


async def notify_recipients(
    session: DbSession,
    *,
    household_id: int,
    recipient_ids: list[int],
    notification_type: str,
    message_vi: str,
    approval_id: int | None = None,
    push_ws: bool = True,
) -> None:
    """Ghi thông báo cho NHIỀU người (mỗi người một bản để theo dõi đã đọc riêng) + đẩy WS.

    ``push_ws=False``: chỉ ghi DB, không đẩy WS — dùng khi caller đã có WS event riêng
    (VD: approval_request đã push qua ``ws.notify_approval_request``).
    """
    if not recipient_ids:
        return
    notifs: list[tuple[int, Notification]] = []
    for rid in recipient_ids:
        n = Notification(
            household_id=household_id,
            recipient_id=rid,
            notification_type=notification_type,
            message_vi=message_vi,
            approval_id=approval_id,
        )
        session.add(n)
        notifs.append((rid, n))
    session.commit()
    if not push_ws:
        return
    for rid, n in notifs:
        session.refresh(n)
        await ws.manager.broadcast(
            household_id,
            {"type": "notification", "id": n.id, "notification_type": notification_type, "message_vi": message_vi},
            user_id=rid,
        )


def owner_ids(session: DbSession, household_id: int) -> list[int]:
    return list(
        session.scalars(
            select(User.id).where(User.household_id == household_id, User.role == Role.OWNER)
        )
    )


def member_ids(session: DbSession, household_id: int) -> list[int]:
    return list(
        session.scalars(
            select(User.id).where(User.household_id == household_id, User.role == Role.MEMBER)
        )
    )


async def push_alert(session: DbSession, *, actor: User, device: Device) -> None:
    """Ghi thông báo cho MỌI chủ hộ trong hộ + đẩy realtime. Gọi sau khi lệnh đã chạy.

    Một bản ghi cho mỗi chủ hộ (để theo dõi đã đọc riêng), một sự kiện WS chung gửi
    tới các máy của chủ hộ.
    """
    owners = list(
        session.scalars(
            select(User).where(User.household_id == actor.household_id, User.role == Role.OWNER)
        )
    )
    if not owners:
        return
    message = f"{actor.full_name or actor.username} vừa dùng {device.name}."
    notifs: list[tuple[int, Notification]] = []
    for owner in owners:
        n = Notification(
            household_id=actor.household_id,
            recipient_id=owner.id,
            actor_id=actor.id,
            device_id=device.id,
            notification_type="sensitive_device_used",
            message_vi=message,
        )
        session.add(n)
        notifs.append((owner.id, n))
    session.commit()
    # Mỗi chủ hộ có bản ghi riêng (id riêng) → đẩy riêng từng người kèm đúng id của họ,
    # thay vì một broadcast chung thiếu id (FE sẽ không thêm được vào dropdown).
    for oid, n in notifs:
        session.refresh(n)
        await ws.manager.broadcast(
            actor.household_id,
            {
                "type": "notification",
                "id": n.id,
                "notification_type": "sensitive_device_used",
                "message_vi": message,
            },
            user_id=oid,
        )


@router.get("", response_model=list[NotificationOut])
async def list_notifications(user: CurrentUser, session: DbSession) -> list[NotificationOut]:
    """Thông báo gần đây gửi tới người đang đăng nhập (mới nhất trước)."""
    rows = session.scalars(
        select(Notification)
        .where(Notification.recipient_id == user.id)
        .order_by(Notification.created_at.desc())
        .limit(_RECENT_LIMIT)
    )
    return [_to_out(n) for n in rows]


@router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(notification_id: int, user: CurrentUser, session: DbSession) -> None:
    n = session.get(Notification, notification_id)
    if n is None or n.recipient_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy thông báo.")
    n.read = True
    session.commit()


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def mark_all_read(user: CurrentUser, session: DbSession) -> None:
    session.execute(
        update(Notification).where(Notification.recipient_id == user.id, ~Notification.read).values(read=True)
    )
    session.commit()
