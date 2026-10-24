""""khỏi" là phủ định trong "khỏi bật đèn", nhưng là giới từ chỉ hướng trong "ra khỏi nhà".

Bug gốc: pattern phủ định bắt mọi chữ "khỏi", nên "ra khỏi nhà thôi" — một nếp sinh hoạt
RỜI NHÀ — bị gắn phủ định giả, kéo theo goal.negated=True và không sinh được kế hoạch nào.
Sai âm thầm: người dùng bảo chuẩn bị ra khỏi nhà, hệ thống im lặng không làm gì.
"""

from __future__ import annotations

import pytest

from src.nlu.normalizer import analyze


@pytest.mark.parametrize(
    "utterance",
    ["khỏi bật đèn bếp", "khoi bat den bep", "khỏi tắt điều hoà phòng khách", "khỏi làm", "thôi khỏi", "thoi khoi"],
)
def test_khoi_van_la_phu_dinh(utterance: str) -> None:
    assert analyze(utterance).has_negation is True


@pytest.mark.parametrize(
    "utterance",
    ["ra khỏi nhà thôi", "ra khoi nha thoi", "rời khỏi phòng", "thoát khỏi chế độ", "tránh khỏi chỗ đó"],
)
def test_khoi_chi_huong_khong_phai_phu_dinh(utterance: str) -> None:
    assert analyze(utterance).has_negation is False


def test_khoi_dong_van_khong_dinh_phu_dinh() -> None:
    # "khởi động" khác "khỏi" — lookahead cũ đã chặn, giữ lại để không hồi quy.
    assert analyze("khởi động lại").has_negation is False
