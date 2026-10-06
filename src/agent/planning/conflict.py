"""Deterministic household conflict rules used by the multi-agent runtime."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TypedDict

from src.domain.enums import ConflictType, DeviceType, Severity
from src.iot.registry import spec_for
from src.iot.simulator import (
    ACTION_CLOSE,
    ACTION_OPEN,
    ACTION_SET_POSITION,
    ACTION_SET_TEMPERATURE,
    ACTION_TURN_ON,
    ACTION_UNLOCK,
)


class PlanStep(TypedDict, total=False):
    device_slug: str
    action: str
    params: dict


class Conflict(TypedDict, total=False):
    type: str
    severity: str
    message_vi: str
    device_slug: str
    suggestion_vi: str
    blocking: bool
    # Chỉ dùng bởi HABIT_SCHEDULE: ai ra lệnh vs. thói quen của ai đang bị đụng.
    origin: str
    against: str
    resolution: str


@dataclass(slots=True)
class HabitInfo:
    """Một thói quen đang hiệu lực ở giờ hiện tại (đầu vào cho xung đột lịch thói quen)."""

    device_slug: str
    device_name: str
    hour: int
    action: str
    user_name: str = ""


@dataclass(slots=True)
class ContextSnapshot:
    """Household context captured when evaluating a plan."""

    device_states: dict[str, dict] = field(default_factory=dict)
    someone_home: bool = True
    away_minutes: int = 0
    actor_name: str = ""
    away_threshold_minutes: int = 120
    # Mục 7 — lịch thói quen: giờ hiện tại + các thói quen đang hiệu lực
    current_hour: int = 0
    active_habits: list[HabitInfo] = field(default_factory=list)


def resolve_priority(origin_role: str, against_role: str) -> str:
    """Ma trận ưu tiên theo vai trò khi hai bên đụng nhau.

    Chủ hộ thắng thành viên; ngang vai → cảnh báo để người dùng tự quyết.
    """
    if origin_role == against_role:
        return "warn"
    return "origin_wins" if origin_role == "owner" else "against_wins"


def detect(plan: list[PlanStep], ctx: ContextSnapshot) -> list[Conflict]:
    conflicts: list[Conflict] = []
    conflicts.extend(_detect_waste(plan, ctx))
    conflicts.extend(_detect_contradiction(plan, ctx))
    conflicts.extend(_detect_safety(plan, ctx))
    conflicts.extend(_detect_habit_schedule(plan, ctx))
    return conflicts


def _detect_habit_schedule(plan: list[PlanStep], ctx: ContextSnapshot) -> list[Conflict]:
    """Lệnh đụng THÓI QUEN cùng thiết bị, cùng giờ, nhưng hành động ngược nhau."""
    conflicts: list[Conflict] = []
    for step in plan:
        slug = step.get("device_slug", "")
        action = step.get("action", "")
        for habit in ctx.active_habits:
            if habit.device_slug == slug and habit.hour == ctx.current_hour and habit.action and habit.action != action:
                who = habit.user_name or "thành viên"
                conflicts.append(
                    Conflict(
                        type=str(ConflictType.HABIT_SCHEDULE),
                        severity=str(Severity.WARNING),
                        device_slug=slug,
                        message_vi=(
                            f"Giờ này {who} thường {habit.action} {habit.device_name}; "
                            f"lệnh “{action}” đang đi ngược thói quen đó."
                        ),
                        suggestion_vi="Xác nhận nếu bạn vẫn muốn thay đổi.",
                        blocking=False,
                        origin=ctx.actor_name,
                        against=who,
                        resolution="warn",
                    )
                )
    return conflicts


def _detect_waste(plan: list[PlanStep], ctx: ContextSnapshot) -> list[Conflict]:
    if ctx.someone_home or ctx.away_minutes < ctx.away_threshold_minutes:
        return []

    conflicts: list[Conflict] = []
    wasteful_types = (DeviceType.WATER_HEATER, DeviceType.AIR_CONDITIONER)
    for step in plan:
        if step.get("action") not in (ACTION_TURN_ON, ACTION_SET_TEMPERATURE):
            continue
        spec = spec_for(step.get("device_slug", ""))
        if spec is None or spec.device_type not in wasteful_types:
            continue
        hours = ctx.away_minutes // 60
        conflicts.append(
            Conflict(
                type=str(ConflictType.WASTE),
                severity=str(Severity.WARNING),
                device_slug=spec.slug,
                message_vi=f"Cả nhà đã vắng khoảng {hours} tiếng mà lệnh này lại bật {spec.name} — rất tốn điện.",
                suggestion_vi=f"Bạn có chắc muốn bật {spec.name} khi không có ai ở nhà không?",
                blocking=False,
            )
        )
    return conflicts


def _detect_contradiction(plan: list[PlanStep], ctx: ContextSnapshot) -> list[Conflict]:
    conflicts: list[Conflict] = []
    cooling = [
        step
        for step in plan
        if step.get("action") in (ACTION_TURN_ON, ACTION_SET_TEMPERATURE)
        and (spec := spec_for(step.get("device_slug", ""))) is not None
        and spec.device_type is DeviceType.AIR_CONDITIONER
    ]
    for cooling_step in cooling:
        cooling_spec = spec_for(cooling_step.get("device_slug", ""))
        if cooling_spec is None:
            continue
        room = cooling_spec.room

        def _window_step(step: PlanStep) -> bool:
            spec = spec_for(step.get("device_slug", ""))
            return spec is not None and spec.device_type is DeviceType.WINDOW and spec.room == room

        def _opens_window(step: PlanStep) -> bool:
            if not _window_step(step):
                return False
            action = step.get("action")
            position = step.get("params", {}).get("position")
            return action == ACTION_OPEN or (
                action == ACTION_SET_POSITION and isinstance(position, int | float) and position > 0
            )

        def _closes_window(step: PlanStep) -> bool:
            if not _window_step(step):
                return False
            action = step.get("action")
            position = step.get("params", {}).get("position")
            return action == ACTION_CLOSE or (
                action == ACTION_SET_POSITION and isinstance(position, int | float) and position <= 0
            )

        opening_window = any(_opens_window(step) for step in plan)
        closing_window = any(_closes_window(step) for step in plan)
        window_already_open = any(
            (spec := spec_for(slug)) is not None
            and spec.device_type is DeviceType.WINDOW
            and spec.room == room
            and (state or {}).get("position", 0) > 0
            for slug, state in ctx.device_states.items()
        )
        if (opening_window or window_already_open) and not closing_window:
            conflicts.append(
                Conflict(
                    type=str(ConflictType.CONTRADICTION),
                    severity=str(Severity.WARNING),
                    device_slug=cooling_step.get("device_slug", ""),
                    message_vi=(
                        f"Đang bật {cooling_spec.name} trong khi cửa sổ cùng phòng đang mở — "
                        "hơi lạnh sẽ thoát hết ra ngoài."
                    ),
                    suggestion_vi="Tôi đóng cửa sổ lại trước khi bật điều hoà nhé?",
                    blocking=False,
                )
            )

    seen: dict[str, str] = {}
    for step in plan:
        slug = step.get("device_slug", "")
        action = step.get("action", "")
        previous = seen.get(slug)
        if previous and previous != action and {previous, action} <= {ACTION_TURN_ON, "turn_off"}:
            spec = spec_for(slug)
            conflicts.append(
                Conflict(
                    type=str(ConflictType.CONTRADICTION),
                    severity=str(Severity.CRITICAL),
                    device_slug=slug,
                    message_vi=f"Kế hoạch vừa bật vừa tắt {spec.name if spec else slug} — không thực hiện được cả hai.",
                    suggestion_vi="Bạn muốn giữ lại hành động nào ạ?",
                    blocking=True,
                )
            )
        seen[slug] = action
    return conflicts


def _detect_safety(plan: list[PlanStep], ctx: ContextSnapshot) -> list[Conflict]:
    if ctx.someone_home:
        return []

    conflicts: list[Conflict] = []
    for step in plan:
        spec = spec_for(step.get("device_slug", ""))
        if spec is None:
            continue
        action = step.get("action")
        if spec.device_type is DeviceType.DOOR_LOCK and action == ACTION_UNLOCK:
            conflicts.append(
                Conflict(
                    type=str(ConflictType.SAFETY),
                    severity=str(Severity.CRITICAL),
                    device_slug=spec.slug,
                    message_vi="Đang định mở khoá cửa chính trong khi trong nhà không có ai.",
                    suggestion_vi="Bạn xác nhận lại giúp tôi — có đúng là muốn mở cửa lúc này không?",
                    blocking=True,
                )
            )
        elif spec.device_type is DeviceType.CAMERA and action == "turn_off":
            conflicts.append(
                Conflict(
                    type=str(ConflictType.SAFETY),
                    severity=str(Severity.CRITICAL),
                    device_slug=spec.slug,
                    message_vi="Đang định tắt camera an ninh trong khi nhà đang không có người.",
                    suggestion_vi="Nên giữ camera bật khi cả nhà đi vắng. Bạn vẫn muốn tắt chứ?",
                    blocking=True,
                )
            )
    return conflicts


def has_blocking(conflicts: list[Conflict]) -> bool:
    return any(conflict.get("blocking") for conflict in conflicts)


# ---------------------------------------------------------------------------
# Giải quyết xung đột (auto-resolve + ưu tiên vai trò)
#
# Trước đây gặp xung đột "blocking" là chặn cứng, không làm gì. Hai bổ sung:
#   1) Auto-resolve: vài case gỡ được bằng một bước phụ deterministic (đóng cửa
#      sổ cùng phòng TRƯỚC khi bật điều hoà) — không cần hỏi người dùng.
#   2) resolve_priority: giữ lại cho các xung đột người-với-người như lịch thói quen.
# Đây chỉ là tầng "đề xuất/lọc" thuần, KHÔNG tự thực thi và KHÔNG nới quyền —
# tầng gọi vẫn phải kiểm quyền từng bước remediation và vẫn qua HITL cho blocking.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AffectedPerson:
    """Người bị một xung đột giữa-người-với-người ảnh hưởng — để BÁO LẠI sau khi override."""

    name: str
    message_vi: str


@dataclass(slots=True)
class Resolution:
    """Kết quả sau khi cố gắng tự gỡ xung đột cho một kế hoạch."""

    remediation: list[PlanStep] = field(default_factory=list)  # bước phụ cần chạy TRƯỚC
    conflicts: list[Conflict] = field(default_factory=list)  # xung đột còn lại sau khi gỡ
    notes: list[str] = field(default_factory=list)  # ghi chú cho người ra lệnh
    affected: list[AffectedPerson] = field(default_factory=list)  # người bị đè → cần báo sau


def _auto_close_windows(plan: list[PlanStep], ctx: ContextSnapshot) -> tuple[list[PlanStep], list[str]]:
    """Đóng cửa sổ đang mở cùng phòng TRƯỚC khi bật/đặt nhiệt độ điều hoà."""
    cooling_rooms: set[str] = set()
    for step in plan:
        if step.get("action") not in (ACTION_TURN_ON, ACTION_SET_TEMPERATURE):
            continue
        spec = spec_for(step.get("device_slug", ""))
        if spec is not None and spec.device_type is DeviceType.AIR_CONDITIONER:
            cooling_rooms.add(spec.room)
    if not cooling_rooms:
        return [], []

    already_closing = {
        step.get("device_slug") for step in plan if step.get("action") == ACTION_CLOSE
    }
    remediation: list[PlanStep] = []
    notes: list[str] = []
    for slug, state in ctx.device_states.items():
        spec = spec_for(slug)
        if spec is None or spec.device_type is not DeviceType.WINDOW or spec.room not in cooling_rooms:
            continue
        if (state or {}).get("position", 0) <= 0 or slug in already_closing:
            continue  # cửa đã đóng hoặc plan đã tự đóng
        remediation.append(PlanStep(device_slug=slug, action=ACTION_CLOSE, params={}))
        notes.append(f"Đã tự đóng {spec.name} trước khi bật điều hoà cùng phòng.")
    return remediation, notes


def _affected_people(conflicts: list[Conflict], actor_name: str) -> list[AffectedPerson]:
    """Người bị đè bởi xung đột GIỮA-NGƯỜI-VỚI-NGƯỜI (HABIT_SCHEDULE).

    Xung đột an ninh / tự-mâu-thuẫn KHÔNG có "người bị đè" nên không phát sinh ai."""
    affected: list[AffectedPerson] = []
    for conflict in conflicts:
        kind = conflict.get("type")
        if kind == str(ConflictType.HABIT_SCHEDULE):
            name = conflict.get("against", "")
            if name and name != actor_name and name != "thành viên":
                who = actor_name or "Ai đó"
                affected.append(AffectedPerson(
                    name=name,
                    message_vi=f"{who} vừa ra lệnh đi ngược thói quen của bạn: {conflict.get('message_vi', '')}",
                ))
    return affected


def resolve(plan: list[PlanStep], ctx: ContextSnapshot, *, actor_role: str) -> Resolution:
    """Phát hiện xung đột SAU khi đã tự gỡ những gì gỡ được.

    Thứ tự: (1) sinh bước đóng cửa sổ, (2) phản ánh cửa đã đóng vào ngữ cảnh để không
    còn báo mâu thuẫn cửa-sổ, (3) chấm lại xung đột, (4) gom người bị đè để BÁO SAU.
    """
    _ = actor_role  # Giữ chữ ký tương thích với caller; preference priority đã bị xoá.
    remediation, notes = _auto_close_windows(plan, ctx)
    if remediation:
        states = dict(ctx.device_states)
        for step in remediation:
            slug = step["device_slug"]
            states[slug] = {**(states.get(slug) or {}), "position": 0}
        ctx = replace(ctx, device_states=states)
    conflicts = detect(plan, ctx)
    affected = _affected_people(conflicts, ctx.actor_name)
    return Resolution(remediation=remediation, conflicts=conflicts, notes=notes, affected=affected)
