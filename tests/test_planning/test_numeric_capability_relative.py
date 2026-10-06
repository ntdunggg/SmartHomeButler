"""Lệnh TĂNG/GIẢM tương đối phải chỉnh ĐÚNG capability, không rơi nhầm sang cái khác.

Bug gốc: `_numeric_capability` chỉ chọn TEMPERATURE khi `params` đã có sẵn con số. Lệnh
tương đối để params rỗng theo đúng thiết kế (không bịa số), nên điều hoà luôn rơi xuống
vòng dự phòng — mà vòng đó thiếu TEMPERATURE → "tăng nhiệt độ điều hoà" đi chỉnh TỐC ĐỘ QUẠT.
Sai âm thầm: plan vẫn hợp lệ, vẫn chạy, chỉ là chỉnh nhầm núm.
"""

from __future__ import annotations

from src.domain.enums import Capability
from src.nlu.schemas import SemanticGoal
from src.planning.plan_validator import synthesize_direct_plan


def _cap(utterance: str, slug: str, hint: str, params: dict | None = None) -> Capability:
    goal = SemanticGoal(
        intent="t",
        raw_utterance=utterance,
        confidence=0.99,
        action_hint=hint,
        target_device_ids=[slug],
        parameters=params or {},
    )
    actions = synthesize_direct_plan(goal).actions
    assert actions, f"không sinh được action cho {utterance!r}"
    return actions[0].capability


def test_tang_dieu_hoa_chinh_nhiet_do_khong_phai_gio() -> None:
    # Điều hoà có cả temperature lẫn fan_speed; không nêu rõ thì nhiệt độ là núm chính.
    assert _cap("tăng điều hoà phòng khách lên", "dieu_hoa_phong_khach", "increase") is Capability.TEMPERATURE


def test_giam_dieu_hoa_chinh_nhiet_do() -> None:
    assert _cap("giảm điều hoà phòng khách đi", "dieu_hoa_phong_khach", "decrease") is Capability.TEMPERATURE


def test_cau_neu_ro_nhiet_do() -> None:
    assert _cap("tăng nhiệt độ điều hoà phòng khách", "dieu_hoa_phong_khach", "increase") is Capability.TEMPERATURE


def test_cau_neu_ro_gio_thi_van_la_fan_speed() -> None:
    # Người dùng nói rõ "gió" → phải tôn trọng, không được ép về nhiệt độ.
    assert _cap("tăng gió điều hoà phòng khách", "dieu_hoa_phong_khach", "increase") is Capability.FAN_SPEED


def test_den_khong_bi_anh_huong() -> None:
    # Đèn không có TEMPERATURE nên thứ tự ưu tiên mới không đổi hành vi của nó.
    assert _cap("tăng đèn bàn học lên", "den_ban_hoc", "increase") is Capability.BRIGHTNESS


def test_set_gia_tri_tuyet_doi_van_dung() -> None:
    cap = _cap("đặt điều hoà phòng khách 25 độ", "dieu_hoa_phong_khach", "set", {"temperature": 25})
    assert cap is Capability.TEMPERATURE
