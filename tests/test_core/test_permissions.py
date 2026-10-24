"""Phân quyền quản-lý-theo-thiết-bị + khoá trẻ em (không còn phân theo tuổi).

Lớp bảo vệ quan trọng nhất: chủ hộ toàn quyền; thành viên chỉ theo quyền per-device
chủ hộ cấp; khoá trẻ em chặn thành viên với thiết bị nhạy cảm dù đã cấp.
"""

from __future__ import annotations

from sqlalchemy import select

from src.core.permissions import can_approve, evaluate, grant_private_room_access, resolve_access
from src.domain.enums import AccessEffect, RiskLevel, Role
from src.domain.models import AccessRule, Device, Household, Room, User


# ---- evaluate: chỉ theo role, không tuổi ----
def test_chu_ho_toan_quyen_moi_muc_rui_ro():
    for risk in RiskLevel:
        d = evaluate(role=Role.OWNER, risk=risk)
        assert d.allowed and not d.requires_approval


def test_chap_nhan_chuoi_thay_cho_enum():
    """SQLAlchemy/LangGraph trả về chuỗi — không được vì thế mà phân quyền sai."""
    assert evaluate(role="owner", risk="security") == evaluate(role=Role.OWNER, risk=RiskLevel.SECURITY)


def test_ai_du_tu_cach_duyet():
    assert can_approve(approver_role=Role.OWNER, required_role=Role.OWNER)
    assert not can_approve(approver_role=Role.MEMBER, required_role=Role.OWNER)
    assert can_approve(approver_role=Role.MEMBER, required_role=Role.MEMBER)


# ---- resolve_access: mô hình per-device + childlock ----
def _member(session) -> User:
    return session.scalar(select(User).where(User.username == "con_nho"))


def _device(session, slug: str) -> Device:
    return session.scalar(select(Device).where(Device.slug == slug))


def _grant(session, user: User, device: Device, effect: AccessEffect) -> None:
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


def _set_child_lock(session, household_id: int, on: bool) -> None:
    hh = session.get(Household, household_id)
    hh.child_lock_enabled = on
    session.flush()


def test_chu_ho_qua_resolve_access_luon_duoc(seeded):
    owner = seeded.scalar(select(User).where(User.username == "bo"))
    d = resolve_access(seeded, user=owner, device=_device(seeded, "khoa_cua_chinh"))
    assert d.allowed and not d.requires_approval


def test_thanh_vien_khong_co_luat_thi_trang_quyen(seeded):
    # Thiết bị NGOÀI phòng riêng của con → không được seed cấp sẵn → trắng quyền.
    d = resolve_access(seeded, user=_member(seeded), device=_device(seeded, "den_chum_phong_khach"))
    assert not d.allowed


def test_accepted_dung_ngay(seeded):
    user, dev = _member(seeded), _device(seeded, "den_ngu_con")
    _grant(seeded, user, dev, AccessEffect.ACCEPTED)
    d = resolve_access(seeded, user=user, device=dev)
    assert d.allowed and not d.requires_approval and not d.notify_owner


def test_alert_dung_ngay_kem_thong_bao(seeded):
    user, dev = _member(seeded), _device(seeded, "den_ngu_con")
    _grant(seeded, user, dev, AccessEffect.ALERT)
    d = resolve_access(seeded, user=user, device=dev)
    assert d.allowed and not d.requires_approval and d.notify_owner


def test_request_phai_duyet(seeded):
    user, dev = _member(seeded), _device(seeded, "den_ngu_con")
    _grant(seeded, user, dev, AccessEffect.REQUEST)
    d = resolve_access(seeded, user=user, device=dev)
    assert d.allowed and d.requires_approval and d.approver_role is Role.OWNER


def test_an_ninh_accepted_khong_ep_hitl_khi_khong_khoa(seeded):
    user, lock = _member(seeded), _device(seeded, "khoa_cua_chinh")
    _grant(seeded, user, lock, AccessEffect.ACCEPTED)
    d = resolve_access(seeded, user=user, device=lock)
    assert d.allowed and not d.requires_approval


# ---- childlock ----
def test_childlock_chan_cong_suat_lon_du_da_cap(seeded):
    user = _member(seeded)
    ac = _device(seeded, "dieu_hoa_phong_con")  # high_power, phòng con
    _grant(seeded, user, ac, AccessEffect.ACCEPTED)
    _set_child_lock(seeded, user.household_id, True)
    d = resolve_access(seeded, user=user, device=ac)
    assert not d.allowed and d.child_locked


def test_childlock_chan_an_ninh_du_da_cap(seeded):
    user = _member(seeded)
    lock = _device(seeded, "khoa_cua_chinh")
    _grant(seeded, user, lock, AccessEffect.ACCEPTED)
    _set_child_lock(seeded, user.household_id, True)
    d = resolve_access(seeded, user=user, device=lock)
    assert not d.allowed and d.child_locked


def test_childlock_khong_dung_thiet_bi_thuong(seeded):
    user = _member(seeded)
    light = _device(seeded, "den_ngu_con")  # normal
    _grant(seeded, user, light, AccessEffect.ACCEPTED)
    _set_child_lock(seeded, user.household_id, True)
    d = resolve_access(seeded, user=user, device=light)
    assert d.allowed  # khoá trẻ em chỉ chặn công suất lớn/an ninh, không chặn đèn


def test_childlock_khong_anh_huong_chu_ho(seeded):
    owner = seeded.scalar(select(User).where(User.username == "bo"))
    _set_child_lock(seeded, owner.household_id, True)
    d = resolve_access(seeded, user=owner, device=_device(seeded, "khoa_cua_chinh"))
    assert d.allowed  # chủ hộ không bị khoá trẻ em


# ---- grant_private_room_access: cấp sẵn quyền phòng riêng cho thành viên ----
def _room_id(session, name: str) -> int:
    return session.scalar(select(Room).where(Room.name == name)).id


def _rules_of(session, user: User) -> dict[str, AccessEffect]:
    """Map slug thiết bị → effect trong luật riêng của user."""
    rows = session.scalars(select(AccessRule).where(AccessRule.user_id == user.id)).all()
    by_id = {d.id: d.slug for d in session.scalars(select(Device))}
    return {by_id[r.device_id]: AccessEffect(r.effect) for r in rows}


def test_cap_san_moi_thiet_bi_phong_rieng_tru_an_ninh(seeded):
    """Thành viên phòng con → accepted mọi thiết bị phòng đó (kể cả điều hoà high_power)."""
    user = _member(seeded)  # con_nho, phòng riêng = Phòng ngủ con
    # Xoá sạch luật để test helper trên nền trắng, độc lập với seed.
    seeded.execute(AccessRule.__table__.delete().where(AccessRule.user_id == user.id))
    seeded.flush()

    added = grant_private_room_access(seeded, user=user)
    seeded.flush()

    rules = _rules_of(seeded, user)
    assert added == len(rules) > 0
    assert rules["dieu_hoa_phong_con"] is AccessEffect.ACCEPTED  # high_power vẫn được cấp
    assert rules["den_ngu_con"] is AccessEffect.ACCEPTED
    assert all(effect is AccessEffect.ACCEPTED for effect in rules.values())


def test_khong_cap_thiet_bi_an_ninh_trong_phong_rieng(seeded):
    """Phòng bố mẹ có khoá cửa (SECURITY) → không tự cấp, giữ ma sát an ninh."""
    user = _member(seeded)
    user.private_room_id = _room_id(seeded, "Phòng ngủ bố mẹ")
    seeded.execute(AccessRule.__table__.delete().where(AccessRule.user_id == user.id))
    seeded.flush()

    grant_private_room_access(seeded, user=user)
    seeded.flush()

    rules = _rules_of(seeded, user)
    assert "khoa_cua_phong_bo_me" not in rules  # thiết bị an ninh bị loại
    assert rules  # nhưng các thiết bị thường vẫn được cấp


def test_idempotent_va_khong_de_luat_da_co(seeded):
    """Gọi lại không nhân bản; luật chủ hộ đã đặt (REQUEST) không bị đè về ACCEPTED."""
    user = _member(seeded)
    seeded.execute(AccessRule.__table__.delete().where(AccessRule.user_id == user.id))
    seeded.flush()
    # Chủ hộ đã siết đèn ngủ về REQUEST trước đó
    _grant(seeded, user, _device(seeded, "den_ngu_con"), AccessEffect.REQUEST)

    first = grant_private_room_access(seeded, user=user)
    seeded.flush()
    second = grant_private_room_access(seeded, user=user)  # gọi lại
    seeded.flush()

    rules = _rules_of(seeded, user)
    assert second == 0  # lần hai không thêm gì
    assert rules["den_ngu_con"] is AccessEffect.REQUEST  # không bị đè
    # Không có cặp (user, device) trùng lặp
    assert len(rules) == len(set(rules))
    assert first > 0


def test_khong_co_phong_rieng_thi_khong_cap(seeded):
    user = _member(seeded)
    user.private_room_id = None
    seeded.execute(AccessRule.__table__.delete().where(AccessRule.user_id == user.id))
    seeded.flush()
    assert grant_private_room_access(seeded, user=user) == 0
