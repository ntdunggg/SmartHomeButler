"""Room-alias mapping layer của live-semantic eval (dataset vocab → registry dự án).

Bảo vệ hành vi ánh xạ: chỉ CHUẨN HOÁ từ vựng phòng chung của dataset về 4 phòng registry,
KHÔNG đụng từ đã cụ thể, idempotent, và mọi đích map phải là phòng registry hợp lệ.
"""

from __future__ import annotations

import pytest

from scripts.eval_live_semantic import map_rooms_to_registry
from src.iot.registry import ROOMS


@pytest.mark.parametrize(
    ("src", "expected_room"),
    [
        ("Ở phòng ngủ chói mắt quá.", "Phòng ngủ bố mẹ"),   # bedroom trần → phòng chính
        ("Trong phòng ngủ nóng quá.", "Phòng ngủ bố mẹ"),
        ("Chỗ làm việc sáng gắt quá.", "Phòng ngủ bố mẹ"),  # registry đặt đèn bàn làm việc ở đây
        ("phòng làm việc tối", "Phòng ngủ bố mẹ"),
        ("dọn phòng ăn", "Phòng bếp"),                        # phòng ăn → bếp
        ("đèn hành lang", "Phòng khách"),                     # hành lang → khu chung
    ],
)
def test_maps_generic_terms_to_registry_rooms(src: str, expected_room: str):
    out = map_rooms_to_registry(src)
    assert expected_room in out
    assert expected_room in ROOMS  # đích luôn là phòng registry thật


@pytest.mark.parametrize(
    "src",
    [
        "Bật đèn phòng ngủ con.",     # bedroom CỤ THỂ giữ nguyên (không map thành bố mẹ)
        "đèn phòng ngủ bố mẹ",        # đã cụ thể → không nhân đôi hậu tố
        "bật đèn phòng khách",        # phòng đã chuẩn → không đổi
        "Bật lên đi.",                # không nêu phòng → không thêm phòng
    ],
)
def test_specific_or_no_room_unchanged(src: str):
    assert map_rooms_to_registry(src) == src


def test_specific_bedroom_not_rewritten_to_parents():
    """Negative-lookahead: 'phòng ngủ con'/'phòng ngủ bố mẹ' KHÔNG bị bắt bởi rule 'phòng ngủ' trần."""
    assert "Phòng ngủ bố mẹ con" not in map_rooms_to_registry("phòng ngủ con")
    assert map_rooms_to_registry("phòng ngủ con") == "phòng ngủ con"


def test_mapping_is_idempotent():
    for src in ["Trong phòng ngủ nóng quá.", "Chỗ làm việc sáng gắt.", "đèn hành lang", "dọn phòng ăn"]:
        once = map_rooms_to_registry(src)
        assert map_rooms_to_registry(once) == once
