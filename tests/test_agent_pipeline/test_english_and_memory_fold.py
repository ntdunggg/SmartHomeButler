"""Regression tất định: (1) memory token fold bỏ dấu, (2) alias thiết bị/phòng tiếng Anh.

Bối cảnh (LIVE §2026-08-10): câu trộn Việt-Anh ("air conditioner phòng khách") không ground
được; memory situation-match trượt khi lượt gõ không dấu. Hai fix ở tầng tất định.
"""

from __future__ import annotations

from src.memory.retrieval import situation_tokens_from
from src.memory.store import _tokens
from src.nlu.normalizer import analyze


# ---- (1) Memory token fold ----
def test_memory_tokens_fold_diacritics_consistently() -> None:
    """Store và query fold cùng cách → câu không dấu vẫn khớp memory có dấu."""
    stored = set(_tokens("Tối nay nhà có khách"))
    query_accented = situation_tokens_from("nhà có khách")
    query_folded = situation_tokens_from("nha co khach")  # gõ không dấu
    assert stored & query_accented, "cùng dấu phải khớp"
    assert stored & query_folded, "không dấu vẫn phải khớp (fold đồng nhất)"
    # "khach" (folded) phải xuất hiện ở cả hai bề mặt
    assert "khach" in stored and "khach" in query_folded


# ---- (2) English device/room aliases ----
def test_english_device_noun_grounds() -> None:
    """'air conditioner phòng khách' (trộn Việt-Anh, VN word order) → đúng điều hoà PK."""
    nu = analyze("bật cái air conditioner phòng khách lên")
    assert "dieu_hoa_phong_khach" in nu.matched_device_ids


def test_english_room_name_maps() -> None:
    for utt, room in [("turn on the living room light", "Phòng khách"),
                      ("master bedroom air conditioner", "Phòng ngủ bố mẹ"),
                      ("open the kitchen window", "Phòng bếp")]:
        assert room in analyze(utt).matched_rooms, utt


def test_english_expansion_does_not_corrupt_vietnamese() -> None:
    """Câu tiếng Việt thuần KHÔNG bị map tiếng Anh đụng vào (ranh giới từ)."""
    nu = analyze("bật đèn phòng khách")
    assert "den_chum_phong_khach" in nu.matched_device_ids
    assert nu.normalized == "bật đèn phòng khách"
