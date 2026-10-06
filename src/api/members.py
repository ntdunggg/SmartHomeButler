"""Quản lý thành viên trong hộ — chỉ chủ hộ được dùng.

Cho phép chủ hộ thêm/sửa thành viên và đặt quyền truy cập riêng của từng thành
viên với từng thiết bị (bảng ``access_rules``). Xem ``src/core/permissions.py``
để biết các luật này được áp dụng thế nào khi điều khiển thiết bị.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, func, select

from src.api.deps import CurrentUser, DbSession, OwnerUser
from src.core.permissions import grant_private_room_access, resolve_access
from src.core.security import hash_password
from src.domain.enums import AccessEffect, RiskLevel, Role
from src.domain.models import AccessRule, Device, Room, User
from src.memory.profile import build_member_profile
from src.models.schemas import (
    AccessDeviceOut,
    AccessRuleOut,
    AccessRulesUpdate,
    MemberAccessOut,
    MemberCreate,
    MemberOut,
    MemberProfileOut,
    MemberUpdate,
    RoomAccessUpdate,
    ScheduleSlotOut,
)

router = APIRouter(prefix="/members", tags=["members"])


def _room_name(session: DbSession, room_id: int | None) -> str:
    if room_id is None:
        return ""
    room = session.get(Room, room_id)
    return room.name if room else ""


def _to_member_out(session: DbSession, user: User) -> MemberOut:
    return MemberOut(
        id=user.id,
        username=user.username,
        account_id=user.account_id,
        full_name=user.full_name or user.username,
        role=str(user.role),
        home_room_id=user.home_room_id,
        home_room_name=_room_name(session, user.home_room_id),
        private_room_id=user.private_room_id,
        private_room_name=_room_name(session, user.private_room_id),
    )


def _resolve_user_room(
    session: DbSession, owner: User, room_id: int | None, *, label_vi: str
) -> int | None:
    """Kiểm tra phòng gán cho user thuộc đúng hộ; None nghĩa là gỡ gán."""
    if room_id is None:
        return None
    room = session.get(Room, room_id)
    if room is None or room.household_id != owner.household_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy {label_vi}.",
        )
    return room.id


def _parse_role(raw: str) -> Role:
    try:
        return Role(raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Vai trò không hợp lệ: {raw!r}. Chỉ nhận 'owner' hoặc 'member'.",
        ) from None


def _get_member(session: DbSession, member_id: int, owner: User) -> User:
    """Lấy thành viên và chặn truy cập chéo hộ."""
    user = session.get(User, member_id)
    if user is None or user.household_id != owner.household_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy thành viên.")
    return user


@router.get("", response_model=list[MemberOut])
async def list_members(owner: OwnerUser, session: DbSession) -> list[MemberOut]:
    """Danh sách thành viên trong hộ."""
    users = session.scalars(select(User).where(User.household_id == owner.household_id).order_by(User.id))
    return [_to_member_out(session, u) for u in users]


@router.post("", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def create_member(payload: MemberCreate, owner: OwnerUser, session: DbSession) -> MemberOut:
    """Thêm thành viên mới vào hộ."""
    if session.scalar(select(User).where(User.username == payload.username)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Tên đăng nhập đã tồn tại.")

    role = _parse_role(payload.role)
    home_room_id = _resolve_user_room(session, owner, payload.home_room_id, label_vi="phòng mặc định")
    private_room_id = _resolve_user_room(session, owner, payload.private_room_id, label_vi="phòng riêng")
    user = User(
        household_id=owner.household_id,
        username=payload.username,
        account_id=uuid.uuid4().hex[:12],
        password_hash=hash_password(payload.password),
        role=role,
        home_room_id=home_room_id,
        private_room_id=private_room_id,
    )
    # full_name là property mã hoá theo hộ — gán sau khi đã có household_id
    user.full_name = payload.full_name
    session.add(user)
    session.flush()  # cần user.id để cấp quyền phòng riêng
    # Cấp sẵn quyền các thiết bị trong phòng riêng (trừ an ninh) — thành viên mới
    # không trắng quyền. Chủ hộ vẫn sửa/thu hồi được qua set_access.
    grant_private_room_access(session, user=user, set_by_id=owner.id)
    session.commit()
    session.refresh(user)
    return _to_member_out(session, user)


@router.patch("/{member_id}", response_model=MemberOut)
async def update_member(
    member_id: int, payload: MemberUpdate, owner: OwnerUser, session: DbSession
) -> MemberOut:
    """Sửa tên, tuổi, mật khẩu hoặc vai trò của một thành viên."""
    user = _get_member(session, member_id, owner)

    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.password is not None:
        user.password_hash = hash_password(payload.password)
    if payload.role is not None:
        new_role = _parse_role(payload.role)
        _guard_last_owner(session, owner, target=user, new_role=new_role)
        user.role = new_role
    # -1 là sentinel "không gửi trường này"; None nghĩa là gỡ phòng đã gán.
    if payload.home_room_id != -1:
        user.home_room_id = _resolve_user_room(
            session, owner, payload.home_room_id, label_vi="phòng mặc định"
        )
    if payload.private_room_id != -1:
        user.private_room_id = _resolve_user_room(
            session, owner, payload.private_room_id, label_vi="phòng riêng"
        )

    session.commit()
    session.refresh(user)
    return _to_member_out(session, user)


def _guard_last_owner(session: DbSession, owner: User, *, target: User, new_role: Role) -> None:
    """Không cho hạ vai trò chủ hộ cuối cùng xuống thành viên (hộ sẽ mất người quản trị)."""
    if Role(target.role) is Role.OWNER and new_role is not Role.OWNER:
        owner_count = session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.household_id == owner.household_id, User.role == Role.OWNER)
        )
        if (owner_count or 0) <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Hộ phải có ít nhất một chủ hộ.",
            )


@router.delete("/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_member(member_id: int, owner: OwnerUser, session: DbSession) -> None:
    """Xoá một thành viên khỏi hộ.

    Không cho tự xoá chính mình và không cho xoá chủ hộ cuối cùng — tránh để hộ
    mất người quản trị. Các luật quyền riêng của thành viên bị xoá theo (cascade).
    """
    user = _get_member(session, member_id, owner)
    if user.id == owner.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Không thể tự xoá tài khoản của bạn.")
    if Role(user.role) is Role.OWNER:
        owner_count = session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.household_id == owner.household_id, User.role == Role.OWNER)
        )
        if (owner_count or 0) <= 1:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Hộ phải có ít nhất một chủ hộ.")

    session.execute(delete(AccessRule).where(AccessRule.user_id == user.id))
    session.delete(user)
    session.commit()


@router.get("/me/profile", response_model=MemberProfileOut)
async def get_my_profile(user: CurrentUser, session: DbSession) -> MemberProfileOut:
    """Hồ sơ hành vi của chính người đang đăng nhập (mọi role)."""
    profile = build_member_profile(session, household_id=user.household_id, user_id=user.id)
    return MemberProfileOut(
        user_id=profile.user_id,
        preferences=profile.preferences,
        routines=profile.routines,
        habits=profile.habits,
        schedule=[
            ScheduleSlotOut(part=s.part, hours=list(s.hours), habits=list(s.habits), routines=list(s.routines))
            for s in profile.schedule
        ],
    )


@router.get("/{member_id}/profile", response_model=MemberProfileOut)
async def get_profile(member_id: int, owner: OwnerUser, session: DbSession) -> MemberProfileOut:
    """Hồ sơ hành vi + lịch trình theo buổi mà agent đã học cho một thành viên.

    Chỉ đọc: tổng hợp sở thích, thói quen đã học (routine) và habit theo giờ. Không thay đổi
    gì — việc học vẫn ở tầng consolidate/learn_habits."""
    user = _get_member(session, member_id, owner)
    profile = build_member_profile(session, household_id=owner.household_id, user_id=user.id)
    return MemberProfileOut(
        user_id=profile.user_id,
        preferences=profile.preferences,
        routines=profile.routines,
        habits=profile.habits,
        schedule=[
            ScheduleSlotOut(part=s.part, hours=list(s.hours), habits=list(s.habits), routines=list(s.routines))
            for s in profile.schedule
        ],
    )


def _member_access_out(session: DbSession, user: User) -> MemberAccessOut:
    """Dựng quyền per-device của một thành viên — dùng cho cả chủ hộ xem lẫn tự xem.

    ``effect`` = trạng thái chủ hộ đã cấp (accepted/alert/request) hoặc ``none``
    (chưa cấp). ``allowed`` / ``requires_approval`` là kết quả đã giải quyết.
    """
    devices = list(
        session.scalars(
            select(Device).where(Device.household_id == user.household_id).order_by(Device.room, Device.name)
        )
    )
    rules = {r.device_id: r for r in session.scalars(select(AccessRule).where(AccessRule.user_id == user.id))}

    device_entries: list[AccessDeviceOut] = []
    rule_out: list[AccessRuleOut] = []
    for d in devices:
        decision = resolve_access(session, user=user, device=d)
        rule = rules.get(d.id)
        effect = str(rule.effect) if rule is not None else "none"
        in_private = (
            user.private_room_id is not None and d.room_id is not None and d.room_id == user.private_room_id
        )
        device_entries.append(
            AccessDeviceOut(
                device_slug=d.slug,
                device_name=d.name,
                room=d.room,
                room_id=d.room_id,
                risk_level=str(d.risk_level),
                effect=effect,
                allowed=decision.allowed,
                requires_approval=decision.requires_approval,
                in_private_room=in_private,
                note_vi=decision.reason_vi,
            )
        )
        if rule is not None:
            rule_out.append(AccessRuleOut(device_slug=d.slug, device_name=d.name, effect=str(rule.effect)))

    return MemberAccessOut(
        user_id=user.id,
        private_room_id=user.private_room_id,
        private_room_name=_room_name(session, user.private_room_id),
        devices=device_entries,
        rules=rule_out,
    )


@router.get("/me/access", response_model=MemberAccessOut)
async def get_my_access(user: CurrentUser, session: DbSession) -> MemberAccessOut:
    """Thành viên tự xem quyền của mình với mọi thiết bị (dùng CurrentUser)."""
    return _member_access_out(session, user)


@router.get("/{member_id}/access", response_model=MemberAccessOut)
async def get_access(member_id: int, owner: OwnerUser, session: DbSession) -> MemberAccessOut:
    """Chủ hộ xem quyền của một thành viên (cho popup chỉnh quyền)."""
    return _member_access_out(session, _get_member(session, member_id, owner))


@router.put("/{member_id}/access", response_model=MemberAccessOut)
async def set_access(
    member_id: int, payload: AccessRulesUpdate, owner: OwnerUser, session: DbSession
) -> MemberAccessOut:
    """Thay toàn bộ danh sách quyền riêng của một thành viên.

    Gửi danh sách rỗng nghĩa là xoá hết luật riêng — thành viên quay về phân quyền
    mặc định theo mức rủi ro thiết bị.
    """
    user = _get_member(session, member_id, owner)

    devices_by_slug = {
        d.slug: d
        for d in session.scalars(select(Device).where(Device.household_id == owner.household_id))
    }

    # Dựng sẵn danh sách luật mới trước khi xoá cái cũ, để lỗi validate không làm mất dữ liệu
    new_rules = []
    for item in payload.rules:
        device = devices_by_slug.get(item.device_slug)
        if device is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy thiết bị '{item.device_slug}'.",
            )
        try:
            effect = AccessEffect(item.effect)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Quyền không hợp lệ: {item.effect!r}. Chỉ nhận 'accepted', 'alert' hoặc 'request'.",
            ) from None
        new_rules.append((device, effect))

    # Ma sát chống bấm nhầm: cấp "accepted"/"alert" cho thiết bị an ninh (member dùng
    # KHÔNG cần duyệt) phải được xác nhận một lần. Chủ hộ vẫn toàn quyền quyết định.
    if not payload.confirm_security:
        risky = [
            device.name
            for device, effect in new_rules
            if RiskLevel(device.risk_level) is RiskLevel.SECURITY
            and effect in (AccessEffect.ACCEPTED, AccessEffect.ALERT)
        ]
        if risky:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "confirm_security_required",
                    "devices": risky,
                    "message_vi": (
                        "Bạn đang cho phép thành viên dùng thiết bị an ninh mà không cần bạn duyệt: "
                        + ", ".join(risky)
                        + ". Xác nhận để tiếp tục."
                    ),
                },
            )

    session.execute(delete(AccessRule).where(AccessRule.user_id == user.id))
    out = []
    for device, effect in new_rules:
        session.add(
            AccessRule(
                household_id=owner.household_id,
                user_id=user.id,
                device_id=device.id,
                effect=effect,
                set_by_id=owner.id,
            )
        )
        out.append(AccessRuleOut(device_slug=device.slug, device_name=device.name, effect=str(effect)))
    session.commit()
    return MemberAccessOut(user_id=user.id, rules=out)


@router.post("/{member_id}/access/room/{room_id}", response_model=MemberAccessOut)
async def set_room_access(
    member_id: int, room_id: int, payload: RoomAccessUpdate, owner: OwnerUser, session: DbSession
) -> MemberAccessOut:
    """Cấp một trạng thái cho MỌI thiết bị trong một phòng (thao tác hàng loạt).

    Upsert từng thiết bị — chỉ đụng phòng này, không xoá quyền ở phòng khác. Vẫn
    kiểm tra xác nhận với thiết bị an ninh như ``set_access``.
    """
    user = _get_member(session, member_id, owner)

    room = session.get(Room, room_id)
    if room is None or room.household_id != owner.household_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy phòng.")

    try:
        effect = AccessEffect(payload.effect)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Quyền không hợp lệ: {payload.effect!r}. Chỉ nhận 'accepted', 'alert' hoặc 'request'.",
        ) from None

    devices = list(
        session.scalars(select(Device).where(Device.household_id == owner.household_id, Device.room_id == room_id))
    )

    if not payload.confirm_security and effect in (AccessEffect.ACCEPTED, AccessEffect.ALERT):
        risky = [d.name for d in devices if RiskLevel(d.risk_level) is RiskLevel.SECURITY]
        if risky:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "confirm_security_required",
                    "devices": risky,
                    "message_vi": (
                        "Trong phòng có thiết bị an ninh: "
                        + ", ".join(risky)
                        + ". Xác nhận để cấp quyền không cần duyệt."
                    ),
                },
            )

    existing = {
        r.device_id: r
        for r in session.scalars(select(AccessRule).where(AccessRule.user_id == user.id))
    }
    for d in devices:
        rule = existing.get(d.id)
        if rule is not None:
            rule.effect = effect
        else:
            session.add(
                AccessRule(
                    household_id=owner.household_id,
                    user_id=user.id,
                    device_id=d.id,
                    effect=effect,
                    set_by_id=owner.id,
                )
            )
    session.commit()
    return await get_access(member_id, owner, session)
