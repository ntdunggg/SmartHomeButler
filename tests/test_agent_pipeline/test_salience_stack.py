"""Kiểm thử CÔ LẬP cho Salience Stack (`src/context/salience.py`) — không qua pipeline.

Ràng buộc hành vi cốt lõi TRƯỚC khi tích hợp: (1) cấu trúc Entry đầy đủ; (2) chính sách decay theo
tuổi + eviction theo độ sâu; (3) lọc theo capability (điều chỉnh nhiệt độ chỉ lấy điều hoà, bỏ đèn).
Dùng slug THẬT trong registry để capability suy từ spec là thật (§5 registry-first).
"""

from __future__ import annotations

from src.context.salience import SalienceEntry, SalienceStack, capabilities_of

# Slug thật: đèn (on_off+brightness), điều hoà (on_off+temperature+fan_speed), loa (on_off+volume).
LIGHT = "den_ngu_bo_me"
AC = "dieu_hoa_phong_bo_me"
SPEAKER = "loa_phong_khach"


# --- (1) Cấu trúc Entry -------------------------------------------------------
def test_push_luu_day_du_thong_tin_entry():
    stack = SalienceStack()
    entry = stack.push(AC, turn=3, room="Phòng ngủ bố mẹ", kind="command")
    assert entry.device_id == AC
    assert entry.room == "Phòng ngủ bố mẹ"
    assert entry.turn == 3
    assert entry.kind == "command"
    # capability suy từ registry: điều hoà có temperature.
    assert "temperature" in entry.capabilities
    assert entry.capabilities == capabilities_of(AC)


def test_entry_roundtrip_serialize():
    stack = SalienceStack()
    stack.push(LIGHT, turn=1, room="Phòng ngủ bố mẹ", kind="query", action="turn_on")
    data = stack.to_list()
    restored = SalienceStack.from_list(data)
    assert restored.top() == SalienceEntry.from_dict(data[-1])
    assert restored.top().device_id == LIGHT
    assert restored.top().kind == "query"
    assert restored.top().action == "turn_on"


def test_top_va_recency_lam_moi_khi_day_trung():
    stack = SalienceStack()
    stack.push(LIGHT, turn=1)
    stack.push(AC, turn=2)
    assert stack.top().device_id == AC
    # Nhắc lại đèn ở lượt 3 → đèn lên đỉnh, KHÔNG nhân đôi.
    stack.push(LIGHT, turn=3)
    assert stack.top().device_id == LIGHT
    assert [e.device_id for e in stack.entries()] == [LIGHT, AC]  # mới → cũ, không trùng


# --- (2) Decay & Eviction -----------------------------------------------------
def test_eviction_theo_do_sau_giu_moi_nhat():
    stack = SalienceStack(max_depth=3, max_age=None)
    for i, dev in enumerate([LIGHT, AC, SPEAKER, "tv_phong_khach", "rem_phong_khach"], start=1):
        stack.push(dev, turn=i)
    assert len(stack) == 3
    # Giữ 3 phần tử MỚI NHẤT (turn 3,4,5), bỏ hai cái cũ.
    assert [e.device_id for e in stack.entries()] == ["rem_phong_khach", "tv_phong_khach", SPEAKER]


def test_decay_bo_phan_tu_qua_cu():
    stack = SalienceStack(max_depth=5, max_age=3)
    stack.push(LIGHT, turn=1)
    stack.push(AC, turn=2)
    # Lượt 6: đèn (turn 1) đã quá 3 lượt → decay; điều hoà (turn 2) cách 4 lượt cũng decay.
    stack.push(SPEAKER, turn=6)
    ids = [e.device_id for e in stack.entries()]
    assert ids == [SPEAKER]


def test_decay_giu_phan_tu_trong_han_tuoi():
    stack = SalienceStack(max_depth=5, max_age=3)
    stack.push(AC, turn=2)
    stack.push(SPEAKER, turn=4)  # AC cách 2 lượt (<=3) → còn giữ
    ids = [e.device_id for e in stack.entries()]
    assert ids == [SPEAKER, AC]


def test_deferred_reference_survives_age_and_regular_depth_limits():
    stack = SalienceStack(max_depth=2, max_age=3)
    stack.push("may_rua_bat", turn=1, kind="deferred")
    for turn, device_id in enumerate(
        ("den_bep", "den_ban_an", "loa_bep", "rem_bep", "dieu_hoa_phong_khach"),
        start=2,
    ):
        stack.push(device_id, turn=turn, kind="command")

    deferred = stack.most_recent(predicate=lambda entry: entry.kind == "deferred")
    assert deferred is not None and deferred.device_id == "may_rua_bat"


# --- (3) Lọc theo capability --------------------------------------------------
def test_most_recent_loc_capability_nhiet_do_chi_lay_dieu_hoa():
    stack = SalienceStack()
    stack.push(AC, turn=1)       # có temperature
    stack.push(LIGHT, turn=2)    # đèn: brightness, KHÔNG temperature (mới hơn)
    # Điều chỉnh nhiệt độ → bỏ qua đèn ở đỉnh, lấy điều hoà.
    hit = stack.most_recent(capability="temperature")
    assert hit is not None and hit.device_id == AC


def test_most_recent_khong_capability_lay_dinh():
    stack = SalienceStack()
    stack.push(AC, turn=1)
    stack.push(LIGHT, turn=2)
    assert stack.most_recent().device_id == LIGHT  # đỉnh khi không lọc


def test_most_recent_khong_co_thiet_bi_tuong_thich_tra_none():
    stack = SalienceStack()
    stack.push(LIGHT, turn=1)  # chỉ đèn, không thiết bị nào có volume
    assert stack.most_recent(capability="volume") is None


def test_most_recent_uu_tien_thuc_the_moi_nhat_tuong_thich():
    stack = SalienceStack()
    stack.push("dieu_hoa_phong_con", turn=1)   # temperature
    stack.push(AC, turn=2)                      # temperature (mới hơn)
    stack.push(LIGHT, turn=3)                   # brightness
    assert stack.most_recent(capability="temperature").device_id == AC


def test_remove_dismissal():
    stack = SalienceStack()
    stack.push(SPEAKER, turn=1)
    stack.push("camera_cua_chinh", turn=2)
    stack.remove(SPEAKER)  # "thôi chuyện cái loa để sau"
    assert [e.device_id for e in stack.entries()] == ["camera_cua_chinh"]


def test_ordinal_doc_nhom_gan_nhat_giu_thu_tu_neu():
    stack = SalienceStack()
    stack.push_many([AC, "tv_phong_bo_me"], turn=1, kind="group", action="turn_on")
    assert stack.ordinal(0).device_id == AC
    assert stack.ordinal(1).device_id == "tv_phong_bo_me"
    assert stack.ordinal(-1).device_id == "tv_phong_bo_me"
    assert stack.ordinal(2) is None
