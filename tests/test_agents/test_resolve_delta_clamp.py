"""`resolve_delta` chặn biên: giảm khi đang ở đáy KHÔNG tạo giá trị âm (bug độ sáng -10).

Nhánh execution tất định cho lệnh chỉnh ("giảm bớt độ sáng") không đi qua ground_candidate_plan
nên phải tự clamp về khoảng hợp lệ của capability.
"""

from __future__ import annotations

from src.agent.harness.state_resolution import resolve_delta


def test_giam_do_sang_o_day_khong_am() -> None:
    assert resolve_delta("set_brightness", {"delta": -10}, {"brightness": 0})["brightness"] == 0


def test_giam_do_sang_binh_thuong_van_dung() -> None:
    assert resolve_delta("set_brightness", {"delta": -10}, {"brightness": 70})["brightness"] == 60


def test_tang_khong_vuot_tran() -> None:
    assert resolve_delta("set_brightness", {"delta": 10}, {"brightness": 95})["brightness"] == 100


def test_nhiet_do_chan_day_16() -> None:
    assert resolve_delta("set_temperature", {"delta": -2}, {"temperature": 17})["temperature"] == 16


def test_chua_biet_gia_tri_thi_bo_qua_delta() -> None:
    # Không có giá trị hiện tại → bỏ phần tương đối, không bịa.
    assert "brightness" not in resolve_delta("set_brightness", {"delta": -10}, {})
