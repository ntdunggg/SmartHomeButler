"""Sửa phòng đa lượt (§5, §17) — câu sửa THAY THẾ đích, không cộng dồn hai phòng.

Trước bản vá, cổng nhận diện "câu sửa phòng" chỉ dò TỪ KHOÁ sửa lời (`has_correction`:
"nhầm", "ý tôi là"...). Những cách nói sửa phổ biến không mang từ khoá đó
("à ở phòng ngủ bố mẹ mà", "à phòng bếp cơ") rơi xuống nhánh slot-fill ghép chuỗi, vốn
phát lại NGUYÊN câu cũ — kèm alias phòng cũ — nên kế hoạch tác động lên CẢ HAI phòng:
đúng thứ người dùng vừa nói là sai. Cổng bây giờ là CẤU TRÚC (ledger đã chốt phòng nào,
câu này nêu phòng khác), không phải từ vựng.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.agent.pipeline import PipelineDeps
from src.services.pipeline_bridge import reason

_NOW = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)


def _conversation(*messages: str) -> list:
    deps = PipelineDeps()
    conv = f"room-correction-{abs(hash(messages)) % 10**8}"
    return [
        reason(message=m, conversation_id=conv, role="owner", household_id=1, now=_NOW, deps=deps)
        for m in messages
    ]


def _device_ids(result) -> set[str]:
    return {a.device_id for a in (result.candidate_plan.actions if result.candidate_plan else [])}


def test_room_correction_without_a_correction_keyword_replaces_the_target():
    first, second = _conversation("Bật điều hoà phòng ngủ con", "à ở phòng ngủ bố mẹ mà")

    assert _device_ids(first) == {"dieu_hoa_phong_con"}
    # Phòng cũ phải BIẾN MẤT, không được cộng dồn.
    assert _device_ids(second) == {"dieu_hoa_phong_bo_me"}


def test_room_correction_disambiguates_by_registry_name_not_device_type_alone():
    """Phòng mới có NHIỀU thiết bị cùng loại ("Đèn ngủ" + "Đèn bàn làm việc" đều là light).

    Rebind theo device_type đơn thuần sẽ bỏ cuộc và rơi lại vào ghép chuỗi hai phòng.
    Registry còn định danh mịn hơn (tên/semantic role) để chọn đúng thiết bị đối ứng.
    """
    _, second = _conversation("Bật đèn ngủ phòng ngủ con", "à ở phòng ngủ bố mẹ mà")

    assert _device_ids(second) == {"den_ngu_bo_me"}


def test_answering_a_clarification_still_uses_slot_fill_not_correction():
    """Lượt 1 CHƯA chốt phòng nào → "phòng bếp" là câu TRẢ LỜI, không phải câu sửa."""
    first, second = _conversation("Bật đèn", "phòng bếp")

    assert first.outcome == "clarification"
    assert _device_ids(second) == {"den_bep", "den_ban_an"}


def test_naming_the_same_room_again_is_not_treated_as_a_correction():
    _, second = _conversation("Bật điều hoà phòng ngủ con", "phòng ngủ con")

    assert _device_ids(second) == {"dieu_hoa_phong_con"}
