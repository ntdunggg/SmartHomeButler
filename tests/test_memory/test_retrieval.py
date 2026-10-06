"""Truy hồi ký ức: tất định, giải thích được, và KHÔNG BAO GIỜ thành kế hoạch.

Đây là nơi khoá ranh giới trung tâm của kiến trúc memory-augmented: ký ức chỉ là BẰNG
CHỨNG để hiểu ngữ cảnh. Nếu tầng này để lọt hành động cũ ra ngoài, hoặc tự chọn một bên
khi ký ức mâu thuẫn, thì agent đã lặng lẽ biến thành một bảng tra cứu tình huống → kế
hoạch — đúng thứ thiết kế cấm.
"""

from __future__ import annotations

from src.memory.retrieval import retrieve, score_item, select_routine, situation_tokens_from
from src.memory.types import KIND_EPISODE, KIND_TRAIT, MemoryItem, RoutineCandidate


def _ep(**kw) -> MemoryItem:
    # `match_tokens` là bề mặt khớp thật (token tiếng Việt của lượt cũ); `situation_label`
    # chỉ là khoá gom nhóm cho consolidation và KHÔNG dùng để khớp.
    base = dict(kind=KIND_EPISODE, text="t", confidence=0.7, room="living_room", hour=20,
                situation_label="prepare_for_guests", match_tokens=("khách", "nay", "tối"))
    return MemoryItem(**{**base, **kw})


# ---------------------------------------------------------------------------
# Bất biến: ký ức không mang hành động ra ngoài
# ---------------------------------------------------------------------------
def test_evidence_payload_never_exposes_past_actions() -> None:
    """`as_evidence` cố tình KHÔNG chứa kế hoạch cũ — thấy là chép, chép là hết suy luận."""
    item = _ep(text="Lần trước bạn đồng ý bật đèn", devices=("den_phong_khach",))
    payload = item.as_evidence()
    flat = str(payload).lower()
    assert "action" not in flat
    assert "den_phong_khach" not in flat


def test_negative_evidence_is_labelled_so_the_model_cannot_miss_it() -> None:
    item = _ep(polarity="negative")
    assert "TỪ CHỐI" in item.as_evidence()["ghi_chú"]


# ---------------------------------------------------------------------------
# Chấm điểm
# ---------------------------------------------------------------------------
def test_score_rises_with_matching_room_and_hour() -> None:
    tokens = situation_tokens_from("tối nay có khách")
    near = score_item(_ep(), room="living_room", devices=frozenset(), now_hour=20,
                      situation_tokens=tokens, same_speaker=False)
    far = score_item(_ep(), room="bedroom", devices=frozenset(), now_hour=8,
                     situation_tokens=frozenset(), same_speaker=False)
    assert near > far


def test_old_memory_loses_to_recent_one_all_else_equal() -> None:
    kw = dict(room="living_room", devices=frozenset(), now_hour=20,
              situation_tokens=situation_tokens_from("tối nay có khách"), same_speaker=False)
    fresh = score_item(_ep(age_days=0.0), **kw)
    stale = score_item(_ep(age_days=60.0), **kw)
    assert fresh > stale


def test_low_confidence_memory_cannot_outrank_on_room_match_alone() -> None:
    kw = dict(room="living_room", devices=frozenset(), now_hour=20,
              situation_tokens=situation_tokens_from("tối nay có khách"), same_speaker=False)
    assert score_item(_ep(confidence=0.05), **kw) < score_item(_ep(confidence=0.9), **kw)


# ---------------------------------------------------------------------------
# Fail-closed khi ký ức mâu thuẫn
# ---------------------------------------------------------------------------
def test_conflicting_traits_are_both_dropped_not_arbitrated() -> None:
    """Hai mệnh đề cùng trait_key nhưng giá trị khác nhau → bỏ CẢ HAI.

    Chọn bên điểm cao hơn chính là đoán khi ta chưa thật sự biết."""
    a = MemoryItem(kind=KIND_TRAIT, text="thích sáng", confidence=0.9, trait_key="guests.brightness",
                   value={"brightness": "high"}, room="living_room")
    b = MemoryItem(kind=KIND_TRAIT, text="thích tối", confidence=0.8, trait_key="guests.brightness",
                   value={"brightness": "low"}, room="living_room")
    recall = retrieve([a, b], room="living_room", now_hour=20,
                      situation_tokens=situation_tokens_from("có khách"), min_score=0.0)
    assert recall.items == ()
    assert "guests.brightness" in recall.dropped_conflicts


def test_agreeing_traits_survive_as_one() -> None:
    a = MemoryItem(kind=KIND_TRAIT, text="thích sáng", confidence=0.9, trait_key="guests.brightness",
                   value={"brightness": "high"}, room="living_room")
    b = MemoryItem(kind=KIND_TRAIT, text="thích sáng", confidence=0.6, trait_key="guests.brightness",
                   value={"brightness": "high"}, room="living_room")
    recall = retrieve([a, b], room="living_room", now_hour=20,
                      situation_tokens=situation_tokens_from("có khách"), min_score=0.0)
    assert len(recall.items) == 1
    assert recall.dropped_conflicts == ()


# ---------------------------------------------------------------------------
# Lọc nhiễu và giữ bằng chứng nghịch
# ---------------------------------------------------------------------------
def test_irrelevant_memories_are_not_recalled_at_all() -> None:
    """Không liên quan thì thà không nhớ gì — nhớ nhiễu tệ hơn nhớ ít."""
    other = _ep(room="garage", hour=3, match_tokens=("rửa", "xe"), confidence=0.3)
    recall = retrieve([other], room="living_room", now_hour=20,
                      situation_tokens=situation_tokens_from("tối nay có khách"))
    assert recall.items == ()


def test_rejection_is_kept_even_when_top_k_is_full() -> None:
    """Bằng chứng NGHỊCH luôn có chỗ: cắt mất nó là agent lặp lại đúng cái vừa bị chối."""
    positives = [_ep(text=f"p{i}", confidence=0.9) for i in range(6)]
    negative = _ep(text="n", polarity="negative", confidence=0.5)
    recall = retrieve([*positives, negative], room="living_room", now_hour=20,
                      situation_tokens=situation_tokens_from("tối nay có khách"), limit=3)
    assert len(recall.items) <= 3
    assert any(i.polarity == "negative" for i in recall.items)


def test_same_room_alone_does_not_make_a_memory_relevant() -> None:
    """Cùng phòng KHÔNG phải là liên quan.

    Đo được trên đường thật: ký ức «tối nay có khách» bị gọi ra khi người dùng chỉ nói
    «hơi lạnh», chỉ vì cả hai đều ở phòng khách. Phòng và khung giờ là tín hiệu PHỤ TRỢ —
    một mình chúng không đủ để một ký ức được đưa vào suy luận."""
    guests = _ep(confidence=1.0, kind=KIND_TRAIT, trait_key="situation:guests")
    recall = retrieve([guests], room="living_room", now_hour=20,
                      situation_tokens=situation_tokens_from("hơi lạnh"))
    assert recall.items == ()


def test_matching_topic_still_recalls_across_the_same_room() -> None:
    """Ngược lại: đúng chủ đề thì vẫn phải nhớ ra (không siết tay quá đà)."""
    guests = _ep(confidence=1.0)
    recall = retrieve([guests], room="living_room", now_hour=20,
                      situation_tokens=situation_tokens_from("tối nay có khách"))
    assert len(recall.items) == 1


def test_device_overlap_alone_is_enough_when_wording_differs() -> None:
    """Nói cách khác hẳn nhưng cùng thiết bị vẫn là liên quan — khớp không chỉ dựa vào chữ."""
    item = _ep(match_tokens=("đèn", "chùm"), devices=("den_chum_phong_khach",), confidence=0.9)
    recall = retrieve([item], room="living_room", devices=frozenset({"den_chum_phong_khach"}),
                      now_hour=20, situation_tokens=situation_tokens_from("làm sáng lên chút"))
    assert len(recall.items) == 1


# ---------------------------------------------------------------------------
# select_routine: TÁI DÙNG chỉ khi ĐỦ chắc (ngưỡng cao hơn ngưỡng bằng chứng)
# ---------------------------------------------------------------------------
def _routine(**kw) -> RoutineCandidate:
    base = dict(
        trait_key="situation:cool_living_room:Phòng khách",
        goal={"desired_outcomes": [{"selector": {"domain": "air_conditioner"}}]},
        confidence=0.9,
        room="Phòng khách",
        match_tokens=("nong", "qua", "mat"),
        hours=(22,),
        user_id=1,
    )
    return RoutineCandidate(**{**base, **kw})


def test_select_routine_matches_on_strong_situation_room_hour() -> None:
    """Cùng chủ đề + phòng + giờ, độ tin cao → tái dùng."""
    match = select_routine(
        [_routine()], room="Phòng khách", now_hour=22,
        situation_tokens=situation_tokens_from("nóng quá muốn mát"),
    )
    assert match is not None
    assert match.goal["desired_outcomes"]


def test_select_routine_returns_none_below_score_threshold() -> None:
    """Câu nói không dính chủ đề routine → KHÔNG tái dùng (để LLM tự hiểu lại)."""
    match = select_routine(
        [_routine()], room="Phòng khách", now_hour=22,
        situation_tokens=situation_tokens_from("mở rèm phòng ngủ"),
    )
    assert match is None


def test_select_routine_returns_none_below_confidence() -> None:
    """Routine chưa đủ tin (bị chối nhiều) → không tự nhảy vào dù câu khớp."""
    match = select_routine(
        [_routine(confidence=0.3)], room="Phòng khách", now_hour=22,
        situation_tokens=situation_tokens_from("nóng quá muốn mát"),
    )
    assert match is None


def test_select_routine_picks_highest_scoring() -> None:
    """Nhiều routine khớp → chọn ĐÚNG một cái điểm cao nhất, không trả nhiều."""
    weak = _routine(trait_key="weak", confidence=0.6, room=None, hours=())
    strong = _routine(trait_key="strong", confidence=0.95)
    match = select_routine(
        [weak, strong], room="Phòng khách", now_hour=22,
        situation_tokens=situation_tokens_from("nóng quá muốn mát"),
    )
    assert match is not None and match.trait_key == "strong"
