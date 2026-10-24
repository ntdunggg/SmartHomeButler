"""Dashboard và điều khiển thiết bị trực tiếp.

Nút bấm trên dashboard đi qua đúng ma trận phân quyền như lệnh của agent — không
có đường tắt nào bỏ qua HITL, kể cả khi người dùng bấm tay.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import cast

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, select

from src.api import history, notifications, ws
from src.api.deps import CurrentUser, DbSession, OwnerUser
from src.core.errors import SmartHomeError
from src.core.permissions import Decision, resolve_access
from src.domain.action_registry import action_label_vi
from src.domain.enums import ActionStatus, DeviceType, Role
from src.domain.models import AccessRule, ActionLog, Approval, Device, Habit, Room, Sensor, User
from src.iot.factory import get_bus
from src.iot.registry import TEMPLATE_BY_TYPE, template_for
from src.models.schemas import (
    ConflictOut,
    ControlRequest,
    ControlResponse,
    DashboardOut,
    DeviceCreate,
    DeviceOut,
    DeviceTypeOut,
    DeviceUpdate,
    SensorOut,
)
from src.services.audit import SOURCE_USER, log_action
from src.services.power_usage import POWER_SENSOR_SLUG, estimated_power_sensor

router = APIRouter(tags=["devices"])

def _humanize_cmd(action: str, device_name: str) -> str:
    return f"{action_label_vi(action)} {device_name}"


def _device_out(device: Device, decision: Decision) -> DeviceOut:
    return DeviceOut(
        id=device.id,
        slug=device.slug,
        name=device.name,
        room=device.room,
        room_id=device.room_id,
        device_type=str(device.device_type),
        risk_level=str(device.risk_level),
        capabilities=list(device.capabilities or []),
        state=dict(device.state or {}),
        online=device.online,
        can_control=decision.allowed,
        requires_approval=decision.requires_approval,
        child_locked=decision.child_locked,
        permission_note_vi=decision.reason_vi,
    )


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(user: CurrentUser, session: DbSession) -> DashboardOut:
    """Toàn bộ thiết bị và cảm biến của hộ, kèm quyền của người đang đăng nhập."""
    devices = session.scalars(
        select(Device).where(Device.household_id == user.household_id).order_by(Device.room, Device.name)
    )
    sensors = session.scalars(select(Sensor).where(Sensor.household_id == user.household_id))

    device_out = [_device_out(d, resolve_access(session, user=user, device=d)) for d in devices]

    sensor_out = [
        SensorOut(
            slug=s.slug,
            name=s.name,
            sensor_type=str(s.sensor_type),
            value=round(s.value, 1),
            unit=s.unit,
            room=s.room,
        )
        for s in sensors
        if s.slug != POWER_SENSOR_SLUG
    ]
    sensor_out.append(SensorOut(**estimated_power_sensor(session, household_id=user.household_id)))
    return DashboardOut(devices=device_out, sensors=sensor_out)


@router.post("/devices/{slug}/control", response_model=ControlResponse)
async def control(slug: str, payload: ControlRequest, user: CurrentUser, session: DbSession) -> ControlResponse:
    started = time.perf_counter()

    device = session.scalar(select(Device).where(Device.household_id == user.household_id, Device.slug == slug))
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Không tìm thấy thiết bị '{slug}'.")

    decision = resolve_access(session, user=user, device=device)

    if not decision.allowed:
        denied = log_action(
            session,
            household_id=user.household_id,
            user_id=user.id,
            device_slug=slug,
            action=payload.action,
            params=payload.params,
            status=ActionStatus.DENIED,
            detail=decision.reason_vi,
            source=SOURCE_USER,
        )
        session.commit()
        await history.push_log(session, user.household_id, denied)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=decision.reason_vi)

    if decision.requires_approval:
        approval, pending_log = _create_approval(session, user=user, device=device, payload=payload, decision=decision)
        await history.push_log(session, user.household_id, pending_log)
        # Đẩy popup realtime cho chủ hộ vào duyệt, không bắt họ phải tự mở trang chờ
        await ws.notify_approval_request(user.household_id, approval, requester_name=user.full_name or user.username)
        # Thông báo bền cho chủ hộ (xem lại được kể cả khi offline lúc đó).
        await notifications.notify_recipients(
            session,
            household_id=user.household_id,
            recipient_ids=notifications.owner_ids(session, user.household_id),
            notification_type="approval_requested",
            message_vi=f"{user.full_name or user.username} xin duyệt: {approval.command_text}",
            approval_id=approval.id,
            push_ws=False,
        )
        return ControlResponse(
            ok=False,
            detail_vi=decision.reason_vi,
            state=dict(device.state or {}),
            latency_ms=int((time.perf_counter() - started) * 1000),
            requires_approval=True,
            approval_id=approval.id,
        )

    # Mục 6 + auto-resolve/override: soi xung đột TRƯỚC khi thực thi (map cũng qua conflict engine).
    resolution = _resolve_control_conflicts(session, user=user, device=device, payload=payload)
    conflicts_out = [
        ConflictOut(**{k: v for k, v in c.items() if k in ConflictOut.model_fields}) for c in resolution.conflicts
    ]

    # Xung đột blocking → KHÔNG chặn cứng nữa: xin chủ hộ xác nhận (HITL override).
    # Chủ hộ duyệt xong, resume_after_approval chạy các bước mà KHÔNG chấm lại conflict.
    if any(c.get("blocking") for c in resolution.conflicts):
        approval, pending_log = _create_conflict_approval(
            session, user=user, device=device, payload=payload, resolution=resolution
        )
        await history.push_log(session, user.household_id, pending_log)
        conflict_msg = f"Cần xác nhận vượt xung đột: {approval.command_text}"
        await ws.notify_conflict_override_request(
            user.household_id,
            approval,
            requester_name=user.full_name or user.username,
            message_vi=conflict_msg,
        )
        await notifications.notify_recipients(
            session,
            household_id=user.household_id,
            recipient_ids=notifications.owner_ids(session, user.household_id),
            notification_type="conflict_override_requested",
            message_vi=conflict_msg,
            approval_id=approval.id,
            push_ws=False,
        )
        return ControlResponse(
            ok=False,
            detail_vi="Lệnh có xung đột nghiêm trọng — cần chủ hộ xác nhận trước khi thực hiện.",
            state=dict(device.state or {}),
            latency_ms=int((time.perf_counter() - started) * 1000),
            requires_approval=True,
            approval_id=approval.id,
            conflicts=conflicts_out,
        )

    # Auto-resolve: chạy các bước khắc phục (đóng cửa sổ cùng phòng) TRƯỚC lệnh chính.
    if resolution.remediation:
        await _execute_remediation(session, user=user, remediation=resolution.remediation)

    response = await _execute_now(session, user=user, slug=slug, payload=payload, started=started)
    response.conflicts = conflicts_out
    if resolution.notes and response.ok:
        response.detail_vi = (response.detail_vi + " " + " ".join(resolution.notes)).strip()
    # Override-rồi-báo-sau: lệnh đã chạy, giờ báo cho NGƯỜI BỊ ĐÈ (sở thích/thói quen) biết.
    if response.ok and resolution.affected:
        await _notify_affected(session, household_id=user.household_id, affected=resolution.affected)
    if response.ok:
        await _notify_habit_overridden(session, household_id=user.household_id, actor=user, slug=slug, action=payload.action)
    if resolution.conflicts:
        await _notify_conflict(session, user=user, conflicts=resolution.conflicts)
    # Trạng thái ALERT: lệnh đã chạy, giờ báo cho chủ hộ nắm tình hình.
    if decision.notify_owner and response.ok:
        await notifications.push_alert(session, actor=user, device=device)
    return response


async def _notify_affected(session, *, household_id: int, affected: list) -> None:
    """Báo cho những người bị đè bởi xung đột sở thích/thói quen (sau khi override đã chạy)."""
    if not affected:
        return
    users = session.scalars(select(User).where(User.household_id == household_id)).all()
    by_name: dict[str, int] = {}
    for u in users:
        by_name.setdefault(u.full_name or u.username, u.id)
    for person in affected:
        recipient_id = by_name.get(person.name)
        if recipient_id is None:
            continue  # không map được tên → bỏ qua (best-effort)
        await notifications.notify_user(
            session,
            household_id=household_id,
            recipient_id=recipient_id,
            notification_type="conflict_affected",
            message_vi=person.message_vi,
        )


async def _notify_habit_overridden(
    session,
    *,
    household_id: int,
    actor: User,
    slug: str,
    action: str,
) -> None:
    """Sau khi lệnh thực thi thành công, báo cho những người có thói quen
    trên cùng thiết bị nhưng hành động ngược lại biết rằng thói quen của họ
    vừa bị ghi đè."""
    from src.core import clock

    current_hour = clock.local_now().hour
    habits = session.scalars(
        select(Habit).where(
            Habit.household_id == household_id,
            Habit.device_slug == slug,
            Habit.action != action,
            Habit.hour == current_hour,
            Habit.user_id != actor.id,
            Habit.user_id.is_not(None),
            Habit.enabled.is_(True),
        )
    ).all()
    if not habits:
        return

    user_ids = [h.user_id for h in habits]
    users_by_id = {
        u.id: u
        for u in session.scalars(select(User).where(User.id.in_(user_ids))).all()
    }
    action_vi = action_label_vi(action)
    actor_name = actor.full_name or actor.username

    device = session.scalar(
        select(Device).where(Device.household_id == household_id, Device.slug == slug)
    )
    device_name = device.name if device else slug

    for habit in habits:
        recipient = users_by_id.get(habit.user_id)
        if recipient is None:
            continue
        msg = (
            f"{actor_name} vừa {action_vi.lower()} {device_name} "
            f"lúc {current_hour}h — thói quen của bạn vào khung giờ này đã bị ghi đè."
        )
        await notifications.notify_user(
            session,
            household_id=household_id,
            recipient_id=recipient.id,
            notification_type="conflict_affected",
            message_vi=msg,
        )


def _resolve_control_conflicts(session, *, user, device: Device, payload: ControlRequest):
    """Chấm xung đột cho MỘT lệnh bấm tay, đã kèm auto-resolve + ưu tiên vai trò."""
    from src.agent.planning.conflict import PlanStep, resolve
    from src.services.context import build_snapshot

    snapshot = build_snapshot(session, household_id=user.household_id, actor_name=user.full_name or user.username)
    step: PlanStep = {
        "device_slug": device.slug,
        "action": payload.action,
        "params": payload.params,
    }
    return resolve(cast(list[PlanStep], [step]), snapshot, actor_role=str(user.role))


def _remediation_step_dicts(session, *, user, remediation: list[dict]) -> list[dict]:
    """Biến bước khắc phục (PlanStep) thành step thực thi được, CHỈ giữ bước người dùng đủ quyền.

    Không tự đóng cửa nếu người ra lệnh không được phép điều khiển cửa đó — auto-resolve
    không được nới quyền (invariant: RL/tiện ích không làm yếu authorization)."""
    out: list[dict] = []
    for r in remediation:
        slug = r["device_slug"]
        dev = session.scalar(select(Device).where(Device.household_id == user.household_id, Device.slug == slug))
        if dev is None:
            continue
        dec = resolve_access(session, user=user, device=dev)
        if not dec.allowed or dec.requires_approval:
            continue
        out.append(
            {
                "device_slug": slug,
                "device_name": dev.name,
                "action": r["action"],
                "params": dict(r.get("params") or {}),
                "risk_level": str(dev.risk_level),
                "allowed": True,
                "skipped": False,
                "auto_resolved": True,
            }
        )
    return out


async def _execute_remediation(session, *, user, remediation: list[dict]) -> None:
    """Chạy các bước khắc phục phụ trợ (đóng cửa sổ). Lỗi ở đây KHÔNG chặn lệnh chính."""
    for step in _remediation_step_dicts(session, user=user, remediation=remediation):
        try:
            result = await get_bus().send_command(
                user.household_id, step["device_slug"], {"action": step["action"], "params": step["params"]}
            )
        except SmartHomeError:
            continue
        entry = log_action(
            session,
            household_id=user.household_id,
            user_id=user.id,
            device_slug=step["device_slug"],
            action=step["action"],
            params=step["params"],
            status=ActionStatus.EXECUTED if result.ok else ActionStatus.FAILED,
            detail=result.detail,
            source=SOURCE_USER,
        )
        session.commit()
        await history.push_log(session, user.household_id, entry)


def _create_conflict_approval(session, *, user, device: Device, payload: ControlRequest, resolution):
    """Tạo yêu cầu HITL để chủ hộ xác nhận một lệnh có xung đột blocking.

    Steps gồm cả bước khắc phục (nếu có) + lệnh chính, để khi duyệt chúng chạy trọn gói."""
    steps = _remediation_step_dicts(session, user=user, remediation=resolution.remediation)
    steps.append(
        {
            "device_slug": device.slug,
            "device_name": device.name,
            "action": payload.action,
            "params": payload.params,
            "risk_level": str(device.risk_level),
            "allowed": True,
            "skipped": False,
        }
    )
    reason = (
        "; ".join(c.get("message_vi", "") for c in resolution.conflicts if c.get("blocking"))
        or "Cần xác nhận do có xung đột."
    )
    approval = Approval(
        household_id=user.household_id,
        requested_by_id=user.id,
        conversation_id="",
        command_text=_humanize_cmd(payload.action, device.name),
        reason_vi=reason,
        steps=steps,
        required_role=str(Role.OWNER),
        status=ActionStatus.PENDING_APPROVAL,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        source="map",
    )
    session.add(approval)
    session.flush()
    pending_log = log_action(
        session,
        household_id=user.household_id,
        user_id=user.id,
        device_slug=device.slug,
        action=payload.action,
        params=payload.params,
        status=ActionStatus.PENDING_APPROVAL,
        detail=reason,
        source=SOURCE_USER,
        approval_id=approval.id,
    )
    session.commit()
    session.refresh(approval)
    return approval, pending_log


async def _notify_conflict(session, *, user, conflicts: list[dict]) -> None:
    await notifications.notify_recipients(
        session,
        household_id=user.household_id,
        recipient_ids=notifications.owner_ids(session, user.household_id),
        notification_type="conflict_warning",
        message_vi=conflicts[0].get("message_vi", "Có xung đột trong lệnh vừa thực hiện."),
    )


def _create_approval(
    session, *, user, device: Device, payload: ControlRequest, decision: Decision
) -> tuple[Approval, ActionLog]:
    """Ghi yêu cầu HITL cho một lệnh bấm tay.

    ``conversation_id`` để rỗng — đây là dấu hiệu phân biệt với yêu cầu do agent
    tạo ra, vì lệnh bấm tay không có luồng LangGraph nào đang chờ resume.
    """
    approval = Approval(
        household_id=user.household_id,
        requested_by_id=user.id,
        conversation_id="",
        command_text=_humanize_cmd(payload.action, device.name),
        reason_vi=decision.reason_vi,
        steps=[
            {
                "device_slug": device.slug,
                "device_name": device.name,
                "action": payload.action,
                "params": payload.params,
                "risk_level": str(device.risk_level),
                # Bước này đã qua kiểm quyền (được phép, chỉ chờ duyệt). Executor khi resume
                # đọc "allowed"/"skipped" để quyết chạy — thiếu thì bị coi là DENIED và không
                # gửi lệnh xuống bus (thiết bị không đổi, mọi trang đứng im sau khi duyệt).
                "allowed": True,
                "skipped": False,
            }
        ],
        required_role=str(decision.approver_role or Role.OWNER),
        status=ActionStatus.PENDING_APPROVAL,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        source="map",
    )
    session.add(approval)
    session.flush()
    pending_log = log_action(
        session,
        household_id=user.household_id,
        user_id=user.id,
        device_slug=device.slug,
        action=payload.action,
        params=payload.params,
        status=ActionStatus.PENDING_APPROVAL,
        detail=decision.reason_vi,
        source=SOURCE_USER,
        approval_id=approval.id,
    )
    session.commit()
    session.refresh(approval)
    return approval, pending_log


async def _execute_now(session, *, user, slug: str, payload: ControlRequest, started: float) -> ControlResponse:
    try:
        result = await get_bus().send_command(
            user.household_id, slug, {"action": payload.action, "params": payload.params}
        )
    except SmartHomeError as exc:
        failed = log_action(
            session,
            household_id=user.household_id,
            user_id=user.id,
            device_slug=slug,
            action=payload.action,
            params=payload.params,
            status=ActionStatus.FAILED,
            detail=exc.message,
            source=SOURCE_USER,
        )
        session.commit()
        await history.push_log(session, user.household_id, failed)
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    latency_ms = int((time.perf_counter() - started) * 1000)
    entry = log_action(
        session,
        household_id=user.household_id,
        user_id=user.id,
        device_slug=slug,
        action=payload.action,
        params=payload.params,
        status=ActionStatus.EXECUTED if result.ok else ActionStatus.FAILED,
        detail=result.detail,
        source=SOURCE_USER,
        latency_ms=latency_ms,
    )
    session.commit()
    await history.push_log(session, user.household_id, entry)

    return ControlResponse(
        ok=result.ok,
        detail_vi=result.detail,
        state=result.state,
        latency_ms=latency_ms,
    )


# --------------------------------------------------------------------------
# Quản lý thiết bị — thêm/sửa/xoá (chỉ chủ hộ). Danh sách LOẠI thiết bị cố định,
# người dùng chỉ tạo instance mới từ một loại đã biết.
# --------------------------------------------------------------------------
@router.get("/device-catalog", response_model=list[DeviceTypeOut])
async def device_catalog(user: CurrentUser) -> list[DeviceTypeOut]:
    """Các loại thiết bị có thể thêm (khuôn theo loại, danh sách không đổi)."""
    return [
        DeviceTypeOut(
            device_type=str(dtype),
            default_name=tmpl.name,
            risk_level=str(tmpl.risk_level),
            capabilities=[str(c) for c in tmpl.capabilities],
        )
        for dtype, tmpl in TEMPLATE_BY_TYPE.items()
    ]


def _resolve_room(session: DbSession, owner, room_id: int | None) -> Room | None:
    if room_id is None:
        return None
    room = session.get(Room, room_id)
    if room is None or room.household_id != owner.household_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy phòng.")
    return room


def _generate_slug(session: DbSession, household_id: int, device_type: str) -> str:
    """Sinh slug duy nhất trong hộ, dạng '<loại>_<n>'."""
    taken = set(session.scalars(select(Device.slug).where(Device.household_id == household_id)))
    i = 1
    while f"{device_type}_{i}" in taken:
        i += 1
    return f"{device_type}_{i}"


@router.post("/devices", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
async def create_device(payload: DeviceCreate, owner: OwnerUser, session: DbSession) -> DeviceOut:
    """Thêm một thiết bị mới vào hộ, tạo từ một loại đã biết trong catalog."""
    try:
        dtype = DeviceType(payload.device_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Loại thiết bị không hợp lệ: {payload.device_type!r}.",
        ) from None
    template = template_for(dtype)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Chưa có khuôn cho loại '{payload.device_type}'.",
        )

    room = _resolve_room(session, owner, payload.room_id)
    device = Device(
        household_id=owner.household_id,
        slug=_generate_slug(session, owner.household_id, str(dtype)),
        name=payload.name,
        room=room.name if room else "",
        room_id=room.id if room else None,
        device_type=dtype,
        risk_level=template.risk_level,
        capabilities=[c.value for c in template.capabilities],
        state=dict(template.initial_state),
    )
    session.add(device)
    session.commit()
    session.refresh(device)
    return _device_out(device, resolve_access(session, user=owner, device=device))


@router.patch("/devices/{slug}", response_model=DeviceOut)
async def update_device(slug: str, payload: DeviceUpdate, owner: OwnerUser, session: DbSession) -> DeviceOut:
    """Đổi tên thiết bị hoặc chuyển sang phòng khác."""
    device = session.scalar(select(Device).where(Device.household_id == owner.household_id, Device.slug == slug))
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Không tìm thấy thiết bị '{slug}'.")

    if payload.name is not None:
        device.name = payload.name
    if payload.room_id is not None:
        room = _resolve_room(session, owner, payload.room_id)
        assert room is not None  # room_id không None ở đây -> _resolve_room trả Room (hoặc raise 404)
        device.room_id = room.id
        device.room = room.name

    session.commit()
    session.refresh(device)
    return _device_out(device, resolve_access(session, user=owner, device=device))


@router.delete("/devices/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(slug: str, owner: OwnerUser, session: DbSession) -> None:
    """Xoá một thiết bị khỏi hộ (các luật quyền riêng liên quan bị xoá theo cascade)."""
    device = session.scalar(select(Device).where(Device.household_id == owner.household_id, Device.slug == slug))
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Không tìm thấy thiết bị '{slug}'.")
    session.execute(delete(AccessRule).where(AccessRule.device_id == device.id))
    session.delete(device)
    session.commit()
