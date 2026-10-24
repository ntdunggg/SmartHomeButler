"""Vòng lặp hỏi phòng (§15) — câu trả lời của người dùng phải LUÔN đẩy hội thoại đi tiếp.

Lỗi người dùng gặp: "bật đèn" → "Bạn muốn mình làm ở phòng nào ạ?" → "phòng ngủ" → hỏi
lại y NGUYÊN câu cũ. Ba nguyên nhân độc lập, mỗi cái được khoá bằng test riêng ở đây:

1. Câu hỏi được dựng từ một LÁT CẮT đầu catalog (context builder cắt bớt khi câu chưa nêu
   phòng) chứ không từ registry → chỉ chào 2/4 phòng có đèn, nên người dùng trả lời một
   phòng KHÔNG hề được liệt kê.
2. "phòng ngủ" khớp HAI phòng chuẩn nên không alias đơn nào khớp → lượt đó bị bỏ qua hoàn
   toàn thay vì được hiểu là THU HẸP.
3. Lượt thu hẹp ghi đè `raw_utterance` của lệnh đang treo → lượt sau không còn mốc để
   slot-fill, hội thoại quay vòng ngay cả khi đã trả lời đủ.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.agent.pipeline import PipelineDeps
from src.nlu.understanding import analyze
from src.services.pipeline_bridge import reason

_NOW = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)


def _conversation(name: str, *messages: str) -> list:
    deps = PipelineDeps()
    return [
        reason(message=m, conversation_id=name, role="owner", household_id=1, now=_NOW, deps=deps)
        for m in messages
    ]


def _devices(result) -> set[str]:
    return {a.device_id for a in (result.candidate_plan.actions if result.candidate_plan else [])}


def test_room_question_offers_every_room_that_has_the_named_device_type():
    """"Bật đèn": đèn có ở CẢ BỐN phòng nên câu hỏi không được bỏ sót phòng nào."""
    (first,) = _conversation("clar-all-rooms", "bật đèn")

    assert first.outcome == "clarification"
    for room in ("Phòng khách", "Phòng bếp", "Phòng ngủ bố mẹ", "Phòng ngủ con"):
        assert room in first.reply


def test_generic_room_phrase_narrows_instead_of_being_dropped():
    """"phòng ngủ" khớp hai phòng → hỏi lại giữa ĐÚNG hai phòng đó, không lặp câu cũ."""
    first, second = _conversation("clar-narrow", "bật đèn", "phòng ngủ")

    assert second.outcome == "clarification"
    assert second.reply != first.reply
    assert "Phòng ngủ bố mẹ" in second.reply
    assert "Phòng ngủ con" in second.reply
    assert "Phòng bếp" not in second.reply


def test_narrowing_then_answering_completes_the_original_command():
    """Lượt thu hẹp không được làm mất lệnh gốc: cuối cùng vẫn phải bật đèn phòng ngủ con."""
    *_, third = _conversation("clar-complete", "bật đèn", "phòng ngủ", "phòng ngủ con")

    assert third.outcome == "candidate_plan"
    assert _devices(third) == {"den_ngu_con", "den_ban_hoc"}


def test_elliptical_answer_matching_one_offered_option_resolves():
    """Người dùng đáp gọn bằng phần phân biệt của lựa chọn đã chào ("bố mẹ")."""
    *_, third = _conversation("clar-elliptical", "bật đèn", "phòng ngủ", "bố mẹ")

    assert third.outcome == "candidate_plan"
    assert _devices(third) == {"den_ngu_bo_me", "den_ban_lam_viec"}


def test_a_generic_room_phrase_does_not_commit_to_any_room():
    """"phòng ngủ" là THU HẸP, không phải đích: không được bật đèn cả hai phòng ngủ."""
    (only,) = _conversation("clar-no-commit", "bật đèn phòng ngủ")

    assert only.outcome == "clarification"
    assert not _devices(only)


def test_partial_rooms_is_registry_derived_and_yields_to_an_explicit_room():
    nu = analyze("phòng ngủ")
    assert nu.matched_rooms == ()
    assert set(nu.partial_rooms) == {"Phòng ngủ bố mẹ", "Phòng ngủ con"}

    # Câu đã chốt phòng thì không còn gì để thu hẹp.
    explicit = analyze("phòng ngủ con")
    assert explicit.matched_rooms == ("Phòng ngủ con",)
    assert explicit.partial_rooms == ()

    # "phòng" trần khớp MỌI phòng nên không thu hẹp được gì.
    assert analyze("bật đèn trong phòng").partial_rooms == ()


def test_a_new_command_still_replaces_the_pending_one():
    """Lượt sau MANG động từ hành động là lệnh MỚI, không phải câu trả lời."""
    _, second = _conversation("clar-new-command", "bật đèn", "mở rèm phòng khách")

    assert _devices(second) == {"rem_phong_khach"}


# ---------------------------------------------------------------------------
# Danh tính người nói là bằng chứng thu hẹp phòng
# ---------------------------------------------------------------------------
def _conversation_as(name: str, home_room: str | None, *messages: str) -> list:
    deps = PipelineDeps()
    return [
        reason(
            message=m, conversation_id=name, role="owner", household_id=1,
            now=_NOW, deps=deps, speaker_home_room=home_room,
        )
        for m in messages
    ]


def test_generic_bedroom_resolves_to_the_speakers_own_room():
    """"Bật đèn phòng ngủ" từ bố/mẹ = phòng ngủ CỦA HỌ — không hỏi lại thứ đã biết."""
    (parents,) = _conversation_as("home-parents", "Phòng ngủ bố mẹ", "bật đèn phòng ngủ")
    assert _devices(parents) == {"den_ngu_bo_me", "den_ban_lam_viec"}

    (kid,) = _conversation_as("home-kid", "Phòng ngủ con", "bật đèn phòng ngủ")
    assert _devices(kid) == {"den_ngu_con", "den_ban_hoc"}


def test_answering_a_narrowing_question_uses_the_speakers_room_too():
    _, second = _conversation_as("home-answer", "Phòng ngủ bố mẹ", "bật đèn", "phòng ngủ")
    assert _devices(second) == {"den_ngu_bo_me", "den_ban_lam_viec"}


def test_speaker_room_outside_the_narrowed_set_still_asks():
    """Phòng riêng người nói KHÔNG nằm trong tập vừa thu hẹp → vẫn phải hỏi lại."""
    (only,) = _conversation_as("home-outside", "Phòng khách", "bật đèn phòng ngủ")

    assert only.outcome == "clarification"
    assert not _devices(only)
    assert "Phòng ngủ bố mẹ" in only.reply and "Phòng ngủ con" in only.reply


def test_an_explicit_room_always_beats_the_speakers_own_room():
    """Nêu rõ phòng thì danh tính KHÔNG được ghi đè — bố vẫn bật được đèn phòng con."""
    (explicit,) = _conversation_as("home-explicit", "Phòng ngủ bố mẹ", "bật đèn phòng ngủ con")
    assert _devices(explicit) == {"den_ngu_con", "den_ban_hoc"}


def test_speaker_home_room_has_one_definition_for_every_entry_point():
    """agent_runner và nlu_gateway phải suy CÙNG một phòng cho cùng một vai trò."""
    from src.services.context import speaker_home_room_for

    assert speaker_home_room_for("owner") == "Phòng ngủ bố mẹ"
    assert speaker_home_room_for("member") == "Phòng ngủ con"
    assert speaker_home_room_for("khong-biet-vai-tro-nay") == "Phòng ngủ con"


def test_private_room_and_home_room_have_distinct_grounding_semantics():
    """Cụm sở hữu dùng private; cụm phòng chung mơ hồ dùng home."""
    owned = analyze(
        "bật đèn phòng ngủ của tôi",
        speaker_home_room="Phòng ngủ bố mẹ",
        speaker_private_room="Phòng ngủ con",
    )
    generic = analyze(
        "bật đèn phòng ngủ",
        speaker_home_room="Phòng ngủ bố mẹ",
        speaker_private_room="Phòng ngủ con",
    )

    assert owned.matched_rooms == ("Phòng ngủ con",)
    assert generic.matched_rooms == ("Phòng ngủ bố mẹ",)
