"""Phát hiện lệnh mâu thuẫn — bốn nhóm đề bài yêu cầu."""

from __future__ import annotations

from src.agent.planning.conflict import ContextSnapshot, detect, has_blocking
from src.domain.enums import ConflictType


def step(slug: str, action: str, **params) -> dict:
    return {"device_slug": slug, "action": action, "params": params}


def types_of(conflicts) -> set[str]:
    return {c["type"] for c in conflicts}


# --------------------------------------------------------------------------
# WASTE — bình nóng lạnh khi cả nhà vắng nhiều giờ
# --------------------------------------------------------------------------
def test_canh_bao_bat_binh_nong_lanh_khi_ca_nha_vang():
    ctx = ContextSnapshot(someone_home=False, away_minutes=180, away_threshold_minutes=120)
    conflicts = detect([step("binh_nong_lanh", "turn_on")], ctx)

    assert ConflictType.WASTE in types_of(conflicts)
    assert "3 tiếng" in conflicts[0]["message_vi"]


def test_khong_canh_bao_lang_phi_khi_co_nguoi_o_nha():
    ctx = ContextSnapshot(someone_home=True, away_minutes=0)
    conflicts = detect([step("binh_nong_lanh", "turn_on")], ctx)
    assert ConflictType.WASTE not in types_of(conflicts)


def test_vang_chua_du_lau_thi_chua_canh_bao():
    ctx = ContextSnapshot(someone_home=False, away_minutes=30, away_threshold_minutes=120)
    conflicts = detect([step("binh_nong_lanh", "turn_on")], ctx)
    assert ConflictType.WASTE not in types_of(conflicts)


# --------------------------------------------------------------------------
# CONTRADICTION — kế hoạch tự đá nhau
# --------------------------------------------------------------------------
def test_canh_bao_bat_dieu_hoa_khi_cua_so_dang_mo():
    ctx = ContextSnapshot(device_states={"cua_so_phong_khach": {"position": 100}})
    conflicts = detect([step("dieu_hoa_phong_khach", "turn_on")], ctx)
    assert ConflictType.CONTRADICTION in types_of(conflicts)


def test_ke_hoach_co_dong_cua_so_thi_khong_con_mau_thuan():
    ctx = ContextSnapshot(device_states={"cua_so_phong_khach": {"position": 100}})
    conflicts = detect([step("cua_so_phong_khach", "close"), step("dieu_hoa_phong_khach", "turn_on")], ctx)
    assert ConflictType.CONTRADICTION not in types_of(conflicts)


def test_cua_so_phong_khac_khong_xung_dot_voi_dieu_hoa():
    ctx = ContextSnapshot(device_states={"cua_so_phong_khach": {"position": 100}})

    conflicts = detect([step("dieu_hoa_phong_bo_me", "turn_on")], ctx)

    assert ConflictType.CONTRADICTION not in types_of(conflicts)


def test_set_position_zero_duoc_hieu_la_dong_cua_so():
    ctx = ContextSnapshot(device_states={"cua_so_phong_bo_me": {"position": 100}})
    conflicts = detect(
        [
            step("cua_so_phong_bo_me", "set_position", position=0),
            step("dieu_hoa_phong_bo_me", "turn_on"),
        ],
        ctx,
    )

    assert ConflictType.CONTRADICTION not in types_of(conflicts)


def test_set_position_open_cung_phong_xung_dot_voi_dieu_hoa():
    conflicts = detect(
        [
            step("cua_so_phong_bo_me", "set_position", position=80),
            step("dieu_hoa_phong_bo_me", "turn_on"),
        ],
        ContextSnapshot(),
    )

    assert ConflictType.CONTRADICTION in types_of(conflicts)


def test_vua_bat_vua_tat_cung_thiet_bi_bi_chan():
    conflicts = detect(
        [step("den_chum_phong_khach", "turn_on"), step("den_chum_phong_khach", "turn_off")],
        ContextSnapshot(),
    )
    assert ConflictType.CONTRADICTION in types_of(conflicts)
    assert has_blocking(conflicts), "mâu thuẫn không thể thực hiện được thì phải chặn"


# --------------------------------------------------------------------------
# SAFETY — rủi ro an ninh
# --------------------------------------------------------------------------
def test_mo_khoa_cua_khi_nha_khong_co_ai_bi_chan():
    conflicts = detect([step("khoa_cua_chinh", "unlock")], ContextSnapshot(someone_home=False))
    assert ConflictType.SAFETY in types_of(conflicts)
    assert has_blocking(conflicts)


def test_tat_camera_khi_vang_nha_bi_chan():
    conflicts = detect([step("camera_cua_chinh", "turn_off")], ContextSnapshot(someone_home=False))
    assert ConflictType.SAFETY in types_of(conflicts)
    assert has_blocking(conflicts)


def test_mo_khoa_khi_co_nguoi_o_nha_la_binh_thuong():
    conflicts = detect([step("khoa_cua_chinh", "unlock")], ContextSnapshot(someone_home=True))
    assert ConflictType.SAFETY not in types_of(conflicts)


def test_ke_hoach_rong_khong_sinh_canh_bao():
    assert detect([], ContextSnapshot()) == []


# --------------------------------------------------------------------------
# HABIT_SCHEDULE — lệnh đụng thói quen cùng thiết bị cùng giờ (Mục 7)
# --------------------------------------------------------------------------
def test_canh_bao_khi_lenh_di_nguoc_thoi_quen_cung_gio():
    from src.agent.planning.conflict import HabitInfo

    ctx = ContextSnapshot(
        actor_name="Bố",
        current_hour=22,
        active_habits=[HabitInfo(device_slug="den_ngu_con", device_name="Đèn ngủ", hour=22, action="turn_on", user_name="Con")],
    )
    conflicts = detect([step("den_ngu_con", "turn_off")], ctx)
    assert ConflictType.HABIT_SCHEDULE in types_of(conflicts)
    c = next(c for c in conflicts if c["type"] == str(ConflictType.HABIT_SCHEDULE))
    assert not c["blocking"] and c["against"] == "Con"


def test_khong_canh_bao_khi_cung_hanh_dong_hoac_khac_gio():
    from src.agent.planning.conflict import HabitInfo

    hb = HabitInfo(device_slug="den_ngu_con", device_name="Đèn ngủ", hour=22, action="turn_on", user_name="Con")
    same_action = ContextSnapshot(current_hour=22, active_habits=[hb])
    assert ConflictType.HABIT_SCHEDULE not in types_of(detect([step("den_ngu_con", "turn_on")], same_action))
    other_hour = ContextSnapshot(current_hour=7, active_habits=[hb])
    assert ConflictType.HABIT_SCHEDULE not in types_of(detect([step("den_ngu_con", "turn_off")], other_hour))


def test_resolve_priority_theo_vai_tro():
    from src.agent.planning.conflict import resolve_priority

    assert resolve_priority("owner", "member") == "origin_wins"
    assert resolve_priority("member", "owner") == "against_wins"
    assert resolve_priority("owner", "owner") == "warn"


# --------------------------------------------------------------------------
# resolve() — auto-resolve + override (2 tính năng mới, Dũng yêu cầu)
# --------------------------------------------------------------------------
def test_resolve_tu_dong_dong_cua_so_truoc_khi_bat_dieu_hoa():
    """Auto-resolve: bật điều hoà khi cửa sổ cùng phòng mở → sinh bước đóng cửa, hết mâu thuẫn."""
    from src.agent.planning.conflict import resolve

    ctx = ContextSnapshot(device_states={"cua_so_phong_khach": {"position": 100}})
    res = resolve([step("dieu_hoa_phong_khach", "turn_on")], ctx, actor_role="owner")

    assert any(r["device_slug"] == "cua_so_phong_khach" and r["action"] == "close" for r in res.remediation)
    assert ConflictType.CONTRADICTION not in types_of(res.conflicts)
    assert res.notes


def test_resolve_van_chan_xung_dot_an_ninh_de_dua_len_hitl():
    """Override KHÔNG làm yếu safety: xung đột an ninh vẫn blocking (để đẩy lên chủ hộ duyệt)."""
    from src.agent.planning.conflict import resolve

    res = resolve([step("khoa_cua_chinh", "unlock")], ContextSnapshot(someone_home=False), actor_role="owner")
    assert has_blocking(res.conflicts)
    assert not res.remediation  # không auto-fix an ninh


# --------------------------------------------------------------------------
# Override-rồi-báo-sau: gom NGƯỜI BỊ ĐÈ để thông báo (Hybrid)
# --------------------------------------------------------------------------
def test_resolve_gom_nguoi_bi_de_thoi_quen():
    """Lệnh đè thói quen người khác → gom người có thói quen để báo."""
    from src.agent.planning.conflict import HabitInfo, resolve

    ctx = ContextSnapshot(
        actor_name="Bố",
        current_hour=22,
        active_habits=[HabitInfo(device_slug="den_ngu_con", device_name="Đèn ngủ", hour=22, action="turn_on", user_name="Con")],
    )
    res = resolve([step("den_ngu_con", "turn_off")], ctx, actor_role="owner")
    assert "Con" in [a.name for a in res.affected]
