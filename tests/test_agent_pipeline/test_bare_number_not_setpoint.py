"""Con số trong câu KHÔNG mặc nhiên là setpoint cho thiết bị lượt trước.

Chốt fix §2026-08-30, phát hiện khi chạy thử trên server thật: sau lượt "bật đèn phòng khách",
câu "1 cộng 1" ra "Đặt độ sáng Đèn chùm = 1%" — một câu hỏi toán ĐỔI TRẠNG THÁI NHÀ.

Hai nguyên nhân độc lập trong `detect_modifier` (turn_intent.py):
1. `_NUM_UNIT` để đơn vị là TUỲ CHỌN, nên mọi con số trần thành giá trị tuyệt đối.
2. Cue đặt-tới được dò ở BẤT KỲ đâu trong câu, nên từ rất thường gặp ("tới", "về", "lên")
   mở cửa cho mọi câu có số: "tôi có 3 người bạn sắp TỚI" ghi độ sáng = 3%.

Ranh giới dễ vỡ: chặn quá tay sẽ giết các lượt tiếp nối hợp lệ, nên test khoá cả hai chiều.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.agent.pipeline import PipelineDeps
from src.services.pipeline_bridge import reason

_NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def _actions(first: str, probe: str, cid: str):
    deps = PipelineDeps()
    reason(message=first, conversation_id=cid, now=_NOW, deps=deps)
    result = reason(message=probe, conversation_id=cid, now=_NOW, deps=deps)
    plan = getattr(result, "candidate_plan", None)
    return [(a.device_id, str(a.action)) for a in (plan.actions if plan else [])]


@pytest.mark.parametrize(
    "probe",
    [
        "1 cộng 1",                      # ca thật trên server
        "2 cộng 3 bằng mấy",
        "tôi có 3 người bạn sắp tới",    # "tới" là cue đặt-tới nhưng KHÔNG kề con số
        "5 phút nữa tôi về",             # "về" cũng vậy
    ],
)
def test_so_lan_trong_cau_khong_ghi_gia_tri(probe: str) -> None:
    """Số là dữ kiện của câu, không phải giá trị điền vào mục tiêu lượt trước.

    Khẳng định trên HÀNH ĐỘNG, không trên nhãn: điều phải giữ là nhà KHÔNG bị đổi trạng thái.
    """
    assert _actions("bật đèn phòng khách", probe, f"bare-{probe[:6]}") == []


@pytest.mark.parametrize(
    ("first", "probe"),
    [
        ("bật đèn phòng khách", "60 thôi"),          # số trần = toàn bộ nội dung lượt
        ("bật đèn phòng khách", "à 30"),
        ("bật đèn phòng khách", "80%"),              # có đơn vị
        ("bật đèn phòng khách", "độ sáng 40"),       # có tên capability
        ("bật tv phòng khách", "âm lượng 25 thôi"),
        ("đặt điều hoà phòng khách 26 độ", "25 độ"),
        ("đặt điều hoà phòng khách 26 độ", "xuống 22"),      # cue KỀ con số
        ("đặt điều hoà phòng khách 26 độ", "hạ xuống 20 độ"),
        ("đặt điều hoà phòng khách 26 độ", "giảm còn 21"),
    ],
)
def test_tiep_noi_gia_tri_hop_le_van_chay(first: str, probe: str) -> None:
    assert _actions(first, probe, f"ok-{first[:5]}-{probe[:6]}"), f"{probe!r} phải vẫn sinh hành động"
