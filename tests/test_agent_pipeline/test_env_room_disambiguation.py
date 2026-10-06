"""Regression TỔNG QUÁT: yêu cầu tiện nghi (ENVIRONMENT_REQUEST) thiếu phòng → CLARIFY.

KHÔNG vá riêng 4 ca eval (nóng/tối/ồn quá). Luật chung áp cho MỌI comfort request: tiện nghi
là của MỘT phòng; phòng phải grounded từ câu nói / vị trí người nói / focus, TUYỆT ĐỐI không
lấy phòng LLM tự ĐOÁN. Test cố tình dùng cụm từ KHÔNG có trong dataset để chứng minh luật
khái quát theo utterance_type, không phải khớp chữ từng câu.

Phủ hai fix:
- validator (`validate`): comfort thiếu phòng → MISSING_AREA kể cả khi selector có labels.
- V2 goal author (`_finalize`): phòng LLM đoán bị bỏ nếu không có tín hiệu quan sát được.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.agent.understanding.goal_author import _finalize
from src.core.interfaces import DeviceSelector
from src.nlu.context import build_runtime_context
from src.nlu.normalizer import analyze
from src.nlu.ontology import UtteranceType
from src.nlu.schemas import DesiredOutcome, SemanticGoal, ValidationDecision
from src.nlu.validator import validate

_NOW = datetime(2026, 8, 5, 22, 0, tzinfo=UTC)
_BEDROOM = "Phòng ngủ bố mẹ"  # phòng THẬT trong registry


def _env_goal(utt: str, *, target_area: str | None = None, with_labels: bool = False) -> SemanticGoal:
    sel = DeviceSelector(area=None, labels=["ambient_lighting"] if with_labels else [])
    return SemanticGoal(
        intent="environment.request",
        raw_utterance=utt,
        confidence=0.85,
        utterance_type=UtteranceType.ENVIRONMENT_REQUEST,
        goal_description=utt,
        target_area=target_area,
        desired_outcomes=[
            DesiredOutcome(
                selector=sel,
                perceived_state="hot",
                relative_change={"temperature": "decrease"},
                cardinality="any",
            )
        ],
    )


def test_env_without_room_clarifies_even_with_labels() -> None:
    """Cụm lạ + selector CÓ labels (loophole cũ) + không phòng nào → phải HỎI phòng."""
    utt = "oi bức khó thở quá đi mất"
    nu = analyze(utt)  # câu không nêu phòng
    ctx = build_runtime_context(nu, now=_NOW)  # focus_room=None, speaker_location=None
    res = validate(_env_goal(utt, with_labels=True), nu=nu, ctx=ctx)
    assert res.decision == ValidationDecision.CLARIFY
    assert any(e.code == "MISSING_AREA" for e in res.errors)


def test_env_with_known_room_proceeds() -> None:
    """Cùng cụm lạ nhưng ĐÃ biết phòng → PROCEED, không hỏi thừa (không phải chặn mù)."""
    utt = "oi bức khó thở quá đi mất"
    nu = analyze(utt)
    ctx = build_runtime_context(nu, now=_NOW, focus_room=_BEDROOM)
    res = validate(_env_goal(utt, target_area=_BEDROOM), nu=nu, ctx=ctx)
    assert res.decision == ValidationDecision.PROCEED


def test_finalize_drops_llm_guessed_room_for_env() -> None:
    """LLM ĐOÁN 'Phòng khách' nhưng câu không nêu phòng & không có speaker_location → null."""
    utt = "ngột ngạt kinh khủng"
    nu = analyze(utt)
    ctx = build_runtime_context(nu, now=_NOW)  # không phòng nào
    final = _finalize(_env_goal(utt, target_area="Phòng khách"), nu=nu, ctx=ctx, utt=utt)
    assert final.target_area is None  # phòng LLM đoán bị bỏ → validator sẽ hỏi lại


def test_finalize_keeps_room_from_speaker_location_for_env() -> None:
    """Có speaker_location thật → dùng nó (grounded), KHÔNG null → sẽ proceed ở phòng đó."""
    utt = "ngột ngạt kinh khủng"
    nu = analyze(utt)
    ctx = build_runtime_context(nu, now=_NOW, speaker_location="Phòng ngủ con")
    final = _finalize(_env_goal(utt, target_area="Phòng khách"), nu=nu, ctx=ctx, utt=utt)
    assert final.target_area == "Phòng ngủ con"  # lấy phòng người nói, bỏ phòng LLM đoán
