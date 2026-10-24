"""Tự thêm/sửa thói quen thủ công (giao diện kiểu báo thức) + nhập hàng loạt từ JSON.

Khác với ``memory/habits.py`` (tổng hợp thói quen TỪ lịch sử), file này lo phần
NGƯỜI DÙNG tự khai báo một thói quen: chọn thiết bị, hành động, mức độ và giờ:phút.

Ba việc chính, viết tách khỏi tầng API để test được độc lập:

1. ``build_options`` — liệt kê thiết bị người dùng ĐIỀU KHIỂN ĐƯỢC và bảng hành động
   kèm schema "mức độ", để frontend dựng modal.
2. ``preview`` — kiểm tra từng thói quen (thiết bị có thật, có quyền, hành động hợp lệ,
   tham số đúng khoảng) rồi gom theo KHUNG ``(thiết bị, giờ)`` và chỉ ra xung đột kiểu
   git: một khung có nhiều ứng viên khác nhau thì người dùng phải chọn một.
3. ``commit`` — lưu thật, nhưng CHỈ khi không còn xung đột. Mỗi khung chỉ giữ đúng một
   thói quen: lưu cái mới đồng nghĩa TẮT (bia mộ) các thói quen cũ cùng khung.

Ràng buộc bất biến: người dùng **chỉ tạo được thói quen cho chính mình** — mọi thói
quen lưu ra đều mang ``user_id`` của người đang đăng nhập, bỏ qua mọi user_id trong
payload. "Khung" lấy mốc theo GIỜ (không tính phút) cho khớp ``uq_habits_signature``
và cách ``due_habits`` so "tới giờ"; phút chỉ là mốc nhắc trong giờ, kiểu báo thức.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.agent.planning.conflict import resolve_priority
from src.core.errors import DeviceError
from src.core.permissions import resolve_access
from src.domain.action_registry import (
    action_label_vi,
    actions_for_capabilities,
    get_action_spec,
    habit_action_specs,
    validate_action,
)
from src.domain.enums import Role
from src.domain.models import Device, Habit, User
from src.iot.registry import spec_for, spec_from_device
from src.iot.simulator import apply_command
from src.memory.habits import SOURCE_MANUAL, describe
from src.models.schemas import (
    HabitActionOption,
    HabitCandidateOut,
    HabitCommitOut,
    HabitCrossUserWarning,
    HabitDeviceOption,
    HabitDraftIn,
    HabitInvalidOut,
    HabitOptionsOut,
    HabitOut,
    HabitParamOption,
    HabitPreviewOut,
    HabitSlotOut,
)


def _habit_param(action: str) -> HabitParamOption | None:
    spec = get_action_spec(action)
    if spec is None or spec.parameter is None:
        return None
    parameter = spec.parameter
    bounds = parameter.bounds_for()
    habit_kind: Literal["range", "choice", "bool", "color", "text"]
    if parameter.kind == "number":
        habit_kind = "range"
    elif parameter.kind == "boolean":
        habit_kind = "bool"
    else:
        habit_kind = parameter.kind
    return HabitParamOption(
        key=parameter.name,
        kind=habit_kind,
        label_vi=parameter.label_vi,
        unit=parameter.unit,
        min=bounds[0] if bounds is not None else None,
        max=bounds[1] if bounds is not None else None,
        step=parameter.step,
        choices=[{"value": value, "label": label} for value, label in parameter.choices],
        default=parameter.default,
    )


def build_action_catalog() -> list[HabitActionOption]:
    """Bảng hành động cho frontend: mỗi hành động kèm capability cần có và schema mức độ."""
    return [
            HabitActionOption(
                action=spec.name,
                label_vi=spec.label_vi,
                capability=spec.capability.value,
                param=_habit_param(spec.name),
            )
        for spec in habit_action_specs()
    ]


_CATALOG_ACTIONS: frozenset[str] = frozenset(spec.name for spec in habit_action_specs())


def _actions_for_device(device: Device) -> list[str]:
    """Các hành động trong catalog mà thiết bị này hỗ trợ (theo capabilities đã lưu)."""
    return list(
        actions_for_capabilities(
            device.capabilities or [],
            habit_only=True,
            device_type=device.device_type,
        )
    )


def build_options(session: Session, *, user: User) -> HabitOptionsOut:
    """Thiết bị người dùng ĐIỀU KHIỂN ĐƯỢC (allowed=True) + bảng hành động cho modal.

    Thiết bị bị chặn hẳn (``resolve_access`` không cho) không xuất hiện — không cho đặt
    thói quen lên thứ mình không được đụng. Thiết bị 'cần duyệt' vẫn hiện: lúc tới giờ,
    đề xuất sẽ đi qua HITL như thường.
    """
    devices = session.scalars(
        select(Device).where(Device.household_id == user.household_id).order_by(Device.room, Device.name)
    )
    options: list[HabitDeviceOption] = []
    for device in devices:
        decision = resolve_access(session, user=user, device=device)
        if not decision.allowed:
            continue
        actions = _actions_for_device(device)
        if not actions:
            continue
        options.append(
            HabitDeviceOption(
                slug=device.slug,
                name=device.name,
                room=device.room or "",
                device_type=str(device.device_type),
                capabilities=list(device.capabilities or []),
                requires_approval=decision.requires_approval,
                actions=actions,
            )
        )
    return HabitOptionsOut(devices=options, actions=build_action_catalog())


@dataclass(slots=True)
class _Normalized:
    """Một thói quen đã qua kiểm tra, sẵn sàng để so khung và lưu."""

    index: int
    device: Device
    action: str
    hour: int
    minute: int
    params: dict
    description_vi: str
    requires_approval: bool

    @property
    def slot(self) -> tuple[str, int]:
        return (self.device.slug, self.hour)

    @property
    def fingerprint(self) -> tuple[str, str, int]:
        """Khoá phân biệt hai thói quen trong cùng khung (hành động + tham số + phút)."""
        return (self.action, json.dumps(self.params, sort_keys=True, ensure_ascii=False), self.minute)

    def to_candidate(self) -> HabitCandidateOut:
        return HabitCandidateOut(
            kind="incoming",
            index=self.index,
            source=SOURCE_MANUAL,
            action=self.action,
            action_label_vi=action_label_vi(self.action),
            hour=self.hour,
            minute=self.minute,
            params=dict(self.params),
            description_vi=self.description_vi,
            confidence=1.0,
            enabled=True,
        )


def _validate(session: Session, user: User, draft: HabitDraftIn, index: int) -> tuple[_Normalized | None, HabitInvalidOut | None]:
    """Kiểm tra một thói quen. Trả (normalized, None) nếu hợp lệ, ngược lại (None, lý do)."""

    def invalid(reason: str) -> tuple[None, HabitInvalidOut]:
        return None, HabitInvalidOut(
            index=index,
            device_slug=draft.device_slug,
            action=draft.action,
            hour=draft.hour,
            minute=draft.minute,
            reason_vi=reason,
        )

    if not isinstance(draft.hour, int) or not (0 <= draft.hour <= 23):
        return invalid("Giờ phải là số nguyên trong khoảng 0–23.")
    if not isinstance(draft.minute, int) or not (0 <= draft.minute <= 59):
        return invalid("Phút phải là số nguyên trong khoảng 0–59.")

    device = session.scalar(
        select(Device).where(Device.household_id == user.household_id, Device.slug == draft.device_slug)
    )
    if device is None:
        return invalid(f"Không tìm thấy thiết bị '{draft.device_slug}' trong nhà bạn.")

    if draft.action not in _CATALOG_ACTIONS:
        return invalid(f"Hành động '{draft.action}' không được hỗ trợ để đặt thói quen.")

    decision = resolve_access(session, user=user, device=device)
    if not decision.allowed:
        return invalid(decision.reason_vi)

    spec = spec_for(device.slug) or spec_from_device(device)
    action_spec = get_action_spec(draft.action)
    raw_params = dict(draft.params)
    if action_spec is not None and action_spec.parameter is not None:
        parameter = action_spec.parameter
        if not any(key in raw_params for key in parameter.accepted_input_keys):
            raw_params[parameter.name] = parameter.default
    contract = validate_action(
        draft.action,
        raw_params,
        capabilities=device.capabilities or [],
        device_type=device.device_type,
    )
    if not contract.ok:
        assert contract.issue is not None
        return invalid(contract.issue.message_vi)
    params = contract.params
    # Simulator remains a defensive state-transition check, but reads the same registry.
    try:
        apply_command(spec, dict(device.state or {}), draft.action, params)
    except DeviceError as exc:
        return invalid(exc.message)

    normalized = _Normalized(
        index=index,
        device=device,
        action=draft.action,
        hour=draft.hour,
        minute=draft.minute,
        params=params,
        description_vi=describe(device.slug, draft.action, draft.hour, params, draft.minute),
        requires_approval=decision.requires_approval,
    )
    return normalized, None


def _existing_candidate(habit: Habit) -> HabitCandidateOut:
    return HabitCandidateOut(
        kind="existing",
        habit_id=habit.id,
        source=habit.source,
        action=habit.action,
        action_label_vi=action_label_vi(habit.action),
        hour=habit.hour,
        minute=habit.minute or 0,
        params=dict(habit.params or {}),
        description_vi=habit.description_vi,
        confidence=round(habit.confidence, 2),
        enabled=habit.enabled,
    )


def _existing_fingerprint(habit: Habit) -> tuple[str, str, int]:
    return (
        habit.action,
        json.dumps(dict(habit.params or {}), sort_keys=True, ensure_ascii=False),
        habit.minute or 0,
    )


@dataclass(slots=True)
class _Plan:
    normalized: list[_Normalized] = field(default_factory=list)
    invalid: list[HabitInvalidOut] = field(default_factory=list)


def _build_plan(session: Session, user: User, drafts: list[HabitDraftIn]) -> _Plan:
    plan = _Plan()
    for i, draft in enumerate(drafts):
        norm, bad = _validate(session, user, draft, i)
        if norm is not None:
            plan.normalized.append(norm)
        elif bad is not None:
            plan.invalid.append(bad)
    return plan


def _load_existing_by_slot(session: Session, user: User) -> dict[tuple[str, int], list[Habit]]:
    """Thói quen ĐANG BẬT của chính người dùng, gom theo khung (thiết bị, giờ)."""
    rows = session.scalars(
        select(Habit).where(
            Habit.household_id == user.household_id,
            Habit.user_id == user.id,
            Habit.enabled.is_(True),
        )
    )
    by_slot: dict[tuple[str, int], list[Habit]] = {}
    for habit in rows:
        by_slot.setdefault((habit.device_slug, habit.hour), []).append(habit)
    return by_slot


def _load_other_users_habits(session: Session, user: User) -> dict[tuple[str, int], list[tuple[Habit, User]]]:
    """Thói quen ĐANG BẬT của NGƯỜI KHÁC trong cùng hộ, gom theo khung."""
    rows = session.execute(
        select(Habit, User)
        .join(User, User.id == Habit.user_id)
        .where(
            Habit.household_id == user.household_id,
            Habit.user_id != user.id,
            Habit.enabled.is_(True),
        )
    ).all()
    by_slot: dict[tuple[str, int], list[tuple[Habit, User]]] = {}
    for habit, owner in rows:
        by_slot.setdefault((habit.device_slug, habit.hour), []).append((habit, owner))
    return by_slot


def preview(session: Session, *, user: User, drafts: list[HabitDraftIn]) -> HabitPreviewOut:
    """Kiểm tra trước khi lưu và chỉ ra xung đột theo khung (thiết bị, giờ).

    Một khung có nhiều ứng viên KHÁC NHAU (giữa cái đang có và cái mới, hoặc giữa các
    cái mới với nhau) = xung đột → người dùng phải chọn một. Cái mới trùng khít cái đang
    có coi như không thêm gì (không tính là xung đột).

    Cross-user: nếu thói quen mới xung đột với thói quen của người khác trong hộ, hiện
    cảnh báo. Thói quen của chủ hộ (owner) luôn ưu tiên — member chỉ được thông báo."""
    plan = _build_plan(session, user, drafts)
    existing_by_slot = _load_existing_by_slot(session, user)
    others_by_slot = _load_other_users_habits(session, user)

    # Gom các thói quen MỚI hợp lệ theo khung, giữ thứ tự.
    incoming_by_slot: dict[tuple[str, int], list[_Normalized]] = {}
    for norm in plan.normalized:
        incoming_by_slot.setdefault(norm.slot, []).append(norm)

    slots_out: list[HabitSlotOut] = []
    cross_user_warnings: list[HabitCrossUserWarning] = []
    ready = 0
    has_conflicts = False

    user_is_owner = Role(user.role) is Role.OWNER

    for slot, incomings in incoming_by_slot.items():
        device_slug, hour = slot
        device_name = incomings[0].device.name
        existing = existing_by_slot.get(slot, [])
        existing_fps = {_existing_fingerprint(h): h for h in existing}

        candidates: list[HabitCandidateOut] = [_existing_candidate(h) for h in existing]
        seen_fps: set[tuple[str, str, int]] = set(existing_fps.keys())
        distinct_incoming = 0
        for norm in incomings:
            fp = norm.fingerprint
            if fp in seen_fps:
                continue
            seen_fps.add(fp)
            candidates.append(norm.to_candidate())
            distinct_incoming += 1

        if distinct_incoming == 0:
            continue

        conflict = len(candidates) > 1
        if conflict:
            has_conflicts = True
        else:
            ready += 1

        slots_out.append(
            HabitSlotOut(
                device_slug=device_slug,
                device_name=device_name,
                hour=hour,
                conflict=conflict,
                candidates=candidates,
            )
        )

        # Cross-user conflict: thói quen mới xung đột với thói quen người khác
        others_in_slot = others_by_slot.get(slot, [])
        for other_habit, other_user in others_in_slot:
            other_fp = _existing_fingerprint(other_habit)
            for norm in incomings:
                if norm.fingerprint == other_fp:
                    continue  # trùng khít → không xung đột
                other_name = other_user.full_name or other_user.username
                other_label = action_label_vi(other_habit.action)
                other_desc = other_habit.description_vi or describe(
                    other_habit.device_slug, other_habit.action, other_habit.hour,
                    dict(other_habit.params or {}), other_habit.minute or 0,
                )
                # Dùng ma trận ưu tiên CHUNG của conflict engine (Mục 7 — hợp nhất).
                resolution = resolve_priority(
                    "owner" if user_is_owner else "member", str(other_user.role)
                )
                if user_is_owner or resolution == "origin_wins":
                    msg = (
                        f"{other_name} đã có thói quen \"{other_desc}\" cùng khung giờ. "
                        f"Thói quen của bạn (chủ hộ) sẽ được ưu tiên khi tới giờ."
                    )
                elif resolution == "against_wins":
                    msg = (
                        f"{other_name} (chủ hộ) đã có thói quen \"{other_desc}\" cùng khung giờ. "
                        f"Thói quen của chủ hộ sẽ được ưu tiên khi tới giờ."
                    )
                else:
                    msg = (
                        f"{other_name} đã có thói quen \"{other_desc}\" cùng khung giờ. "
                        f"Có thể xảy ra xung đột khi tới giờ."
                    )
                cross_user_warnings.append(HabitCrossUserWarning(
                    device_slug=device_slug,
                    device_name=device_name,
                    hour=hour,
                    other_user_name=other_name,
                    other_user_role=str(other_user.role),
                    other_action=other_habit.action,
                    other_action_label_vi=other_label,
                    other_params=dict(other_habit.params or {}),
                    other_description_vi=other_desc,
                    message_vi=msg,
                ))
                break  # 1 cảnh báo mỗi (slot, other_user)

    slots_out.sort(key=lambda s: (s.device_name, s.hour))
    return HabitPreviewOut(
        slots=slots_out,
        invalid=plan.invalid,
        cross_user_warnings=cross_user_warnings,
        has_conflicts=has_conflicts,
        has_invalid=bool(plan.invalid),
        total=len(drafts),
        ready=ready,
    )


class HabitEditError(Exception):
    """Không lưu được thói quen — kèm mã HTTP và (tuỳ) danh sách lỗi/xung đột."""

    def __init__(self, message: str, *, status_code: int = 400, invalid: list[HabitInvalidOut] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.invalid = invalid or []


def commit(session: Session, *, user: User, drafts: list[HabitDraftIn]) -> HabitCommitOut:
    """Lưu thật danh sách thói quen ĐÃ giải quyết xung đột.

    Chặn nếu còn mục không hợp lệ (400) hoặc còn hai thói quen mới tranh cùng một khung
    (409). Mỗi khung chỉ giữ một thói quen: lưu cái mới thì TẮT (bia mộ) mọi thói quen cũ
    còn bật trong khung đó — kể cả thói quen học được — để chúng không cùng tới giờ.
    """
    plan = _build_plan(session, user, drafts)
    if plan.invalid:
        raise HabitEditError(
            "Còn thói quen không hợp lệ, chưa thể lưu.", status_code=400, invalid=plan.invalid
        )

    # Trong danh sách gửi lên, mỗi khung chỉ được có một hành động/tham số duy nhất.
    by_slot: dict[tuple[str, int], _Normalized] = {}
    for norm in plan.normalized:
        current = by_slot.get(norm.slot)
        if current is None:
            by_slot[norm.slot] = norm
        elif current.fingerprint != norm.fingerprint:
            raise HabitEditError(
                f"Vẫn còn xung đột chưa giải quyết ở {norm.device.name} lúc {norm.hour:02d}h — "
                "mỗi khung giờ chỉ được giữ một thói quen.",
                status_code=409,
            )
        # trùng khít trong cùng payload → giữ cái đầu, bỏ cái sau

    saved: list[HabitOut] = []
    disabled = 0
    for norm in by_slot.values():
        habit, n_disabled = _persist(session, user, norm)
        disabled += n_disabled
        saved.append(_habit_out(habit))

    session.commit()
    return HabitCommitOut(saved=saved, disabled=disabled)


def edit_habit(session: Session, *, habit: Habit, draft: HabitDraftIn, editor: User) -> Habit:
    """Chỉnh sửa lịch một thói quen đã có (đổi thiết bị/hành động/giờ/tham số), tại chỗ.

    Validate theo quyền của CHỦ thói quen (không phải người đang sửa) để chủ hộ sửa hộ
    thành viên vẫn đúng phạm vi quyền của thành viên đó. Chặn nếu trùng khung giờ với một
    thói quen khác đang bật của cùng người."""
    owner = (session.get(User, habit.user_id) if habit.user_id else None) or editor
    norm, invalid = _validate(session, owner, draft, 0)
    if invalid is not None:
        raise HabitEditError(invalid.reason_vi, status_code=400, invalid=[invalid])
    if norm is None:
        raise HabitEditError("Thói quen không thể chuẩn hoá.", status_code=400)

    clash = session.scalar(
        select(Habit).where(
            Habit.household_id == habit.household_id,
            Habit.user_id == habit.user_id,
            Habit.device_slug == norm.device.slug,
            Habit.hour == norm.hour,
            Habit.enabled.is_(True),
            Habit.id != habit.id,
        )
    )
    if clash is not None:
        raise HabitEditError(
            f"Đã có thói quen khác cho {norm.device.name} lúc {norm.hour:02d}h — mỗi khung một thói quen.",
            status_code=409,
        )

    habit.device_slug = norm.device.slug
    habit.action = norm.action
    habit.hour = norm.hour
    habit.minute = norm.minute
    habit.params = dict(norm.params)
    habit.description_vi = norm.description_vi
    habit.source = SOURCE_MANUAL
    habit.confidence = 1.0
    habit.enabled = True
    session.commit()
    return habit


def _persist(session: Session, user: User, norm: _Normalized) -> tuple[Habit, int]:
    """Tạo/cập nhật một thói quen tay và tắt các thói quen cũ cùng khung. Chưa commit."""
    # Upsert theo đúng chữ ký khoá duy nhất (hộ, người, thiết bị, hành động, giờ).
    habit = session.scalar(
        select(Habit).where(
            Habit.household_id == user.household_id,
            Habit.user_id == user.id,
            Habit.device_slug == norm.device.slug,
            Habit.action == norm.action,
            Habit.hour == norm.hour,
        )
    )
    if habit is None:
        habit = Habit(
            household_id=user.household_id,
            user_id=user.id,
            device_slug=norm.device.slug,
            action=norm.action,
            hour=norm.hour,
        )
        session.add(habit)

    habit.minute = norm.minute
    habit.params = dict(norm.params)
    habit.source = SOURCE_MANUAL
    habit.confidence = 1.0  # thói quen tay luôn ≥ ngưỡng → luôn tới lượt đúng giờ
    habit.occurrences = 0
    habit.enabled = True
    habit.description_vi = norm.description_vi
    habit.snoozed_until = None
    habit.last_triggered_at = None
    session.flush()  # để có id, tránh tự tắt chính nó ở bước dưới

    # Tắt mọi thói quen KHÁC còn bật trong cùng khung (thiết bị, giờ) — một khung một thói quen.
    disabled = 0
    others = session.scalars(
        select(Habit).where(
            Habit.household_id == user.household_id,
            Habit.user_id == user.id,
            Habit.device_slug == norm.device.slug,
            Habit.hour == norm.hour,
            Habit.enabled.is_(True),
            Habit.id != habit.id,
        )
    )
    for other in others:
        other.enabled = False
        disabled += 1
    return habit, disabled


def _habit_out(habit: Habit) -> HabitOut:
    spec = spec_for(habit.device_slug)
    return HabitOut(
        id=habit.id,
        user_id=habit.user_id,
        device_slug=habit.device_slug,
        device_name=spec.name if spec else habit.device_slug,
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
