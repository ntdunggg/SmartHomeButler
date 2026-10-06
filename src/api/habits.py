"""Xem và quản lý các thói quen mà agent đã học.

Chủ hộ quản lý được thói quen của cả nhà; thành viên chỉ quản lý thói quen của
chính mình. Tắt một thói quen (``enabled=false``) khiến nó ngừng sinh đề xuất mà
không xoá dữ liệu đã học; xoá hẳn dùng DELETE.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from src.api.deps import CurrentUser, DbSession
from src.domain.enums import Role
from src.domain.models import Device, Habit, User
from src.memory import habit_editing
from src.models.schemas import (
    HabitBatchIn,
    HabitCommitOut,
    HabitDraftIn,
    HabitOptionsOut,
    HabitOut,
    HabitPreviewOut,
    HabitUpdate,
)

router = APIRouter(prefix="/habits", tags=["habits"])


def _to_habit_out(habit: Habit, name_by_slug: dict[str, str]) -> HabitOut:
    return HabitOut(
        id=habit.id,
        user_id=habit.user_id,
        device_slug=habit.device_slug,
        device_name=name_by_slug.get(habit.device_slug, ""),
        action=habit.action,
        hour=habit.hour,
        minute=habit.minute or 0,
        params=dict(habit.params or {}),
        confidence=round(habit.confidence, 2),
        occurrences=habit.occurrences,
        description_vi=habit.description_vi,
        enabled=habit.enabled,
        source=habit.source,
    )


def _get_habit(session: DbSession, habit_id: int, user: User, *, action: str = "quản lý") -> Habit:
    """Lấy thói quen và ép ranh giới quyền hạn."""
    habit = session.get(Habit, habit_id)
    if habit is None or habit.household_id != user.household_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy thói quen.")
    if Role(user.role) is not Role.OWNER and habit.user_id != user.id:
        habit_owner = session.get(User, habit.user_id)
        if habit_owner and Role(habit_owner.role) is Role.OWNER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Không thể {action} thói quen của chủ hộ ({habit_owner.full_name or habit_owner.username}).",
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Bạn chỉ quản lý được thói quen của chính mình."
        )
    return habit


@router.get("", response_model=list[HabitOut])
async def list_habits(
    user: CurrentUser,
    session: DbSession,
    member_id: int | None = Query(default=None),
    include_disabled: bool = Query(default=False),
) -> list[HabitOut]:
    """Liệt kê thói quen. Chủ hộ thấy cả nhà (lọc được theo ``member_id``); thành viên chỉ thấy của mình.

    Mặc định ẩn thói quen đã tắt — chúng vẫn nằm trong bảng làm bia mộ để vòng học
    không tái tạo lại. Truyền ``include_disabled=true`` để xem và bật lại."""
    query = select(Habit).where(Habit.household_id == user.household_id)
    if Role(user.role) is Role.OWNER:
        if member_id is not None:
            query = query.where(Habit.user_id == member_id)
    else:
        query = query.where(Habit.user_id == user.id)
    if not include_disabled:
        query = query.where(Habit.enabled.is_(True))

    habits = list(session.scalars(query.order_by(Habit.user_id, Habit.hour, Habit.id)))
    name_by_slug = {
        d.slug: d.name for d in session.scalars(select(Device).where(Device.household_id == user.household_id))
    }
    return [_to_habit_out(h, name_by_slug) for h in habits]


@router.get("/options", response_model=HabitOptionsOut)
async def habit_options(user: CurrentUser, session: DbSession) -> HabitOptionsOut:
    """Dữ liệu cho modal thêm thói quen: thiết bị người dùng điều khiển được + bảng hành động.

    Chỉ trả về thiết bị người dùng thực sự được đụng — không ai đặt thói quen lên thiết bị
    mình bị cấm. Vì thế route này dùng cho MỌI thành viên, không riêng chủ hộ."""
    return habit_editing.build_options(session, user=user)


@router.post("/preview", response_model=HabitPreviewOut)
async def preview_habits(payload: HabitBatchIn, user: CurrentUser, session: DbSession) -> HabitPreviewOut:
    """Kiểm tra một hoặc nhiều thói quen (form tay / nhập JSON) — KHÔNG lưu.

    Trả về mục không hợp lệ (kèm lý do) và các khung xung đột kiểu git để người dùng chọn
    một trong hai. Người dùng chỉ tạo thói quen cho CHÍNH MÌNH nên mọi user_id trong
    payload đều bị bỏ qua (route luôn dùng danh tính người đăng nhập)."""
    return habit_editing.preview(session, user=user, drafts=payload.habits)


def _target_member(session: DbSession, user: User, member_id: int | None) -> User:
    """Người sẽ SỞ HỮU thói quen. Chủ hộ có thể tạo hộ thành viên; thành viên chỉ cho mình."""
    if member_id is None or member_id == user.id:
        return user
    if Role(user.role) is not Role.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Chỉ chủ hộ mới thêm thói quen cho thành viên khác."
        )
    target = session.get(User, member_id)
    if target is None or target.household_id != user.household_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy thành viên.")
    return target


@router.post("/commit", response_model=HabitCommitOut)
async def commit_habits(
    payload: HabitBatchIn,
    user: CurrentUser,
    session: DbSession,
    member_id: int | None = Query(default=None),
) -> HabitCommitOut:
    """Lưu thật danh sách thói quen đã giải quyết hết xung đột.

    400 nếu còn mục không hợp lệ, 409 nếu còn hai thói quen tranh cùng một khung giờ.
    Chủ hộ có thể truyền ``member_id`` để thêm thói quen hộ một thành viên."""
    target = _target_member(session, user, member_id)
    try:
        result = habit_editing.commit(session, user=target, drafts=payload.habits)
    except habit_editing.HabitEditError as exc:
        detail: dict = {"message": exc.message}
        if exc.invalid:
            detail["invalid"] = [item.model_dump() for item in exc.invalid]
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc
    await _notify_cross_user_conflicts(session, user=target)
    return result


@router.patch("/{habit_id}", response_model=HabitOut)
async def update_habit(habit_id: int, payload: HabitUpdate, user: CurrentUser, session: DbSession) -> HabitOut:
    """Bật/tắt hoặc chỉnh lịch một thói quen (đổi thiết bị/hành động/giờ/tham số)."""
    habit = _get_habit(session, habit_id, user, action="sửa")

    # Có gửi trường lịch → điều chỉnh (validate theo quyền của chủ thói quen).
    schedule_fields = (payload.device_slug, payload.action, payload.hour, payload.minute, payload.params)
    if any(f is not None for f in schedule_fields):
        draft = HabitDraftIn(
            device_slug=payload.device_slug or habit.device_slug,
            action=payload.action or habit.action,
            hour=payload.hour if payload.hour is not None else habit.hour,
            minute=payload.minute if payload.minute is not None else (habit.minute or 0),
            params=payload.params if payload.params is not None else dict(habit.params or {}),
        )
        try:
            habit = habit_editing.edit_habit(session, habit=habit, draft=draft, editor=user)
        except habit_editing.HabitEditError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    if payload.enabled is not None:
        habit.enabled = payload.enabled
        session.commit()

    session.refresh(habit)
    name_by_slug = {
        d.slug: d.name for d in session.scalars(select(Device).where(Device.household_id == user.household_id))
    }
    return _to_habit_out(habit, name_by_slug)


@router.delete("/{habit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_habit(habit_id: int, user: CurrentUser, session: DbSession) -> None:
    """Bỏ một thói quen học nhầm — nó ngừng sinh đề xuất vĩnh viễn.

    KHÔNG xoá dòng khỏi bảng. ``habits`` là bảng DẪN XUẤT từ ``action_logs``: xoá đi
    thì vòng ``learn_habits`` kế tiếp (mỗi 30s) tái tạo lại y nguyên theo chữ ký
    (hộ, người, thiết bị, hành động, giờ) vì dữ liệu nguồn vẫn còn. Tắt mới là thứ
    bền — ``_upsert`` chỉ cập nhật số liệu thống kê, không bao giờ đụng ``enabled``.
    """
    habit = _get_habit(session, habit_id, user, action="xoá")
    habit.enabled = False
    session.commit()


async def _notify_cross_user_conflicts(session: DbSession, *, user: User) -> None:
    from src.api import notifications
    from src.memory.habits import describe as describe_habit

    my_habits = session.scalars(
        select(Habit).where(
            Habit.household_id == user.household_id,
            Habit.user_id == user.id,
            Habit.enabled.is_(True),
        )
    ).all()
    if not my_habits:
        return
    for habit in my_habits:
        conflicts = session.scalars(
            select(Habit).where(
                Habit.household_id == user.household_id,
                Habit.device_slug == habit.device_slug,
                Habit.hour == habit.hour,
                Habit.action != habit.action,
                Habit.user_id != user.id,
                Habit.user_id.is_not(None),
                Habit.enabled.is_(True),
            )
        ).all()
        if not conflicts:
            continue
        creator_name = user.full_name or user.username
        new_desc = describe_habit(habit.device_slug, habit.action, habit.hour, habit.params, habit.minute or 0)
        for other in conflicts:
            recipient_id = other.user_id
            if recipient_id is None:
                continue
            other_desc = describe_habit(other.device_slug, other.action, other.hour, other.params, other.minute or 0)
            msg = (
                f"{creator_name} vừa tạo thói quen \"{new_desc}\" "
                f"xung đột với thói quen \"{other_desc}\" của bạn."
            )
            recipient_id = other.user_id
            if recipient_id is None:
                continue
            await notifications.notify_user(
                session, household_id=user.household_id, recipient_id=recipient_id,
                notification_type="habit_conflict_created", message_vi=msg,
            )
