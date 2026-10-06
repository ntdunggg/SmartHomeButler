"""Động từ bị "đang" chi phối là MÔ TẢ trạng thái, không bao giờ là mệnh lệnh ghi.

Chốt fix §2026-08-30. Ledger thật (id 184, lượt 6) ghi nhận "hiện tại có bao thiết bị đang
bật" — câu hỏi gõ rụng âm tiết "nhiêu" — ra `DEVICE_COMMAND/turn_on` ở Phòng khách: cổng hỏi
khớp CỤM ĐẦY ĐỦ ("bao nhiêu") nên hụt một âm tiết là thoát hết cổng, rồi "bật" khớp `_TURN_ON`.
Lần đó chỉ thoát vì không thiết bị nào ground được nên dừng ở clarification.

Hai lớp chặn, test cả hai chiều: câu mô tả/hỏi KHÔNG sinh lệnh, lệnh-lọc-hiện-trạng VẪN là lệnh.
"""

from __future__ import annotations

import pytest

from src.agent.text import TextView
from src.nlu.normalizer import analyze
from src.nlu.ontology import UtteranceType
from src.nlu.understanding import _classify, _extract_params


def _classify_text(text: str):
    nu = analyze(text)
    view = TextView(raw=nu.normalized, folded=nu.folded)
    return _classify(nu, view, _extract_params(view))


@pytest.mark.parametrize(
    "text",
    [
        "hiện tại có bao thiết bị đang bật",  # ca thật trong ledger id 184
        "có bao đèn đang bật",
        "hiện tại có bao nhiêu thiết bị đang bật",
        "thiết bị nào đang bật",
        "dang bat may loc khong",  # không dấu: lớp folded phải chặn y hệt
    ],
)
def test_cau_hoi_hien_trang_khong_bao_gio_thanh_lenh_ghi(text: str) -> None:
    utterance_type, action_hint, _ = _classify_text(text)
    assert utterance_type is UtteranceType.INFORMATION_QUESTION
    assert action_hint is None


@pytest.mark.parametrize(
    "text",
    [
        "máy lọc đang chạy",
        "điều hoà đang bật",
    ],
)
def test_cau_mo_ta_hien_trang_khong_sinh_action_hint(text: str) -> None:
    """Câu trần thuật ("điều hoà đang bật") tả trạng thái ĐANG CÓ, không yêu cầu đổi gì.

    Không ép thành câu hỏi — chỉ bắt buộc KHÔNG ra lệnh ghi; phần diễn giải mục tiêu ngầm
    để tầng LLM lo (§module docstring: không đoán target ở tầng tất định).
    """
    utterance_type, action_hint, _ = _classify_text(text)
    assert utterance_type is not UtteranceType.DEVICE_COMMAND
    assert action_hint is None


@pytest.mark.parametrize(
    ("text", "expected_hint"),
    [
        # Lệnh LỌC theo hiện trạng: "đang bật" là bộ lọc, "tắt" mới là mệnh lệnh.
        ("tắt cái nào đang bật", "turn_off"),
        ("tắt hết đèn đang sáng trong nhà", "turn_off"),
        # "đang" tả bối cảnh, mệnh lệnh nằm ở vế sau.
        ("đang nóng quá, bật điều hoà lên", "turn_on"),
        # Lệnh thường không được đụng tới.
        ("bật đèn phòng khách", "turn_on"),
        ("đặt điều hoà 25 độ", "set"),
        ("khoá cửa chính", "lock"),
        ("tăng âm lượng tivi lên", "increase"),
    ],
)
def test_lenh_that_van_nguyen_ven(text: str, expected_hint: str) -> None:
    utterance_type, action_hint, _ = _classify_text(text)
    assert utterance_type is UtteranceType.DEVICE_COMMAND
    assert action_hint == expected_hint


def test_bao_gom_khong_phai_tu_de_hoi() -> None:
    """Nới "bao nhiêu" → "bao" không được biến động từ "bao gồm" thành từ để hỏi."""
    from src.nlu.understanding import _SURVEY_QUESTION

    nu = analyze("kịch bản đi ngủ bao gồm đèn nào đang bật")
    view = TextView(raw=nu.normalized, folded=nu.folded)
    hit = _SURVEY_QUESTION.search(view)
    assert hit is not None  # vẫn khớp — nhưng nhờ "nào", không nhờ "bao gồm"
    assert "gồm" not in hit.group(0)
