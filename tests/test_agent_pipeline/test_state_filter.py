"""Kiểm thử CÔ LẬP cho bộ lọc trạng thái (`src/context/state_filter.py`).

Ràng buộc hành vi: (1) TẮT/GIẢM → giữ đang bật; (2) BẬT/MỞ → giữ đang tắt; (3) rút về 1 khi lọc
còn đúng một; (4) BIÊN tất cả cùng trạng thái → giữ nguyên (để clarify). Slug thật trong registry.
"""

from __future__ import annotations

import pytest

from src.context.state_filter import is_active, reduce_by_state

F1, F2, F3 = "may_loc_phong_khach", "may_loc_phong_bo_me", "may_loc_phong_con"


def _states(**power_by_slug):
    return {slug: {"power": p} for slug, p in power_by_slug.items()}


# --- is_active ---------------------------------------------------------------
@pytest.mark.parametrize(
    "state, expected",
    [
        ({"power": "on"}, True),
        ({"power": "off"}, False),
        ({}, False),
        (None, False),
        ({"position": 40}, True),   # rèm/cửa đang mở
        ({"position": 0}, False),
        ({"locked": False}, True),  # khoá đang mở
        ({"locked": True}, False),
    ],
)
def test_is_active(state, expected):
    assert is_active(state) is expected


# --- reduce_by_state: TẮT/GIẢM → giữ đang bật --------------------------------
def test_tat_giu_dang_bat_rut_ve_mot():
    # 3 máy lọc, chỉ F2 đang bật → "tắt" rút về F2.
    out = reduce_by_state([F1, F2, F3], action_hint="turn_off", live_states=_states(**{F1: "off", F2: "on", F3: "off"}))
    assert out == [F2]


def test_giam_giu_dang_bat():
    # LC-004 kiểu: giảm → chỉ thiết bị đang bật.
    out = reduce_by_state([F1, F2], action_hint="decrease", live_states=_states(**{F1: "off", F2: "on"}))
    assert out == [F2]


# --- reduce_by_state: BẬT/MỞ → giữ đang tắt ----------------------------------
def test_bat_giu_dang_tat_rut_ve_mot():
    out = reduce_by_state([F1, F2, F3], action_hint="turn_on", live_states=_states(**{F1: "on", F2: "off", F3: "on"}))
    assert out == [F2]


# --- BIÊN: tất cả cùng trạng thái → giữ nguyên -------------------------------
def test_tat_ca_dang_bat_giu_nguyen():
    ids = [F1, F2, F3]
    out = reduce_by_state(ids, action_hint="turn_off", live_states=_states(**{F1: "on", F2: "on", F3: "on"}))
    assert out == ids  # không tự chọn → để clarify


def test_tat_ca_dang_tat_giu_nguyen():
    ids = [F1, F2]
    out = reduce_by_state(ids, action_hint="turn_on", live_states=_states(**{F1: "off", F2: "off"}))
    assert out == ids


def test_loc_con_hai_giu_nguyen():
    # tắt, 2/3 đang bật → còn >1 → giữ nguyên danh sách gốc.
    ids = [F1, F2, F3]
    out = reduce_by_state(ids, action_hint="turn_off", live_states=_states(**{F1: "on", F2: "on", F3: "off"}))
    assert out == ids


# --- Không lọc khi hành động không suy được trạng thái đích ------------------
def test_set_khong_loc():
    ids = [F1, F2]
    out = reduce_by_state(ids, action_hint="set", live_states=_states(**{F1: "off", F2: "on"}))
    assert out == ids


def test_mot_thiet_bi_tra_nguyen():
    assert reduce_by_state([F1], action_hint="turn_off", live_states=_states(**{F1: "on"})) == [F1]
