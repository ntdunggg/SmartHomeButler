"""Tự thêm thói quen thủ công: kiểm tra hợp lệ, xung đột kiểu git, và lưu.

Khoá các bất biến của tính năng:
- Chỉ hiện/đặt được thói quen lên thiết bị người dùng ĐIỀU KHIỂN ĐƯỢC.
- Thói quen tay được bảo vệ khỏi vòng học/decay (confidence không bị hạ về 0).
- Một khung (thiết bị, giờ) chỉ giữ một thói quen — lưu cái mới thì tắt cái cũ.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.core import clock
from src.domain.enums import AccessEffect
from src.domain.models import AccessRule, Device, Habit, User
from src.memory import habit_editing, habits
from src.models.schemas import HabitDraftIn

_LIGHT = "den_chum_phong_khach"
_AC = "dieu_hoa_phong_khach"  # HIGH_POWER — thành viên chưa được cấp quyền
_KID_LIGHT = "den_ngu_con"  # đèn trong Phòng ngủ con


def _user(session, username: str) -> User:
    return session.scalar(select(User).where(User.username == username))


def _grant(session, username: str, device_slug: str, effect: AccessEffect = AccessEffect.ACCEPTED) -> None:
    """Chủ hộ cấp quyền một thiết bị cho thành viên (mô hình quản-lý-theo-thiết-bị)."""
    user = _user(session, username)
    device = session.scalar(select(Device).where(Device.slug == device_slug))
    # Upsert: seed cấp sẵn quyền phòng riêng nên rule có thể đã tồn tại.
    existing = session.scalar(
        select(AccessRule).where(AccessRule.user_id == user.id, AccessRule.device_id == device.id)
    )
    if existing is not None:
        existing.effect = effect
    else:
        session.add(
            AccessRule(household_id=user.household_id, user_id=user.id, device_id=device.id, effect=effect)
        )
    session.flush()


def _draft(device_slug=_LIGHT, action="turn_on", hour=22, minute=0, params=None) -> HabitDraftIn:
    return HabitDraftIn(device_slug=device_slug, action=action, hour=hour, minute=minute, params=params or {})


def test_options_hide_devices_user_cannot_control(seeded) -> None:
    """Thành viên chỉ thấy thiết bị ĐÃ ĐƯỢC CẤP QUYỀN; thiết bị chưa cấp bị ẩn."""
    _grant(seeded, "con_nho", _KID_LIGHT)  # chủ hộ cấp quyền đúng một thiết bị
    child = _user(seeded, "con_nho")
    opts = habit_editing.build_options(seeded, user=child)
    slugs = {d.slug for d in opts.devices}
    assert _KID_LIGHT in slugs, "thiết bị đã cấp quyền thì thành viên đặt được"
    assert _LIGHT not in slugs, "đèn phòng khách chưa cấp quyền → ẩn"
    assert _AC not in slugs, "điều hoà phòng khách chưa cấp quyền → ẩn"


def test_preview_single_valid_no_conflict(seeded) -> None:
    bo = _user(seeded, "bo")
    out = habit_editing.preview(seeded, user=bo, drafts=[_draft(action="set_brightness", params={"brightness": 60})])
    assert out.has_invalid is False and out.has_conflicts is False
    assert out.ready == 1 and len(out.slots) == 1
    cand = out.slots[0].candidates[0]
    assert cand.kind == "incoming" and cand.params == {"brightness": 60}


def test_invalid_when_member_targets_ungranted_device(seeded) -> None:
    """Thành viên đặt thói quen lên thiết bị chưa được cấp quyền → không hợp lệ."""
    child = _user(seeded, "con_nho")
    out = habit_editing.preview(seeded, user=child, drafts=[_draft(device_slug=_AC, action="turn_on")])
    assert out.has_invalid is True and out.slots == []
    assert "chưa được chủ hộ cấp quyền" in out.invalid[0].reason_vi


def test_invalid_params_out_of_capability(seeded) -> None:
    """Đặt độ sáng cho thiết bị không có khả năng chỉnh sáng → báo lỗi rõ ràng."""
    bo = _user(seeded, "bo")
    out = habit_editing.preview(seeded, user=bo, drafts=[_draft(device_slug="binh_nong_lanh", action="set_brightness", params={"brightness": 50})])
    assert out.has_invalid is True


def test_commit_creates_manual_habit(seeded) -> None:
    bo = _user(seeded, "bo")
    res = habit_editing.commit(seeded, user=bo, drafts=[_draft(action="set_temperature", device_slug=_AC, hour=7, minute=15, params={"temperature": 26})])
    assert res.disabled == 0 and len(res.saved) == 1
    saved = res.saved[0]
    assert saved.source == "manual" and saved.confidence == 1.0 and saved.minute == 15
    row = seeded.scalar(select(Habit).where(Habit.id == saved.id))
    assert row.user_id == bo.id and row.enabled is True
    assert "07:15" in row.description_vi


def test_commit_replaces_same_slot(seeded) -> None:
    """Thêm thói quen mới cùng (thiết bị, giờ) khác hành động → tắt cái cũ, giữ một."""
    bo = _user(seeded, "bo")
    habit_editing.commit(seeded, user=bo, drafts=[_draft(action="turn_on", hour=20)])
    res = habit_editing.commit(seeded, user=bo, drafts=[_draft(action="turn_off", hour=20)])
    assert res.disabled == 1
    enabled = seeded.scalars(
        select(Habit).where(Habit.user_id == bo.id, Habit.device_slug == _LIGHT, Habit.hour == 20, Habit.enabled.is_(True))
    ).all()
    assert len(enabled) == 1 and enabled[0].action == "turn_off"


def test_bulk_conflict_blocks_commit(seeded) -> None:
    """Hai thói quen mới tranh cùng khung mà chưa giải quyết → preview báo, commit 409."""
    bo = _user(seeded, "bo")
    drafts = [_draft(action="turn_on", hour=21), _draft(action="turn_off", hour=21)]
    out = habit_editing.preview(seeded, user=bo, drafts=drafts)
    assert out.has_conflicts is True
    with pytest.raises(habit_editing.HabitEditError) as exc:
        habit_editing.commit(seeded, user=bo, drafts=drafts)
    assert exc.value.status_code == 409


def test_manual_habit_survives_learning_decay(seeded) -> None:
    """Thói quen tay không có bằng chứng log vẫn KHÔNG bị decay về 0 (khác learned)."""
    bo = _user(seeded, "bo")
    try:
        habit_editing.commit(seeded, user=bo, drafts=[_draft(action="turn_on", hour=6)])
        # Chạy vòng học nhiều lần: không có ActionLog cho slot này.
        hh = bo.household_id
        habits.learn_habits(seeded, household_id=hh)
        clock.set_clock(frozen_ms=0, mode="live")
        habits.learn_habits(seeded, household_id=hh)
        row = seeded.scalar(select(Habit).where(Habit.user_id == bo.id, Habit.device_slug == _LIGHT, Habit.hour == 6))
        assert row.source == "manual" and row.confidence == 1.0 and row.enabled is True
    finally:
        clock.reset()


def test_duplicate_incoming_is_noop_not_conflict(seeded) -> None:
    """Hai thói quen mới GIỐNG HỆT nhau: khử trùng, không tính xung đột."""
    bo = _user(seeded, "bo")
    drafts = [_draft(action="turn_on", hour=9), _draft(action="turn_on", hour=9)]
    out = habit_editing.preview(seeded, user=bo, drafts=drafts)
    assert out.has_conflicts is False and out.ready == 1


def test_cross_user_conflict_warns_when_other_has_same_slot(seeded) -> None:
    """Bố tạo thói quen 22h đèn chùm → mẹ tạo thói quen khác cùng slot → cảnh báo cross-user."""
    bo = _user(seeded, "bo")
    me = _user(seeded, "me")
    habit_editing.commit(seeded, user=bo, drafts=[_draft(action="turn_on", hour=22)])
    out = habit_editing.preview(seeded, user=me, drafts=[_draft(action="turn_off", hour=22)])
    assert len(out.cross_user_warnings) >= 1
    w = out.cross_user_warnings[0]
    assert "Bố" in w.other_user_name or "bo" in w.other_user_name.lower()
    assert w.other_user_role == "owner"


def test_cross_user_no_warning_when_same_action(seeded) -> None:
    """Bố và mẹ cùng đặt turn_on cùng slot → trùng khít, không cảnh báo."""
    bo = _user(seeded, "bo")
    me = _user(seeded, "me")
    habit_editing.commit(seeded, user=bo, drafts=[_draft(action="turn_on", hour=23)])
    out = habit_editing.preview(seeded, user=me, drafts=[_draft(action="turn_on", hour=23)])
    assert len(out.cross_user_warnings) == 0


def test_cross_user_owner_priority_message(seeded) -> None:
    """Khi member tạo thói quen xung đột với owner → thông báo owner ưu tiên."""
    bo = _user(seeded, "bo")
    con = _user(seeded, "con_lon")
    # Cấp quyền đèn cho con để cả hai đều điều khiển được, tạo xung đột chéo người.
    _grant(seeded, "con_lon", _KID_LIGHT)
    habit_editing.commit(seeded, user=bo, drafts=[_draft(device_slug=_KID_LIGHT, action="turn_on", hour=19)])
    out = habit_editing.preview(seeded, user=con, drafts=[_draft(device_slug=_KID_LIGHT, action="turn_off", hour=19)])
    assert len(out.cross_user_warnings) >= 1
    assert "chủ hộ" in out.cross_user_warnings[0].message_vi.lower()
