"""Kiểm thử CÔ LẬP cho bộ giải deixis vị trí (`src/context/location_deixis.py`).

Chốt hợp đồng TRƯỚC khi tích hợp: (a) nhận diện đúng cụm deixis, không bắt nhầm câu thường;
(b) ưu tiên nguồn phòng presence → conversation_location → ledger_room; (c) mơ hồ → None.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.agent.pipeline import PipelineDeps
from src.context.location_deixis import (
    has_location_deixis,
    resolve_deictic_room,
    strip_deixis,
)
from src.iot.registry import ROOMS
from src.services.pipeline_bridge import reason

BO_ME = "Phòng ngủ bố mẹ"
CON = "Phòng ngủ con"
_NOW = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Bật đèn ngủ ở đây.", True),
        ("Bật máy lọc ở đây.", True),
        ("Cùng phòng đó.", True),
        ("Đóng rèm ở đây.", True),
        ("Phòng này nóng quá.", True),
        ("Bật đèn phòng khách.", False),
        ("Tắt điều hoà phòng ngủ con.", False),
        ("Giảm âm lượng xuống.", False),
    ],
)
def test_has_location_deixis(text, expected):
    assert has_location_deixis(text) is expected


def test_presence_khi_khong_conversation_location():
    # CR-006: không conversation_location, người đang ở phòng bố mẹ (presence đơn nhất) → phòng bố mẹ.
    room = resolve_deictic_room(
        "Bật đèn ngủ ở đây.", presence_rooms=[BO_ME],
        conversation_location=None, ledger_room=CON, valid_rooms=ROOMS,
    )
    assert room == BO_ME


def test_conversation_location_thang_presence_mac_dinh():
    # LC-008: "chuyển sang phòng bố mẹ" đặt conversation_location; presence có thể là MẶC ĐỊNH của
    # registry (phòng khách) → conversation_location phải THẮNG để không hiểu nhầm.
    room = resolve_deictic_room(
        "Bật máy lọc ở đây.", presence_rooms=["Phòng khách"],
        conversation_location=BO_ME, ledger_room=None, valid_rooms=ROOMS,
    )
    assert room == BO_ME


def test_presence_mo_ho_bo_qua_xet_ledger():
    # presence nhiều phòng (mơ hồ) + không conversation_location → phòng chốt gần nhất trong ledger.
    room = resolve_deictic_room(
        "Bật máy lọc ở đây.", presence_rooms=[BO_ME, CON],
        conversation_location=None, ledger_room=CON, valid_rooms=ROOMS,
    )
    assert room == CON


def test_fallback_conversation_location_khi_khong_presence():
    # LC-008: không presence, "chuyển sang phòng bố mẹ" đặt conversation_location.
    room = resolve_deictic_room(
        "Bật máy lọc ở đây.", presence_rooms=[],
        conversation_location=BO_ME, ledger_room=None, valid_rooms=ROOMS,
    )
    assert room == BO_ME


def test_fallback_ledger_room_cho_cung_phong_do():
    # LC-007: "cùng phòng đó" = phòng chốt gần nhất trong ledger.
    room = resolve_deictic_room(
        "Cùng phòng đó.", presence_rooms=None,
        conversation_location=None, ledger_room=BO_ME, valid_rooms=ROOMS,
    )
    assert room == BO_ME


def test_khong_deixis_tra_none():
    assert resolve_deictic_room(
        "Bật đèn phòng khách.", presence_rooms=[BO_ME],
        conversation_location=BO_ME, ledger_room=BO_ME, valid_rooms=ROOMS,
    ) is None


def test_khong_nguon_phong_tra_none():
    # Có deixis nhưng không nguồn phòng nào → None (tầng trên clarify).
    assert resolve_deictic_room(
        "Bật máy lọc ở đây.", presence_rooms=[], conversation_location=None,
        ledger_room=None, valid_rooms=ROOMS,
    ) is None


def test_phong_khong_hop_le_bi_loai():
    assert resolve_deictic_room(
        "Bật máy lọc ở đây.", presence_rooms=["Phòng tắm"],
        conversation_location=None, ledger_room=None, valid_rooms=ROOMS,
    ) is None


# --- strip_deixis (viết lại tường minh) --------------------------------------
def test_strip_deixis_go_dai_tu_chi_dinh():
    assert strip_deixis("Cùng phòng đó.") == ""
    assert strip_deixis("Bật đèn ngủ ở đây.") == "Bật đèn ngủ"
    assert strip_deixis("Bật máy lọc ở đây.") == "Bật máy lọc"
    assert strip_deixis("Đóng rèm ở đây.") == "Đóng rèm"
    assert strip_deixis("Bật đèn phòng khách.") == "Bật đèn phòng khách"  # không deixis → nguyên


# --- Tích hợp end-to-end qua pipeline ----------------------------------------
def test_cr006_presence_o_day_ground_dung_phong():
    """CR-006: người ở phòng bố mẹ (presence) → "đèn ngủ ở đây" = đèn ngủ phòng bố mẹ (khử mơ hồ)."""
    sensors = [
        {"slug": "hien_dien_phong_bo_me", "sensor_type": "presence", "value": 1.0, "room": BO_ME},
        {"slug": "hien_dien_phong_khach", "sensor_type": "presence", "value": 0.0, "room": "Phòng khách"},
    ]
    r = reason(message="Bật đèn ngủ ở đây.", conversation_id="cr6", now=_NOW,
               live_sensors=sensors, deps=PipelineDeps())
    assert r.outcome == "candidate_plan"
    assert [a.device_id for a in r.candidate_plan.actions] == ["den_ngu_bo_me"]


def test_lc008_conversation_location_o_day_ground_type_in_room():
    """LC-008: conversation_location=phòng bố mẹ → "máy lọc ở đây" ground theo LOẠI trong phòng."""
    r = reason(message="Bật máy lọc ở đây.", conversation_id="lc8", now=_NOW,
               speaker_location=BO_ME, deps=PipelineDeps())
    assert r.outcome == "candidate_plan"
    assert "may_loc_phong_bo_me" in [a.device_id for a in r.candidate_plan.actions]
