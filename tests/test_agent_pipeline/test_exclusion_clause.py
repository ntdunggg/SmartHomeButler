"""Kiểm thử CÔ LẬP cho bộ tách mệnh đề loại trừ (`src/nlu/exclusion_clause.py`).

Chỉ chạm hàm `split_exclusion_clause` — KHÔNG qua pipeline/registry. Mục tiêu: chốt hợp đồng
"tách văn bản" trước khi tích hợp phép trừ thiết bị. Mẫu câu là câu thực tế tiếng Việt (có/không
có mệnh đề loại trừ) + các bẫy dễ cắt nhầm (câu hỏi "... không?", loại trừ đứng một mình).
"""

from __future__ import annotations

import pytest

from src.nlu.exclusion_clause import split_exclusion_clause


@pytest.mark.parametrize(
    "text, positive, exclusion_has, marker",
    [
        # "nhưng đừng <verb>" — danh sách dương + loại một phần tử.
        (
            "Bật điều hòa phòng khách và phòng ngủ nhưng đừng bật phòng ngủ",
            "Bật điều hòa phòng khách và phòng ngủ",
            "phòng ngủ",
            "nhung dung",
        ),
        # "trừ" — nhóm dương + loại trừ tường minh.
        ("Tắt tất cả đèn trừ đèn đọc sách", "Tắt tất cả đèn", "đèn đọc sách", "tru"),
        # Có dấu phẩy + "nhưng đừng bật" (case goldenset NC-002).
        (
            "Bật đèn bếp và đèn bàn ăn, nhưng đừng bật đèn bàn ăn.",
            "Bật đèn bếp và đèn bàn ăn",
            "đèn bàn ăn",
            "nhung dung",
        ),
        # "trừ" sau dấu phẩy (case goldenset NC-003).
        ("Tắt các đèn ở phòng bố mẹ, trừ đèn ngủ.", "Tắt các đèn ở phòng bố mẹ", "đèn ngủ", "tru"),
        # "ngoại trừ" phải thắng "trừ" (khớp cụm dài, cùng vị trí).
        ("Mở hết rèm ngoại trừ rèm phòng ngủ", "Mở hết rèm", "rèm phòng ngủ", "ngoai tru"),
        # "không <verb>" cũng là tín hiệu loại trừ.
        ("Tắt đèn cả nhà không tắt đèn ngủ con", "Tắt đèn cả nhà", "đèn ngủ con", "khong tat"),
    ],
)
def test_tach_dung_menh_de_loai_tru(text, positive, exclusion_has, marker):
    split = split_exclusion_clause(text)
    assert split is not None, f"phải tách được: {text!r}"
    assert split.positive_clause == positive
    assert exclusion_has in split.exclusion_clause
    assert split.marker == marker
    # Bất biến: hai mệnh đề rời nhau, mệnh đề dương KHÔNG chứa phần loại trừ.
    assert exclusion_has not in split.positive_clause or text.count(exclusion_has) > 1


@pytest.mark.parametrize(
    "text",
    [
        "Bật đèn phòng khách.",  # lệnh thường, không loại trừ
        "Tắt đèn phòng ngủ con.",
        "Điều hoà đang bật không?",  # câu hỏi: "không" ở cuối, KHÔNG kèm động từ → không cắt
        "Đèn phòng ngủ không sáng.",  # "không" + tính từ (không phải động từ điều khiển)
        "Máy lọc phòng khách đã tắt chưa?",  # "chưa" không phải từ khoá loại trừ
        "Cho phòng khách mát hơn.",
    ],
)
def test_khong_cat_nham_cau_khong_co_loai_tru(text):
    assert split_exclusion_clause(text) is None, f"KHÔNG được tách: {text!r}"


def test_loai_tru_dung_mot_minh_tra_none():
    """"Trừ đèn ngủ ra" đứng một mình (không có mệnh đề dương) → None (để turn_intent xử lý đa lượt)."""
    assert split_exclusion_clause("Trừ đèn ngủ ra") is None


def test_marker_o_cuoi_khong_con_noi_dung_tra_none():
    """Từ khoá ở cuối, không còn ứng viên thiết bị theo sau → None (tránh cắt cụt)."""
    assert split_exclusion_clause("Tắt hết đèn trừ") is None
