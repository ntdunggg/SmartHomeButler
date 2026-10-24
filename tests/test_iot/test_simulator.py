"""Máy trạng thái thiết bị ảo."""

from __future__ import annotations

import pytest

from src.core.errors import DeviceError
from src.iot.registry import DEVICE_SPECS, spec_for
from src.iot.simulator import apply_command


def test_bat_tat_den():
    spec = spec_for("den_chum_phong_khach")
    assert apply_command(spec, {"power": "off"}, "turn_on").state["power"] == "on"
    assert apply_command(spec, {"power": "on"}, "turn_off").state["power"] == "off"


def test_dat_do_sang_ngam_hieu_la_bat_den():
    spec = spec_for("den_chum_phong_khach")
    result = apply_command(spec, {"power": "off", "brightness": 10}, "set_brightness", {"brightness": 80})
    assert result.state["brightness"] == 80
    assert result.state["power"] == "on"


def test_do_sang_bang_0_thi_tat_han():
    spec = spec_for("den_chum_phong_khach")
    result = apply_command(spec, {"power": "on"}, "set_brightness", {"brightness": 0})
    assert result.state["power"] == "off"


def test_nhiet_do_hop_le_duoc_ap_dung():
    spec = spec_for("dieu_hoa_phong_khach")
    result = apply_command(spec, {}, "set_temperature", {"temperature": 24})
    assert result.state["temperature"] == 24


@pytest.mark.parametrize("requested", [10, 31, 99])
def test_nhiet_do_tuyet_doi_ngoai_dai_bi_tu_choi(requested):
    spec = spec_for("dieu_hoa_phong_khach")
    with pytest.raises(DeviceError, match="ngoài dải"):
        apply_command(spec, {}, "set_temperature", {"temperature": requested})


def test_mo_dong_rem():
    spec = spec_for("rem_phong_khach")
    assert apply_command(spec, {"position": 0}, "open").state["position"] == 100
    assert apply_command(spec, {"position": 100}, "close").state["position"] == 0


def test_khoa_mo_khoa_cua():
    spec = spec_for("khoa_cua_chinh")
    unlocked = apply_command(spec, {"locked": True}, "unlock")
    assert unlocked.state["locked"] is False
    assert "mở khóa" in unlocked.detail
    assert apply_command(spec, {"locked": False}, "lock").state["locked"] is True


def test_thiet_bi_khong_ho_tro_thi_bao_loi_ro_rang():
    spec = spec_for("tv_phong_khach")
    with pytest.raises(DeviceError, match="không hỗ trợ"):
        apply_command(spec, {}, "set_temperature", {"temperature": 20})


def test_hanh_dong_khong_ton_tai():
    with pytest.raises(DeviceError, match="Không hỗ trợ"):
        apply_command(spec_for("den_chum_phong_khach"), {}, "nhay_mua")


def test_thieu_tham_so_bat_buoc():
    with pytest.raises(DeviceError, match="thiếu tham số"):
        apply_command(spec_for("den_chum_phong_khach"), {}, "set_brightness", {})


def test_tham_so_khong_phai_so():
    with pytest.raises(DeviceError, match="không hợp lệ"):
        apply_command(spec_for("den_chum_phong_khach"), {}, "set_brightness", {"brightness": "rất sáng"})


def test_simulator_uses_registry_fan_speed_scale():
    with pytest.raises(DeviceError, match="ngoài dải"):
        apply_command(
            spec_for("dieu_hoa_phong_khach"),
            {"power": "on", "fan_speed": 2},
            "set_fan_speed",
            {"fan_speed": 4},
        )


def test_khong_lam_thay_doi_state_dau_vao():
    """apply_command phải là hàm thuần — state cũ không được đụng tới."""
    original = {"power": "off", "brightness": 50}
    apply_command(spec_for("den_chum_phong_khach"), original, "turn_on")
    assert original == {"power": "off", "brightness": 50}


def test_catalog_du_thiet_bi_theo_de_bai():
    assert len(DEVICE_SPECS) >= 8, "đề bài yêu cầu tối thiểu 8 thiết bị"
    slugs = [s.slug for s in DEVICE_SPECS]
    assert len(slugs) == len(set(slugs)), "slug phải là duy nhất"
    # Phải có đủ cả ba mức rủi ro để demo được ma trận phân quyền
    risks = {str(s.risk_level) for s in DEVICE_SPECS}
    assert risks == {"normal", "high_power", "security"}
