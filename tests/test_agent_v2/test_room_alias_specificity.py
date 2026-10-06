"""QA finding 4 — generalization: alias CỤ THỂ hơn phải thắng cụm TỔNG QUÁT mà nó chứa.

Các cách nói tự nhiên về "Phòng ngủ con" (biến thể "trẻ em"/"trẻ con"/"bé"/"các con"...)
đều chứa cụm "phòng ngủ". Vì hệ thống đã xoá phòng generic "Phòng ngủ" và máy sưởi khỏi
nhà mẫu, cụm "phòng ngủ" trơn không được tự ground sang một phòng cô lập; còn alias trẻ em
phải ground đúng vào "Phòng ngủ con".

Các câu paraphrase dưới đây do QA/Coder TỰ NGHĨ (không trùng câu trong
`test_qa_static_audit_findings.py`), để chứng minh luật ưu tiên TỔNG QUÁT — không phải một
bản vá cho đúng một câu benchmark."""

from __future__ import annotations

from src.agent.perception.context_builder import build_perception
from src.agent.understanding.context_resolver import _room_from_text, resolve_room
from src.iot.registry import KIDS_ROOM, PARENTS_ROOM


def test_bare_bedroom_without_qualifier_is_not_a_valid_room_after_heater_removal():
    """Câu 'phòng ngủ' TRƠN không còn phòng generic để ground sau khi xoá máy sưởi."""
    assert _room_from_text("phòng ngủ hơi nóng, bật máy sưởi lên") is None


def test_parents_room_qualifier_unaffected_by_kids_room_fix():
    assert _room_from_text("phòng ngủ bố mẹ hơi lạnh, tắt điều hoà đi") == PARENTS_ROOM


PARAPHRASES = [
    "phòng của bé hơi tối, bật đèn lên giúp mình",
    "phòng trẻ con hơi bí, mở máy lọc không khí lên",
    "phòng ngủ bé con nóng quá, chỉnh điều hoà xuống 24 độ",
    "phòng bé con đang bí, bật quạt lên",
    "phòng ngủ các con hơi lạnh, tắt điều hoà đi",
    "phòng các con tối quá, bật đèn lên",
    "phòng ngủ của bé nóng quá, mở điều hoà lên",
    "phòng trẻ em tối quá, bật đèn lên",
    "phòng ngủ trẻ con hơi nóng, chỉnh điều hoà xuống",
    "trong phòng trẻ em hơi ngột, mở máy lọc không khí lên",
]


def test_at_least_eight_paraphrases_cover_generalization():
    assert len(PARAPHRASES) >= 8


def test_child_room_paraphrases_resolve_to_kids_room():
    for text in PARAPHRASES:
        assert _room_from_text(text) == KIDS_ROOM, (
            f"'{text}' phải ground vào {KIDS_ROOM!r} (phòng thật có thiết bị), "
            "không phải cụm phòng ngủ generic đã bị xoá."
        )


def test_child_room_paraphrase_resolves_in_single_turn_utterance():
    """Không chỉ recent_dialogue — câu NÓI TRỰC TIẾP trong lượt hiện tại cũng phải ground
    đúng qua normalizer.analyze().matched_rooms (nguồn 1: explicit_utterance, spec §14)."""
    nu, _ctx, _ = build_perception("phòng ngủ trẻ con hơi nóng, bật quạt lên")
    assert nu.matched_rooms == (KIDS_ROOM,), (
        f"explicit_utterance phải khớp ĐÚNG {KIDS_ROOM!r}, thực tế matched_rooms={nu.matched_rooms!r}"
    )


def test_child_room_paraphrase_inherited_from_recent_dialogue_multi_turn():
    """Multi-turn: câu HIỆN TẠI không nêu phòng ('to hơn tí'), nhưng recent_dialogue lượt
    trước nói 'phòng ngủ bé con' — resolve_room (tier 4, spec §14) phải kế thừa ĐÚNG
    Phòng ngủ con, không phải cụm phòng ngủ generic đã bị xoá."""
    nu, ctx, _ = build_perception(
        "to hơn tí", recent_dialogue=["phòng ngủ bé con hơi tối, bật đèn lên giúp mình"]
    )
    resolved = resolve_room(nu=nu, ctx=ctx)
    assert resolved is not None
    assert resolved.value == KIDS_ROOM
    assert resolved.source == "recent_dialogue"
