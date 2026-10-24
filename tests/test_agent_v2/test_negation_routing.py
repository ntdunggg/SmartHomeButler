"""KI-01 regression — phủ định/sentiment KHÔNG được route nhầm sang `answer`.

Kiểm theo SEMANTIC FAMILY (không hardcode câu fail):
  F1. Sentiment/cảm thán có "không" thành ngữ, không đích thiết bị → clarify/act, KHÔNG từ chối.
  F2. Mục tiêu TÍCH CỰC + loại trừ ("X nhưng đừng Y") → plan/clarify, KHÔNG từ chối.
  F3. Cấm THUẦN TUÝ có thiết bị đã resolve ("đừng tắt đèn phòng khách") → answer "sẽ không...".
  F4. Cấm MƠ HỒ (không rõ thiết bị/phòng) → clarify, KHÔNG từ chối vu vơ.

Gate là `pipeline_bridge.is_pure_prohibition` (thuần, tất định) — test trực tiếp + end-to-end reason().
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.agent.cognitive.ledger import LedgerStore
from src.agent.memory.event_store import EventStore
from src.agent.memory.profile_store import ProfileStore
from src.agent.memory.turn_store import TurnStore
from src.agent.pipeline import PipelineDeps
from src.agent.preference.preference_store import PreferenceStore
from src.agent.schemas import DesiredOutcome, SemanticGoal
from src.core.interfaces import DeviceSelector
from src.core.reasoning import FakeReasoningModel
from src.nlu.ontology import UtteranceType
from src.services.pipeline_bridge import has_unresolved_exclusion, is_pure_prohibition, reason


def _goal(**kw) -> SemanticGoal:
    base = dict(
        intent="x", raw_utterance="x", utterance_type=UtteranceType.DEVICE_COMMAND, confidence=0.9,
    )
    base.update(kw)
    return SemanticGoal(**base)


def _cool_outcome() -> DesiredOutcome:
    return DesiredOutcome(selector=DeviceSelector(), perceived_state="hot", relative_change={"temperature": "decrease"})


# -- F-unit: is_pure_prohibition theo family -----------------------------------

def test_family_sentiment_negation_is_not_prohibition():
    """F1: polarity âm do thành ngữ, không đích thiết bị, không mục tiêu → KHÔNG phải cấm thuần tuý."""
    g = _goal(polarity="negative", negated=False, target_device_ids=[], desired_outcomes=[])
    assert is_pure_prohibition(g) is False


def test_family_positive_goal_with_exclusion_is_not_prohibition():
    """F2: có desired_outcome tích cực (dù negated) → ràng buộc, KHÔNG phải cấm thuần tuý."""
    g = _goal(negated=True, polarity="negative", target_device_ids=[], desired_outcomes=[_cool_outcome()])
    assert is_pure_prohibition(g) is False
    # Ngay cả khi có target vẫn KHÔNG cấm thuần tuý nếu còn mục tiêu tích cực.
    g2 = _goal(negated=True, target_device_ids=["dieu_hoa_phong_khach"], desired_outcomes=[_cool_outcome()])
    assert is_pure_prohibition(g2) is False


def test_family_pure_prohibition_with_resolved_device():
    """F3: phủ định + thiết bị đã resolve + không mục tiêu tích cực → cấm thuần tuý (được từ chối)."""
    g = _goal(action_hint="turn_off", negated=True, polarity="negative",
              target_device_ids=["den_chum_phong_khach"], desired_outcomes=[])
    assert is_pure_prohibition(g) is True


def test_family_vague_prohibition_without_device_is_not_prohibition():
    """F4: phủ định nhưng chưa resolve thiết bị → KHÔNG từ chối vu vơ (phải clarify)."""
    g = _goal(action_hint="turn_on", negated=True, polarity="negative", target_device_ids=[], desired_outcomes=[])
    assert is_pure_prohibition(g) is False


def test_affirmative_goal_never_prohibition():
    g = _goal(action_hint="turn_on", polarity="affirmative", target_device_ids=["den_chum_phong_khach"])
    assert is_pure_prohibition(g) is False


def test_none_goal_not_prohibition():
    assert is_pure_prohibition(None) is False


# -- F-integration: reason() end-to-end ---------------------------------------

def _deps() -> PipelineDeps:
    return PipelineDeps(
        ledger_store=LedgerStore(), event_store=EventStore(), turn_store=TurnStore(),
        preference_store=PreferenceStore(), profile_store=ProfileStore(), model_client=FakeReasoningModel(),
    )


def _reason(msg: str, **kw):
    return reason(message=msg, conversation_id="c", now=datetime(2026, 8, 15, 20, 0, tzinfo=UTC),
                  model_client=FakeReasoningModel(), deps=_deps(), **kw)


def test_reason_positive_with_exclusion_not_answered_as_refusal():
    """F2 end-to-end: "cho mát hơn ... nhưng đừng bật điều hoà" KHÔNG bị trả lời 'sẽ không' — plan/clarify."""
    r = _reason("cho mát hơn tí nhưng đừng bật điều hoà", focus_room="Phòng khách")
    assert r.outcome in ("candidate_plan", "clarification")
    assert r.outcome != "answer"


def test_reason_pure_prohibition_still_refuses():
    """F3 end-to-end: cấm thuần tuý có thiết bị resolve → giữ câu trả lời 'mình sẽ không...'."""
    r = _reason("đừng tắt đèn phòng khách")
    assert r.outcome == "answer"
    assert "không" in r.reply.lower()


def test_reason_vague_prohibition_clarifies():
    """F4 end-to-end: cấm mơ hồ (không rõ phòng/thiết bị) → clarify, không từ chối vu vơ."""
    r = _reason("đừng bật điều hoà")
    assert r.outcome == "clarification"


# -- F5: mục tiêu tích cực + loại trừ CHƯA resolve → clarify, KHÔNG plan mù (an toàn) ----------

def test_unresolved_exclusion_true_when_positive_goal_negated_no_excluded():
    g = _goal(action_hint=None, polarity="negative", desired_outcomes=[_cool_outcome()], excluded_device_ids=[])
    assert has_unresolved_exclusion(g) is True


def test_unresolved_exclusion_false_when_excluded_resolved():
    g = _goal(action_hint=None, polarity="negative", desired_outcomes=[_cool_outcome()],
              excluded_device_ids=["dieu_hoa_phong_khach"])
    assert has_unresolved_exclusion(g) is False


def test_unresolved_exclusion_false_without_positive_goal():
    g = _goal(polarity="negative", desired_outcomes=[], target_device_ids=["den_chum_phong_khach"])
    assert has_unresolved_exclusion(g) is False


def test_unresolved_exclusion_false_affirmative():
    g = _goal(action_hint=None, polarity="affirmative", desired_outcomes=[_cool_outcome()])
    assert has_unresolved_exclusion(g) is False


def test_reason_positive_with_unresolved_exclusion_clarifies_not_plan_with_forbidden():
    """F5 end-to-end: "cho mát hơn ... nhưng đừng bật điều hoà" (điều hoà mơ hồ) → clarify, KHÔNG
    lập kế hoạch có điều hoà (thứ vừa bị cấm)."""
    r = _reason("cho mát hơn tí nhưng đừng bật điều hoà", focus_room="Phòng khách")
    assert r.outcome == "clarification"
    devs = [a.device_id for a in r.candidate_plan.actions] if r.candidate_plan else []
    assert "dieu_hoa_phong_khach" not in devs


# -- Ambiguity: alias khớp nhiều thiết bị cùng loại, không phòng/không lượng từ → clarify -------

def test_bare_multi_alias_command_clarifies():
    """"mở rèm"/"kéo rèm lại" khớp 3 rèm mà không nêu phòng/không lượng từ → clarify, không mở hết."""
    for utt in ("mở rèm", "kéo rèm lại"):
        r = _reason(utt)
        assert r.outcome == "clarification", utt


def test_group_quantifier_multi_device_proceeds():
    """Lệnh NHÓM có lượng từ ("tắt hết rèm") vẫn ground toàn bộ, KHÔNG clarify."""
    r = _reason("tắt hết rèm")
    assert r.outcome == "candidate_plan"
    devs = {a.device_id for a in r.candidate_plan.actions}
    assert {"rem_phong_khach", "rem_phong_bo_me", "rem_phong_con"} <= devs


def test_explicit_room_single_device_proceeds():
    """Nêu phòng rõ ("mở rèm phòng khách") → ground đúng 1 thiết bị, không clarify."""
    r = _reason("mở rèm phòng khách")
    assert r.outcome == "candidate_plan"
    assert [a.device_id for a in r.candidate_plan.actions] == ["rem_phong_khach"]
